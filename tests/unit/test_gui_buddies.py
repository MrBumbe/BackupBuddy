"""
Unit tests for gatekeeper/gui/routes/buddies.py.

Covers:
  - GET /buddies          — HTML page (setup mode, operational, Tailscale guard)
  - GET /api/buddies      — JSON snapshot (members, invites, votes, ADR-010 filter)
  - POST /api/buddies/invite               — generate invite code
  - POST /api/buddies/invite/{code}/revoke — revoke invite (valid, nonexistent)
  - POST /api/buddies/removal              — propose removal (valid, self-remove, unknown)
  - POST /api/buddies/vote/{id}/cast       — cast vote (yes/no, auto-resolve removal,
                                              auto-resolve grace extension, not found)
  - POST /api/buddies/grace-extend         — propose grace extension (valid, invalid)
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from gatekeeper.config import (
    FragmentationConfig,
    GatekeeperConfig,
    NodeConfig,
    StoragePoolEntry,
    TahoeConfig,
)
from gatekeeper.gui.app import setup_gui


# ── Fixtures ──────────────────────────────────────────────────────────────────

_LOCAL_NODE_ID = "local-node"
_OTHER_NODE_ID = "other-node"


def _make_config() -> GatekeeperConfig:
    return GatekeeperConfig(
        node=NodeConfig(name=_LOCAL_NODE_ID, display_name="Local Node"),
        tahoe=TahoeConfig(introducer="pb://fake", run_introducer=False),
        fragmentation=FragmentationConfig(profile="balanced"),
        storage_pool=[StoragePoolEntry(path="/fake/pool", quota_bytes=2 * 1024**3)],
    )


class _MockPool:
    def get_usage(self) -> list[dict]:
        return [{
            "path": "/fake/pool",
            "quota_bytes": 2 * 1024**3,
            "used_bytes": 512 * 1024**2,
            "free_bytes": 1536 * 1024**2,
        }]


class _MockClusterDB:
    """Minimal in-memory cluster DB for testing."""

    def __init__(self) -> None:
        self._members: list[dict] = []
        self._invites: list[dict] = []
        self._votes: list[dict] = []
        self._ballots: list[dict] = []
        self._next_vote_id = 1

    def list_members(self, status: str | None = None) -> list[dict]:
        if status is not None:
            return [m for m in self._members if m.get("status") == status]
        return list(self._members)

    def get_member(self, node_id: str) -> dict | None:
        return next((m for m in self._members if m["node_id"] == node_id), None)

    def update_member(self, node_id: str, **fields) -> None:
        for m in self._members:
            if m["node_id"] == node_id:
                m.update(fields)

    def insert_member(
        self,
        node_id: str,
        display_name: str,
        tailscale_hostname: str,
        joined_at: float,
        contribution_bytes: int = 0,
        usage_bytes: int = 0,
        profile: str = "lagom",
        status: str = "active",
    ) -> None:
        self._members.append({
            "node_id": node_id,
            "display_name": display_name,
            "tailscale_hostname": tailscale_hostname,
            "joined_at": joined_at,
            "contribution_bytes": contribution_bytes,
            "usage_bytes": usage_bytes,
            "profile": profile,
            "status": status,
            "grace_started_at": None,
            "grace_days": 7,
        })

    def list_invites(self) -> list[dict]:
        return list(self._invites)

    def get_invite(self, code: str) -> dict | None:
        return next((i for i in self._invites if i["code"] == code), None)

    def insert_invite(
        self, code: str, created_by: str, created_at: float, expires_at: float
    ) -> None:
        self._invites.append({
            "code": code,
            "created_by": created_by,
            "created_at": created_at,
            "expires_at": expires_at,
            "used": 0,
            "revoked": 0,
        })

    def update_invite(self, code: str, **fields) -> None:
        for inv in self._invites:
            if inv["code"] == code:
                inv.update(fields)

    def list_votes(self, resolved: bool | None = None) -> list[dict]:
        if resolved is not None:
            return [v for v in self._votes if bool(v["resolved"]) == bool(resolved)]
        return list(self._votes)

    def get_vote(self, vote_id: int) -> dict | None:
        return next((v for v in self._votes if v["id"] == vote_id), None)

    def insert_vote(
        self,
        vote_type: str,
        target_node_id: str,
        proposed_by: str,
        proposed_at: float,
        closes_at: float,
        grace_extension_days: int | None = None,
    ) -> int:
        vid = self._next_vote_id
        self._next_vote_id += 1
        self._votes.append({
            "id": vid,
            "vote_type": vote_type,
            "target_node_id": target_node_id,
            "proposed_by": proposed_by,
            "proposed_at": proposed_at,
            "closes_at": closes_at,
            "votes_yes": 0,
            "votes_no": 0,
            "resolved": 0,
            "grace_extension_days": grace_extension_days,
        })
        return vid

    def update_vote(self, vote_id: int, **fields) -> None:
        for v in self._votes:
            if v["id"] == vote_id:
                v.update(fields)

    def list_ballots(self, vote_id: int) -> list[dict]:
        return [b for b in self._ballots if b["vote_id"] == vote_id]

    def insert_ballot(
        self, vote_id: int, voter_node_id: str, voted_at: float, choice: int
    ) -> None:
        self._ballots.append({
            "vote_id": vote_id,
            "voter_node_id": voter_node_id,
            "voted_at": voted_at,
            "choice": choice,
        })

    def get_last_lifeboat_status(self) -> dict | None:
        return None


def _make_app(setup_required: bool = False, cluster_db: _MockClusterDB | None = None) -> FastAPI:
    app = FastAPI()
    app.state.setup_required = setup_required
    if not setup_required:
        app.state.config = _make_config()
        app.state.pool = _MockPool()
        app.state.cluster_db = cluster_db if cluster_db is not None else _MockClusterDB()
        app.state.catalog_db = None
        app.state.local_node_id = _LOCAL_NODE_ID
        app.state.config_path = None
        app.state.data_dir = None
    setup_gui(app, gui_on_lan=True, gui_on_tailscale=True)
    return app


def _ts(app: FastAPI) -> TestClient:
    """TestClient with a Tailscale source IP."""
    return TestClient(app, client=("100.64.0.1", 9999))


def _non_ts(app: FastAPI) -> TestClient:
    """TestClient with a LAN source IP."""
    return TestClient(app, client=("192.168.1.1", 9999))


# ── GET /buddies ───────────────────────────────────────────────────────────────

def test_buddies_page_setup_mode():
    app = _make_app(setup_required=True)
    r = _ts(app).get("/buddies")
    assert r.status_code == 200
    assert "setup" in r.text.lower()


def test_buddies_page_accessible_from_lan():
    """Default config (gui_on_lan=True, gui_on_tailscale=True in test helper): both allowed."""
    app = _make_app()
    r = _non_ts(app).get("/buddies")
    assert r.status_code == 200


def test_buddies_page_operational():
    db = _MockClusterDB()
    db.insert_member(_LOCAL_NODE_ID, "Local Node", "100.64.0.1", time.time())
    db.insert_member(_OTHER_NODE_ID, "Alice", "100.64.0.2", time.time())
    app = _make_app(cluster_db=db)
    r = _ts(app).get("/buddies")
    assert r.status_code == 200
    assert "Alice" in r.text


# ── GET /api/buddies ───────────────────────────────────────────────────────────

def test_api_buddies_setup_mode():
    app = _make_app(setup_required=True)
    r = _ts(app).get("/api/buddies")
    assert r.status_code == 200
    assert r.json()["setup_required"] is True


def test_api_buddies_members_and_storage():
    db = _MockClusterDB()
    db.insert_member(_LOCAL_NODE_ID, "Local Node", "100.64.0.1", time.time(),
                     contribution_bytes=1024**3, usage_bytes=512 * 1024**2)
    db.insert_member(_OTHER_NODE_ID, "Alice", "100.64.0.2", time.time())
    app = _make_app(cluster_db=db)
    r = _ts(app).get("/api/buddies")
    assert r.status_code == 200
    data = r.json()
    assert data["setup_required"] is False
    assert len(data["members"]) == 2
    assert data["total_capacity_bytes"] > 0
    self_member = next(m for m in data["members"] if m["node_id"] == _LOCAL_NODE_ID)
    assert self_member["is_self"] is True


def test_api_buddies_adr010_vote_filter():
    """Open removal votes targeting the local node must be hidden (ADR-010)."""
    db = _MockClusterDB()
    db.insert_member(_LOCAL_NODE_ID, "Local Node", "100.64.0.1", time.time())
    db.insert_member(_OTHER_NODE_ID, "Alice", "100.64.0.2", time.time())
    now = time.time()
    db.insert_vote("removal", _LOCAL_NODE_ID, _OTHER_NODE_ID, now, now + 172800)
    app = _make_app(cluster_db=db)
    r = _ts(app).get("/api/buddies")
    assert r.status_code == 200
    assert r.json()["votes"] == []


def test_api_buddies_visible_vote():
    """Open removal vote targeting another node must be visible."""
    db = _MockClusterDB()
    third = "third-node"
    db.insert_member(_LOCAL_NODE_ID, "Local Node", "100.64.0.1", time.time())
    db.insert_member(_OTHER_NODE_ID, "Alice", "100.64.0.2", time.time())
    db.insert_member(third, "Bob", "100.64.0.3", time.time())
    now = time.time()
    db.insert_vote("removal", _OTHER_NODE_ID, _LOCAL_NODE_ID, now, now + 172800)
    app = _make_app(cluster_db=db)
    r = _ts(app).get("/api/buddies")
    votes = r.json()["votes"]
    assert len(votes) == 1
    assert votes[0]["target_node_id"] == _OTHER_NODE_ID


def test_api_buddies_invite_masking():
    db = _MockClusterDB()
    now = time.time()
    db.insert_invite("coffee-trumpet-7", _LOCAL_NODE_ID, now, now + 172800)
    db.insert_member(_LOCAL_NODE_ID, "Local Node", "100.64.0.1", now)
    app = _make_app(cluster_db=db)
    r = _ts(app).get("/api/buddies")
    invites = r.json()["invites"]
    assert len(invites) == 1
    assert invites[0]["code"] == "coffee-***-7"
    assert invites[0]["code_raw"] == "coffee-trumpet-7"


# ── POST /api/buddies/invite ───────────────────────────────────────────────────

def test_generate_invite():
    db = _MockClusterDB()
    app = _make_app(cluster_db=db)
    r = _ts(app).post("/api/buddies/invite")
    assert r.status_code == 200
    data = r.json()
    assert "code" in data
    assert "expires_at" in data
    assert len(db._invites) == 1
    assert db._invites[0]["created_by"] == _LOCAL_NODE_ID


def test_generate_invite_503_in_setup_mode():
    app = _make_app(setup_required=True)
    r = _ts(app).post("/api/buddies/invite")
    assert r.status_code == 503


# ── POST /api/buddies/invite/{code}/revoke ────────────────────────────────────

def test_revoke_invite():
    db = _MockClusterDB()
    now = time.time()
    db.insert_invite("coffee-trumpet-7", _LOCAL_NODE_ID, now, now + 172800)
    app = _make_app(cluster_db=db)
    r = _ts(app).post("/api/buddies/invite/coffee-trumpet-7/revoke")
    assert r.status_code == 200
    assert db._invites[0]["revoked"] == 1


def test_revoke_nonexistent_invite():
    db = _MockClusterDB()
    app = _make_app(cluster_db=db)
    r = _ts(app).post("/api/buddies/invite/no-such-code/revoke")
    assert r.status_code == 400
    assert "error" in r.json()


# ── POST /api/buddies/removal ─────────────────────────────────────────────────

def test_propose_removal():
    db = _MockClusterDB()
    now = time.time()
    db.insert_member(_LOCAL_NODE_ID, "Local", "100.64.0.1", now)
    db.insert_member(_OTHER_NODE_ID, "Alice", "100.64.0.2", now)
    app = _make_app(cluster_db=db)
    r = _ts(app).post("/api/buddies/removal", json={"target_node_id": _OTHER_NODE_ID})
    assert r.status_code == 200
    data = r.json()
    assert data["target_node_id"] == _OTHER_NODE_ID
    assert "vote_id" in data
    assert len(db._votes) == 1


def test_propose_removal_self_rejected():
    db = _MockClusterDB()
    db.insert_member(_LOCAL_NODE_ID, "Local", "100.64.0.1", time.time())
    app = _make_app(cluster_db=db)
    r = _ts(app).post("/api/buddies/removal", json={"target_node_id": _LOCAL_NODE_ID})
    assert r.status_code == 400
    assert "error" in r.json()


def test_propose_removal_unknown_target():
    db = _MockClusterDB()
    db.insert_member(_LOCAL_NODE_ID, "Local", "100.64.0.1", time.time())
    app = _make_app(cluster_db=db)
    r = _ts(app).post("/api/buddies/removal", json={"target_node_id": "ghost-node"})
    assert r.status_code == 400


# ── POST /api/buddies/vote/{id}/cast ─────────────────────────────────────────

def test_cast_vote_yes():
    db = _MockClusterDB()
    now = time.time()
    db.insert_member(_LOCAL_NODE_ID, "Local", "100.64.0.1", now)
    db.insert_member(_OTHER_NODE_ID, "Alice", "100.64.0.2", now)
    vid = db.insert_vote("removal", _OTHER_NODE_ID, _LOCAL_NODE_ID, now, now + 172800)
    app = _make_app(cluster_db=db)
    r = _ts(app).post(f"/api/buddies/vote/{vid}/cast", json={"choice": True})
    assert r.status_code == 200
    assert r.json()["result"] in ("passed", "failed", "pending")
    assert len(db._ballots) == 1
    assert db._ballots[0]["choice"] == 1


def test_cast_vote_nonexistent():
    db = _MockClusterDB()
    db.insert_member(_LOCAL_NODE_ID, "Local", "100.64.0.1", time.time())
    app = _make_app(cluster_db=db)
    r = _ts(app).post("/api/buddies/vote/999/cast", json={"choice": True})
    assert r.status_code == 404


def test_cast_vote_removal_auto_starts_grace():
    """When a removal vote passes, start_grace_period must be called automatically."""
    db = _MockClusterDB()
    now = time.time()
    # Two members — majority threshold is 1
    db.insert_member(_LOCAL_NODE_ID, "Local", "100.64.0.1", now)
    db.insert_member(_OTHER_NODE_ID, "Alice", "100.64.0.2", now)
    vid = db.insert_vote("removal", _OTHER_NODE_ID, _LOCAL_NODE_ID, now, now + 172800)
    app = _make_app(cluster_db=db)
    r = _ts(app).post(f"/api/buddies/vote/{vid}/cast", json={"choice": True})
    assert r.status_code == 200
    assert r.json()["result"] == "passed"
    target = db.get_member(_OTHER_NODE_ID)
    assert target["status"] == "grace"


def test_cast_vote_grace_extension_auto_applies():
    """When a grace extension vote passes, apply_grace_extension must run."""
    db = _MockClusterDB()
    now = time.time()
    db.insert_member(_LOCAL_NODE_ID, "Local", "100.64.0.1", now)
    # Alice is in grace status with 7 days remaining
    db.insert_member(_OTHER_NODE_ID, "Alice", "100.64.0.2", now, status="grace")
    vid = db.insert_vote("grace_extension", _OTHER_NODE_ID, _LOCAL_NODE_ID,
                         now, now + 172800, grace_extension_days=14)
    app = _make_app(cluster_db=db)
    r = _ts(app).post(f"/api/buddies/vote/{vid}/cast", json={"choice": True})
    assert r.status_code == 200
    assert r.json()["result"] == "passed"
    target = db.get_member(_OTHER_NODE_ID)
    assert target["grace_days"] == 21  # 7 original + 14 extension


# ── POST /api/buddies/grace-extend ───────────────────────────────────────────

def test_grace_extend_proposes_vote():
    db = _MockClusterDB()
    now = time.time()
    db.insert_member(_LOCAL_NODE_ID, "Local", "100.64.0.1", now)
    db.insert_member(_OTHER_NODE_ID, "Alice", "100.64.0.2", now, status="grace")
    app = _make_app(cluster_db=db)
    r = _ts(app).post("/api/buddies/grace-extend",
                      json={"target_node_id": _OTHER_NODE_ID, "days": 14})
    assert r.status_code == 200
    data = r.json()
    assert data["grace_extension_days"] == 14
    assert data["target_node_id"] == _OTHER_NODE_ID
    assert len(db._votes) == 1
    assert db._votes[0]["vote_type"] == "grace_extension"


def test_grace_extend_invalid_target():
    db = _MockClusterDB()
    db.insert_member(_LOCAL_NODE_ID, "Local", "100.64.0.1", time.time())
    app = _make_app(cluster_db=db)
    r = _ts(app).post("/api/buddies/grace-extend",
                      json={"target_node_id": "ghost-node", "days": 7})
    assert r.status_code == 400
    assert "error" in r.json()
