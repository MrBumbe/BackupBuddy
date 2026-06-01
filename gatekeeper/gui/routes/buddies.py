"""Buddies routes: cluster member management, invites, and removal votes.

Routes:
  GET  /buddies                           — HTML buddies page
  GET  /api/buddies                       — JSON snapshot (30-second polling)
  POST /api/buddies/invite                — generate new invite code (returns full code once)
  POST /api/buddies/invite/{code}/revoke  — revoke an active invite
  POST /api/buddies/removal               — propose removal vote for a member
  POST /api/buddies/vote/{vote_id}/cast   — cast yes/no ballot on an open vote
  POST /api/buddies/grace-extend          — propose grace period extension vote

ADR-010 security: open removal votes where the local node is the target are
filtered from GET /api/buddies.  The target must not know a vote is open.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from gatekeeper.cluster.invites import generate_invite, revoke_invite
from gatekeeper.cluster.removal import (
    VoteResult,
    apply_grace_extension,
    cast_vote,
    extend_grace_period,
    propose_removal,
    start_grace_period,
)

logger = logging.getLogger(__name__)

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


# ── Request models ─────────────────────────────────────────────────────────────

class _ProposeRemovalRequest(BaseModel):
    target_node_id: str


class _CastVoteRequest(BaseModel):
    choice: bool  # true = yes, false = no


class _GraceExtendRequest(BaseModel):
    target_node_id: str
    days: int


# ── Utilities ──────────────────────────────────────────────────────────────────

def _mask_code(code: str) -> str:
    """Return a partially masked invite code for display in the active invites list."""
    parts = code.split("-")
    if len(parts) == 3:  # word-word-N format
        return f"{parts[0]}-***-{parts[2]}"
    if len(parts) == 2:  # bb-{hex} fallback format
        return f"{parts[0]}-***"
    return code[:4] + "***"


def _fmt_timestamp(ts: float | None) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _setup_guard(request: Request) -> JSONResponse | None:
    if getattr(request.app.state, "setup_required", True):
        return JSONResponse({"error": "Gatekeeper not ready"}, status_code=503)
    return None


# ── Data builder ───────────────────────────────────────────────────────────────

def _build_buddies_data(request: Request) -> dict[str, Any]:
    """Assemble buddies page data from app.state.  Safe to call in setup mode."""
    state = request.app.state
    setup_required: bool = getattr(state, "setup_required", True)
    config = getattr(state, "config", None)
    node_name: str = config.node.display_name if config else "BackupBuddy"

    if setup_required or config is None:
        return {"setup_required": True, "node_name": node_name}

    cluster_db = getattr(state, "cluster_db", None)
    pool = getattr(state, "pool", None)
    local_node_id: str = getattr(state, "local_node_id", "")
    now = time.time()

    # Members
    raw_members: list[dict] = cluster_db.list_members() if cluster_db else []

    # Pre-compute which nodes already have open votes so the UI can
    # show/hide propose buttons correctly.
    raw_open_votes: list[dict] = (
        cluster_db.list_votes(resolved=False) if cluster_db else []
    )
    open_removal_targets: set[str] = {
        v["target_node_id"]
        for v in raw_open_votes
        if v["vote_type"] == "removal" and now < v["closes_at"]
    }
    open_extension_targets: set[str] = {
        v["target_node_id"]
        for v in raw_open_votes
        if v["vote_type"] == "grace_extension" and now < v["closes_at"]
    }

    members: list[dict] = []
    for m in raw_members:
        contrib: int = m.get("contribution_bytes") or 0
        usage: int = m.get("usage_bytes") or 0
        ratio: float | None = contrib / usage if usage > 0 else None
        warning = ratio is not None and ratio < 1.2
        error = ratio is not None and ratio < 1.0
        node_id = m["node_id"]
        status = m.get("status", "unknown")
        is_self = node_id == local_node_id

        members.append({
            "node_id": node_id,
            "display_name": m["display_name"],
            "status": status,
            "is_self": is_self,
            "contribution_bytes": contrib,
            "usage_bytes": usage,
            "ratio": ratio,
            "profile": m.get("profile", ""),
            "warning": warning,
            "error": error,
            "can_propose_removal": (
                not is_self
                and status in ("active", "grace")
                and node_id not in open_removal_targets
            ),
            "can_propose_grace_extension": (
                status == "grace"
                and node_id not in open_extension_targets
            ),
        })

    # Storage summary
    pool_paths: list[dict] = pool.get_usage() if pool else []
    total_capacity = sum(p["quota_bytes"] for p in pool_paths)
    total_used = sum(p["used_bytes"] for p in pool_paths)
    total_percent = (
        round(100.0 * total_used / total_capacity, 1) if total_capacity > 0 else 0.0
    )

    # Active invites (not used, not revoked)
    raw_invites: list[dict] = cluster_db.list_invites() if cluster_db else []
    invites: list[dict] = []
    for inv in raw_invites:
        if inv.get("used") or inv.get("revoked"):
            continue
        expires_at = inv["expires_at"]
        invites.append({
            "code": _mask_code(inv["code"]),
            "code_raw": inv["code"],
            "created_by": inv["created_by"],
            "created_at": inv["created_at"],
            "expires_at": expires_at,
            "is_expired": now > expires_at,
        })

    # Votes visible to the local node (ADR-010 filter applied)
    local_member = cluster_db.get_member(local_node_id) if cluster_db else None
    is_eligible_voter = bool(
        local_member and local_member.get("status") in ("active", "grace")
    )
    member_names: dict[str, str] = {m["node_id"]: m["display_name"] for m in raw_members}

    votes: list[dict] = []
    for v in raw_open_votes:
        target_nid = v["target_node_id"]
        # ADR-010: hide open votes that target the local node
        if target_nid == local_node_id:
            continue
        if now > v["closes_at"]:
            continue

        vote_id: int = v["id"]
        ballots: list[dict] = cluster_db.list_ballots(vote_id) if cluster_db else []
        already_voted = any(b["voter_node_id"] == local_node_id for b in ballots)

        votes.append({
            "vote_id": vote_id,
            "vote_type": v["vote_type"],
            "target_node_id": target_nid,
            "target_display_name": member_names.get(target_nid, target_nid),
            "proposed_by": v["proposed_by"],
            "proposed_by_display_name": member_names.get(v["proposed_by"], v["proposed_by"]),
            "votes_yes": v["votes_yes"],
            "votes_no": v["votes_no"],
            "closes_at": v["closes_at"],
            "already_voted": already_voted,
            "can_vote": is_eligible_voter and not already_voted,
            "grace_extension_days": v.get("grace_extension_days"),
        })

    return {
        "setup_required": False,
        "node_name": node_name,
        "local_node_id": local_node_id,
        "members": members,
        "total_capacity_bytes": total_capacity,
        "total_used_bytes": total_used,
        "total_percent": total_percent,
        "invites": invites,
        "votes": votes,
    }


# ── Router factory ─────────────────────────────────────────────────────────────

def create_buddies_router() -> APIRouter:
    router = APIRouter()

    _templates.env.filters["ts_format"] = _fmt_timestamp

    @router.get("/buddies", response_class=HTMLResponse)
    async def buddies_page(request: Request) -> HTMLResponse:
        data = _build_buddies_data(request)
        return _templates.TemplateResponse(request, "buddies.html", {"data": data})

    @router.get("/api/buddies")
    async def api_buddies(request: Request) -> JSONResponse:
        return JSONResponse(_build_buddies_data(request))

    @router.post("/api/buddies/invite")
    async def api_generate_invite(request: Request) -> JSONResponse:
        guard = _setup_guard(request)
        if guard is not None:
            return guard
        db = getattr(request.app.state, "cluster_db", None)
        if db is None:
            return JSONResponse({"error": "Cluster database not available"}, status_code=503)
        local_node_id: str = getattr(request.app.state, "local_node_id", "")
        try:
            invite = generate_invite(db, created_by=local_node_id)
        except Exception as exc:
            logger.error("Failed to generate invite: %s", exc)
            return JSONResponse({"error": "Could not generate invite"}, status_code=500)
        return JSONResponse({
            "code": invite.code,
            "expires_at": invite.expires_at,
        })

    @router.post("/api/buddies/invite/{code}/revoke")
    async def api_revoke_invite(request: Request, code: str) -> JSONResponse:
        guard = _setup_guard(request)
        if guard is not None:
            return guard
        db = getattr(request.app.state, "cluster_db", None)
        if db is None:
            return JSONResponse({"error": "Cluster database not available"}, status_code=503)
        local_node_id: str = getattr(request.app.state, "local_node_id", "")
        try:
            revoke_invite(db, code, revoked_by=local_node_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"status": "revoked"})

    @router.post("/api/buddies/removal")
    async def api_propose_removal(
        request: Request, body: _ProposeRemovalRequest
    ) -> JSONResponse:
        guard = _setup_guard(request)
        if guard is not None:
            return guard
        db = getattr(request.app.state, "cluster_db", None)
        if db is None:
            return JSONResponse({"error": "Cluster database not available"}, status_code=503)
        local_node_id: str = getattr(request.app.state, "local_node_id", "")
        try:
            record = propose_removal(db, body.target_node_id, proposed_by=local_node_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({
            "vote_id": record.vote_id,
            "target_node_id": record.target_node_id,
            "closes_at": record.closes_at,
        })

    @router.post("/api/buddies/vote/{vote_id}/cast")
    async def api_cast_vote(
        request: Request, vote_id: int, body: _CastVoteRequest
    ) -> JSONResponse:
        guard = _setup_guard(request)
        if guard is not None:
            return guard
        db = getattr(request.app.state, "cluster_db", None)
        if db is None:
            return JSONResponse({"error": "Cluster database not available"}, status_code=503)
        local_node_id: str = getattr(request.app.state, "local_node_id", "")

        # Look up vote type before casting so we can auto-resolve if it passes
        vote_row = db.get_vote(vote_id)
        if vote_row is None:
            return JSONResponse({"error": "Vote not found"}, status_code=404)

        try:
            result = cast_vote(db, vote_id, voter_node_id=local_node_id, choice=body.choice)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        if result == VoteResult.PASSED:
            target_nid = vote_row["target_node_id"]
            if vote_row["vote_type"] == "removal":
                def _alert(node_id: str, message: str) -> None:
                    logger.info("grace-alert [node=%s]: %s", node_id, message)
                try:
                    start_grace_period(db, target_nid, send_alert=_alert)
                except ValueError as exc:
                    logger.warning(
                        "Grace period start failed for %s after vote passed: %s",
                        target_nid, exc,
                    )
            elif vote_row["vote_type"] == "grace_extension":
                try:
                    apply_grace_extension(db, vote_id)
                except ValueError as exc:
                    logger.warning(
                        "Grace extension apply failed for vote %d: %s", vote_id, exc
                    )

        return JSONResponse({"result": result.value})

    @router.post("/api/buddies/grace-extend")
    async def api_grace_extend(
        request: Request, body: _GraceExtendRequest
    ) -> JSONResponse:
        guard = _setup_guard(request)
        if guard is not None:
            return guard
        db = getattr(request.app.state, "cluster_db", None)
        if db is None:
            return JSONResponse({"error": "Cluster database not available"}, status_code=503)
        local_node_id: str = getattr(request.app.state, "local_node_id", "")
        try:
            record = extend_grace_period(
                db, body.target_node_id, body.days, proposed_by=local_node_id
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({
            "vote_id": record.vote_id,
            "target_node_id": record.target_node_id,
            "grace_extension_days": record.grace_extension_days,
            "closes_at": record.closes_at,
        })

    return router
