"""Settings routes: fragmentation profile, storage pool, notifications, lifeboat.

Routes:
  GET /settings                              — HTML settings page
  POST /api/settings/profile                 — change fragmentation profile
  POST /api/settings/storage-pool/add        — add storage pool path
  POST /api/settings/storage-pool/remove     — remove storage pool path (blocks if fragments present)
  POST /api/settings/smtp                    — save SMTP settings and password
  POST /api/settings/smtp/test               — test SMTP with explicit credentials (never stored)
  POST /api/settings/webhook                 — save webhook enabled state and URL
  POST /api/settings/webhook/test            — test webhook with explicit URL (never stored)
  POST /api/settings/lifeboat/test-bundle    — create and immediately decrypt a local bundle
  POST /api/settings/lifeboat/test-kit       — decrypt local recovery_kit.enc with passphrase

Config write-back: reads existing INI file, updates relevant section, atomically replaces file.
Passwords and webhook URLs are stored only in SecretsStore, never in config or form values.
EXCLUDED_PATHS and pool manager updates require a gatekeeper restart; UI shows this message.
"""
from __future__ import annotations

import configparser
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from gatekeeper.config import (
    ConfigError,
    NotifySmtpConfig,
    NotifyWebhookConfig,
    _parse_quota_bytes,
)
from gatekeeper.lifeboat.bundle import create_bundle, extract_bundle
from gatekeeper.lifeboat.crypto import IntegrityError
from gatekeeper.lifeboat.keystore import KeyNotFoundError
from gatekeeper.lifeboat.recovery_kit import extract_recovery_kit
from gatekeeper.notify.smtp import test_smtp
from gatekeeper.notify.webhook import test_webhook
from gatekeeper.secrets import SecretsStore

logger = logging.getLogger(__name__)

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _fmt_timestamp(ts: float | None) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


_templates.env.filters["ts_format"] = _fmt_timestamp

_VALID_PROFILES = frozenset({"balanced", "secure", "paranoid", "adaptive"})
_PARANOID_MIN_NODES = 10


# ── Request models ─────────────────────────────────────────────────────────────

class _ProfileRequest(BaseModel):
    profile: str


class _StoragePoolAddRequest(BaseModel):
    path: str
    quota: str  # e.g. "2000 GB"


class _StoragePoolRemoveRequest(BaseModel):
    path: str


class _SmtpSaveRequest(BaseModel):
    enabled: bool
    host: str
    port: int
    user: str
    to: str
    password: str = ""  # empty = keep existing stored password


class _SmtpTestRequest(BaseModel):
    host: str
    port: int
    user: str
    to: str
    password: str  # required for pre-save test


class _WebhookSaveRequest(BaseModel):
    enabled: bool
    url: str = ""  # empty = keep existing stored URL


class _WebhookTestRequest(BaseModel):
    url: str


class _LifeboatTestKitRequest(BaseModel):
    passphrase: str


# ── Config file helpers ────────────────────────────────────────────────────────

def _load_parser(config_path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(allow_no_value=True, delimiters=("=",))
    parser.optionxform = str  # preserve case for paths and section names
    parser.read(config_path, encoding="utf-8")
    return parser


def _save_parser(config_path: Path, parser: configparser.ConfigParser) -> None:
    """Atomically write the config parser back to disk."""
    fd, tmp = tempfile.mkstemp(dir=config_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            parser.write(f)
        os.replace(tmp, config_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── State helpers ──────────────────────────────────────────────────────────────

def _get_config_path(request: Request) -> Path | None:
    return getattr(request.app.state, "config_path", None)


def _get_data_dir(request: Request) -> Path | None:
    return getattr(request.app.state, "data_dir", None)


def _get_secrets_store(request: Request) -> SecretsStore | None:
    data_dir = _get_data_dir(request)
    return SecretsStore(data_dir) if data_dir is not None else None


def _setup_guard(request: Request) -> JSONResponse | None:
    if getattr(request.app.state, "setup_required", True):
        return JSONResponse({"error": "Gatekeeper not ready"}, status_code=503)
    return None


# ── Page data ──────────────────────────────────────────────────────────────────

def _build_settings_data(request: Request) -> dict:
    state = request.app.state
    setup_required: bool = getattr(state, "setup_required", True)
    config = getattr(state, "config", None)
    node_name: str = config.node.display_name if config else "BackupBuddy"

    if setup_required or config is None:
        return {"setup_required": True, "node_name": node_name}

    pool = getattr(state, "pool", None)
    cluster_db = getattr(state, "cluster_db", None)
    data_dir: Path | None = getattr(state, "data_dir", None)

    members = cluster_db.list_members() if cluster_db else []
    pool_usage = pool.get_usage() if pool else []
    lifeboat_status = cluster_db.get_last_lifeboat_status() if cluster_db else None

    return {
        "setup_required": False,
        "node_name": node_name,
        "current_profile": config.fragmentation.profile,
        "member_count": len(members),
        "paranoid_min_nodes": _PARANOID_MIN_NODES,
        "pool_usage": pool_usage,
        "smtp": {
            "enabled": config.notify.smtp.enabled,
            "host": config.notify.smtp.host,
            "port": config.notify.smtp.port,
            "user": config.notify.smtp.user,
            "to": config.notify.smtp.to,
        },
        "webhook_enabled": config.notify.webhook.enabled,
        "lifeboat": {
            "distributed_at": lifeboat_status.get("distributed_at") if lifeboat_status else None,
            "agent_count": lifeboat_status.get("agent_count") if lifeboat_status else None,
            "success_count": lifeboat_status.get("success_count") if lifeboat_status else None,
            "status": lifeboat_status.get("status") if lifeboat_status else None,
        },
        "has_recovery_kit": bool(data_dir and (data_dir / "recovery_kit.enc").exists()),
    }


# ── Route factory ──────────────────────────────────────────────────────────────

def create_settings_router() -> APIRouter:
    """Return the APIRouter for all settings routes."""
    router = APIRouter()

    # ── HTML page ──────────────────────────────────────────────────────────────

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> Any:
        data = _build_settings_data(request)
        return _templates.TemplateResponse(request, "settings.html", {"data": data})

    # ── Fragmentation profile ──────────────────────────────────────────────────

    @router.post("/api/settings/profile")
    async def change_profile(request: Request, body: _ProfileRequest) -> JSONResponse:
        config_path = _get_config_path(request)
        if config_path is None:
            return JSONResponse({"error": "Config path not available"}, status_code=503)

        profile = body.profile.strip().lower()
        if profile not in _VALID_PROFILES:
            return JSONResponse({"error": f"Invalid profile: must be one of balanced, secure, paranoid, adaptive"}, status_code=400)

        try:
            parser = _load_parser(config_path)
            if not parser.has_section("fragmentation"):
                parser.add_section("fragmentation")
            parser.set("fragmentation", "profile", profile)
            _save_parser(config_path, parser)
        except OSError as exc:
            logger.error("Failed to write config for profile change: %s", exc)
            return JSONResponse({"error": "Failed to save profile."}, status_code=500)

        # Update in-memory config so the next backup uses the new profile
        config = getattr(request.app.state, "config", None)
        if config is not None:
            new_frag = config.fragmentation.model_copy(update={"profile": profile})
            request.app.state.config = config.model_copy(update={"fragmentation": new_frag})

        logger.info("Fragmentation profile changed to %r", profile)
        return JSONResponse({
            "ok": True,
            "profile": profile,
            "message": (
                "Profile saved. The new k/n values take effect after the next "
                "Tahoe process restart. Existing backups are not affected."
            ),
        })

    # ── Storage pool — add path ────────────────────────────────────────────────

    @router.post("/api/settings/storage-pool/add")
    async def storage_pool_add(request: Request, body: _StoragePoolAddRequest) -> JSONResponse:
        config_path = _get_config_path(request)
        if config_path is None:
            return JSONResponse({"error": "Config path not available"}, status_code=503)

        path = body.path.strip()
        if not path:
            return JSONResponse({"error": "Path is required."}, status_code=400)
        if not os.path.isabs(path):
            return JSONResponse({"error": "Path must be absolute."}, status_code=400)

        real = os.path.realpath(path)
        if not os.path.exists(real):
            return JSONResponse({"error": "Path does not exist."}, status_code=400)
        if not os.path.isdir(real):
            return JSONResponse({"error": "Path is not a directory."}, status_code=400)
        if not os.access(real, os.W_OK):
            return JSONResponse({"error": "Path is not writable."}, status_code=400)

        try:
            _parse_quota_bytes(body.quota)
        except ConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        pool = getattr(request.app.state, "pool", None)
        if pool:
            for entry in pool.get_usage():
                existing = os.path.realpath(entry["path"])
                if real == existing:
                    return JSONResponse({"error": "Path is already in the storage pool."}, status_code=409)
                if real.startswith(existing + os.sep) or existing.startswith(real + os.sep):
                    return JSONResponse({"error": "Path overlaps with an existing pool path."}, status_code=409)

        try:
            parser = _load_parser(config_path)
            if not parser.has_section("storage-pool"):
                parser.add_section("storage-pool")
            parser.set("storage-pool", real, body.quota.strip())
            _save_parser(config_path, parser)
        except OSError as exc:
            logger.error("Failed to write config for pool add: %s", exc)
            return JSONResponse({"error": "Failed to save settings."}, status_code=500)

        logger.info("Storage pool path added to config: %s (%s)", real, body.quota)
        return JSONResponse({
            "ok": True,
            "path": real,
            "message": (
                "Path added to config. The storage pool and backup exclusion "
                "take effect after the next gatekeeper restart."
            ),
        })

    # ── Storage pool — remove path ─────────────────────────────────────────────

    @router.post("/api/settings/storage-pool/remove")
    async def storage_pool_remove(request: Request, body: _StoragePoolRemoveRequest) -> JSONResponse:
        config_path = _get_config_path(request)
        if config_path is None:
            return JSONResponse({"error": "Config path not available"}, status_code=503)

        real = os.path.realpath(body.path.strip())

        pool = getattr(request.app.state, "pool", None)
        if pool:
            for entry in pool.get_usage():
                if os.path.realpath(entry["path"]) == real:
                    if entry["used_bytes"] > 0:
                        return JSONResponse({
                            "error": (
                                f"Cannot remove path — it contains fragments. "
                                "Wait for rebalancing or orphan cleanup to complete before removing."
                            )
                        }, status_code=409)
                    break

        try:
            parser = _load_parser(config_path)
            if not parser.has_section("storage-pool"):
                return JSONResponse({"error": "Path not found in storage pool config."}, status_code=404)

            removed = False
            for key in list(parser.options("storage-pool")):
                if os.path.realpath(key) == real:
                    parser.remove_option("storage-pool", key)
                    removed = True
                    break
            if not removed:
                return JSONResponse({"error": "Path not found in storage pool config."}, status_code=404)

            _save_parser(config_path, parser)
        except OSError as exc:
            logger.error("Failed to write config for pool remove: %s", exc)
            return JSONResponse({"error": "Failed to save settings."}, status_code=500)

        logger.info("Storage pool path removed from config: %s", real)
        return JSONResponse({
            "ok": True,
            "message": "Path removed. Change takes effect after the next gatekeeper restart.",
        })

    # ── SMTP — save settings ───────────────────────────────────────────────────

    @router.post("/api/settings/smtp")
    async def smtp_save(request: Request, body: _SmtpSaveRequest) -> JSONResponse:
        config_path = _get_config_path(request)
        if config_path is None:
            return JSONResponse({"error": "Config path not available"}, status_code=503)

        if body.password:
            secrets = _get_secrets_store(request)
            if secrets is None:
                return JSONResponse({"error": "Secrets store not available."}, status_code=503)
            try:
                secrets.set_secret("smtp_password", body.password)
            except Exception as exc:
                logger.error("Failed to store SMTP password: %s", type(exc).__name__)
                return JSONResponse({"error": "Failed to store password."}, status_code=500)

        try:
            parser = _load_parser(config_path)
            if not parser.has_section("notify.smtp"):
                parser.add_section("notify.smtp")
            parser.set("notify.smtp", "enabled", "true" if body.enabled else "false")
            parser.set("notify.smtp", "host", body.host)
            parser.set("notify.smtp", "port", str(body.port))
            parser.set("notify.smtp", "user", body.user)
            parser.set("notify.smtp", "to", body.to)
            _save_parser(config_path, parser)
        except OSError as exc:
            logger.error("Failed to write config for SMTP save: %s", exc)
            return JSONResponse({"error": "Failed to save settings."}, status_code=500)

        config = getattr(request.app.state, "config", None)
        if config is not None:
            new_smtp = NotifySmtpConfig(
                enabled=body.enabled,
                host=body.host,
                port=body.port,
                user=body.user,
                to=body.to,
            )
            new_notify = config.notify.model_copy(update={"smtp": new_smtp})
            request.app.state.config = config.model_copy(update={"notify": new_notify})

        logger.info("SMTP settings saved (enabled=%s, host=%s)", body.enabled, body.host)
        return JSONResponse({"ok": True})

    # ── SMTP — test ────────────────────────────────────────────────────────────

    @router.post("/api/settings/smtp/test")
    async def smtp_test_route(request: Request, body: _SmtpTestRequest) -> JSONResponse:
        smtp_config = NotifySmtpConfig(
            enabled=True,
            host=body.host,
            port=body.port,
            user=body.user,
            to=body.to,
        )
        ok = await test_smtp(smtp_config, password=body.password)
        if ok:
            return JSONResponse({"ok": True, "message": "Test email sent successfully."})
        return JSONResponse({
            "ok": False,
            "message": "Failed to send test email. Check host, port, credentials, and gatekeeper log.",
        })

    # ── Webhook — save settings ────────────────────────────────────────────────

    @router.post("/api/settings/webhook")
    async def webhook_save(request: Request, body: _WebhookSaveRequest) -> JSONResponse:
        config_path = _get_config_path(request)
        if config_path is None:
            return JSONResponse({"error": "Config path not available"}, status_code=503)

        if body.url:
            secrets = _get_secrets_store(request)
            if secrets is None:
                return JSONResponse({"error": "Secrets store not available."}, status_code=503)
            try:
                secrets.set_secret("webhook_url", body.url)
            except Exception as exc:
                logger.error("Failed to store webhook URL: %s", type(exc).__name__)
                return JSONResponse({"error": "Failed to store webhook URL."}, status_code=500)

        try:
            parser = _load_parser(config_path)
            if not parser.has_section("notify.webhook"):
                parser.add_section("notify.webhook")
            parser.set("notify.webhook", "enabled", "true" if body.enabled else "false")
            _save_parser(config_path, parser)
        except OSError as exc:
            logger.error("Failed to write config for webhook save: %s", exc)
            return JSONResponse({"error": "Failed to save settings."}, status_code=500)

        config = getattr(request.app.state, "config", None)
        if config is not None:
            new_webhook = NotifyWebhookConfig(enabled=body.enabled)
            new_notify = config.notify.model_copy(update={"webhook": new_webhook})
            request.app.state.config = config.model_copy(update={"notify": new_notify})

        logger.info("Webhook settings saved (enabled=%s)", body.enabled)
        return JSONResponse({"ok": True})

    # ── Webhook — test ─────────────────────────────────────────────────────────

    @router.post("/api/settings/webhook/test")
    async def webhook_test_route(request: Request, body: _WebhookTestRequest) -> JSONResponse:
        if not body.url:
            return JSONResponse({"error": "Webhook URL is required."}, status_code=400)
        ok = await test_webhook(url=body.url)
        if ok:
            return JSONResponse({"ok": True, "message": "Test webhook delivered successfully."})
        return JSONResponse({
            "ok": False,
            "message": "Failed to deliver test webhook. Check the URL and gatekeeper log.",
        })

    # ── Lifeboat — test bundle ─────────────────────────────────────────────────

    @router.post("/api/settings/lifeboat/test-bundle")
    async def lifeboat_test_bundle(request: Request) -> JSONResponse:
        guard = _setup_guard(request)
        if guard:
            return guard

        data_dir = _get_data_dir(request)
        config_path = _get_config_path(request)
        catalog_db = getattr(request.app.state, "catalog_db", None)

        if data_dir is None or config_path is None or catalog_db is None:
            return JSONResponse({"error": "Required state not available."}, status_code=503)

        try:
            bundle = create_bundle(data_dir, config_path, catalog_db.connection)
            extract_bundle(bundle)
        except KeyNotFoundError:
            return JSONResponse({
                "ok": False,
                "message": "Lifeboat key not found — the lifeboat has not been initialised yet.",
            })
        except IntegrityError:
            return JSONResponse({
                "ok": False,
                "message": "Bundle decryption failed — keystore key may be corrupted.",
            })
        except FileNotFoundError as exc:
            fname = getattr(exc, "filename", None) or str(exc)
            return JSONResponse({
                "ok": False,
                "message": f"Required file not found: {fname}",
            })
        except Exception:
            logger.error("Lifeboat bundle test failed", exc_info=True)
            return JSONResponse({
                "ok": False,
                "message": "Bundle test failed. Check the gatekeeper log.",
            })

        logger.info("Lifeboat bundle test: create+decrypt OK")
        return JSONResponse({
            "ok": True,
            "message": "Bundle created and decrypted successfully. Lifeboat key and bundle format are valid.",
        })

    # ── Lifeboat — test recovery kit ───────────────────────────────────────────

    @router.post("/api/settings/lifeboat/test-kit")
    async def lifeboat_test_kit(request: Request, body: _LifeboatTestKitRequest) -> JSONResponse:
        data_dir = _get_data_dir(request)
        if data_dir is None:
            return JSONResponse({"error": "Data directory not available."}, status_code=503)

        kit_path = data_dir / "recovery_kit.enc"
        if not kit_path.exists():
            return JSONResponse({
                "error": "No local recovery kit found. Download your recovery kit from the setup wizard and save it safely."
            }, status_code=404)

        if not body.passphrase:
            return JSONResponse({"error": "Passphrase is required."}, status_code=400)

        try:
            data = kit_path.read_bytes()
            extract_recovery_kit(data, body.passphrase)
        except IntegrityError:
            return JSONResponse({
                "ok": False,
                "message": "Decryption failed — wrong passphrase or corrupted recovery kit.",
            })
        except Exception:
            logger.error("Recovery kit test failed", exc_info=True)
            return JSONResponse({
                "ok": False,
                "message": "Recovery kit test failed. Check the gatekeeper log.",
            })

        logger.info("Recovery kit test: decryption OK")
        return JSONResponse({
            "ok": True,
            "message": "Recovery kit decrypted successfully. Your passphrase is correct.",
        })

    return router
