"""Restore routes: file search, folder restore, and emergency catalog reconstruction.

Routes:
  GET /restore                   — HTML restore page
  GET /api/restore/catalog       — search files in catalog (O(catalog) — HMAC blind index
                                   prevents SQL prefix search; Python-side filter is acceptable
                                   for Phase 1 PoC catalog sizes)
  POST /api/restore/start/file   — queue single-file restore job
  POST /api/restore/start/folder — queue folder restore job
  POST /api/restore/emergency    — start emergency catalog reconstruction
  GET /api/restore/jobs/{job_id} — poll job status

Job tracking: in-memory dict capped at _MAX_JOBS entries (oldest completed jobs evicted first).
Dest path validation: realpath, must be absolute, must not overlap EXCLUDED_PATHS.

Emergency restore design decision (ADR option A):
  Reconstruction writes into the main catalog.db only when it is empty.
  If the catalog already has records, a 409 is returned with a clear message.
  This prevents accidental overwrites of a healthy catalog.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from gatekeeper.restore.reconstruct import reconstruct_catalog
from gatekeeper.restore.restore import (
    RestoreIntegrityError,
    RestoreNotFoundError,
    restore_file,
    restore_folder,
)

logger = logging.getLogger(__name__)

_MAX_JOBS = 50  # cap in-memory registry; oldest completed jobs evicted when limit reached

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

# In-memory job registry keyed by UUID string.
_restore_jobs: dict[str, dict] = {}


# ── Request models ─────────────────────────────────────────────────────────────

class _RestoreFileRequest(BaseModel):
    original_path: str
    agent: str
    dest_path: str


class _RestoreFolderRequest(BaseModel):
    folder_path: str
    agent: str
    dest_path: str


class _EmergencyRequest(BaseModel):
    recovery_key: str  # root_dir.cap — user-facing label is "recovery key" (onboarding.md)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _validate_dest_path(dest_path: str) -> str:
    """Resolve and validate a restore destination path.

    Raises ValueError if the path is not absolute or overlaps with a storage
    pool path (which is excluded from backup and must not receive restore files).
    """
    if not os.path.isabs(dest_path):
        raise ValueError("Destination path must be absolute")
    real = os.path.realpath(dest_path)

    from gatekeeper.storage.pool import EXCLUDED_PATHS  # lazy — pool may not be initialised in tests
    for pool_path in EXCLUDED_PATHS:
        pool_real = os.path.realpath(pool_path)
        if real == pool_real or real.startswith(pool_real + os.sep):
            raise ValueError(
                "Destination overlaps with a storage pool path — cannot restore there"
            )
    return real


def _make_job(job_type: str) -> dict:
    """Register a new job and return it. Prunes oldest completed jobs if at cap."""
    job_id = str(uuid.uuid4())
    job: dict[str, Any] = {
        "job_id": job_id,
        "type": job_type,
        "status": "running",
        "progress": 0,
        "total": None,
        "results": [],
        "error": None,
        "started_at": time.time(),
    }
    _prune_jobs()
    _restore_jobs[job_id] = job
    return job


def _prune_jobs() -> None:
    """Evict oldest completed/failed jobs when the registry reaches _MAX_JOBS."""
    if len(_restore_jobs) < _MAX_JOBS:
        return
    done = sorted(
        [jid for jid, j in _restore_jobs.items() if j["status"] != "running"],
        key=lambda jid: _restore_jobs[jid]["started_at"],
    )
    for jid in done[: max(1, len(done) // 2)]:
        del _restore_jobs[jid]


def _setup_guard(request: Request) -> JSONResponse | None:
    """Return 503 if the gatekeeper is in setup mode, else None."""
    if getattr(request.app.state, "setup_required", True):
        return JSONResponse({"error": "Gatekeeper not ready"}, status_code=503)
    return None


# ── Route factory ──────────────────────────────────────────────────────────────

def create_restore_router() -> APIRouter:
    """Return the APIRouter for all restore routes."""
    router = APIRouter()

    # ── HTML page ──────────────────────────────────────────────────────────────

    @router.get("/restore", response_class=HTMLResponse)
    async def restore_page(request: Request) -> Any:
        setup_required = getattr(request.app.state, "setup_required", True)
        config = getattr(request.app.state, "config", None)
        node_name = config.node.display_name if config else "BackupBuddy"

        agents: list[str] = []
        catalog_db = getattr(request.app.state, "catalog_db", None)
        if catalog_db and not setup_required:
            agents = [r["agent"] for r in catalog_db.get_last_backup_per_agent()]

        return _templates.TemplateResponse(request, "restore.html", {
            "setup_required": setup_required,
            "node_name": node_name,
            "agents": agents,
        })

    # ── Catalog search ─────────────────────────────────────────────────────────

    @router.get("/api/restore/catalog")
    async def catalog_search(request: Request) -> JSONResponse:
        guard = _setup_guard(request)
        if guard:
            return guard

        catalog_db = getattr(request.app.state, "catalog_db", None)
        if catalog_db is None:
            return JSONResponse({"error": "Catalog not available"}, status_code=503)

        q = request.query_params.get("q", "").strip().lower()
        agent_filter = request.query_params.get("agent", "").strip()
        try:
            limit = min(int(request.query_params.get("limit", "200")), 500)
        except ValueError:
            limit = 200

        # O(catalog) — HMAC blind index prevents SQL prefix search on encrypted paths.
        all_files = catalog_db.get_all_files()
        results: list[dict] = []
        for r in all_files:
            path = r.get("original_path")
            if path is None:
                continue
            if agent_filter and r["agent"] != agent_filter:
                continue
            name = os.path.basename(path)
            if q and q not in name.lower() and q not in path.lower():
                continue
            results.append({
                "id": r["id"],
                "agent": r["agent"],
                "original_path": path,
                "filename": name,
                "backed_up_at": r.get("backed_up_at"),
                "size_bytes": r.get("size_bytes"),
            })
            if len(results) >= limit:
                break

        return JSONResponse({
            "results": results,
            "total": len(results),
            "truncated": len(results) >= limit and len(all_files) > limit,
        })

    # ── Single-file restore ────────────────────────────────────────────────────

    @router.post("/api/restore/start/file")
    async def start_file_restore(
        request: Request, body: _RestoreFileRequest
    ) -> JSONResponse:
        guard = _setup_guard(request)
        if guard:
            return guard

        catalog_db = getattr(request.app.state, "catalog_db", None)
        tahoe = getattr(request.app.state, "tahoe_client", None)
        if catalog_db is None or tahoe is None:
            return JSONResponse({"error": "Restore service not available"}, status_code=503)

        try:
            dest = _validate_dest_path(body.dest_path)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        job = _make_job("file")

        async def _run() -> None:
            try:
                result = await restore_file(
                    body.original_path,
                    body.agent,
                    dest,
                    catalog=catalog_db,
                    tahoe=tahoe,
                )
                job["status"] = "done"
                job["results"] = [{
                    "path": body.original_path,
                    "dest": result.dest_path,
                    "success": result.success,
                    "sha256": result.sha256,
                }]
            except RestoreNotFoundError:
                job["status"] = "failed"
                job["error"] = "File not found in backup catalog."
            except RestoreIntegrityError:
                job["status"] = "failed"
                job["error"] = (
                    "Integrity check failed — the restored file may be damaged. "
                    "Try again or contact your buddies to check cluster health."
                )
            except Exception:
                job["status"] = "failed"
                job["error"] = "Restore failed. Check the gatekeeper log for details."
                logger.error("File restore failed unexpectedly", exc_info=True)

        asyncio.create_task(_run())
        return JSONResponse({"job_id": job["job_id"]})

    # ── Folder restore ─────────────────────────────────────────────────────────

    @router.post("/api/restore/start/folder")
    async def start_folder_restore(
        request: Request, body: _RestoreFolderRequest
    ) -> JSONResponse:
        guard = _setup_guard(request)
        if guard:
            return guard

        catalog_db = getattr(request.app.state, "catalog_db", None)
        tahoe = getattr(request.app.state, "tahoe_client", None)
        if catalog_db is None or tahoe is None:
            return JSONResponse({"error": "Restore service not available"}, status_code=503)

        try:
            dest = _validate_dest_path(body.dest_path)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        job = _make_job("folder")

        async def _run() -> None:
            try:
                summary = await restore_folder(
                    body.folder_path,
                    body.agent,
                    dest,
                    catalog=catalog_db,
                    tahoe=tahoe,
                )
                total = summary.files_restored + summary.files_failed
                job["status"] = "done"
                job["progress"] = total
                job["total"] = total
                job["results"] = [
                    {
                        "dest": r.dest_path,
                        "success": r.success,
                        "sha256": r.sha256,
                        "error": r.error,
                    }
                    for r in summary.results
                ]
            except Exception:
                job["status"] = "failed"
                job["error"] = "Folder restore failed. Check the gatekeeper log for details."
                logger.error("Folder restore failed unexpectedly", exc_info=True)

        asyncio.create_task(_run())
        return JSONResponse({"job_id": job["job_id"]})

    # ── Emergency catalog reconstruction ──────────────────────────────────────

    @router.post("/api/restore/emergency")
    async def start_emergency_restore(
        request: Request, body: _EmergencyRequest
    ) -> JSONResponse:
        guard = _setup_guard(request)
        if guard:
            return guard

        catalog_db = getattr(request.app.state, "catalog_db", None)
        tahoe = getattr(request.app.state, "tahoe_client", None)
        if catalog_db is None or tahoe is None:
            return JSONResponse({"error": "Restore service not available"}, status_code=503)

        recovery_key = body.recovery_key.strip()
        if not recovery_key:
            return JSONResponse({"error": "Recovery key is required"}, status_code=400)

        # Refuse to overwrite a non-empty catalog (ADR option A).
        existing_count = len(catalog_db.get_all_files())
        if existing_count > 0:
            return JSONResponse(
                {
                    "error": (
                        f"Main catalog has {existing_count} record(s) — "
                        "emergency reconstruction is only needed when the catalog is empty or missing."
                    )
                },
                status_code=409,
            )

        job = _make_job("emergency")
        progress_queue: asyncio.Queue = asyncio.Queue()

        async def _run() -> None:
            try:
                async def _drain_progress() -> None:
                    while True:
                        update = await progress_queue.get()
                        if update is None:
                            break
                        job["progress"] = update.get("files_processed", job["progress"])

                drain_task = asyncio.create_task(_drain_progress())

                count = await reconstruct_catalog(
                    recovery_key,
                    catalog=catalog_db,
                    tahoe=tahoe,
                    progress_queue=progress_queue,
                )
                await progress_queue.put(None)  # signal drain task to exit
                await drain_task

                job["status"] = "done"
                job["progress"] = count
                job["total"] = count
                job["results"] = [{"files_reconstructed": count}]
            except Exception:
                job["status"] = "failed"
                job["error"] = (
                    "Catalog reconstruction failed. "
                    "Check that your recovery key is correct and the cluster is reachable."
                )
                logger.error("Emergency catalog reconstruction failed", exc_info=True)

        asyncio.create_task(_run())
        return JSONResponse({"job_id": job["job_id"]})

    # ── Job status polling ─────────────────────────────────────────────────────

    @router.get("/api/restore/jobs/{job_id}")
    async def get_job_status(job_id: str) -> JSONResponse:
        job = _restore_jobs.get(job_id)
        if job is None:
            return JSONResponse({"error": "Job not found"}, status_code=404)
        return JSONResponse(job)

    return router
