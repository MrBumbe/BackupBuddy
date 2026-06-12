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
  8. Register background schedulers (watcher/lifeboat/rebalance stubs; verify live)
  9. Start FastAPI on Tailscale IP
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import os
import secrets as _secrets_mod
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles
import shutil
import uvicorn
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gatekeeper.cluster.join import JoinAcceptResponse, JoinRequest, accept_join
from gatekeeper.cluster.orphans import cleanup_orphans
from gatekeeper.cluster.removal import (
    VoteResult,
    apply_grace_extension,
    cast_vote,
    start_grace_period,
)
from gatekeeper.cluster.sync import (
    BallotSyncMessage,
    MemberListPushMessage,
    VoteSyncMessage,
    fetch_member_list_from_peer,
    push_member_list_to_peers,
)
from gatekeeper.config import (
    ConfigError,
    GatekeeperConfig,
    install_sighup_handler,
    load_config,
)
from gatekeeper.db.catalog import CatalogDB
from gatekeeper.db.cluster import ClusterDB
from gatekeeper.fragmenter.adaptive import get_current_kn
from gatekeeper.fragmenter.fragmenter import Fragmenter, derive_metadata_key
from gatekeeper.fragmenter.profiles import get_profile
from gatekeeper.fragmenter.queue_worker import UploadItem, UploadQueueWorker
from gatekeeper.lifeboat.distributor import LifeboatDistributor
from gatekeeper.storage.pool import PoolPathError, StoragePoolManager, delete_fragment as _pool_delete_fragment
from gatekeeper.tahoe.client import TahoeClient
from gatekeeper.tahoe.introducer import IntroducerNode
from gatekeeper.tahoe.storage_node import StorageNode
from gatekeeper.gui.app import setup_gui, setup_onboarding_app
from gatekeeper.verify.nightly import NightlyVerifier
from gatekeeper.tailscale import (
    _TAILSCALE_CGNAT,
    TailscaleNotRunning,
    assert_tailscale_running,
    get_lan_ip,
)

logger = logging.getLogger("gatekeeper.main")

# Default web port used in setup mode before gatekeeper.cfg is written.
_DEFAULT_WEB_PORT = 8080

_DEFAULT_LOG_FILE = "/var/lib/backup-buddy/gatekeeper.log"

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
    share_log: bool = False


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
    logger.info("Watcher scheduler not yet active")


async def _rebalance_stub() -> None:
    logger.info("Rebalance scheduler not yet active")


async def _orphan_cleanup_loop(
    cluster_db: "ClusterDB",
    tahoe_client: TahoeClient,
    pool: StoragePoolManager,
    config: "GatekeeperConfig",
) -> None:
    """Daily orphan fragment cleanup.

    Waits one full interval before the first run so Tahoe is fully started
    and the cluster has stabilised.  Runs every orphan_check_interval_seconds
    thereafter.

    Uses asyncio.to_thread to call the sync cleanup_orphans() function,
    and bridges the async delete_fragment() call back to the main event loop
    via run_coroutine_threadsafe so the httpx client stays on its own loop.
    """
    interval = config.maintenance.orphan_check_interval_seconds
    logger.info("Orphan cleanup loop started — first run in %ds", interval)

    while True:
        await asyncio.sleep(interval)

        logger.info("Orphan cleanup job started")
        loop = asyncio.get_running_loop()

        def _delete_fragment_sync(fragment_id: str) -> int:
            # Called from a worker thread (via asyncio.to_thread).
            # Dispatch async delete back to the main event loop so the
            # httpx.AsyncClient runs on the loop it was created on.
            future = asyncio.run_coroutine_threadsafe(
                _pool_delete_fragment(tahoe_client, pool, fragment_id),
                loop,
            )
            return future.result(timeout=300)

        try:
            result = await asyncio.to_thread(
                cleanup_orphans,
                cluster_db,
                orphan_grace_days=config.maintenance.orphan_grace_days,
                is_refrag_complete=lambda _: True,
                delete_fragment=_delete_fragment_sync,
            )
            logger.info(
                "Orphan cleanup complete: eligible=%d deleted=%d "
                "skipped_grace=%d skipped_refrag=%d",
                result["eligible"],
                result["deleted"],
                result["skipped_grace"],
                result["skipped_refrag"],
            )
        except Exception:
            logger.error("Orphan cleanup job failed", exc_info=True)


async def _member_reconciliation_loop(
    cluster_db: "ClusterDB",
    local_node_id: str,
    web_port: int,
) -> None:
    """Periodic member list reconciliation (5-minute interval).

    Polls a random active peer for their member list and upserts any identity
    changes locally.  Ensures eventual consistency if an on-join push was lost.
    Runs forever; asyncio.CancelledError propagates on shutdown.
    """
    import random as _random

    _INTERVAL = 300  # 5 minutes
    logger.info("Member reconciliation loop started (interval=%ds)", _INTERVAL)

    while True:
        await asyncio.sleep(_INTERVAL)

        peers = [
            m for m in cluster_db.list_members(status="active")
            if m["node_id"] != local_node_id
        ]
        if not peers:
            continue

        peer = _random.choice(peers)
        hostname = peer.get("tailscale_hostname", "")
        if not hostname:
            continue

        logger.debug("Member reconciliation: polling %s", hostname)
        received = await fetch_member_list_from_peer(hostname, web_port)
        if received is None:
            continue

        upserted = 0
        for entry in received:
            if entry["node_id"] == local_node_id:
                continue
            cluster_db.upsert_peer_member(
                node_id=entry["node_id"],
                display_name=entry["display_name"],
                tailscale_hostname=entry["tailscale_hostname"],
                profile=entry.get("profile", "lagom"),
            )
            upserted += 1

        if upserted:
            logger.info(
                "Member reconciliation: upserted %d peer(s) from %s", upserted, hostname
            )


def _register_background_tasks(
    lifeboat_distributor: LifeboatDistributor | None = None,
    cluster_db: "ClusterDB | None" = None,
    tahoe_client: TahoeClient | None = None,
    pool: StoragePoolManager | None = None,
    config: "GatekeeperConfig | None" = None,
    nightly_verifier: NightlyVerifier | None = None,
) -> list[asyncio.Task]:
    tasks: list[asyncio.Task] = []
    tasks.append(asyncio.create_task(_watcher_stub(), name="watcher"))
    if lifeboat_distributor is not None:
        tasks.append(
            asyncio.create_task(lifeboat_distributor.run_scheduler(), name="lifeboat")
        )
    tasks.append(asyncio.create_task(_rebalance_stub(), name="rebalance"))
    if nightly_verifier is not None:
        tasks.append(asyncio.create_task(nightly_verifier.run_scheduler(), name="verify"))
    if cluster_db is not None and tahoe_client is not None and pool is not None and config is not None:
        tasks.append(
            asyncio.create_task(
                _orphan_cleanup_loop(cluster_db, tahoe_client, pool, config),
                name="orphan_cleanup",
            )
        )
    if cluster_db is not None and config is not None:
        tasks.append(
            asyncio.create_task(
                _member_reconciliation_loop(
                    cluster_db,
                    local_node_id=config.node.name,
                    web_port=config.web.port,
                ),
                name="member_reconciliation",
            )
        )
    return tasks


# ── FastAPI lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Steps 3–9 of the startup sequence; full cleanup on shutdown.

    Three modes:
    - Pre-config setup mode: gatekeeper.cfg absent — GUI binds to LAN IP, only the
      onboarding wizard is served. No Tailscale, no databases, no Tahoe. (ADR-019)
    - Post-config setup mode: config present but root_dir.cap absent — GUI starts on
      Tailscale IP but databases and Tahoe are not yet active. (existing behaviour)
    - Normal mode: both config and root_dir.cap present — full startup.

    app.state.setup_required is True in both setup modes so route handlers can gate
    access to features that require a fully onboarded gatekeeper.
    """
    # ── Pre-config setup mode (ADR-019) ──────────────────────────────────────
    if _state.get("setup_mode"):
        app.state.setup_required = True
        app.state.config = None
        app.state.config_path = _state.get("config_path")
        app.state.data_dir = _state.get("data_dir")
        app.state.log_file = _state.get("log_file", _DEFAULT_LOG_FILE)
        app.state.pool = None
        app.state.catalog_db = None
        app.state.cluster_db = None
        app.state.storage_node = None
        app.state.tahoe_client = None
        app.state.fragmenter = None
        app.state.nightly_verifier = None
        app.state.introducer_furl = ""
        app.state.local_node_id = None
        app.state.background_tasks = []
        yield
        return

    # ── Normal / post-config startup ──────────────────────────────────────────
    config: GatekeeperConfig = _state["config"]
    data_dir: Path = _state["data_dir"]
    config_path: Path = _state.get("config_path", data_dir / "gatekeeper.cfg")

    storage_node: StorageNode | None = None
    introducer: IntroducerNode | None = None
    catalog_db: CatalogDB | None = None
    cluster_db: ClusterDB | None = None
    tahoe_client: TahoeClient | None = None
    fragmenter: Fragmenter | None = None
    queue_worker: UploadQueueWorker | None = None
    nightly_verifier: NightlyVerifier | None = None

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

            # Register local node in cluster.db so it can cast votes and
            # generate invites (config.node.name is the local node's node_id).
            cluster_db.upsert_self_member(
                node_id=config.node.name,
                display_name=config.node.display_name,
                tailscale_hostname=config.tailscale_ip or config.node.name,
                profile=config.fragmentation.profile,
            )

            # Step 6 — introducer (only on the designated introducer node)
            introducer_furl = config.tahoe.introducer
            if config.tahoe.run_introducer:
                logger.info("Starting Tahoe introducer node")
                introducer = IntroducerNode(str(data_dir / "tahoe" / "introducer"))
                introducer.create(hostname=get_lan_ip() or "127.0.0.1")
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
            # active profile; adaptive reads current cluster size from cluster_db.
            active_profile = config.fragmentation.profile
            if active_profile == "adaptive":
                assert cluster_db is not None
                shares_k, shares_n = get_current_kn(cluster_db, config.fragmentation.adaptive)
                shares_happy = shares_k  # happy = k: upload succeeds when decode is possible
                logger.info("Adaptive profile selected — k=%d n=%d", shares_k, shares_n)
            else:
                _p = get_profile(active_profile)
                shares_k, shares_n = _p.k, _p.n
                shares_happy = _p.happy if _p.happy is not None else shares_n

            storage_node = StorageNode(
                basedir=str(data_dir / "tahoe" / "storage_node"),
                storage_dir=largest_pool_path,
                reserved_space=_TAHOE_RESERVED_BYTES,
                nickname=config.node.name,
                web_port=config.tahoe.tahoe_web_port,
                shares_needed=shares_k,
                shares_happy=shares_happy,
                shares_total=shares_n,
                hostname=config.tailscale_ip or "127.0.0.1",
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
                adaptive_kn=(shares_k, shares_n) if active_profile == "adaptive" else None,
            )
            logger.info("Fragmenter ready (profile=%s k=%d n=%d)",
                        active_profile, shares_k, shares_n)

            # Step 7 (continued) — upload queue and worker
            upload_queue: asyncio.Queue[UploadItem] = asyncio.Queue()
            _state["upload_queue"] = upload_queue
            upload_tmp_dir = data_dir / "upload_tmp"
            upload_tmp_dir.mkdir(parents=True, exist_ok=True)
            _state["upload_tmp_dir"] = upload_tmp_dir
            queue_worker = UploadQueueWorker(
                queue=upload_queue,
                fragmenter=fragmenter,
                upload_concurrent=config.watcher.upload_concurrent,
            )
            queue_worker.start()

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
            nightly_verifier = NightlyVerifier(
                verify_config=config.verify,
                catalog=catalog_db,  # type: ignore[arg-type]
                cluster=cluster_db,  # type: ignore[arg-type]
                tahoe=tahoe_client,  # type: ignore[arg-type]
                root_dir_cap=root_dir_cap,  # type: ignore[arg-type]
                agent_token=config.agent_api.token,
                send_alert=None,
            )
            background_tasks = _register_background_tasks(
                lifeboat_distributor,
                cluster_db=cluster_db,
                tahoe_client=tahoe_client,
                pool=pool,
                config=config,
                nightly_verifier=nightly_verifier,
            )
            app.state.background_tasks = background_tasks  # keep refs to prevent GC

        # Expose shared state to route handlers (None in setup mode)
        app.state.setup_required = setup_required
        app.state.log_file = _state.get("log_file", _DEFAULT_LOG_FILE)
        app.state.config = config
        app.state.config_path = config_path
        app.state.data_dir = data_dir
        app.state.pool = pool
        app.state.catalog_db = catalog_db
        app.state.cluster_db = cluster_db
        app.state.storage_node = storage_node
        app.state.tahoe_client = tahoe_client
        app.state.fragmenter = fragmenter
        app.state.nightly_verifier = nightly_verifier
        # FURL is internal — available to the join route but never logged or shown to users
        app.state.introducer_furl = introducer_furl
        app.state.local_node_id = config.node.name

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
        if queue_worker is not None:
            await queue_worker.stop()
        _state.pop("upload_queue", None)
        _state.pop("upload_tmp_dir", None)
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

    @app.post("/api/verify/run-now")
    async def verify_run_now(request: Request) -> JSONResponse:
        """Trigger an on-demand verification run.

        Returns 202 and starts the run in the background.
        Returns 429 if a run is already in progress.
        The caller may poll GET /api/verify/status for the result.
        """
        if request.app.state.setup_required:
            return JSONResponse({"error": "Gatekeeper not ready"}, status_code=503)
        verifier: NightlyVerifier | None = getattr(
            request.app.state, "nightly_verifier", None
        )
        if verifier is None:
            return JSONResponse({"error": "Verifier not available"}, status_code=503)
        if verifier.is_running:
            return JSONResponse(
                {"error": "Verification already in progress"}, status_code=429
            )
        triggered_at = datetime.now(timezone.utc).isoformat()
        # Atomic: no await between the is_running check above and this set.
        verifier._is_running = True

        async def _bg() -> None:
            try:
                await verifier.run(triggered_by="api")
            except Exception:
                logger.exception("On-demand verify raised unexpectedly")
            finally:
                verifier._is_running = False

        task = asyncio.create_task(_bg())
        bg = getattr(request.app.state, "background_tasks", None)
        if bg is not None:
            bg.append(task)
        return JSONResponse({"status": "started", "triggered_at": triggered_at}, status_code=202)

    @app.get("/api/verify/status")
    async def verify_status(request: Request) -> JSONResponse:
        """Return the result of the last verification run."""
        if request.app.state.setup_required:
            return JSONResponse({"error": "Gatekeeper not ready"}, status_code=503)
        db = request.app.state.cluster_db
        if db is None:
            return JSONResponse(
                {"error": "Cluster database not available"}, status_code=503
            )
        last = db.get_last_verify_run()
        if last is None:
            return JSONResponse(
                {"last_run_at": None, "result": "never", "triggered_by": None, "layers": []}
            )
        layers_raw: dict = json.loads(last.get("detail_json", "{}"))
        layers = [
            {"layer": int(k[5:]), **v}
            for k, v in sorted(layers_raw.items())
        ]
        return JSONResponse({
            "last_run_at": last["run_at"],
            "result": last["result"],
            "triggered_by": last.get("triggered_by", "scheduler"),
            "layers": layers,
        })

    @app.post("/api/cluster/sync/vote")
    async def cluster_sync_vote(request: Request, body: VoteSyncMessage) -> JSONResponse:
        """Receive a vote record pushed by the proposer node (ADR-021).

        Bound to Tailscale interface — callers must be cluster members.
        """
        if request.app.state.setup_required:
            return JSONResponse({"error": "Gatekeeper not ready"}, status_code=503)
        db = request.app.state.cluster_db
        if db is None:
            return JSONResponse({"error": "Cluster database not available"}, status_code=503)
        db.upsert_vote(
            vote_id=body.vote_id,
            vote_type=body.vote_type,
            target_node_id=body.target_node_id,
            proposed_by=body.proposed_by,
            proposed_at=body.proposed_at,
            closes_at=body.closes_at,
            votes_yes=body.votes_yes,
            votes_no=body.votes_no,
            resolved=body.resolved,
            grace_extension_days=body.grace_extension_days,
        )
        logger.info(
            "Synced vote %d (type=%s target=%s) from peer",
            body.vote_id, body.vote_type, body.target_node_id,
        )
        return JSONResponse({"status": "ok"})

    @app.post("/api/cluster/sync/ballot")
    async def cluster_sync_ballot(request: Request, body: BallotSyncMessage) -> JSONResponse:
        """Receive a forwarded ballot from a non-proposer node (ADR-021).

        Voter identity is derived from the sender's Tailscale IP — never from
        the request body — to prevent ballot forgery (ADR-021 security).
        """
        if request.app.state.setup_required:
            return JSONResponse({"error": "Gatekeeper not ready"}, status_code=503)
        db = request.app.state.cluster_db
        if db is None:
            return JSONResponse({"error": "Cluster database not available"}, status_code=503)

        sender_ip = request.client.host if request.client else ""
        members = db.list_members()
        voter = next((m for m in members if m["tailscale_hostname"] == sender_ip), None)
        if voter is None:
            logger.warning("sync/ballot rejected: unknown sender IP %s", sender_ip)
            return JSONResponse({"error": "Sender not a cluster member"}, status_code=403)
        voter_node_id: str = voter["node_id"]

        vote_row = db.get_vote(body.vote_id)
        if vote_row is None:
            return JSONResponse({"error": "Vote not found"}, status_code=404)
        if vote_row["proposed_by"] != request.app.state.local_node_id:
            return JSONResponse({"error": "Not the proposer for this vote"}, status_code=403)

        try:
            result = cast_vote(db, body.vote_id, voter_node_id=voter_node_id, choice=body.choice)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        if result == VoteResult.PASSED:
            target_nid: str = vote_row["target_node_id"]
            if vote_row["vote_type"] == "removal":
                def _sync_alert(node_id: str, message: str) -> None:
                    logger.info("grace-alert [node=%s]: %s", node_id, message)
                try:
                    start_grace_period(db, target_nid, send_alert=_sync_alert)
                except ValueError as exc:
                    logger.warning(
                        "Grace period start failed for %s after vote passed: %s",
                        target_nid, exc,
                    )
            elif vote_row["vote_type"] == "grace_extension":
                try:
                    apply_grace_extension(db, body.vote_id)
                except ValueError as exc:
                    logger.warning(
                        "Grace extension apply failed for vote %d: %s",
                        body.vote_id, exc,
                    )

        logger.info(
            "Ballot from %s (%s) on vote %d: choice=%s result=%s",
            voter_node_id, sender_ip, body.vote_id, body.choice, result.value,
        )
        return JSONResponse({"result": result.value})

    @app.post("/api/cluster/sync/members")
    async def cluster_sync_members(
        request: Request, body: MemberListPushMessage
    ) -> JSONResponse:
        """Receive a member list push from a peer after a new node joins.

        Upserts identity fields (display_name, tailscale_hostname, profile) for
        each received entry.  Never overwrites status, grace columns, or joined_at
        — those fields are managed locally.  Never updates the local node's own row.

        Bound to Tailscale interface — callers must be cluster members.
        """
        if request.app.state.setup_required:
            return JSONResponse({"error": "Gatekeeper not ready"}, status_code=503)
        db = request.app.state.cluster_db
        if db is None:
            return JSONResponse({"error": "Cluster database not available"}, status_code=503)
        local_node_id: str = getattr(request.app.state, "local_node_id", "") or ""

        upserted = 0
        for entry in body.members:
            if entry.node_id == local_node_id:
                continue
            db.upsert_peer_member(
                node_id=entry.node_id,
                display_name=entry.display_name,
                tailscale_hostname=entry.tailscale_hostname,
                profile=entry.profile,
            )
            upserted += 1
        logger.info("sync/members: upserted %d peer(s) from push", upserted)
        return JSONResponse({"status": "ok", "upserted": upserted})

    @app.get("/api/cluster/sync/members")
    async def get_cluster_sync_members(request: Request) -> JSONResponse:
        """Return the local member list for peer reconciliation polling.

        Used by the periodic reconciliation loop on other nodes.
        Returns active and grace members; omits removed/evicted nodes.
        """
        if request.app.state.setup_required:
            return JSONResponse({"error": "Gatekeeper not ready"}, status_code=503)
        db = request.app.state.cluster_db
        if db is None:
            return JSONResponse({"error": "Cluster database not available"}, status_code=503)

        return JSONResponse({
            "members": [
                {
                    "node_id": m["node_id"],
                    "display_name": m["display_name"],
                    "tailscale_hostname": m["tailscale_hostname"],
                    "profile": m.get("profile", "lagom"),
                }
                for m in db.list_members()
                if m.get("status") in ("active", "grace")
            ]
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

        # Push updated member list to existing peers (fire-and-forget, ADR-021 pattern).
        # The new joiner already has the full list in the response body, so exclude it.
        local_node_id: str = getattr(request.app.state, "local_node_id", "") or ""
        config = getattr(request.app.state, "config", None)
        web_port: int = config.web.port if config else 8080
        all_members = db.list_members(status="active")
        task = asyncio.create_task(
            push_member_list_to_peers(
                all_members,
                local_node_id=local_node_id,
                web_port=web_port,
                exclude_node_id=body.node_info.node_id,
            )
        )
        bg = getattr(request.app.state, "background_tasks", None)
        if bg is not None:
            bg.append(task)

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
    if _state.get("setup_mode"):
        setup_onboarding_app(app)
    else:
        cfg = _state.get("config")
        setup_gui(
            app,
            gui_on_lan=cfg.web.gui_on_lan if cfg else True,
            gui_on_tailscale=cfg.web.gui_on_tailscale if cfg else False,
        )
    return app


# ── Agent API (LAN-only — ADR-017) ───────────────────────────────────────────

class _FragmentMetadata(BaseModel):
    """Validated header payload for POST /api/agents/fragments."""
    original_path: str
    agent_name: str


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
            share_log=message.share_log,
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

    @app.post("/api/agents/fragments")
    async def receive_file(request: Request) -> JSONResponse:
        client_ip = request.client.host if request.client else ""

        if not _is_lan_ip(client_ip):
            return JSONResponse({"error": "Forbidden"}, status_code=403)

        auth = request.headers.get("authorization", "")
        expected = cfg.agent_api.token
        if (
            not expected
            or not auth.startswith("Bearer ")
            or not _secrets_mod.compare_digest(auth[7:], expected)
        ):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        upload_queue = _state.get("upload_queue")
        if upload_queue is None:
            return JSONResponse({"error": "Gatekeeper not ready"}, status_code=503)

        meta_header = request.headers.get("x-fragment-metadata", "")
        if not meta_header:
            return JSONResponse(
                {"error": "Missing X-Fragment-Metadata header"}, status_code=400
            )
        try:
            meta = _FragmentMetadata.model_validate_json(meta_header)
        except Exception:
            return JSONResponse(
                {"error": "Invalid X-Fragment-Metadata"}, status_code=400
            )

        upload_tmp_dir: Path = _state.get("upload_tmp_dir", data_dir / "upload_tmp")
        upload_tmp_dir.mkdir(parents=True, exist_ok=True)

        # Reject before streaming if Content-Length exceeds available disk space.
        content_length_str = request.headers.get("content-length")
        if content_length_str is not None:
            try:
                content_length = int(content_length_str)
            except ValueError:
                return JSONResponse({"error": "Invalid Content-Length"}, status_code=400)
            free_bytes = shutil.disk_usage(upload_tmp_dir).free
            if free_bytes < content_length:
                logger.warning(
                    "Upload rejected — insufficient disk space in upload_tmp/: "
                    "need %d bytes, %d bytes free (agent='%s')",
                    content_length, free_bytes, meta.agent_name,
                )
                return JSONResponse({"error": "Insufficient storage space"}, status_code=507)
            if free_bytes < 2 * content_length:
                logger.warning(
                    "Low disk space in upload_tmp/: %d bytes free, file is %d bytes — "
                    "concurrent uploads may exhaust disk",
                    free_bytes, content_length,
                )

        tmp_path = upload_tmp_dir / f"{uuid.uuid4().hex}.tmp"

        bytes_received = 0
        try:
            async with aiofiles.open(tmp_path, "wb") as f:
                async for chunk in request.stream():
                    await f.write(chunk)
                    bytes_received += len(chunk)
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            logger.error(
                "Error streaming upload from agent '%s': %s",
                meta.agent_name,
                type(exc).__name__,
            )
            return JSONResponse({"error": "Upload failed"}, status_code=500)

        if bytes_received == 0:
            tmp_path.unlink(missing_ok=True)
            return JSONResponse({"error": "Empty body"}, status_code=400)

        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass

        config_state: GatekeeperConfig | None = _state.get("config")
        profile = (
            config_state.fragmentation.profile if config_state else "balanced"
        )

        item = UploadItem(
            file_path=str(tmp_path),
            profile=profile,
            agent=meta.agent_name,
            original_path=meta.original_path,
        )
        await upload_queue.put(item)

        logger.info(
            "Received file from agent '%s' (%d bytes) — queued for upload",
            meta.agent_name,
            bytes_received,
        )
        return JSONResponse({"status": "queued"})

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
    gui_lan_host: str | None = None,
) -> None:
    """Run GUI and (optionally) agent API and LAN GUI servers concurrently.

    gui_host (Tailscale IP) always starts — serves cluster API routes and GUI if
    gui_on_tailscale is enabled. gui_lan_host, when set, starts a second Uvicorn
    server on the LAN interface with lifespan="off" so that startup/shutdown hooks
    run only once on the primary server (ADR-017, ADR-023).
    """
    gui_cfg = uvicorn.Config(
        gui_app, host=gui_host, port=gui_port, log_level=log_level
    )
    coroutines = [uvicorn.Server(gui_cfg).serve()]

    if gui_lan_host is not None:
        # Second listener on LAN — lifespan="off" to avoid double-initialisation
        gui_lan_cfg = uvicorn.Config(
            gui_app, host=gui_lan_host, port=gui_port,
            log_level=log_level, lifespan="off",
        )
        coroutines.append(uvicorn.Server(gui_lan_cfg).serve())

    if agent_app is not None and agent_host is not None:
        agent_cfg = uvicorn.Config(
            agent_app, host=agent_host, port=agent_port, log_level=log_level
        )
        coroutines.append(uvicorn.Server(agent_cfg).serve())

    await asyncio.gather(*coroutines)


# ── Setup mode (ADR-019) ─────────────────────────────────────────────────────

def _start_setup_mode(data_dir: Path, config_path: Path, log_level: str) -> None:
    """Start in setup mode when gatekeeper.cfg does not exist.

    Binds the onboarding wizard to the LAN IP so it is reachable before
    Tailscale is authenticated. After the wizard writes gatekeeper.cfg and
    triggers a service restart, normal mode activates.
    """
    lan_ip = get_lan_ip()
    if not lan_ip:
        logger.error(
            "Setup mode aborted: no LAN IP address found. "
            "Check that a network interface with a private IPv4 address is up, "
            "then retry."
        )
        raise RuntimeError(
            "No LAN IP address available — cannot start setup mode. "
            "Check your network connection and try again."
        )
    gui_host = lan_ip
    gui_port = _DEFAULT_WEB_PORT

    logger.warning(
        "No configuration found at %s — starting in setup mode", config_path
    )
    logger.info("Onboarding wizard at http://%s:%d", gui_host, gui_port)

    # Ensure data directory exists so lifeboat.key and databases have a home.
    data_dir.mkdir(parents=True, exist_ok=True)

    log_file = _attach_file_handler(_DEFAULT_LOG_FILE)
    _state.update({
        "setup_mode": True,
        "data_dir": data_dir,
        "config_path": config_path,
        "config": None,
        "log_file": log_file,
    })

    gui_app = _create_app()
    asyncio.run(
        _run_servers(
            gui_app=gui_app,
            gui_host=gui_host,
            gui_port=gui_port,
            agent_app=None,
            agent_host=None,
            agent_port=0,
            log_level=log_level,
        )
    )


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

    subparsers = parser.add_subparsers(dest="command")
    verify_parser = subparsers.add_parser(
        "verify",
        help="on-demand verification commands",
    )
    verify_parser.add_argument(
        "--now",
        action="store_true",
        help="trigger a full verification run and wait for the result",
    )

    return parser.parse_args()


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )


def _attach_file_handler(log_file: str) -> str:
    """Add a RotatingFileHandler to the root logger alongside the existing StreamHandler.

    Creates the parent directory if it does not exist.  If the directory cannot be
    created or the file cannot be opened (e.g. permission denied), logs a warning
    and returns without raising — stream logging continues unaffected.

    Returns the resolved log file path.
    """
    from logging.handlers import RotatingFileHandler

    log_path = Path(log_file).resolve()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        logging.getLogger().addHandler(handler)
        logger.info("File logging enabled: %s", log_path)
    except Exception as exc:
        logger.warning(
            "Could not set up file logging at %s: %s", log_path, type(exc).__name__
        )
    return str(log_path)


def _cmd_verify_now(data_dir: Path, config_path: Path, log_level: str) -> None:
    """Trigger an on-demand verification run via the gatekeeper API and wait for the result.

    Exits 0 if the run passes, 1 on failure, 1 on error.
    """
    import time as _time

    import httpx

    _configure_logging(log_level)

    try:
        tailscale_ip = assert_tailscale_running()
    except TailscaleNotRunning as exc:
        logger.critical("Tailscale not running: %s", exc)
        sys.exit(1)

    try:
        config = load_config(config_path, tailscale_ip=tailscale_ip)
    except ConfigError as exc:
        logger.critical("Configuration error: %s", exc)
        sys.exit(1)

    base_url = f"http://{tailscale_ip}:{config.web.port}"

    with httpx.Client(timeout=10.0) as client:
        try:
            resp = client.post(f"{base_url}/api/verify/run-now")
        except Exception as exc:
            logger.error(
                "Could not reach gatekeeper at %s: %s", base_url, type(exc).__name__
            )
            sys.exit(1)

        if resp.status_code == 429:
            logger.info("Verification already in progress — waiting for completion")
            pre_trigger = 0.0
        elif resp.status_code == 202:
            triggered_at_str = resp.json().get("triggered_at", "")
            try:
                pre_trigger = datetime.fromisoformat(triggered_at_str).timestamp()
            except (ValueError, TypeError):
                pre_trigger = 0.0
            logger.info("Verification run triggered")
        elif resp.status_code == 503:
            logger.error("Gatekeeper not ready: %s", resp.json().get("error", ""))
            sys.exit(1)
        else:
            logger.error("Unexpected response from gatekeeper: %d", resp.status_code)
            sys.exit(1)

        deadline = _time.time() + 3600
        while _time.time() < deadline:
            _time.sleep(5.0)
            try:
                status_resp = client.get(f"{base_url}/api/verify/status")
            except Exception as exc:
                logger.warning("Error polling verify status: %s", type(exc).__name__)
                continue
            if status_resp.status_code != 200:
                logger.warning(
                    "Unexpected status response: %d", status_resp.status_code
                )
                continue
            data = status_resp.json()
            last_run_at = data.get("last_run_at")
            if last_run_at is None or last_run_at <= pre_trigger:
                continue
            result = data.get("result", "failed")
            if result == "passed":
                logger.info("Verification passed")
                sys.exit(0)
            else:
                logger.error("Verification failed")
                sys.exit(1)

    logger.error("Timed out waiting for verification to complete (1 hour)")
    sys.exit(1)


def main() -> None:
    args = _parse_args()
    _configure_logging(args.log_level)

    data_dir = Path(args.data_dir).resolve()
    config_path = (
        Path(args.config).resolve() if args.config else data_dir / "gatekeeper.cfg"
    )

    if getattr(args, "command", None) == "verify":
        if getattr(args, "now", False):
            _cmd_verify_now(data_dir, config_path, args.log_level)
        return

    logger.info("BackupBuddy gatekeeper starting")
    logger.info("Data directory: %s", data_dir)

    # Pre-config setup mode: config file absent → skip all normal startup steps
    # and serve the onboarding wizard on the LAN interface (ADR-019).
    if not config_path.exists():
        _start_setup_mode(data_dir, config_path, args.log_level.lower())
        return

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
    _state["log_file"] = _attach_file_handler(config.logging.log_file)

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

    # Determine GUI listener(s) per ADR-023.
    # The Tailscale listener always starts (cluster API).
    # The LAN listener starts only when gui_on_lan = true and a LAN IP is available.
    gui_lan_host: str | None = None
    if config.web.gui_on_lan:
        if lan_ip is None:
            logger.warning(
                "gui_on_lan = true but no LAN interface found — "
                "GUI will not be available on LAN"
            )
        else:
            gui_lan_host = lan_ip
            logger.info("GUI on LAN: http://%s:%d", lan_ip, config.web.port)

    if config.web.gui_on_tailscale:
        logger.info(
            "GUI on Tailscale: http://%s:%d", tailscale_ip, config.web.port
        )

    if not config.web.gui_on_lan and not config.web.gui_on_tailscale:
        logger.warning(
            "GUI is disabled — both gui_on_lan and gui_on_tailscale are false. "
            "Cluster API on %s:%d continues to operate normally.",
            tailscale_ip,
            config.web.port,
        )

    # Cluster API always binds to Tailscale; agent API to LAN only (ADR-017, ADR-023)
    asyncio.run(
        _run_servers(
            gui_app=gui_app,
            gui_host=tailscale_ip,
            gui_port=config.web.port,
            gui_lan_host=gui_lan_host,
            agent_app=agent_app,
            agent_host=lan_ip,
            agent_port=config.agent_api.port,
            log_level=args.log_level.lower(),
        )
    )


if __name__ == "__main__":
    main()
