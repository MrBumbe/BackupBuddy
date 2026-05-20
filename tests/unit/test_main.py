"""
Unit tests for gatekeeper/main.py.

Covers:
  - Catalog key derivation (_derive_catalog_key)
  - main() abort paths: Tailscale not running, invalid/missing config
  - Lifespan in setup mode (root_dir.cap absent → 503 from /api/status)
  - Lifespan in normal mode with fully mocked Tahoe components
  - Graceful shutdown (TahoeClient.aclose, StorageNode.stop, DB.close called)

Note: starlette.testclient.TestClient is used instead of httpx.ASGITransport
because ASGITransport 0.28.1 does not trigger the ASGI lifespan. TestClient
runs the lifespan correctly via anyio inside a sync context.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from starlette.testclient import TestClient

from gatekeeper.main import _derive_catalog_key, _create_app, _state, main
from gatekeeper.tailscale import TailscaleNotRunning


# ── Key derivation ────────────────────────────────────────────────────────────

class TestDeriveCatalogKey:
    def test_returns_32_bytes(self):
        key = _derive_catalog_key("URI:DIR2:someexamplecapability")
        assert len(key) == 32

    def test_deterministic(self):
        cap = "URI:DIR2:someexamplecapability"
        assert _derive_catalog_key(cap) == _derive_catalog_key(cap)

    def test_different_caps_produce_different_keys(self):
        key1 = _derive_catalog_key("URI:DIR2:capabilityA")
        key2 = _derive_catalog_key("URI:DIR2:capabilityB")
        assert key1 != key2

    def test_empty_string_produces_32_bytes(self):
        key = _derive_catalog_key("")
        assert len(key) == 32

    def test_context_separation_from_raw_sha256(self):
        import hashlib
        cap = "URI:DIR2:someexamplecapability"
        derived = _derive_catalog_key(cap)
        raw_sha256 = hashlib.sha256(cap.encode()).digest()
        assert derived != raw_sha256


# ── main() abort paths ────────────────────────────────────────────────────────

class TestMainAbortPaths:
    def test_aborts_when_tailscale_not_running(self):
        with patch("sys.argv", ["gk"]):
            with patch(
                "gatekeeper.main.assert_tailscale_running",
                side_effect=TailscaleNotRunning("Tailscale is not running"),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == 1

    def test_aborts_when_config_file_missing(self, tmp_path):
        nonexistent = str(tmp_path / "nonexistent.cfg")
        with patch("sys.argv", ["gk", "--config", nonexistent]):
            with patch("gatekeeper.main.assert_tailscale_running", return_value="100.64.0.1"):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == 1

    def test_aborts_when_config_missing_required_section(self, tmp_path):
        cfg = tmp_path / "gatekeeper.cfg"
        cfg.write_text("[node]\nname = test\n", encoding="utf-8")
        with patch("sys.argv", ["gk", "--config", str(cfg), "--data-dir", str(tmp_path)]):
            with patch("gatekeeper.main.assert_tailscale_running", return_value="100.64.0.1"):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == 1


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_config(tailscale_ip: str = "100.64.0.1", web_port: int = 8080) -> MagicMock:
    cfg = MagicMock()
    cfg.node.name = "test-node"
    cfg.node.display_name = "Test Node"
    cfg.tailscale_ip = tailscale_ip
    cfg.web.port = web_port
    cfg.tahoe.run_introducer = False
    cfg.tahoe.introducer = "pb://fake@host:3456/fake"
    cfg.storage_pool = []
    return cfg


def _mock_pool() -> MagicMock:
    mock = MagicMock()
    mock.get_usage.return_value = [
        {"path": "/fake/pool", "quota_bytes": 10**9, "used_bytes": 0, "free_bytes": 10**9}
    ]
    return mock


def _mock_storage_node() -> MagicMock:
    node = MagicMock()
    node.node_url = "http://127.0.0.1:3456"
    node.start = AsyncMock()
    node.stop = AsyncMock()
    return node


def _mock_tahoe_client() -> MagicMock:
    client = MagicMock()
    client.aclose = AsyncMock()
    return client


# ── Lifespan — setup mode ─────────────────────────────────────────────────────

class TestLifespanSetupMode:
    def test_returns_503_when_root_dir_cap_absent(self, tmp_path):
        """When root_dir.cap is absent, /api/status returns 503 setup_required."""
        _state["config"] = _make_mock_config()
        _state["data_dir"] = tmp_path

        with patch("gatekeeper.main.StoragePoolManager", return_value=_mock_pool()):
            app = _create_app()
            with TestClient(app, raise_server_exceptions=True) as client:
                response = client.get("/api/status")

        assert response.status_code == 503
        assert response.json()["status"] == "setup_required"

    def test_pool_always_initialized(self, tmp_path):
        """StoragePoolManager is initialised even in setup mode."""
        _state["config"] = _make_mock_config()
        _state["data_dir"] = tmp_path

        mock_pool_cls = MagicMock(return_value=_mock_pool())

        with patch("gatekeeper.main.StoragePoolManager", mock_pool_cls):
            app = _create_app()
            with TestClient(app) as client:
                client.get("/api/status")

        mock_pool_cls.assert_called_once()


# ── Lifespan — normal mode ────────────────────────────────────────────────────

class TestLifespanNormalMode:
    def _patches(self, mock_sn=None, mock_tc=None):
        """Return a list of patches for a full normal-mode startup."""
        sn = mock_sn or _mock_storage_node()
        tc = mock_tc or _mock_tahoe_client()
        return [
            patch("gatekeeper.main.StoragePoolManager", return_value=_mock_pool()),
            patch("gatekeeper.main.CatalogDB", return_value=MagicMock()),
            patch("gatekeeper.main.ClusterDB", return_value=MagicMock()),
            patch("gatekeeper.main.StorageNode", return_value=sn),
            patch("gatekeeper.main.TahoeClient", return_value=tc),
        ], sn, tc

    def test_returns_200_when_initialized(self, tmp_path):
        """With root_dir.cap present and mocked components, status returns 200."""
        (tmp_path / "root_dir.cap").write_text("URI:DIR2:dummycapability", encoding="utf-8")
        _state["config"] = _make_mock_config()
        _state["data_dir"] = tmp_path

        patches, _, _ = self._patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app = _create_app()
            with TestClient(app) as client:
                response = client.get("/api/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["node"] == "test-node"

    def test_shutdown_closes_tahoe_client(self, tmp_path):
        """On shutdown, TahoeClient.aclose is awaited."""
        (tmp_path / "root_dir.cap").write_text("URI:DIR2:dummycapability", encoding="utf-8")
        _state["config"] = _make_mock_config()
        _state["data_dir"] = tmp_path

        mock_tc = _mock_tahoe_client()
        patches, _, _ = self._patches(mock_tc=mock_tc)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app = _create_app()
            with TestClient(app) as client:
                client.get("/api/status")

        mock_tc.aclose.assert_awaited_once()

    def test_shutdown_stops_storage_node(self, tmp_path):
        """On shutdown, StorageNode.stop is awaited."""
        (tmp_path / "root_dir.cap").write_text("URI:DIR2:dummycapability", encoding="utf-8")
        _state["config"] = _make_mock_config()
        _state["data_dir"] = tmp_path

        mock_sn = _mock_storage_node()
        patches, _, _ = self._patches(mock_sn=mock_sn)

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            app = _create_app()
            with TestClient(app) as client:
                client.get("/api/status")

        mock_sn.stop.assert_awaited_once()

    def test_shutdown_closes_databases(self, tmp_path):
        """On shutdown, CatalogDB.close and ClusterDB.close are called."""
        (tmp_path / "root_dir.cap").write_text("URI:DIR2:dummycapability", encoding="utf-8")
        _state["config"] = _make_mock_config()
        _state["data_dir"] = tmp_path

        mock_catalog = MagicMock()
        mock_cluster = MagicMock()

        with (
            patch("gatekeeper.main.StoragePoolManager", return_value=_mock_pool()),
            patch("gatekeeper.main.CatalogDB", return_value=mock_catalog),
            patch("gatekeeper.main.ClusterDB", return_value=mock_cluster),
            patch("gatekeeper.main.StorageNode", return_value=_mock_storage_node()),
            patch("gatekeeper.main.TahoeClient", return_value=_mock_tahoe_client()),
        ):
            app = _create_app()
            with TestClient(app) as client:
                client.get("/api/status")

        mock_catalog.close.assert_called_once()
        mock_cluster.close.assert_called_once()

    def test_introducer_started_when_run_introducer_true(self, tmp_path):
        """IntroducerNode.start is called when run_introducer=True."""
        (tmp_path / "root_dir.cap").write_text("URI:DIR2:dummycapability", encoding="utf-8")

        cfg = _make_mock_config()
        cfg.tahoe.run_introducer = True
        cfg.tahoe.introducer = ""
        _state["config"] = cfg
        _state["data_dir"] = tmp_path

        mock_introducer = MagicMock()
        mock_introducer.start = AsyncMock(return_value="pb://fake@host:3456/furl")
        mock_introducer.stop = AsyncMock()

        with (
            patch("gatekeeper.main.StoragePoolManager", return_value=_mock_pool()),
            patch("gatekeeper.main.CatalogDB", return_value=MagicMock()),
            patch("gatekeeper.main.ClusterDB", return_value=MagicMock()),
            patch("gatekeeper.main.IntroducerNode", return_value=mock_introducer),
            patch("gatekeeper.main.StorageNode", return_value=_mock_storage_node()),
            patch("gatekeeper.main.TahoeClient", return_value=_mock_tahoe_client()),
        ):
            app = _create_app()
            with TestClient(app) as client:
                client.get("/api/status")

        mock_introducer.start.assert_awaited_once()

    def test_tahoe_client_receives_storage_node_url(self, tmp_path):
        """TahoeClient is constructed with the storage node's node_url."""
        (tmp_path / "root_dir.cap").write_text("URI:DIR2:dummycapability", encoding="utf-8")
        _state["config"] = _make_mock_config()
        _state["data_dir"] = tmp_path

        expected_url = "http://127.0.0.1:3456"
        mock_sn = _mock_storage_node()
        mock_sn.node_url = expected_url
        mock_tc_cls = MagicMock(return_value=_mock_tahoe_client())

        with (
            patch("gatekeeper.main.StoragePoolManager", return_value=_mock_pool()),
            patch("gatekeeper.main.CatalogDB", return_value=MagicMock()),
            patch("gatekeeper.main.ClusterDB", return_value=MagicMock()),
            patch("gatekeeper.main.StorageNode", return_value=mock_sn),
            patch("gatekeeper.main.TahoeClient", mock_tc_cls),
        ):
            app = _create_app()
            with TestClient(app) as client:
                client.get("/api/status")

        mock_tc_cls.assert_called_once_with(expected_url)
