"""Dashboard routes: GET / (HTML) and GET /api/dashboard (JSON)."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

_AGENT_OFFLINE_SECONDS = 900   # 15 minutes — node considered offline
_RATIO_WARNING = 1.2           # ADR-013: warn below this contribution:usage ratio
_RATIO_ERROR = 1.0             # ADR-013: error below this ratio

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _fmt_timestamp(ts: float | None) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


_templates.env.filters["ts_format"] = _fmt_timestamp


def _build_dashboard_data(request: Request) -> dict:
    """Assemble dashboard data from app.state.  Safe to call in setup mode."""
    state = request.app.state
    setup_required: bool = getattr(state, "setup_required", True)
    config = getattr(state, "config", None)
    node_name: str = config.node.display_name if config else "BackupBuddy"

    if setup_required:
        return {"setup_required": True, "node_name": node_name}

    is_introducer: bool = bool(config and config.tahoe.run_introducer)
    local_node_id: str | None = config.node.name if config else None

    catalog_db = getattr(state, "catalog_db", None)
    cluster_db = getattr(state, "cluster_db", None)
    pool = getattr(state, "pool", None)
    now = time.time()

    # Cluster members
    raw_members: list[dict] = cluster_db.list_members() if cluster_db else []
    online_count = 0
    members: list[dict] = []
    for m in raw_members:
        contrib: int = m.get("contribution_bytes") or 0
        usage: int = m.get("usage_bytes") or 0
        if usage > 0:
            ratio: float | None = contrib / usage
            warning = ratio < _RATIO_WARNING
            error = ratio < _RATIO_ERROR
        else:
            ratio = None
            warning = False
            error = False
        is_online = m.get("status") == "active"
        if is_online:
            online_count += 1
        members.append({
            "display_name": m["display_name"],
            "status": m.get("status", "unknown"),
            "contribution_bytes": contrib,
            "usage_bytes": usage,
            "ratio": ratio,
            "profile": m.get("profile", ""),
            "warning": warning,
            "error": error,
            "is_introducer": is_introducer and m.get("node_id") == local_node_id,
        })

    # Storage pool
    pool_paths: list[dict] = pool.get_usage() if pool else []
    total_quota = sum(p["quota_bytes"] for p in pool_paths)
    total_used = sum(p["used_bytes"] for p in pool_paths)
    path_data = [
        {
            **p,
            "percent": round(100.0 * p["used_bytes"] / p["quota_bytes"], 1)
            if p["quota_bytes"] > 0 else 0.0,
        }
        for p in pool_paths
    ]
    total_percent = round(100.0 * total_used / total_quota, 1) if total_quota > 0 else 0.0

    # Agents
    raw_agents: list[dict] = cluster_db.list_agents() if cluster_db else []
    last_backup_map: dict[str, dict] = {}
    if catalog_db:
        for row in catalog_db.get_last_backup_per_agent():
            last_backup_map[row["agent"]] = row
    agents: list[dict] = []
    for a in raw_agents:
        last_seen = a.get("last_seen")
        online = last_seen is not None and (now - last_seen) < _AGENT_OFFLINE_SECONDS
        backup_info = last_backup_map.get(a["agent_name"], {})
        agents.append({
            "agent_name": a["agent_name"],
            "last_seen": last_seen,
            "online": online,
            "last_backup_at": backup_info.get("last_backup_at"),
            "file_count": backup_info.get("file_count", 0),
        })

    # Background jobs
    rebalance = cluster_db.get_rebalance_state() if cluster_db else None
    lifeboat = cluster_db.get_last_lifeboat_status() if cluster_db else None

    return {
        "setup_required": False,
        "node_name": node_name,
        "is_introducer": is_introducer,
        "cluster": {
            "total_members": len(raw_members),
            "online_count": online_count,
            "members": members,
        },
        "storage_pool": {
            "paths": path_data,
            "total_quota_bytes": total_quota,
            "total_used_bytes": total_used,
            "total_percent": total_percent,
        },
        "agents": agents,
        "jobs": {
            "rebalance": {
                "in_progress": bool(rebalance.get("in_progress")) if rebalance else False,
                "last_run_at": rebalance.get("last_run_at") if rebalance else None,
                "baseline_count": rebalance.get("baseline_count") if rebalance else None,
            },
            "lifeboat": {
                "distributed_at": lifeboat.get("distributed_at") if lifeboat else None,
                "agent_count": lifeboat.get("agent_count") if lifeboat else None,
                "success_count": lifeboat.get("success_count") if lifeboat else None,
                "status": lifeboat.get("status") if lifeboat else None,
            },
        },
    }


def create_dashboard_router() -> APIRouter:
    """Return the APIRouter for the dashboard.

    GET /              — HTML dashboard (Jinja2 SSR, then JS polling every 30 s)
    GET /api/dashboard — JSON snapshot consumed by the polling loop
    """
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Any:
        data = _build_dashboard_data(request)
        return _templates.TemplateResponse(request, "dashboard.html", {"data": data})

    @router.get("/api/dashboard")
    async def dashboard_api(request: Request) -> JSONResponse:
        data = _build_dashboard_data(request)
        return JSONResponse(data)

    return router
