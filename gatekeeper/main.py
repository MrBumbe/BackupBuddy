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
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import FastAPI

from gatekeeper.config import (
    ConfigError,
    GatekeeperConfig,
    install_sighup_handler,
    load_config,
)
from gatekeeper.db.catalog import CatalogDB
from gatekeeper.db.cluster import ClusterDB
from gatekeeper.storage.pool import PoolPathError, StoragePoolManager
from gatekeeper.tahoe.introducer import IntroducerNode
from gatekeeper.tahoe.storage_node import StorageNode
from gatekeeper.tailscale import TailscaleNotRunning, assert_tailscale_running

logger = logging.getLogger(__name__)

# Floor of free space Tahoe's storage node will leave in the storage directory.
# StoragePoolManager enforces the real quota; this is Tahoe's own safety valve.
_TAHOE_RESERVED_BYTES = 1 * 1024**3  # 1 GB

# Populated by main() before uvicorn starts; consumed by lifespan.
_state: dict[str, Any] = {}


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


async def _lifeboat_stub() -> None:
    logger.info("Lifeboat scheduler: pending implementation (task 1.8.3)")


async def _rebalance_stub() -> None:
    logger.info("Rebalance scheduler: pending implementation (task 1.11.2)")


async def _verify_stub() -> None:
    logger.info("Verify scheduler: pending implementation (task 1.13.2)")


def _register_background_tasks() -> None:
    asyncio.create_task(_watcher_stub(), name="watcher")
    asyncio.create_task(_lifeboat_stub(), name="lifeboat")
    asyncio.create_task(_rebalance_stub(), name="rebalance")
    asyncio.create_task(_verify_stub(), name="verify")


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

    storage_node: StorageNode | None = None
    introducer: IntroducerNode | None = None
    catalog_db: CatalogDB | None = None
    cluster_db: ClusterDB | None = None

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
            storage_node = StorageNode(
                basedir=str(data_dir / "tahoe" / "storage_node"),
                storage_dir=largest_pool_path,
                reserved_space=_TAHOE_RESERVED_BYTES,
                nickname=config.node.name,
            )
            storage_node.create(introducer_furl)
            await storage_node.start()

            # Step 8 — background scheduler stubs
            # TODO: store task refs on app.state.background_tasks to prevent GC
            # once stubs become long-running coroutines (tasks 1.6.2, 1.8.3, 1.11.2, 1.13.2)
            _register_background_tasks()

        # Expose shared state to route handlers (None in setup mode)
        app.state.setup_required = setup_required
        app.state.config = config
        app.state.pool = pool
        app.state.catalog_db = catalog_db
        app.state.cluster_db = cluster_db
        app.state.storage_node = storage_node

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
    from fastapi import Request
    from fastapi.responses import JSONResponse

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


# ── App factory ───────────────────────────────────────────────────────────────

def _create_app() -> FastAPI:
    app = FastAPI(
        title="BackupBuddy Gatekeeper",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    _register_routes(app)
    return app


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

    # Reload config on SIGHUP (no-op on Windows)
    install_sighup_handler(
        config_path,
        lambda new_cfg: _state.update({"config": new_cfg}),
    )

    app = _create_app()

    # GUI binds to Tailscale interface only — never 0.0.0.0 (ADR-002, SECURITY.md §3)
    uvicorn.run(
        app,
        host=tailscale_ip,
        port=config.web.port,
        log_level=args.log_level.lower(),
    )


if __name__ == "__main__":
    main()
