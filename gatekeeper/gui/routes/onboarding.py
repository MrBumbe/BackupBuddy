"""Onboarding wizard routes (pre-config setup mode — ADR-019).

Served when gatekeeper.cfg does not exist.  All routes are under /onboarding/.
The app is bound to the LAN IP so it is reachable before Tailscale is active.

Flow — new cluster:
  step/1 → step/2 → step/3 → step/4 → step/5 (triggers finish cascade)
  → /complete (recovery key) → /first-invite → /done (restart)

Flow — join cluster:
  step/1 → /join (credentials) → step/2 → step/3 → step/4 → step/5
  → /complete (no recovery key; join has no root_dir.cap to show) → /done

Finish cascade atomicity: gatekeeper.cfg is written LAST via atomic rename.
Partial failures leave the cascade retryable; idempotency is ensured by
checking whether each artifact already exists before (re-)creating it.
"""
from __future__ import annotations

import configparser
import logging
import os
import re
import secrets as _secrets_mod
import signal
import sys
from pathlib import Path
from typing import Any

import asyncio
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from gatekeeper.cluster.invites import generate_invite
from gatekeeper.cluster.join import NodeInfo, initiate_join
from gatekeeper.db.cluster import ClusterDB
from gatekeeper.gui.wizard_state import WizardState, clear_state, load_state, save_state
from gatekeeper.lifeboat.keystore import DEFAULT_KEY_PATH, generate_key
from gatekeeper.lifeboat.recovery_kit import create_recovery_kit
from gatekeeper.secrets import SecretsStore
from gatekeeper.tahoe.client import TahoeClient
from gatekeeper.tahoe.introducer import IntroducerNode
from gatekeeper.tahoe.storage_node import StorageNode
from gatekeeper.tailscale import get_tailscale_ip

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

_VALID_PROFILES = frozenset({"balanced", "secure", "paranoid", "adaptive"})
_NODE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,30}[a-z0-9]$")

_PROFILE_DESCRIPTIONS = [
    {
        "id": "balanced",
        "name": "Balanced",
        "desc": "3-of-5 erasure coding. Good redundancy, works with 3+ nodes.",
    },
    {
        "id": "secure",
        "name": "Secure",
        "desc": "4-of-7 erasure coding. Higher redundancy, needs 4+ nodes.",
    },
    {
        "id": "paranoid",
        "name": "Paranoid",
        "desc": "5-of-10 erasure coding. Maximum redundancy, needs 10+ nodes.",
    },
    {
        "id": "adaptive",
        "name": "Adaptive",
        "desc": "Automatically adjusts erasure coding based on cluster size.",
    },
]

_TAHOE_RESERVED_BYTES = 1 * 1024 ** 3  # 1 GB safety margin
_NODE_PRIVKEY_RELPATH = Path("tahoe") / "storage_node" / "private" / "node.privkey"
_RECOVERY_KIT_FILENAME = "recovery_kit.enc"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_data_dir(request: Request) -> Path:
    return Path(request.app.state.data_dir)


def _get_config_path(request: Request) -> Path:
    return Path(request.app.state.config_path)


def _render(request: Request, template: str, ctx: dict) -> HTMLResponse:
    return _templates.TemplateResponse(request, template, ctx)


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _validate_storage_paths(raw: str) -> tuple[list[str], str]:
    """Parse and validate newline-separated storage paths.

    Returns (resolved_paths, error_message).  error_message is empty on success.
    Paths must be absolute, exist, and be writable.
    """
    lines = [p.strip() for p in raw.splitlines() if p.strip()]
    if not lines:
        return [], "At least one storage path is required."

    resolved: list[str] = []
    for raw_path in lines:
        if not os.path.isabs(raw_path):
            return [], f"Path must be absolute: {raw_path!r}"
        real = os.path.realpath(raw_path)
        if not os.path.exists(real):
            return [], f"Path does not exist: {raw_path!r}"
        if not os.path.isdir(real):
            return [], f"Not a directory: {raw_path!r}"
        if not os.access(real, os.W_OK):
            return [], f"Path is not writable: {raw_path!r}"
        resolved.append(real)

    return resolved, ""


# ── Finish cascade ────────────────────────────────────────────────────────────

def _write_gatekeeper_cfg(
    config_path: Path,
    state: WizardState,
    run_introducer: bool,
    introducer_furl: str,
    agent_api_token: str,
) -> None:
    """Write gatekeeper.cfg atomically from wizard state.

    This is the LAST step of the finish cascade — once this file exists the
    service switches to normal mode on next restart.
    """
    parser = configparser.ConfigParser(allow_no_value=True, delimiters=("=",))
    parser.optionxform = str  # preserve case

    parser.add_section("node")
    parser.set("node", "name", state.node_name)
    parser.set("node", "display_name", state.node_display_name)

    parser.add_section("tahoe")
    parser.set("tahoe", "run_introducer", "true" if run_introducer else "false")
    if not run_introducer and introducer_furl:
        parser.set("tahoe", "introducer", introducer_furl)

    parser.add_section("fragmentation")
    parser.set("fragmentation", "profile", state.profile)

    parser.add_section("storage-pool")
    quota_str = f"{state.storage_quota_gb} GB"
    for path in state.storage_paths:
        parser.set("storage-pool", path, quota_str)

    if state.notify_smtp_enabled:
        parser.add_section("notify.smtp")
        parser.set("notify.smtp", "enabled", "true")
        parser.set("notify.smtp", "host", state.notify_smtp_host)
        parser.set("notify.smtp", "port", str(state.notify_smtp_port))
        parser.set("notify.smtp", "user", state.notify_smtp_user)
        parser.set("notify.smtp", "to", state.notify_smtp_to)

    if state.notify_webhook_enabled:
        parser.add_section("notify.webhook")
        parser.set("notify.webhook", "enabled", "true")

    parser.add_section("web")
    parser.set("web", "enabled", "true")
    parser.set("web", "port", "8080")
    parser.set("web", "bind", "tailscale")

    parser.add_section("agent_api")
    parser.set("agent_api", "enabled", "true")
    parser.set("agent_api", "port", "8081")
    parser.set("agent_api", "token", agent_api_token)

    tmp = config_path.with_suffix(".tmp")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        parser.write(f)
    os.replace(tmp, config_path)
    logger.info("gatekeeper.cfg written to %s", config_path)


async def _cascade_new_cluster(
    data_dir: Path,
    config_path: Path,
    state: WizardState,
    smtp_password: str,
    webhook_url: str,
    passphrase: str,
) -> str:
    """Run the new-cluster finish cascade. Returns the root_dir_cap string.

    Steps are idempotent: skipped if the artifact already exists.
    gatekeeper.cfg is written LAST so a partial failure leaves the system
    in a retryable state.
    """
    # -- Tahoe bootstrap --
    cap_path = data_dir / "root_dir.cap"
    root_dir_cap: str

    if cap_path.exists():
        root_dir_cap = cap_path.read_text(encoding="utf-8").strip()
        logger.info("root_dir.cap already exists — skipping Tahoe bootstrap")
    else:
        logger.info("Starting Tahoe introducer for initial setup")
        introducer = IntroducerNode(str(data_dir / "tahoe" / "introducer"))
        introducer.create()
        introducer_furl = await introducer.start()

        primary_path = state.storage_paths[0]
        logger.info("Starting Tahoe storage node at %s", primary_path)
        storage_node = StorageNode(
            basedir=str(data_dir / "tahoe" / "storage_node"),
            storage_dir=primary_path,
            reserved_space=_TAHOE_RESERVED_BYTES,
            nickname=state.node_name,
        )
        storage_node.create(introducer_furl)
        await storage_node.start()

        logger.info("Creating Tahoe root directory")
        client = TahoeClient(storage_node.node_url)
        root_dir_cap = await client.mkdir()

        # Stop nodes; service restart will start them properly
        await storage_node.stop()
        await introducer.stop()

        # Persist root_dir.cap (chmod 600 on POSIX)
        cap_path.write_text(root_dir_cap, encoding="utf-8")
        try:
            os.chmod(cap_path, 0o600)
        except OSError:
            pass
        logger.info("root_dir.cap written")

    # -- Recovery kit --
    kit_path = data_dir / _RECOVERY_KIT_FILENAME
    if not kit_path.exists():
        privkey_path = data_dir / _NODE_PRIVKEY_RELPATH
        node_privkey = privkey_path.read_text(encoding="utf-8").strip()
        kit_bytes = create_recovery_kit(passphrase, node_privkey, root_dir_cap)
        kit_path.write_bytes(kit_bytes)
        try:
            os.chmod(kit_path, 0o600)
        except OSError:
            pass
        logger.info("Recovery kit written to %s", kit_path)

    # -- Lifeboat key --
    if not DEFAULT_KEY_PATH.exists():
        logger.info("Generating lifeboat key at %s", DEFAULT_KEY_PATH)
        generate_key(DEFAULT_KEY_PATH)

    # -- Cluster database --
    cluster_db = ClusterDB(str(data_dir / "cluster.db"))
    cluster_db.upsert_self_member(
        node_id=state.node_name,
        display_name=state.node_display_name,
        tailscale_hostname=state.node_name,
        profile=state.profile,
    )

    # -- First invite code --
    if not state.first_invite_code:
        invite = generate_invite(cluster_db, created_by=state.node_name)
        state.first_invite_code = invite.code
        logger.info("First invite code generated")

    cluster_db.close()

    # -- Secrets --
    secrets = SecretsStore(data_dir)
    if smtp_password:
        secrets.set_secret("smtp_password", smtp_password)
    if webhook_url:
        secrets.set_secret("webhook_url", webhook_url)

    # -- gatekeeper.cfg (LAST — atomic) --
    agent_api_token = _secrets_mod.token_hex(32)
    _write_gatekeeper_cfg(
        config_path=config_path,
        state=state,
        run_introducer=True,
        introducer_furl="",
        agent_api_token=agent_api_token,
    )

    return root_dir_cap


async def _cascade_join(
    data_dir: Path,
    config_path: Path,
    state: WizardState,
    smtp_password: str,
    webhook_url: str,
) -> None:
    """Run the join-cluster finish cascade.

    Contacts the existing gatekeeper, obtains the introducer FURL, sets up
    the local Tahoe storage node, creates a local root_dir, then writes config.
    """
    cap_path = data_dir / "root_dir.cap"
    introducer_furl: str

    if cap_path.exists():
        logger.info("root_dir.cap already exists — skipping Tahoe bootstrap")
        introducer_furl = _read_introducer_furl_from_storage_node(data_dir)
    else:
        logger.info("Contacting existing cluster to join")
        node_info = NodeInfo(
            node_id=state.node_name,
            display_name=state.node_display_name,
            tailscale_hostname=state.node_name,
            profile="adaptive",
        )
        result = await initiate_join(
            invite_code=state.invite_code,
            node_info=node_info,
            member_url=state.gatekeeper_url,
        )
        if not result.success:
            raise RuntimeError(f"Join failed: {result.error}")

        introducer_furl = result.introducer_furl
        logger.info("Join accepted by cluster — introducer_furl received")

        primary_path = state.storage_paths[0]
        storage_node = StorageNode(
            basedir=str(data_dir / "tahoe" / "storage_node"),
            storage_dir=primary_path,
            reserved_space=_TAHOE_RESERVED_BYTES,
            nickname=state.node_name,
        )
        storage_node.create(introducer_furl)
        await storage_node.start()

        client = TahoeClient(storage_node.node_url)
        root_dir_cap = await client.mkdir()

        await storage_node.stop()

        cap_path.write_text(root_dir_cap, encoding="utf-8")
        try:
            os.chmod(cap_path, 0o600)
        except OSError:
            pass
        logger.info("root_dir.cap written")

    # -- Lifeboat key --
    if not DEFAULT_KEY_PATH.exists():
        generate_key(DEFAULT_KEY_PATH)

    # -- Cluster database --
    cluster_db = ClusterDB(str(data_dir / "cluster.db"))
    cluster_db.upsert_self_member(
        node_id=state.node_name,
        display_name=state.node_display_name,
        tailscale_hostname=state.node_name,
        profile=state.profile,
    )
    cluster_db.close()

    # -- Secrets --
    secrets = SecretsStore(data_dir)
    if smtp_password:
        secrets.set_secret("smtp_password", smtp_password)
    if webhook_url:
        secrets.set_secret("webhook_url", webhook_url)

    # -- gatekeeper.cfg (LAST — atomic) --
    agent_api_token = _secrets_mod.token_hex(32)
    _write_gatekeeper_cfg(
        config_path=config_path,
        state=state,
        run_introducer=False,
        introducer_furl=introducer_furl,
        agent_api_token=agent_api_token,
    )


def _read_introducer_furl_from_storage_node(data_dir: Path) -> str:
    """Read the introducer FURL from the existing storage node config (for retry)."""
    import configparser as _cp
    cfg_file = data_dir / "tahoe" / "storage_node" / "tahoe.cfg"
    if not cfg_file.exists():
        return ""
    p = _cp.ConfigParser()
    p.read(str(cfg_file))
    return p.get("client", "introducer.furl", fallback="")


# ── Route factory ─────────────────────────────────────────────────────────────

def create_onboarding_router() -> APIRouter:
    router = APIRouter()

    # ── Root redirect ──────────────────────────────────────────────────────────

    @router.get("/onboarding/", response_class=HTMLResponse)
    async def wizard_root(request: Request) -> Any:
        return _redirect("/onboarding/step/1")

    # ── Step 1: Role ───────────────────────────────────────────────────────────

    @router.get("/onboarding/step/1", response_class=HTMLResponse)
    async def step1_get(request: Request) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)
        return _render(request, "wizard_step1.html", {"state": state})

    @router.post("/onboarding/step/1")
    async def step1_post(request: Request, role: str = Form(...)) -> Any:
        if role not in ("new", "join"):
            data_dir = _get_data_dir(request)
            state = load_state(data_dir)
            return _render(request, "wizard_step1.html", {
                "state": state,
                "error": "Please select a role.",
            })
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)
        state.role = role
        save_state(data_dir, state)
        if role == "join":
            return _redirect("/onboarding/join")
        return _redirect("/onboarding/step/2")

    # ── Join credentials ───────────────────────────────────────────────────────

    @router.get("/onboarding/join", response_class=HTMLResponse)
    async def join_get(request: Request) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)
        if state.role != "join":
            return _redirect("/onboarding/step/1")
        return _render(request, "wizard_join.html", {"state": state})

    @router.post("/onboarding/join")
    async def join_post(
        request: Request,
        invite_code: str = Form(...),
        gatekeeper_url: str = Form(...),
    ) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)
        if state.role != "join":
            return _redirect("/onboarding/step/1")

        invite_code = invite_code.strip()
        gatekeeper_url = gatekeeper_url.strip().rstrip("/")

        error = ""
        if not invite_code:
            error = "Invite code is required."
        elif not re.match(r"^[a-z]+-[a-z]+-\d+$", invite_code):
            error = "Invite code format is invalid. Expected: word-word-number."
        elif not gatekeeper_url.startswith(("http://", "https://")):
            error = "Gatekeeper address must start with http:// or https://."

        if error:
            return _render(request, "wizard_join.html", {"state": state, "error": error})

        state.invite_code = invite_code
        state.gatekeeper_url = gatekeeper_url
        save_state(data_dir, state)
        return _redirect("/onboarding/step/2")

    # ── Step 2: Node name ──────────────────────────────────────────────────────

    @router.get("/onboarding/step/2", response_class=HTMLResponse)
    async def step2_get(request: Request) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)
        if not state.role:
            return _redirect("/onboarding/step/1")
        return _render(request, "wizard_step2.html", {"state": state})

    @router.post("/onboarding/step/2")
    async def step2_post(
        request: Request,
        node_name: str = Form(...),
        node_display_name: str = Form(...),
    ) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)

        node_name = node_name.strip().lower()
        node_display_name = node_display_name.strip()

        if not _NODE_NAME_RE.match(node_name):
            return _render(request, "wizard_step2.html", {
                "state": state,
                "error": (
                    "Node ID must be 2–32 characters: lowercase letters, digits, "
                    "and hyphens only. Must start and end with a letter or digit."
                ),
            })
        if not node_display_name:
            return _render(request, "wizard_step2.html", {
                "state": state,
                "error": "Display name is required.",
            })

        state.node_name = node_name
        state.node_display_name = node_display_name
        save_state(data_dir, state)
        return _redirect("/onboarding/step/3")

    # ── Step 3: Storage ────────────────────────────────────────────────────────

    @router.get("/onboarding/step/3", response_class=HTMLResponse)
    async def step3_get(request: Request) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)
        if not state.node_name:
            return _redirect("/onboarding/step/2")
        return _render(request, "wizard_step3.html", {"state": state})

    @router.post("/onboarding/step/3")
    async def step3_post(
        request: Request,
        storage_paths: str = Form(...),
        storage_quota_gb: int = Form(...),
    ) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)

        resolved, error = _validate_storage_paths(storage_paths)
        if error:
            return _render(request, "wizard_step3.html", {"state": state, "error": error})

        if storage_quota_gb < 10:
            return _render(request, "wizard_step3.html", {
                "state": state,
                "error": "Quota must be at least 10 GB.",
            })

        state.storage_paths = resolved
        state.storage_quota_gb = storage_quota_gb
        save_state(data_dir, state)
        return _redirect("/onboarding/step/4")

    # ── Step 4: Profile ────────────────────────────────────────────────────────

    @router.get("/onboarding/step/4", response_class=HTMLResponse)
    async def step4_get(request: Request) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)
        if not state.storage_paths:
            return _redirect("/onboarding/step/3")
        return _render(request, "wizard_step4.html", {
            "state": state,
            "profiles": _PROFILE_DESCRIPTIONS,
        })

    @router.post("/onboarding/step/4")
    async def step4_post(request: Request, profile: str = Form(...)) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)

        if profile not in _VALID_PROFILES:
            return _render(request, "wizard_step4.html", {
                "state": state,
                "profiles": _PROFILE_DESCRIPTIONS,
                "error": "Please select a profile.",
            })

        state.profile = profile
        save_state(data_dir, state)
        return _redirect("/onboarding/step/5")

    # ── Step 5: Notifications + finish cascade ─────────────────────────────────

    @router.get("/onboarding/step/5", response_class=HTMLResponse)
    async def step5_get(request: Request) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)
        if not state.profile:
            return _redirect("/onboarding/step/4")
        return _render(request, "wizard_step5.html", {"state": state})

    @router.post("/onboarding/step/5")
    async def step5_post(request: Request) -> Any:
        form = await request.form()
        data_dir = _get_data_dir(request)
        config_path = _get_config_path(request)
        state = load_state(data_dir)

        # Read notification settings from form
        smtp_enabled = bool(form.get("smtp_enabled"))
        smtp_host = str(form.get("smtp_host", "")).strip()
        smtp_port_raw = str(form.get("smtp_port", "587")).strip()
        smtp_user = str(form.get("smtp_user", "")).strip()
        smtp_password = str(form.get("smtp_password", ""))
        smtp_to = str(form.get("smtp_to", "")).strip()
        webhook_enabled = bool(form.get("webhook_enabled"))
        webhook_url = str(form.get("webhook_url", "")).strip()
        passphrase = str(form.get("passphrase", ""))
        passphrase_confirm = str(form.get("passphrase_confirm", ""))

        try:
            smtp_port = int(smtp_port_raw)
        except ValueError:
            smtp_port = 587

        # Basic SMTP validation
        if smtp_enabled and not smtp_host:
            return _render(request, "wizard_step5.html", {
                "state": state,
                "error": "SMTP host is required when email notifications are enabled.",
            })

        # Recovery passphrase required for new cluster
        if state.role == "new":
            if not passphrase:
                return _render(request, "wizard_step5.html", {
                    "state": state,
                    "error": "A recovery passphrase is required.",
                })
            if passphrase != passphrase_confirm:
                return _render(request, "wizard_step5.html", {
                    "state": state,
                    "error": "Passphrases do not match — please try again.",
                })

        # Update non-sensitive state
        state.notify_smtp_enabled = smtp_enabled
        state.notify_smtp_host = smtp_host
        state.notify_smtp_port = smtp_port
        state.notify_smtp_user = smtp_user
        state.notify_smtp_to = smtp_to
        state.notify_webhook_enabled = webhook_enabled
        save_state(data_dir, state)

        # Run finish cascade
        try:
            if state.role == "new":
                root_dir_cap = await _cascade_new_cluster(
                    data_dir, config_path, state, smtp_password, webhook_url, passphrase
                )
                # Persist root_dir_cap for the /complete page (will be cleared after confirmation)
                # SECURITY: never log this value
                request.app.state.wizard_root_dir_cap = root_dir_cap
                save_state(data_dir, state)
                return _redirect("/onboarding/complete")
            else:
                await _cascade_join(
                    data_dir, config_path, state, smtp_password, webhook_url
                )
                state.completed = True
                save_state(data_dir, state)
                return _redirect("/onboarding/done")
        except Exception as exc:
            logger.error("Finish cascade failed: %s", type(exc).__name__, exc_info=True)
            return _render(request, "wizard_error.html", {
                "message": str(exc),
            })

    # ── Recovery key confirmation ──────────────────────────────────────────────

    @router.get("/onboarding/complete", response_class=HTMLResponse)
    async def complete_get(request: Request) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)

        # Key already confirmed — don't re-show it
        if state.recovery_key_confirmed:
            return _redirect("/onboarding/first-invite")

        # Recovery kit must exist (created by cascade) — redirect if missing
        kit_path = data_dir / _RECOVERY_KIT_FILENAME
        if not kit_path.exists():
            return _redirect("/onboarding/step/1")

        return _render(request, "wizard_complete.html", {"state": state})

    @router.post("/onboarding/confirm-key")
    async def confirm_key(request: Request) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)
        state.recovery_key_confirmed = True
        save_state(data_dir, state)
        return _redirect("/onboarding/first-invite")

    # ── First invite code ──────────────────────────────────────────────────────

    @router.get("/onboarding/first-invite", response_class=HTMLResponse)
    async def first_invite_get(request: Request) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)
        if not state.recovery_key_confirmed:
            return _redirect("/onboarding/complete")
        return _render(request, "wizard_first_invite.html", {
            "invite_code": state.first_invite_code,
            "state": state,
        })

    # ── Done / restart ─────────────────────────────────────────────────────────

    @router.get("/onboarding/done", response_class=HTMLResponse)
    async def done_get(request: Request) -> Any:
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)

        # Best-effort: detect Tailscale IP for the dashboard URL hint
        ts_ip = get_tailscale_ip()
        dashboard_url = f"http://{ts_ip}:8080" if ts_ip else ""

        return _render(request, "wizard_done.html", {
            "state": state,
            "dashboard_url": dashboard_url,
        })

    @router.post("/api/onboarding/restart")
    async def api_restart(request: Request) -> JSONResponse:
        """Trigger a graceful service restart after a short delay.

        The delay gives the HTTP response time to reach the browser before
        the process exits.  systemd Restart=always will restart the service.
        """
        async def _delayed_exit() -> None:
            await asyncio.sleep(1.5)
            logger.info("Wizard complete — triggering service restart")
            if hasattr(signal, "SIGTERM"):
                os.kill(os.getpid(), signal.SIGTERM)
            else:
                # Windows fallback (dev environment only)
                os._exit(0)

        asyncio.create_task(_delayed_exit())
        return JSONResponse({"ok": True})

    # ── Download recovery key ──────────────────────────────────────────────────

    @router.get("/api/onboarding/download-key")
    async def download_key(request: Request) -> Response:
        """Return the encrypted recovery kit as a downloadable binary file."""
        data_dir = _get_data_dir(request)
        state = load_state(data_dir)
        # Block download once the user has confirmed receipt — shown once only
        if state.recovery_key_confirmed:
            return Response("Not available", status_code=404)
        kit_path = data_dir / _RECOVERY_KIT_FILENAME
        if not kit_path.exists():
            return Response("Not available", status_code=404)
        content = kit_path.read_bytes()
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": 'attachment; filename="recovery-kit.enc"'},
        )

    # ── Catch-all: redirect to current step ───────────────────────────────────

    @router.get("/{_path:path}", response_class=HTMLResponse)
    async def catchall(request: Request, _path: str) -> Any:
        return _redirect("/onboarding/step/1")

    return router
