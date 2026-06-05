"""
Unit tests for gatekeeper/gui/routes/settings.py.

Covers:
  - GET /settings                         — HTML page (setup mode, operational)
  - POST /api/settings/profile            — valid profile, invalid profile, 503 without config_path
  - POST /api/settings/storage-pool/add  — valid path, bad path, bad quota, overlap, 409
  - POST /api/settings/storage-pool/remove — no fragments ok, has fragments 409, not found 404
  - POST /api/settings/smtp               — saves config + password
  - POST /api/settings/smtp/test          — calls test_smtp, returns result
  - POST /api/settings/webhook            — saves config + URL
  - POST /api/settings/webhook/test       — calls test_webhook, returns result
  - POST /api/settings/lifeboat/test-bundle  — ok, key not found, integrity error, file not found
  - POST /api/settings/lifeboat/test-kit     — ok, wrong passphrase, no kit file
  - GET  /api/settings/recovery-kit/download — kit present, kit missing, no data_dir
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from gatekeeper.config import (
    FragmentationConfig,
    GatekeeperConfig,
    LifeboatConfig,
    NodeConfig,
    NotifyConfig,
    NotifySmtpConfig,
    NotifyWebhookConfig,
    StoragePoolEntry,
    TahoeConfig,
)
from gatekeeper.gui.app import setup_gui
from gatekeeper.lifeboat.crypto import IntegrityError
from gatekeeper.lifeboat.keystore import KeyNotFoundError


# ── Helpers and fixtures ──────────────────────────────────────────────────────

def _make_config(profile: str = "balanced") -> GatekeeperConfig:
    return GatekeeperConfig(
        node=NodeConfig(name="test-node", display_name="Test Node"),
        tahoe=TahoeConfig(introducer="pb://fake", run_introducer=False),
        fragmentation=FragmentationConfig(profile=profile),
        storage_pool=[StoragePoolEntry(path="/fake/pool", quota_bytes=1024**3)],
        notify=NotifyConfig(
            smtp=NotifySmtpConfig(enabled=False, host="smtp.test", port=587, user="u@t.com", to="a@t.com"),
            webhook=NotifyWebhookConfig(enabled=False),
        ),
    )


class _MockPool:
    def __init__(self, paths: list[dict] | None = None) -> None:
        self._paths = paths or []

    def get_usage(self) -> list[dict]:
        return self._paths


class _MockClusterDB:
    def __init__(self) -> None:
        self._members: list[dict] = []

    def list_members(self) -> list[dict]:
        return self._members

    def get_last_lifeboat_status(self) -> dict | None:
        return None


class _MockCatalogDB:
    def __init__(self) -> None:
        self.connection = MagicMock()


def _make_app(
    setup_required: bool = False,
    config_path: Path | None = None,
    data_dir: Path | None = None,
    pool: _MockPool | None = None,
    cluster_db: _MockClusterDB | None = None,
    catalog_db: _MockCatalogDB | None = None,
) -> FastAPI:
    app = FastAPI()
    app.state.setup_required = setup_required
    if not setup_required:
        app.state.config = _make_config()
        app.state.config_path = config_path
        app.state.data_dir = data_dir
        app.state.pool = pool if pool is not None else _MockPool()
        app.state.cluster_db = cluster_db if cluster_db is not None else _MockClusterDB()
        app.state.catalog_db = catalog_db if catalog_db is not None else _MockCatalogDB()
    setup_gui(app)
    return app


def _ts(app: FastAPI) -> TestClient:
    return TestClient(app, client=("100.64.0.1", 9999))


def _cfg_file(tmp_path: Path, content: str = "") -> Path:
    """Write a minimal config file to tmp_path and return its path."""
    if not content:
        content = (
            "[node]\nname = test\ndisplay_name = Test\n\n"
            "[tahoe]\nintroducer = pb://fake\n\n"
            "[storage-pool]\n/fake/pool = 1 TB\n\n"
            "[fragmentation]\nprofile = balanced\n"
        )
    p = tmp_path / "gatekeeper.cfg"
    p.write_text(content, encoding="utf-8")
    return p


# ── GET /settings ─────────────────────────────────────────────────────────────

class TestSettingsPage:
    def test_returns_200_html(self, tmp_path):
        app = _make_app(config_path=_cfg_file(tmp_path))
        resp = _ts(app).get("/settings")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_contains_profile_section(self, tmp_path):
        app = _make_app(config_path=_cfg_file(tmp_path))
        resp = _ts(app).get("/settings")
        assert "Fragmentation profile" in resp.text

    def test_setup_mode_returns_page(self):
        app = _make_app(setup_required=True)
        resp = _ts(app).get("/settings")
        assert resp.status_code == 200
        assert "not been configured" in resp.text


# ── POST /api/settings/profile ────────────────────────────────────────────────

class TestProfileChange:
    def test_valid_profile_returns_ok(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        app = _make_app(config_path=cfg)
        resp = _ts(app).post("/api/settings/profile", json={"profile": "secure"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["profile"] == "secure"
        assert "restart" in data["message"].lower()

    def test_profile_written_to_file(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        app = _make_app(config_path=cfg)
        _ts(app).post("/api/settings/profile", json={"profile": "paranoid"})
        content = cfg.read_text(encoding="utf-8")
        assert "paranoid" in content

    def test_invalid_profile_returns_400(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        app = _make_app(config_path=cfg)
        resp = _ts(app).post("/api/settings/profile", json={"profile": "unknown"})
        assert resp.status_code == 400

    def test_missing_config_path_returns_503(self):
        app = _make_app(config_path=None)
        resp = _ts(app).post("/api/settings/profile", json={"profile": "secure"})
        assert resp.status_code == 503

    def test_in_memory_config_updated(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        app = _make_app(config_path=cfg)
        _ts(app).post("/api/settings/profile", json={"profile": "adaptive"})
        assert app.state.config.fragmentation.profile == "adaptive"


# ── POST /api/settings/storage-pool/add ───────────────────────────────────────

class TestStoragePoolAdd:
    def test_valid_path_returns_ok(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        new_dir = tmp_path / "pool2"
        new_dir.mkdir()
        app = _make_app(config_path=cfg)
        resp = _ts(app).post("/api/settings/storage-pool/add", json={
            "path": str(new_dir),
            "quota": "500 GB",
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_path_written_to_config(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        new_dir = tmp_path / "pool2"
        new_dir.mkdir()
        app = _make_app(config_path=cfg)
        _ts(app).post("/api/settings/storage-pool/add", json={
            "path": str(new_dir),
            "quota": "500 GB",
        })
        content = cfg.read_text(encoding="utf-8")
        assert "500 GB" in content

    def test_nonexistent_path_returns_400(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        app = _make_app(config_path=cfg)
        resp = _ts(app).post("/api/settings/storage-pool/add", json={
            "path": str(tmp_path / "does_not_exist"),
            "quota": "500 GB",
        })
        assert resp.status_code == 400

    def test_relative_path_returns_400(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        app = _make_app(config_path=cfg)
        resp = _ts(app).post("/api/settings/storage-pool/add", json={
            "path": "relative/path",
            "quota": "500 GB",
        })
        assert resp.status_code == 400

    def test_invalid_quota_returns_400(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        new_dir = tmp_path / "pool2"
        new_dir.mkdir()
        app = _make_app(config_path=cfg)
        resp = _ts(app).post("/api/settings/storage-pool/add", json={
            "path": str(new_dir),
            "quota": "not a valid quota",
        })
        assert resp.status_code == 400

    def test_duplicate_path_returns_409(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        existing = tmp_path / "pool_existing"
        existing.mkdir()
        pool = _MockPool([{"path": str(existing), "quota_bytes": 1024**3, "used_bytes": 0}])
        app = _make_app(config_path=cfg, pool=pool)
        resp = _ts(app).post("/api/settings/storage-pool/add", json={
            "path": str(existing),
            "quota": "500 GB",
        })
        assert resp.status_code == 409


# ── POST /api/settings/storage-pool/remove ────────────────────────────────────

class TestStoragePoolRemove:
    def test_empty_path_removed_ok(self, tmp_path):
        cfg = _cfg_file(tmp_path, content=(
            "[node]\nname=t\ndisplay_name=T\n\n"
            "[tahoe]\nintroducer=pb://x\n\n"
            f"[storage-pool]\n{tmp_path}/pool1 = 1 TB\n"
        ))
        pool_dir = tmp_path / "pool1"
        pool_dir.mkdir()
        pool = _MockPool([{"path": str(pool_dir), "quota_bytes": 1024**4, "used_bytes": 0}])
        app = _make_app(config_path=cfg, pool=pool)
        resp = _ts(app).post("/api/settings/storage-pool/remove", json={"path": str(pool_dir)})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_path_with_fragments_returns_409(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        pool_dir = tmp_path / "pool1"
        pool_dir.mkdir()
        pool = _MockPool([{"path": str(pool_dir), "quota_bytes": 1024**4, "used_bytes": 1024}])
        app = _make_app(config_path=cfg, pool=pool)
        resp = _ts(app).post("/api/settings/storage-pool/remove", json={"path": str(pool_dir)})
        assert resp.status_code == 409

    def test_path_not_in_config_returns_404(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        app = _make_app(config_path=cfg)
        resp = _ts(app).post("/api/settings/storage-pool/remove", json={
            "path": str(tmp_path / "not_in_config"),
        })
        assert resp.status_code == 404


# ── POST /api/settings/smtp ───────────────────────────────────────────────────

class TestSmtpSave:
    def test_save_without_password_writes_config(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        app = _make_app(config_path=cfg, data_dir=tmp_path)
        resp = _ts(app).post("/api/settings/smtp", json={
            "enabled": True,
            "host": "smtp.test.com",
            "port": 587,
            "user": "u@test.com",
            "to": "a@test.com",
            "password": "",
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        content = cfg.read_text(encoding="utf-8")
        assert "smtp.test.com" in content

    def test_save_with_password_stores_secret(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        app = _make_app(config_path=cfg, data_dir=tmp_path)
        with patch("gatekeeper.gui.routes.settings.SecretsStore") as MockStore:
            mock_instance = MagicMock()
            MockStore.return_value = mock_instance
            _ts(app).post("/api/settings/smtp", json={
                "enabled": True,
                "host": "smtp.x.com",
                "port": 587,
                "user": "u",
                "to": "a",
                "password": "s3cr3t",
            })
            mock_instance.set_secret.assert_called_once_with("smtp_password", "s3cr3t")

    def test_in_memory_config_updated(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        app = _make_app(config_path=cfg, data_dir=tmp_path)
        _ts(app).post("/api/settings/smtp", json={
            "enabled": True,
            "host": "newhost",
            "port": 465,
            "user": "u",
            "to": "a",
            "password": "",
        })
        assert app.state.config.notify.smtp.host == "newhost"
        assert app.state.config.notify.smtp.port == 465


# ── POST /api/settings/smtp/test ──────────────────────────────────────────────

class TestSmtpTest:
    def test_successful_test_returns_ok(self, tmp_path):
        app = _make_app(config_path=_cfg_file(tmp_path))
        with patch("gatekeeper.gui.routes.settings.test_smtp", new_callable=AsyncMock, return_value=True):
            resp = _ts(app).post("/api/settings/smtp/test", json={
                "host": "smtp.test.com", "port": 587, "user": "u", "to": "a", "password": "pw",
            })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_failed_test_returns_ok_false(self, tmp_path):
        app = _make_app(config_path=_cfg_file(tmp_path))
        with patch("gatekeeper.gui.routes.settings.test_smtp", new_callable=AsyncMock, return_value=False):
            resp = _ts(app).post("/api/settings/smtp/test", json={
                "host": "smtp.test.com", "port": 587, "user": "u", "to": "a", "password": "pw",
            })
        assert resp.status_code == 200
        assert resp.json()["ok"] is False


# ── POST /api/settings/webhook ────────────────────────────────────────────────

class TestWebhookSave:
    def test_save_without_url_writes_config(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        app = _make_app(config_path=cfg, data_dir=tmp_path)
        resp = _ts(app).post("/api/settings/webhook", json={"enabled": True, "url": ""})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        content = cfg.read_text(encoding="utf-8")
        assert "enabled" in content

    def test_save_with_url_stores_secret(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        app = _make_app(config_path=cfg, data_dir=tmp_path)
        with patch("gatekeeper.gui.routes.settings.SecretsStore") as MockStore:
            mock_instance = MagicMock()
            MockStore.return_value = mock_instance
            _ts(app).post("/api/settings/webhook", json={
                "enabled": True,
                "url": "https://hooks.slack.com/test",
            })
            mock_instance.set_secret.assert_called_once_with(
                "webhook_url", "https://hooks.slack.com/test"
            )

    def test_in_memory_config_updated(self, tmp_path):
        cfg = _cfg_file(tmp_path)
        app = _make_app(config_path=cfg, data_dir=tmp_path)
        _ts(app).post("/api/settings/webhook", json={"enabled": True, "url": ""})
        assert app.state.config.notify.webhook.enabled is True


# ── POST /api/settings/webhook/test ──────────────────────────────────────────

class TestWebhookTest:
    def test_successful_test_returns_ok(self, tmp_path):
        app = _make_app(config_path=_cfg_file(tmp_path))
        with patch("gatekeeper.gui.routes.settings.test_webhook", new_callable=AsyncMock, return_value=True):
            resp = _ts(app).post("/api/settings/webhook/test", json={"url": "https://x.com/hook"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_empty_url_returns_400(self, tmp_path):
        app = _make_app(config_path=_cfg_file(tmp_path))
        resp = _ts(app).post("/api/settings/webhook/test", json={"url": ""})
        assert resp.status_code == 400

    def test_failed_test_returns_ok_false(self, tmp_path):
        app = _make_app(config_path=_cfg_file(tmp_path))
        with patch("gatekeeper.gui.routes.settings.test_webhook", new_callable=AsyncMock, return_value=False):
            resp = _ts(app).post("/api/settings/webhook/test", json={"url": "https://x.com/hook"})
        assert resp.json()["ok"] is False


# ── POST /api/settings/lifeboat/test-bundle ───────────────────────────────────

class TestLifeboatBundleTest:
    def _app_with_state(self, tmp_path) -> FastAPI:
        cfg = _cfg_file(tmp_path)
        return _make_app(config_path=cfg, data_dir=tmp_path)

    def test_success_returns_ok(self, tmp_path):
        app = self._app_with_state(tmp_path)
        with (
            patch("gatekeeper.gui.routes.settings.create_bundle", return_value=b"bundle"),
            patch("gatekeeper.gui.routes.settings.extract_bundle", return_value={}),
        ):
            resp = _ts(app).post("/api/settings/lifeboat/test-bundle")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_key_not_found_returns_ok_false(self, tmp_path):
        app = self._app_with_state(tmp_path)
        with patch("gatekeeper.gui.routes.settings.create_bundle", side_effect=KeyNotFoundError("no key")):
            resp = _ts(app).post("/api/settings/lifeboat/test-bundle")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert "key" in resp.json()["message"].lower()

    def test_integrity_error_returns_ok_false(self, tmp_path):
        app = self._app_with_state(tmp_path)
        with (
            patch("gatekeeper.gui.routes.settings.create_bundle", return_value=b"bundle"),
            patch("gatekeeper.gui.routes.settings.extract_bundle", side_effect=IntegrityError("bad")),
        ):
            resp = _ts(app).post("/api/settings/lifeboat/test-bundle")
        assert resp.json()["ok"] is False

    def test_file_not_found_returns_ok_false(self, tmp_path):
        app = self._app_with_state(tmp_path)
        err = FileNotFoundError(2, "No such file", "/fake/root_dir.cap")
        with patch("gatekeeper.gui.routes.settings.create_bundle", side_effect=err):
            resp = _ts(app).post("/api/settings/lifeboat/test-bundle")
        assert resp.json()["ok"] is False

    def test_setup_mode_returns_503(self):
        app = _make_app(setup_required=True)
        resp = _ts(app).post("/api/settings/lifeboat/test-bundle")
        assert resp.status_code == 503


# ── POST /api/settings/lifeboat/test-kit ─────────────────────────────────────

class TestLifeboatKitTest:
    def test_success_returns_ok(self, tmp_path):
        kit = tmp_path / "recovery_kit.enc"
        kit.write_bytes(b"x" * 48)  # fake kit content (>= min length)
        app = _make_app(data_dir=tmp_path)
        with patch("gatekeeper.gui.routes.settings.extract_recovery_kit", return_value={"ok": True}):
            resp = _ts(app).post("/api/settings/lifeboat/test-kit", json={"passphrase": "correct"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_wrong_passphrase_returns_ok_false(self, tmp_path):
        kit = tmp_path / "recovery_kit.enc"
        kit.write_bytes(b"x" * 48)
        app = _make_app(data_dir=tmp_path)
        with patch(
            "gatekeeper.gui.routes.settings.extract_recovery_kit",
            side_effect=IntegrityError("wrong passphrase"),
        ):
            resp = _ts(app).post("/api/settings/lifeboat/test-kit", json={"passphrase": "wrong"})
        assert resp.json()["ok"] is False
        assert "passphrase" in resp.json()["message"].lower()

    def test_no_kit_file_returns_404(self, tmp_path):
        app = _make_app(data_dir=tmp_path)
        resp = _ts(app).post("/api/settings/lifeboat/test-kit", json={"passphrase": "any"})
        assert resp.status_code == 404

    def test_empty_passphrase_returns_400(self, tmp_path):
        kit = tmp_path / "recovery_kit.enc"
        kit.write_bytes(b"x" * 48)
        app = _make_app(data_dir=tmp_path)
        resp = _ts(app).post("/api/settings/lifeboat/test-kit", json={"passphrase": ""})
        assert resp.status_code == 400


# ── GET /api/settings/recovery-kit/download ───────────────────────────────────

class TestRecoveryKitDownload:
    def test_returns_kit_bytes(self, tmp_path):
        kit = tmp_path / "recovery_kit.enc"
        kit.write_bytes(b"\xde\xad\xbe\xef" * 12)
        app = _make_app(data_dir=tmp_path)
        resp = _ts(app).get("/api/settings/recovery-kit/download")
        assert resp.status_code == 200
        assert resp.content == b"\xde\xad\xbe\xef" * 12
        assert resp.headers["content-type"] == "application/octet-stream"
        assert 'filename="recovery-kit.enc"' in resp.headers["content-disposition"]

    def test_returns_404_when_kit_missing(self, tmp_path):
        app = _make_app(data_dir=tmp_path)
        resp = _ts(app).get("/api/settings/recovery-kit/download")
        assert resp.status_code == 404

    def test_returns_503_without_data_dir(self):
        app = _make_app(data_dir=None)
        resp = _ts(app).get("/api/settings/recovery-kit/download")
        assert resp.status_code == 503
