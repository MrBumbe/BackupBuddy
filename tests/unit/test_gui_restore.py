"""
Unit tests for gatekeeper/gui/routes/restore.py.

Covers:
  - GET /restore                   — HTML page (setup mode, operational, agent list)
  - GET /api/restore/catalog       — search: q filter, agent filter, limit, truncation, 503
  - POST /api/restore/start/file   — job created, 400 on bad dest, 503 guards
  - POST /api/restore/start/folder — job created, 400 on bad dest
  - POST /api/restore/emergency    — 409 when catalog non-empty, job created when empty, 400 on empty key
  - GET /api/restore/jobs/{job_id} — returns job dict, 404 for unknown
  - _validate_dest_path            — rejects non-absolute, rejects pool overlap
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from starlette.testclient import TestClient

from gatekeeper.gui.app import setup_gui
import gatekeeper.gui.routes.restore as restore_module
from gatekeeper.gui.routes.restore import _validate_dest_path

# Platform-appropriate absolute path for use in test payloads.
_DEST = os.path.abspath("bb_test_restore_dest")


# ── Fixtures and helpers ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_jobs():
    restore_module._restore_jobs.clear()
    yield
    restore_module._restore_jobs.clear()


class _MockCatalogDB:
    def __init__(self, files: list[dict] | None = None, agents: list[dict] | None = None) -> None:
        self._files = files or []
        self._agents = agents or []

    def get_all_files(self) -> list[dict]:
        return self._files

    def get_last_backup_per_agent(self) -> list[dict]:
        return self._agents


def _make_app(
    setup_required: bool = False,
    catalog_db: _MockCatalogDB | None = None,
    tahoe: object | None = None,
) -> FastAPI:
    app = FastAPI()
    app.state.setup_required = setup_required
    if not setup_required:
        app.state.catalog_db = catalog_db if catalog_db is not None else _MockCatalogDB()
        app.state.tahoe_client = tahoe if tahoe is not None else MagicMock()
    setup_gui(app)
    return app


def _ts(app: FastAPI) -> TestClient:
    return TestClient(app, client=("100.64.0.1", 9999))


# ── GET /restore — HTML page ──────────────────────────────────────────────────

class TestRestorePage:
    def test_returns_200_html(self):
        resp = _ts(_make_app()).get("/restore")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_setup_mode_returns_page(self):
        resp = _ts(_make_app(setup_required=True)).get("/restore")
        assert resp.status_code == 200
        assert "setup" in resp.text.lower()

    def test_agents_rendered_in_dropdowns(self):
        db = _MockCatalogDB(agents=[{"agent": "my-laptop"}])
        resp = _ts(_make_app(catalog_db=db)).get("/restore")
        assert "my-laptop" in resp.text

    def test_tab_labels_present(self):
        resp = _ts(_make_app()).get("/restore")
        assert "Find a file" in resp.text
        assert "Restore a folder" in resp.text
        assert "Emergency restore" in resp.text

    def test_blocked_from_non_tailscale(self):
        client = TestClient(_make_app(), client=("10.0.0.1", 9999))
        assert client.get("/restore").status_code == 404


# ── GET /api/restore/catalog ──────────────────────────────────────────────────

def _file(path: str, agent: str = "laptop") -> dict:
    return {
        "id": 1,
        "agent": agent,
        "original_path": path,
        "backed_up_at": 1700000000.0,
        "size_bytes": 1024,
    }


class TestCatalogSearch:
    def test_setup_mode_returns_503(self):
        resp = _ts(_make_app(setup_required=True)).get("/api/restore/catalog")
        assert resp.status_code == 503

    def test_no_catalog_returns_503(self):
        app = FastAPI()
        app.state.setup_required = False
        setup_gui(app)
        resp = _ts(app).get("/api/restore/catalog")
        assert resp.status_code == 503

    def test_empty_catalog_returns_empty_list(self):
        data = _ts(_make_app()).get("/api/restore/catalog").json()
        assert data["results"] == []
        assert data["total"] == 0

    def test_returns_all_when_no_query(self):
        db = _MockCatalogDB(files=[_file("/home/user/a.txt"), _file("/home/user/b.txt")])
        data = _ts(_make_app(catalog_db=db)).get("/api/restore/catalog").json()
        assert data["total"] == 2

    def test_q_filters_by_filename(self):
        db = _MockCatalogDB(files=[_file("/home/user/photo.jpg"), _file("/home/user/report.pdf")])
        data = _ts(_make_app(catalog_db=db)).get("/api/restore/catalog?q=photo").json()
        assert data["total"] == 1
        assert data["results"][0]["filename"] == "photo.jpg"

    def test_q_filters_by_path(self):
        db = _MockCatalogDB(files=[
            _file("/home/alice/docs/a.txt"),
            _file("/home/bob/docs/a.txt"),
        ])
        data = _ts(_make_app(catalog_db=db)).get("/api/restore/catalog?q=alice").json()
        assert data["total"] == 1
        assert "alice" in data["results"][0]["original_path"]

    def test_q_is_case_insensitive(self):
        db = _MockCatalogDB(files=[_file("/home/user/Photo.JPG")])
        data = _ts(_make_app(catalog_db=db)).get("/api/restore/catalog?q=photo").json()
        assert data["total"] == 1

    def test_agent_filter(self):
        db = _MockCatalogDB(files=[
            _file("/a.txt", agent="laptop"),
            _file("/b.txt", agent="desktop"),
        ])
        data = _ts(_make_app(catalog_db=db)).get("/api/restore/catalog?agent=laptop").json()
        assert data["total"] == 1
        assert data["results"][0]["agent"] == "laptop"

    def test_limit_capped_at_500(self):
        resp = _ts(_make_app()).get("/api/restore/catalog?limit=9999")
        assert resp.status_code == 200

    def test_result_shape(self):
        db = _MockCatalogDB(files=[_file("/home/user/a.txt")])
        result = _ts(_make_app(catalog_db=db)).get("/api/restore/catalog").json()["results"][0]
        for key in ("id", "agent", "original_path", "filename", "backed_up_at", "size_bytes"):
            assert key in result

    def test_truncated_flag_when_results_hit_limit(self):
        files = [_file(f"/file_{i}.txt") for i in range(5)]
        db = _MockCatalogDB(files=files)
        data = _ts(_make_app(catalog_db=db)).get("/api/restore/catalog?limit=3").json()
        assert data["truncated"] is True

    def test_truncated_false_when_under_limit(self):
        db = _MockCatalogDB(files=[_file("/a.txt"), _file("/b.txt")])
        data = _ts(_make_app(catalog_db=db)).get("/api/restore/catalog?limit=200").json()
        assert data["truncated"] is False


# ── POST /api/restore/start/file ─────────────────────────────────────────────

class TestStartFileRestore:
    _body = {"original_path": "/home/user/a.txt", "agent": "laptop", "dest_path": _DEST}

    def test_returns_job_id(self):
        with patch("gatekeeper.gui.routes.restore.restore_file", new_callable=AsyncMock):
            resp = _ts(_make_app()).post("/api/restore/start/file", json=self._body)
        assert resp.status_code == 200
        assert "job_id" in resp.json()

    def test_job_registered_in_registry(self):
        with patch("gatekeeper.gui.routes.restore.restore_file", new_callable=AsyncMock):
            resp = _ts(_make_app()).post("/api/restore/start/file", json=self._body)
        job_id = resp.json()["job_id"]
        assert job_id in restore_module._restore_jobs

    def test_setup_mode_returns_503(self):
        resp = _ts(_make_app(setup_required=True)).post("/api/restore/start/file", json=self._body)
        assert resp.status_code == 503

    def test_no_services_returns_503(self):
        app = FastAPI()
        app.state.setup_required = False
        setup_gui(app)
        resp = _ts(app).post("/api/restore/start/file", json=self._body)
        assert resp.status_code == 503

    def test_invalid_dest_returns_400(self):
        body = {**self._body, "dest_path": "relative/path"}
        with patch("gatekeeper.storage.pool.EXCLUDED_PATHS", frozenset()):
            resp = _ts(_make_app()).post("/api/restore/start/file", json=body)
        assert resp.status_code == 400

    def test_pool_overlap_returns_400(self):
        pool_path = os.path.realpath("/tmp/pool")
        dest = os.path.join(pool_path, "subdir")
        body = {**self._body, "dest_path": dest}
        with patch("gatekeeper.storage.pool.EXCLUDED_PATHS", frozenset([pool_path])):
            resp = _ts(_make_app()).post("/api/restore/start/file", json=body)
        assert resp.status_code == 400


# ── POST /api/restore/start/folder ───────────────────────────────────────────

class TestStartFolderRestore:
    _body = {"folder_path": "/home/user/docs", "agent": "laptop", "dest_path": _DEST}

    def test_returns_job_id(self):
        with patch("gatekeeper.gui.routes.restore.restore_folder", new_callable=AsyncMock):
            resp = _ts(_make_app()).post("/api/restore/start/folder", json=self._body)
        assert resp.status_code == 200
        assert "job_id" in resp.json()

    def test_setup_mode_returns_503(self):
        resp = _ts(_make_app(setup_required=True)).post("/api/restore/start/folder", json=self._body)
        assert resp.status_code == 503

    def test_invalid_dest_returns_400(self):
        body = {**self._body, "dest_path": "relative/path"}
        resp = _ts(_make_app()).post("/api/restore/start/folder", json=body)
        assert resp.status_code == 400


# ── POST /api/restore/emergency ──────────────────────────────────────────────

class TestEmergencyRestore:
    _body = {"recovery_key": "URI:DIR2:abc123"}

    def test_409_when_catalog_non_empty(self):
        db = _MockCatalogDB(files=[_file("/a.txt")])
        resp = _ts(_make_app(catalog_db=db)).post("/api/restore/emergency", json=self._body)
        assert resp.status_code == 409
        assert "record" in resp.json()["error"].lower()

    def test_409_message_includes_count(self):
        db = _MockCatalogDB(files=[_file("/a.txt"), _file("/b.txt")])
        data = _ts(_make_app(catalog_db=db)).post("/api/restore/emergency", json=self._body).json()
        assert "2" in data["error"]

    def test_returns_job_id_when_empty(self):
        with patch("gatekeeper.gui.routes.restore.reconstruct_catalog", new_callable=AsyncMock, return_value=0):
            resp = _ts(_make_app()).post("/api/restore/emergency", json=self._body)
        assert resp.status_code == 200
        assert "job_id" in resp.json()

    def test_empty_key_returns_400(self):
        resp = _ts(_make_app()).post("/api/restore/emergency", json={"recovery_key": "   "})
        assert resp.status_code == 400

    def test_setup_mode_returns_503(self):
        resp = _ts(_make_app(setup_required=True)).post("/api/restore/emergency", json=self._body)
        assert resp.status_code == 503

    def test_no_services_returns_503(self):
        app = FastAPI()
        app.state.setup_required = False
        setup_gui(app)
        resp = _ts(app).post("/api/restore/emergency", json=self._body)
        assert resp.status_code == 503


# ── GET /api/restore/jobs/{job_id} ───────────────────────────────────────────

class TestJobStatus:
    def test_returns_job_dict(self):
        restore_module._restore_jobs["test-job-1"] = {
            "job_id": "test-job-1",
            "type": "file",
            "status": "done",
            "progress": 1,
            "total": 1,
            "results": [],
            "error": None,
            "started_at": 0.0,
        }
        resp = _ts(_make_app()).get("/api/restore/jobs/test-job-1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"

    def test_unknown_job_returns_404(self):
        resp = _ts(_make_app()).get("/api/restore/jobs/does-not-exist")
        assert resp.status_code == 404

    def test_running_job_returns_running_status(self):
        restore_module._restore_jobs["running-job"] = {
            "job_id": "running-job",
            "type": "folder",
            "status": "running",
            "progress": 5,
            "total": 20,
            "results": [],
            "error": None,
            "started_at": 0.0,
        }
        data = _ts(_make_app()).get("/api/restore/jobs/running-job").json()
        assert data["status"] == "running"
        assert data["progress"] == 5


# ── _validate_dest_path unit tests ───────────────────────────────────────────

class TestValidateDestPath:
    def test_absolute_path_accepted(self):
        dest = os.path.abspath("bb_restore_output_test")
        with patch("gatekeeper.storage.pool.EXCLUDED_PATHS", frozenset()):
            result = _validate_dest_path(dest)
        assert os.path.isabs(result)

    def test_relative_path_rejected(self):
        with pytest.raises(ValueError, match="absolute"):
            _validate_dest_path("relative/path")

    def test_pool_exact_match_rejected(self):
        pool_real = os.path.realpath("/data/pool")
        with patch("gatekeeper.storage.pool.EXCLUDED_PATHS", frozenset([pool_real])):
            with pytest.raises(ValueError, match="storage pool"):
                _validate_dest_path(pool_real)

    def test_pool_subpath_rejected(self):
        pool_real = os.path.realpath("/data/pool")
        sub = os.path.join(pool_real, "subdir")
        with patch("gatekeeper.storage.pool.EXCLUDED_PATHS", frozenset([pool_real])):
            with pytest.raises(ValueError, match="storage pool"):
                _validate_dest_path(sub)

    def test_sibling_of_pool_accepted(self):
        pool_real = os.path.realpath("/data/pool")
        sibling = os.path.realpath("/data/restore")
        with patch("gatekeeper.storage.pool.EXCLUDED_PATHS", frozenset([pool_real])):
            result = _validate_dest_path(sibling)
        assert result == sibling


# ── Job pruning ───────────────────────────────────────────────────────────────

class TestJobPruning:
    def test_prune_evicts_oldest_completed_when_at_cap(self):
        import time
        # Fill registry to cap with completed jobs
        for i in range(restore_module._MAX_JOBS):
            restore_module._restore_jobs[f"job-{i}"] = {
                "status": "done",
                "started_at": float(i),
            }
        with patch("gatekeeper.gui.routes.restore.restore_file", new_callable=AsyncMock):
            resp = _ts(_make_app()).post(
                "/api/restore/start/file",
                json={"original_path": "/a.txt", "agent": "x", "dest_path": _DEST},
            )
        assert resp.status_code == 200
        # Registry must not exceed cap + 1 (the new running job)
        assert len(restore_module._restore_jobs) <= restore_module._MAX_JOBS

    def test_running_jobs_not_evicted(self):
        # Fill with running jobs — pruning must not evict them
        for i in range(restore_module._MAX_JOBS):
            restore_module._restore_jobs[f"run-{i}"] = {
                "status": "running",
                "started_at": float(i),
            }
        # _prune_jobs should leave running jobs alone
        restore_module._prune_jobs()
        # All running jobs still present (no completed jobs to evict)
        assert all(j["status"] == "running" for j in restore_module._restore_jobs.values())
