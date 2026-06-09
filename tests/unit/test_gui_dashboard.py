"""
Unit tests for gatekeeper/gui/routes/dashboard.py.

Covers:
  - _build_dashboard_data(): data assembly from app.state
  - GET /api/dashboard: JSON structure, setup mode, ratio thresholds, agent online/offline
  - GET /: HTML rendering, status badge
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from gatekeeper.gui.app import setup_gui
from gatekeeper.gui.routes.dashboard import _AGENT_OFFLINE_SECONDS, _RATIO_ERROR, _RATIO_WARNING


# ── Mock state objects ────────────────────────────────────────────────────────

class _MockClusterDB:
    def __init__(
        self,
        members: list[dict] | None = None,
        agents: list[dict] | None = None,
        rebalance: dict | None = None,
        lifeboat: dict | None = None,
    ) -> None:
        self._members = members or []
        self._agents = agents or []
        self._rebalance = rebalance
        self._lifeboat = lifeboat

    def list_members(self) -> list[dict]:
        return self._members

    def list_agents(self) -> list[dict]:
        return self._agents

    def get_rebalance_state(self) -> dict | None:
        return self._rebalance

    def get_last_lifeboat_status(self) -> dict | None:
        return self._lifeboat


class _MockCatalogDB:
    def __init__(self, backup_data: list[dict] | None = None) -> None:
        self._data = backup_data or []

    def get_last_backup_per_agent(self) -> list[dict]:
        return self._data


class _MockPool:
    def __init__(self, paths: list[dict] | None = None) -> None:
        self._paths = paths or []

    def get_usage(self) -> list[dict]:
        return self._paths


def _make_app(
    setup_required: bool = False,
    cluster_db: _MockClusterDB | None = None,
    catalog_db: _MockCatalogDB | None = None,
    pool: _MockPool | None = None,
) -> FastAPI:
    app = FastAPI()
    app.state.setup_required = setup_required
    if not setup_required:
        app.state.cluster_db = cluster_db if cluster_db is not None else _MockClusterDB()
        app.state.catalog_db = catalog_db if catalog_db is not None else _MockCatalogDB()
        app.state.pool = pool if pool is not None else _MockPool()
    setup_gui(app, gui_on_lan=True, gui_on_tailscale=True)
    return app


def _ts_client(app: FastAPI) -> TestClient:
    return TestClient(app, client=("100.64.0.1", 9999))


# ── GET /api/dashboard — setup mode ──────────────────────────────────────────

class TestDashboardApiSetupMode:
    def test_returns_setup_required_true(self):
        resp = _ts_client(_make_app(setup_required=True)).get("/api/dashboard")
        assert resp.status_code == 200
        assert resp.json()["setup_required"] is True

    def test_returns_only_required_keys_in_setup_mode(self):
        data = _ts_client(_make_app(setup_required=True)).get("/api/dashboard").json()
        assert set(data.keys()) == {"setup_required", "node_name"}

    def test_node_name_defaults_when_no_config(self):
        data = _ts_client(_make_app(setup_required=True)).get("/api/dashboard").json()
        assert isinstance(data["node_name"], str)
        assert len(data["node_name"]) > 0


# ── GET /api/dashboard — operational mode ────────────────────────────────────

class TestDashboardApiOperational:
    def test_returns_setup_required_false(self):
        data = _ts_client(_make_app()).get("/api/dashboard").json()
        assert data["setup_required"] is False

    def test_returns_all_top_level_keys(self):
        data = _ts_client(_make_app()).get("/api/dashboard").json()
        for key in ("setup_required", "node_name", "cluster", "storage_pool", "agents", "jobs"):
            assert key in data, f"missing key: {key}"

    def test_cluster_structure(self):
        data = _ts_client(_make_app()).get("/api/dashboard").json()
        c = data["cluster"]
        assert "total_members" in c
        assert "online_count" in c
        assert "members" in c
        assert isinstance(c["members"], list)

    def test_storage_pool_structure(self):
        data = _ts_client(_make_app()).get("/api/dashboard").json()
        s = data["storage_pool"]
        for key in ("paths", "total_quota_bytes", "total_used_bytes", "total_percent"):
            assert key in s

    def test_jobs_structure(self):
        data = _ts_client(_make_app()).get("/api/dashboard").json()
        j = data["jobs"]
        assert "rebalance" in j
        assert "lifeboat" in j
        assert "in_progress" in j["rebalance"]
        assert "distributed_at" in j["lifeboat"]

    def test_accessible_from_lan(self):
        client = TestClient(_make_app(), client=("10.0.0.1", 9999))
        assert client.get("/api/dashboard").status_code == 200

    def test_empty_cluster_when_no_members(self):
        data = _ts_client(_make_app()).get("/api/dashboard").json()
        assert data["cluster"]["total_members"] == 0
        assert data["cluster"]["online_count"] == 0
        assert data["cluster"]["members"] == []


# ── Buddy ratio thresholds (ADR-013) ─────────────────────────────────────────

def _member(contribution_bytes: int, usage_bytes: int) -> dict:
    return {
        "node_id": "x",
        "display_name": "Alice",
        "tailscale_hostname": "alice",
        "joined_at": 0.0,
        "contribution_bytes": contribution_bytes,
        "usage_bytes": usage_bytes,
        "profile": "balanced",
        "status": "active",
    }


class TestRatioThresholds:
    def _get_member(self, contrib: int, usage: int) -> dict:
        db = _MockClusterDB(members=[_member(contrib, usage)])
        return _ts_client(_make_app(cluster_db=db)).get("/api/dashboard").json()["cluster"]["members"][0]

    def test_ratio_computed_correctly(self):
        m = self._get_member(150, 100)
        assert abs(m["ratio"] - 1.5) < 0.001

    def test_ratio_ok_above_warning_threshold(self):
        m = self._get_member(int(_RATIO_WARNING * 100) + 1, 100)
        assert m["warning"] is False
        assert m["error"] is False

    def test_ratio_warning_below_1_2(self):
        m = self._get_member(110, 100)  # 1.1x — below 1.2 warning
        assert m["warning"] is True
        assert m["error"] is False

    def test_ratio_error_below_1_0(self):
        m = self._get_member(90, 100)   # 0.9x — below 1.0 error
        assert m["warning"] is True
        assert m["error"] is True

    def test_ratio_none_when_usage_zero(self):
        m = self._get_member(500, 0)
        assert m["ratio"] is None
        assert m["warning"] is False
        assert m["error"] is False


# ── Agent online/offline detection ───────────────────────────────────────────

def _agent(name: str, last_seen: float | None) -> dict:
    return {
        "agent_name": name,
        "ip": "10.0.0.1",
        "lifeboat_url": None,
        "registered_at": 0.0,
        "last_seen": last_seen,
    }


class TestAgentOnlineStatus:
    def _get_agent(self, last_seen: float | None) -> dict:
        db = _MockClusterDB(agents=[_agent("laptop", last_seen)])
        return _ts_client(_make_app(cluster_db=db)).get("/api/dashboard").json()["agents"][0]

    def test_online_within_threshold(self):
        a = self._get_agent(time.time() - (_AGENT_OFFLINE_SECONDS - 60))
        assert a["online"] is True

    def test_offline_past_threshold(self):
        a = self._get_agent(time.time() - (_AGENT_OFFLINE_SECONDS + 60))
        assert a["online"] is False

    def test_offline_when_last_seen_none(self):
        a = self._get_agent(None)
        assert a["online"] is False

    def test_file_count_from_catalog(self):
        cluster_db = _MockClusterDB(agents=[_agent("laptop", time.time())])
        catalog_db = _MockCatalogDB(backup_data=[
            {"agent": "laptop", "last_backup_at": time.time() - 3600, "file_count": 42}
        ])
        a = _ts_client(_make_app(cluster_db=cluster_db, catalog_db=catalog_db)).get("/api/dashboard").json()["agents"][0]
        assert a["file_count"] == 42

    def test_file_count_zero_for_unknown_agent(self):
        db = _MockClusterDB(agents=[_agent("laptop", time.time())])
        a = _ts_client(_make_app(cluster_db=db)).get("/api/dashboard").json()["agents"][0]
        assert a["file_count"] == 0


# ── Storage pool percentage ───────────────────────────────────────────────────

class TestStoragePoolPercent:
    def test_percent_computed(self):
        pool = _MockPool(paths=[{
            "path": "/data",
            "quota_bytes": 1000,
            "used_bytes": 500,
            "free_bytes": 500,
        }])
        data = _ts_client(_make_app(pool=pool)).get("/api/dashboard").json()
        assert data["storage_pool"]["paths"][0]["percent"] == 50.0

    def test_total_percent_aggregated(self):
        pool = _MockPool(paths=[
            {"path": "/a", "quota_bytes": 1000, "used_bytes": 200, "free_bytes": 800},
            {"path": "/b", "quota_bytes": 1000, "used_bytes": 800, "free_bytes": 200},
        ])
        data = _ts_client(_make_app(pool=pool)).get("/api/dashboard").json()
        assert data["storage_pool"]["total_percent"] == 50.0

    def test_percent_zero_when_no_paths(self):
        data = _ts_client(_make_app()).get("/api/dashboard").json()
        assert data["storage_pool"]["total_percent"] == 0.0


# ── GET / — HTML dashboard ────────────────────────────────────────────────────

class TestDashboardHtml:
    def test_returns_200_html_for_tailscale_ip(self):
        resp = _ts_client(_make_app()).get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_setup_badge_when_setup_required(self):
        resp = _ts_client(_make_app(setup_required=True)).get("/")
        assert resp.status_code == 200
        assert "setup" in resp.text.lower()

    def test_running_badge_when_operational(self):
        resp = _ts_client(_make_app(setup_required=False)).get("/")
        assert resp.status_code == 200
        assert "running" in resp.text.lower()

    def test_accessible_from_lan(self):
        client = TestClient(_make_app(), client=("192.168.1.1", 9999))
        assert client.get("/").status_code == 200

    def test_poll_script_present_when_operational(self):
        resp = _ts_client(_make_app(setup_required=False)).get("/")
        assert "api/dashboard" in resp.text

    def test_no_poll_script_in_setup_mode(self):
        resp = _ts_client(_make_app(setup_required=True)).get("/")
        assert "api/dashboard" not in resp.text

    def test_cluster_members_rendered(self):
        db = _MockClusterDB(members=[_member(200, 100)])
        resp = _ts_client(_make_app(cluster_db=db)).get("/")
        assert "Alice" in resp.text

    def test_agent_rendered(self):
        db = _MockClusterDB(agents=[_agent("my-laptop", time.time())])
        resp = _ts_client(_make_app(cluster_db=db)).get("/")
        assert "my-laptop" in resp.text
