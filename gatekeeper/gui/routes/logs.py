"""Log viewer routes: GET /logs (HTML) and GET /api/logs (JSON)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

_LEVEL_RANK: dict[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

# Matches lines in the format:
#   2026-06-08T12:34:56 INFO     gatekeeper.verify.nightly — message
_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s+(\w+)\s+([\w.]+)\s+—\s+(.*)$"
)

_DEFAULT_N = 200
_MAX_N = 1000

COMPONENTS = ["cluster", "verify", "watcher", "restore", "lifeboat", "rebalance"]

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _parse_log_lines(
    log_file: str,
    n: int,
    level: str,
    component: str | None,
) -> list[dict]:
    """Read the log file and return the last n matching lines, newest first.

    Returns an empty list if the file does not exist or cannot be read.
    Non-matching lines (e.g. tracebacks) are silently skipped.
    """
    path = Path(log_file)
    if not path.exists():
        return []

    min_rank = _LEVEL_RANK.get(level.upper(), _LEVEL_RANK["INFO"])

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    matched: list[dict] = []
    for raw in text.splitlines():
        m = _LOG_RE.match(raw)
        if not m:
            continue
        ts, lvl, name, msg = m.group(1), m.group(2), m.group(3), m.group(4)
        if _LEVEL_RANK.get(lvl.upper(), 0) < min_rank:
            continue
        if component and not (
            name == f"gatekeeper.{component}"
            or name.startswith(f"gatekeeper.{component}.")
        ):
            continue
        matched.append({"ts": ts, "level": lvl, "name": name, "msg": msg})

    return list(reversed(matched[-n:]))


def create_logs_router() -> APIRouter:
    """Return the APIRouter for the log viewer.

    GET /api/logs  — JSON lines for JS filter requests
    GET /logs      — HTML page (SSR initial render; JS updates on filter change)
    """
    router = APIRouter()

    @router.get("/api/logs")
    async def api_logs(
        request: Request,
        level: str = "INFO",
        component: str = "",
        n: int = _DEFAULT_N,
    ) -> JSONResponse:
        if getattr(request.app.state, "setup_required", True):
            return JSONResponse({"lines": []})
        log_file: str = getattr(request.app.state, "log_file", "")
        if not log_file:
            return JSONResponse({"lines": []})
        n = max(1, min(n, _MAX_N))
        lines = _parse_log_lines(log_file, n, level, component.strip() or None)
        return JSONResponse({"lines": lines})

    @router.get("/logs", response_class=HTMLResponse)
    async def logs_page(
        request: Request,
        level: str = "INFO",
        component: str = "",
        n: int = _DEFAULT_N,
    ) -> Any:
        if getattr(request.app.state, "setup_required", True):
            return _templates.TemplateResponse(
                request,
                "error.html",
                {"code": 503, "message": "Gatekeeper not ready"},
                status_code=503,
            )
        log_file: str = getattr(request.app.state, "log_file", "")
        n = max(1, min(n, _MAX_N))
        lines = _parse_log_lines(log_file, n, level.upper(), component.strip() or None)
        return _templates.TemplateResponse(
            request,
            "logs.html",
            {
                "lines": lines,
                "current_level": level.upper(),
                "current_component": component.strip(),
                "current_n": n,
                "components": COMPONENTS,
            },
        )

    return router
