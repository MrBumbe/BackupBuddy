"""
Gatekeeper startup sequence.

Startup order (abort on steps 1–7 failure):
  1. Assert Tailscale running — get local IP
  2. Load and validate gatekeeper.cfg
  3. Initialise storage pool — sets EXCLUDED_PATHS module global
  4. Locate root_dir.cap — abort if missing (onboarding not complete)
  5. Derive catalog key from root_dir.cap; open catalog.db and cluster.db
  6. Start Tahoe introducer node (only if [tahoe] run_introducer = true)
  7. Start Tahoe storage/client node
  8. Register background scheduler stubs (watcher/lifeboat/rebalance/verify)
  9. Start FastAPI on Tailscale IP
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import secrets as _secrets_mod
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gatekeeper.cluster.join import JoinAcceptResponse, JoinRequest, accept_join
from gatekeeper.config import (
    ConfigError,
    GatekeeperConfig,
    install_sighup_handler,
    load_config,
)
from gatekeeper.db.catalog import CatalogDB
from gatekeeper.db.cluster import ClusterDB
from gatekeeper.fragmenter.fragmenter import Fragmenter, derive_metadata_key
from gatekeeper.fragmenter.profiles import get_profile
from gatekeeper.lifeboat.distributor import LifeboatDistributor
from gatekeeper.storage.pool import PoolPathError, StoragePoolManager
from gatekeeper.tahoe.client import TahoeClient
from gatekeeper.tahoe.introducer import IntroducerNode
from gatekeeper.tahoe.storage_node import StorageNode
from gatekeeper.gui.app import setup_gui
from gatekeeper.tailscale import (
    _TAILSCALE_CGNAT,
    TailscaleNotRunning,
    assert_tailscale_running,
    get_lan_ip,
)

logger = logging.getLogger(__name__)

# Floor of free space Tahoe's storage node will leave in the storage directory.
# StoragePoolManager enforces the real quota; this is Tahoe's own safety valve.
_TAHOE_RESERVED_BYTES = 1 * 1024**3  # 1 GB

# Populated by main() before servers start; consumed by lifespan.
_state: dict[str, Any] = {}

# In-memory registry of connected agents (keyed by agent_name).
# Persisted storage will be added when lifeboat distribution (task 1.8.3) needs it.
_registered_agents: dict[str, dict] = {}


# ── Agent API helpers ─────────────────────────────────────────────────────────

class _AgentRegisterMessage(BaseModel):
    agent_name: str
    lifeboat_port: int | None = None


def _is_lan_ip(ip_str: str) -> bool:
    """Return True if ip_str is a private, non-loopback, non-Tailscale address."""
    try:
        ip = ipaddress.IPv4Address(ip_str)
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback and ip not in _TAILSCALE_CGNAT


# ── Key derivation ─────────────────────────────────────────────────────────────

def _derive_catalog_key(root_dir_cap: str) -> bytes:
    """Derive a 32-byte AES-256-GCM key from root_dir.cap material via HKDF-SHA256."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"backupbuddy:catalog:v1",
    )
    return hkdf.derive(root_dir_cap.encode("utf-8"))


# ── Background scheduler stubs ────────────────────────────────────────────────

async def _watcher_stub() -> None:
    logger.info("Watcher scheduler: pending implementation (task 1.6.2)")


async def _rebalance_stub() -> None:
    logger.info("Rebalance scheduler: pending implementation (task 1.11.2)")


async def _verify_stub() -> None:
    logger.info("Verify scheduler: pending implementation (task 1.13.2)")


def _register_background_tasks(
    lifeboat_distributor: LifeboatDistributor | None = None,
) -> list[asyncio.Task]:
    tasks: list[asyncio.Task] = []
    tasks.append(asyncio.create_task(_watcher_stub(), name="watcher"))
    if lifeboat_distributor is not None:
        tasks.append(
            asyncio.create_task(lifeboat_distributor.run_scheduler(), name="lifeboat")
        )
    tasks.append(asyncio.create_task(_rebalance_stub(), name="rebalance"))
    tasks.append(asyncio.create_task(_verify_stub(), name="verify"))
    return tasks


# ── FastAPI lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Steps 3–9 of the startup sequence; full cleanup on shutdown.

    Two modes:
    - Setup mode: root_dir.cap absent — GUI starts so the user can complete
      onboarding (task 1.7.x). Databases and Tahoe processes are NOT started.
    - Normal mode: root_dir.cap present — full startup, all components active.

    app.state.setup_required is set in both modes so route handlers can gate
    access to features that require a fully onboarded gatekeeper.
    """
    config: GatekeeperConfig = _state["config"]
    data_dir: Path = _state["data_dir"]
    config_path: Path = _state.get("config_path", data_dir / "gatekeeper.cfg")

    storage_node: StorageNode | None = None
    introducer: IntroducerNode | None = None
    catalog_db: CatalogDB | None = None
    cluster_db: ClusterDB | None = None
    tahoe_client: TahoeClient | None = None
    fragmenter: Fragmenter | None = None

    try:
        # Step 3 — storage pool (always runs; validates paths are accessible)
        logger.info("Initialising storage pool (%d path(s))", len(config.storage_pool))
        pool = StoragePoolManager(config.storage_pool)

        # Step 4 — detect setup mode
        root_cap_path = data_dir / "root_dir.cap"
        root_dir_cap = (
            root_cap_path.read_text(encoding="utf-8").strip()
            if root_cap_path.exists()
            else None
        )
        setup_required = not root_dir_cap
        introducer_furl = ""

        if setup_required:
            logger.warning(
                "root_dir.cap not found — starting in setup mode. "
                "Open the GUI to complete onboarding."
            )
        else:
            # Step 5 — databases
            logger.info("Opening databases")
            catalog_key = _derive_catalog_key(root_dir_cap)  # type: ignore[arg-type]
            catalog_db = CatalogDB(str(data_dir / "catalog.db"), catalog_key)
            cluster_db = ClusterDB(str(data_dir / "cluster.db"))

            # Step 6 — introducer (only on the designated introducer node)
            introducer_furl = config.tahoe.introducer
            if config.tahoe.run_introducer:
                logger.info("Starting Tahoe introducer node")
                introducer = IntroducerNode(str(data_dir / "tahoe" / "introducer"))
                introducer.create()
                introducer_furl = await introducer.start()
                # FURL is internal — never logged at INFO level or above

            # Step 7 — storage/client node
            logger.info("Starting Tahoe storage node")
            # Phase 1: Tahoe's storage node writes to the pool path with the
            # largest quota. Remaining paths are used by the fragmenter (task 1.9)
            # via StoragePoolManager.get_target_path().
            # Multi-path distribution at the Tahoe level is a Phase 2 concern.
            largest_pool_path = max(
                pool.get_usage(), key=lambda e: e["quota_bytes"]
            )["path"]

            # ADR-018: k/n is a node-level Tahoe setting.  Resolve from the
            # active profile; adaptive k/n falls back to balanced (3,5) until
            # task 1.11.1 provides get_current_kn().
            active_profile = config.fragmentation.profile
            if active_profile == "adaptive":
                shares_k, shares_n = 3, 5
                logger.info(
                    "Adaptive profile selected — using balanced (3,5) at startup; "
                    "task 1.11.1 will compute final k/n from cluster size"
                )
            else:
                _p = get_profile(active_profile)
                shares_k, shares_n = _p.k, _p.n

            storage_node = StorageNode(
                basedir=str(data_dir / "tahoe" / "storage_node"),
                storage_dir=largest_pool_path,
                reserved_space=_TAHOE_RESERVED_BYTES,
                nickname=config.node.name,
                shares_needed=shares_k,
                shares_happy=shares_n,
                shares_total=shares_n,
            )
            storage_node.create(introducer_furl)
            await storage_node.start()

            # Step 7 (continued) — Tahoe client wrapping the storage node's HTTP gateway
            logger.info("Starting Tahoe client at %s", storage_node.node_url)
            tahoe_client = TahoeClient(storage_node.node_url)
            logger.info("Tahoe client ready")

            # Step 7 (continued) — Fragmenter
            # root_dir.cap is the mutable Tahoe directory cap; its read-write form
            # is used as the root_dir_ref for linking uploaded files (ADR-008).
            root_dir_ref = root_dir_cap  # type: ignore[assignment]
            metadata_key = derive_metadata_key(root_dir_cap)  # type: ignore[arg-type]
            fragmenter = Fragmenter(
                tahoe_client=tahoe_client,
                catalog_db=catalog_db,
                root_dir_ref=root_dir_ref,
                metadata_key=metadata_key,
            )
            logger.info("Fragmenter ready (profile=%s k=%d n=%d)",
                        active_profile, shares_k, shares_n)

            # Step 8 — background schedulers
            lifeboat_distributor: LifeboatDistributor | None = None
            if config.lifeboat.enabled and config.agent_api.token:
                lifeboat_distributor = LifeboatDistributor(
                    data_dir=data_dir,
                    config_path=config_path,
                    catalog_conn=catalog_db.connection,  # type: ignore[union-attr]
                    cluster_db=cluster_db,  # type: ignore[arg-type]
                    agent_token=config.agent_api.token,
                    interval_seconds=config.lifeboat.interval_seconds,
                )
            background_tasks = _register_background_tasks(lifeboat_distributor)
            app.state.background_tasks = background_tasks  # keep refs to prevent GC

        # Expose shared state to route handlers (None in setup mode)
        app.state.setup_required = setup_required
        app.state.config = config
        app.state.pool = pool
        app.state.catalog_db = catalog_db
        app.state.cluster_db = cluster_db
        app.state.storage_node = storage_node
        app.state.tahoe_client = tahoe_client
        app.state.fragmenter = fragmenter
        # FURL is internal — available to the join route but never logged or shown to users
        app.state.introducer_furl = introducer_furl

        logger.info(
            "Gatekeeper '%s' %s — GUI at http://%s:%d",
            config.node.display_name,
            "ready" if not setup_required else "in setup mode",
            config.tailscale_ip,
            config.web.port,
        )

        yield

    finally:
        logger.info("Gatekeeper shutting down")
        if tahoe_client is not None:
            await tahoe_client.aclose()
        if storage_node is not None:
            await storage_node.stop()
        if introducer is not None:
            await introducer.stop()
        if catalog_db is not None:
            catalog_db.close()
        if cluster_db is not None:
            cluster_db.close()
        logger.info("Gatekeeper shutdown complete")


# ── Routes ───────────────────────────────────────────────────────────────────

def _register_routes(app: FastAPI) -> None:
    @app.get("/api/status")
    async def status(request: Request) -> JSONResponse:
        """Returns whether the gatekeeper is fully operational or in setup mode."""
        if request.app.state.setup_required:
            return JSONResponse({"status": "setup_required"}, status_code=503)
        cfg: GatekeeperConfig = request.app.state.config
        return JSONResponse({
            "status": "ok",
            "node": cfg.node.name,
            "display_name": cfg.node.display_name,
        })

    @app.post("/api/cluster/join")
    async def cluster_join(request: Request, body: JoinRequest) -> JSONResponse:
        """Accept a cluster join request from a new gatekeeper node.

        Validates the invite code, registers the node as a cluster member, and
        returns the introducer FURL and current member list so the joining node
        can configure its local Tahoe client.

        Bound to the Tailscale interface only (ADR-002) — callers must be on
        the Tailscale network.
        """
        if request.app.state.setup_required:
            return JSONResponse({"error": "Gatekeeper not ready"}, status_code=503)

        db = request.app.state.cluster_db
        if db is None:
            return JSONResponse({"error": "Cluster database not available"}, status_code=503)

        furl: str = request.app.state.introducer_furl
        if not furl:
            logger.warning("Cluster join attempted but introducer_furl is not configured")
            return JSONResponse({"error": "Introducer not configured"}, status_code=503)

        try:
            result: JoinAcceptResponse = accept_join(db, body.invite_code, body.node_info, furl)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        return JSONResponse({
            "introducer_furl": result.introducer_furl,
            "members": result.members,
        })


# ── App factory ───────────────────────────────────────────────────────────────

def _create_app() -> FastAPI:
    app = FastAPI(
        title="BackupBuddy Gatekeeper",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    _register_routes(app)
    setup_gui(app)
    return app


# ── Agent API (LAN-only — ADR-017) ───────────────────────────────────────────

def _create_agent_api_app(cfg: GatekeeperConfig, data_dir: Path) -> FastAPI:
    """Build the FastAPI app that listens on the LAN interface for agent calls.

    Requests are accepted only from private, non-Tailscale IPv4 addresses and
    must carry a valid Bearer token matching cfg.agent_api.token.

    Opens its own ClusterDB connection at creation time (SQLite WAL mode allows
    multiple concurrent readers/writers).
    """
    _cluster_db = ClusterDB(str(data_dir / "cluster.db"))

    app = FastAPI(title="BackupBuddy Agent API", docs_url=None, redoc_url=None)

    @app.post("/api/agents/register")
    async def register_agent(
        request: Request,
        message: _AgentRegisterMessage,
    ) -> JSONResponse:
        client_ip = request.client.host if request.client else ""

        if not _is_lan_ip(client_ip):
            logger.warning(
                "Agent registration rejected: non-LAN source %s", client_ip
            )
            return JSONResponse({"error": "Forbidden"}, status_code=403)

        auth = request.headers.get("authorization", "")
        expected = cfg.agent_api.token
        if (
            not expected
            or not auth.startswith("Bearer ")
            or not _secrets_mod.compare_digest(auth[7:], expected)
        ):
            logger.warning(
                "Agent registration rejected: invalid token from %s", client_ip
            )
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        now = datetime.now(timezone.utc).timestamp()
        lifeboat_url: str | None = None
        if message.lifeboat_port is not None:
            lifeboat_url = f"http://{client_ip}:{message.lifeboat_port}/lifeboat"

        _cluster_db.upsert_agent(
            agent_name=message.agent_name,
            ip=client_ip,
            lifeboat_url=lifeboat_url,
            registered_at=now,
            last_seen=now,
        )

        _registered_agents[message.agent_name] = {
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "ip": client_ip,
            "lifeboat_url": lifeboat_url,
        }
        logger.info(
            "Agent registered: '%s' from %s (lifeboat_url=%s)",
            message.agent_name,
            client_ip,
            lifeboat_url or "none",
        )
        return JSONResponse({"status": "registered"})

    return app


# ── Multi-server runner ───────────────────────────────────────────────────────

async def _run_servers(
    gui_app: FastAPI,
    gui_host: str,
    gui_port: int,
    agent_app: FastAPI | None,
    agent_host: str | None,
    agent_port: int,
    log_level: str,
) -> None:
    """Run GUI and (optionally) agent API servers concurrently."""
    gui_cfg = uvicorn.Config(
        gui_app, host=gui_host, port=gui_port, log_level=log_level
    )
    coroutines = [uvicorn.Server(gui_cfg).serve()]

    if agent_app is not None and agent_host is not None:
        agent_cfg = uvicorn.Config(
            agent_app, host=agent_host, port=agent_port, log_level=log_level
        )
        coroutines.append(uvicorn.Server(agent_cfg).serve())

    await asyncio.gather(*coroutines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backupbuddy-gatekeeper",
        description="BackupBuddy gatekeeper node",
    )
    parser.add_argument(
        "--data-dir",
        default=str(Path.home() / ".backupbuddy"),
        metavar="PATH",
        help="data directory for databases, keys, and Tahoe node dirs "
             "(default: ~/.backupbuddy)",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="path to gatekeeper.cfg (default: DATA_DIR/gatekeeper.cfg)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="log level (default: INFO)",
    )
    return parser.parse_args()


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )


def main() -> None:
    args = _parse_args()
    _configure_logging(args.log_level)

    data_dir = Path(args.data_dir).resolve()
    config_path = (
        Path(args.config).resolve() if args.config else data_dir / "gatekeeper.cfg"
    )

    logger.info("BackupBuddy gatekeeper starting")
    logger.info("Data directory: %s", data_dir)

    # Step 1 — assert Tailscale
    try:
        tailscale_ip = assert_tailscale_running()
        logger.info("Tailscale interface found: %s", tailscale_ip)
    except TailscaleNotRunning as exc:
        logger.critical("Startup aborted: %s", exc)
        sys.exit(1)

    # Step 2 — load config
    try:
        config = load_config(config_path, tailscale_ip=tailscale_ip)
        logger.info("Configuration loaded from %s", config_path)
    except ConfigError as exc:
        logger.critical("Startup aborted — configuration error: %s", exc)
        sys.exit(1)

    # Steps 3–9 run inside lifespan (above)
    _state["config"] = config
    _state["data_dir"] = data_dir
    _state["config_path"] = config_path

    # Reload config on SIGHUP (no-op on Windows)
    install_sighup_handler(
        config_path,
        lambda new_cfg: _state.update({"config": new_cfg}),
    )

    gui_app = _create_app()

    # Detect LAN IP for the agent API listener (ADR-017)
    lan_ip = get_lan_ip()
    config = config.model_copy(update={"lan_ip": lan_ip})
    _state["config"] = config

    agent_app: FastAPI | None = None
    if config.agent_api.enabled:
        if not config.agent_api.token:
            logger.warning(
                "Agent API enabled but [agent_api] token is not set — "
                "configure agent_api.token in gatekeeper.cfg to accept agent connections"
            )
        elif lan_ip is None:
            logger.warning(
                "Agent API enabled but no LAN interface found — "
                "agent registration unavailable"
            )
        else:
            logger.info(
                "Agent API will listen on %s:%d (LAN only)",
                lan_ip,
                config.agent_api.port,
            )
            agent_app = _create_agent_api_app(config, data_dir)

    # GUI binds to Tailscale only; agent API binds to LAN only (ADR-002, ADR-017)
    asyncio.run(
        _run_servers(
            gui_app=gui_app,
            gui_host=tailscale_ip,
            gui_port=config.web.port,
            agent_app=agent_app,
            agent_host=lan_ip,
            agent_port=config.agent_api.port,
            log_level=args.log_level.lower(),
        )
    )


if __name__ == "__main__":
    main()
