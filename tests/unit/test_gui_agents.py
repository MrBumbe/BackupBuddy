"""
Unit tests for gatekeeper/gui/routes/agents.py.

Covers:
  - GET /agents         — HTML page (setup mode, operational, Tailscale guard)
  - GET /api/agents     — JSON snapshot (agents list, share_log flag, recent events)
  - share_log = false   — recent events returned, no log note
  - share_log = true    — recent events returned with log-sharing note
  - offline agent       — last_seen older than 15 minutes
  - empty agent list    — no agents registered
  - catalog data absent — graceful fallback
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

_TS_CLIENT = ("100.64.0.1", 9999)
_LAN_CLIENT = ("192.168.1.10", 9999)


def _make_config() -> GatekeeperConfig:
    return GatekeeperConfig(
        node=NodeConfig(name="local-node", display_name="Local"),
        tahoe=TahoeConfig(introducer="pb://fake", run_introducer=False),
        fragmentation=FragmentationConfig(profile="balanced"),
        storage_pool=[StoragePoolEntry(path="/fake/pool", quota_bytes=2 * 1024**3)],
    )


class _MockPool:
    def get_usage(self) -> list[dict]:
        return [{"path": "/fake/pool", "quota_bytes": 2 * 1024**3, "used_bytes": 0, "free_bytes": 2 * 1024**3}]


class _MockClusterDB:
    def __init__(self, agents: list[dict] | None = None) -> None:
        self._agents: list[dict] = agents or []

    def list_agents(self) -> list[dict]:
        return list(self._agents)

    def list_members(self, status=None):
        return []

    def get_member(self, node_id):
        return None

    def list_invites(self):
        return []

    def list_votes(self, resolved=None):
        return []

    def get_last_lifeboat_status(self):
        return None

    def get_rebalance_state(self):
        return None


class _MockCatalogDB:
    def __init__(self, per_agent: list[dict] | None = None, recent: list[dict] | None = None) -> None:
        self._per_agent: list[dict] = per_agent or []
        self._recent: list[dict] = recent or []

    def get_last_backup_per_agent(self) -> list[dict]:
        return list(self._per_agent)

    def get_recent_backups_for_agent(self, agent: str, limit: int = 10) -> list[dict]:
        return list(self._recent)

    def get_all_files(self):
        return []


def _make_app(
    *,
    setup_required: bool = False,
    agents: list[dict] | None = None,
    per_agent_catalog: list[dict] | None = None,
    recent_events: list[dict] | None = None,
) -> FastAPI:
    app = FastAPI()
    setup_gui(app, gui_on_lan=True, gui_on_tailscale=True)

    app.state.setup_required = setup_required
    app.state.config = _make_config()
    app.state.local_node_id = "local-node"
    app.state.pool = _MockPool()
    app.state.cluster_db = _MockClusterDB(agents=agents)
    app.state.catalog_db = _MockCatalogDB(per_agent=per_agent_catalog, recent=recent_events)
    return app


def _ts_client(app: FastAPI) -> TestClient:
    return TestClient(app, client=_TS_CLIENT, raise_server_exceptions=True)


def _lan_client(app: FastAPI) -> TestClient:
    return TestClient(app, client=_LAN_CLIENT, raise_server_exceptions=True)


# ── Setup mode ────────────────────────────────────────────────────────────────

class TestAgentsSetupMode:
    def test_html_setup_mode(self):
        client = _ts_client(_make_app(setup_required=True))
        r = client.get("/agents")
        assert r.status_code == 200
        assert "not been configured" in r.text

    def test_api_setup_mode(self):
        client = _ts_client(_make_app(setup_required=True))
        r = client.get("/api/agents")
        assert r.status_code == 200
        assert r.json()["setup_required"] is True


# ── Access control (test helper uses gui_on_lan=True, gui_on_tailscale=True) ──

class TestAgentsAccessControl:
    def test_accepts_lan_ip(self):
        client = _lan_client(_make_app())
        assert client.get("/agents").status_code == 200

    def test_accepts_tailscale_ip(self):
        client = _ts_client(_make_app())
        assert client.get("/agents").status_code == 200


# ── Empty agent list ──────────────────────────────────────────────────────────

class TestAgentsEmpty:
    def test_html_no_agents(self):
        client = _ts_client(_make_app(agents=[]))
        r = client.get("/agents")
        assert r.status_code == 200
        assert "No agents registered yet" in r.text

    def test_api_no_agents(self):
        data = _ts_client(_make_app(agents=[])).get("/api/agents").json()
        assert data["agent_count"] == 0
        assert data["agents"] == []


# ── Online/offline detection ──────────────────────────────────────────────────

def _make_agent(last_seen_ago: float, share_log: bool = False) -> dict:
    return {
        "agent_name": "agent-1",
        "ip": "192.168.1.50",
        "lifeboat_url": None,
        "registered_at": time.time() - 3600,
        "last_seen": time.time() - last_seen_ago,
        "share_log": int(share_log),
    }


class TestAgentsOnlineStatus:
    def test_online_agent(self):
        data = _ts_client(_make_app(agents=[_make_agent(60)])).get("/api/agents").json()
        assert data["agents"][0]["is_online"] is True

    def test_offline_agent_over_15_minutes(self):
        data = _ts_client(_make_app(agents=[_make_agent(901)])).get("/api/agents").json()
        assert data["agents"][0]["is_online"] is False

    def test_html_online_badge(self):
        r = _ts_client(_make_app(agents=[_make_agent(30)])).get("/agents")
        assert "Online" in r.text

    def test_html_offline_badge(self):
        r = _ts_client(_make_app(agents=[_make_agent(9999)])).get("/agents")
        assert "Offline" in r.text


# ── share_log flag ────────────────────────────────────────────────────────────

class TestAgentsShareLog:
    def test_api_share_log_false(self):
        data = _ts_client(_make_app(agents=[_make_agent(10, share_log=False)])).get("/api/agents").json()
        assert data["agents"][0]["share_log"] is False

    def test_api_share_log_true(self):
        data = _ts_client(_make_app(agents=[_make_agent(10, share_log=True)])).get("/api/agents").json()
        assert data["agents"][0]["share_log"] is True

    def test_html_share_log_false_shows_disabled(self):
        r = _ts_client(_make_app(agents=[_make_agent(10, share_log=False)])).get("/agents")
        assert "Disabled" in r.text

    def test_html_share_log_true_shows_enabled(self):
        r = _ts_client(_make_app(agents=[_make_agent(10, share_log=True)])).get("/agents")
        assert "Enabled" in r.text

    def test_html_share_log_false_shows_privacy_note(self):
        r = _ts_client(_make_app(agents=[_make_agent(10, share_log=False)])).get("/agents")
        assert "share_log" in r.text

    def test_html_share_log_true_shows_log_note(self):
        r = _ts_client(_make_app(agents=[_make_agent(10, share_log=True)])).get("/agents")
        assert "log sharing is enabled" in r.text.lower()


# ── Catalog data ──────────────────────────────────────────────────────────────

_BASE_AGENT = {
    "agent_name": "myagent",
    "ip": "192.168.1.55",
    "lifeboat_url": None,
    "registered_at": time.time() - 7200,
    "last_seen": time.time() - 30,
    "share_log": 0,
}


class TestAgentsCatalogData:
    def test_file_count_from_catalog(self):
        per_agent = [{"agent": "myagent", "last_backup_at": time.time() - 300, "file_count": 42}]
        data = _ts_client(_make_app(agents=[_BASE_AGENT], per_agent_catalog=per_agent)).get("/api/agents").json()
        assert data["agents"][0]["file_count"] == 42

    def test_last_backup_at_from_catalog(self):
        ts = time.time() - 600
        per_agent = [{"agent": "myagent", "last_backup_at": ts, "file_count": 5}]
        data = _ts_client(_make_app(agents=[_BASE_AGENT], per_agent_catalog=per_agent)).get("/api/agents").json()
        assert abs(data["agents"][0]["last_backup_at"] - ts) < 1.0

    def test_recent_events_returned(self):
        events = [
            {"backed_up_at": time.time() - i * 60, "size_bytes": 1024 * i, "profile": "balanced"}
            for i in range(1, 5)
        ]
        data = _ts_client(_make_app(agents=[_BASE_AGENT], recent_events=events)).get("/api/agents").json()
        assert len(data["agents"][0]["recent_events"]) == 4

    def test_no_catalog_db_graceful(self):
        app = _make_app(agents=[_BASE_AGENT])
        app.state.catalog_db = None
        data = _ts_client(app).get("/api/agents").json()
        assert data["agents"][0]["file_count"] == 0
        assert data["agents"][0]["recent_events"] == []

    def test_agent_not_in_catalog(self):
        data = _ts_client(_make_app(agents=[_BASE_AGENT], per_agent_catalog=[])).get("/api/agents").json()
        assert data["agents"][0]["file_count"] == 0
        assert data["agents"][0]["last_backup_at"] is None


# ── Multiple agents ───────────────────────────────────────────────────────────

class TestAgentsMultiple:
    def _agents(self, n: int) -> list[dict]:
        return [
            {"agent_name": f"agent-{i}", "ip": f"192.168.1.{i + 50}",
             "lifeboat_url": None, "registered_at": time.time() - 100,
             "last_seen": time.time() - 10, "share_log": 0}
            for i in range(n)
        ]

    def test_multiple_agents_count(self):
        data = _ts_client(_make_app(agents=self._agents(3))).get("/api/agents").json()
        assert data["agent_count"] == 3
        assert len(data["agents"]) == 3

    def test_html_shows_all_agent_names(self):
        agents = [
            {"agent_name": f"box-{i}", "ip": f"10.0.0.{i}",
             "lifeboat_url": None, "registered_at": time.time() - 100,
             "last_seen": time.time() - 5, "share_log": 0}
            for i in range(2)
        ]
        r = _ts_client(_make_app(agents=agents)).get("/agents")
        assert "box-0" in r.text
        assert "box-1" in r.text
