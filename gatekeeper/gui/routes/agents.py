"""Agents routes: list registered agents and per-agent backup detail.

Routes:
  GET /agents          — HTML agents page
  GET /api/agents      — JSON snapshot (30-second polling)

Per-agent detail level is governed by the agent's share_log flag (set at
registration).  When share_log is false the UI shows only:
  name, online status, last backup timestamp, file count.
When share_log is true the UI additionally shows the last 10 backup events
(timestamps, sizes, and fragmentation profile — never file names or paths).

Actual backup.cfg contents and raw log lines are not available in Phase 1:
the agent does not push them.  If share_log is true the UI notes that log
forwarding is enabled but no log data has been received yet.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

_AGENT_OFFLINE_SECONDS = 900   # 15 minutes — same threshold as dashboard

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


_templates.env.filters["ts_format"] = _fmt_ts


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


_templates.env.filters["fmt_bytes"] = _fmt_bytes


def _build_agents_data(request: Request) -> dict:
    """Assemble agents data from app.state.  Safe to call in setup mode."""
    state = request.app.state
    setup_required: bool = getattr(state, "setup_required", True)

    if setup_required:
        config = getattr(state, "config", None)
        node_name = config.node.display_name if config else "BackupBuddy"
        return {"setup_required": True, "node_name": node_name}

    cluster_db = getattr(state, "cluster_db", None)
    catalog_db = getattr(state, "catalog_db", None)
    now = time.time()

    raw_agents: list[dict] = cluster_db.list_agents() if cluster_db else []

    # Build catalog lookup: agent_name → {last_backup_at, file_count}
    catalog_lookup: dict[str, dict] = {}
    if catalog_db:
        for row in catalog_db.get_last_backup_per_agent():
            catalog_lookup[row["agent"]] = row

    agents: list[dict] = []
    for a in raw_agents:
        name: str = a["agent_name"]
        last_seen: float | None = a.get("last_seen")
        registered_at: float | None = a.get("registered_at")
        share_log: bool = bool(a.get("share_log", 0))
        is_online: bool = (
            last_seen is not None and (now - last_seen) < _AGENT_OFFLINE_SECONDS
        )

        cat = catalog_lookup.get(name, {})
        last_backup_at: float | None = cat.get("last_backup_at")
        file_count: int = cat.get("file_count", 0)

        recent_events: list[dict] = []
        if catalog_db:
            recent_events = catalog_db.get_recent_backups_for_agent(name, 10)

        agents.append({
            "agent_name": name,
            "ip": a.get("ip", ""),
            "is_online": is_online,
            "last_seen": last_seen,
            "registered_at": registered_at,
            "share_log": share_log,
            "last_backup_at": last_backup_at,
            "file_count": file_count,
            "recent_events": recent_events,
        })

    return {
        "setup_required": False,
        "agents": agents,
        "agent_count": len(agents),
    }


def create_agents_router() -> APIRouter:
    router = APIRouter()

    @router.get("/agents", response_class=HTMLResponse)
    async def agents_page(request: Request) -> HTMLResponse:
        data = _build_agents_data(request)
        return _templates.TemplateResponse(request, "agents.html", {"data": data})

    @router.get("/api/agents", response_class=JSONResponse)
    async def agents_api(request: Request) -> JSONResponse:
        data = _build_agents_data(request)
        return JSONResponse(data)

    return router
