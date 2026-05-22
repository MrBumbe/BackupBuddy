"""
GUI application setup — middleware, templates, static files, routes.

Registered components:
  TailscaleOnlyMiddleware  — rejects all requests from non-Tailscale IPs (404)
  RequestLoggingMiddleware — logs method + path + status (never query string)
  /static                  — CSS and assets, no external CDN
  /                        — dashboard (cluster status, storage, agents, jobs)
  /api/dashboard           — JSON snapshot for 30-second polling
  /restore                 — file/folder/emergency restore UI
  /api/restore/*           — restore job API

All components are wired into the main FastAPI app via setup_gui(app).
"""
from __future__ import annotations

import ipaddress
import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from gatekeeper.gui.routes.dashboard import create_dashboard_router
from gatekeeper.gui.routes.restore import create_restore_router
from gatekeeper.gui.routes.settings import create_settings_router
from gatekeeper.tailscale import _TAILSCALE_CGNAT

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _is_tailscale_ip(ip_str: str) -> bool:
    """Return True if ip_str falls in the Tailscale CGNAT block (100.64.0.0/10)."""
    try:
        return ipaddress.IPv4Address(ip_str) in _TAILSCALE_CGNAT
    except ValueError:
        return False


class TailscaleOnlyMiddleware(BaseHTTPMiddleware):
    """Reject requests from non-Tailscale source IPs with 404.

    Returns 404 rather than 403 to avoid information disclosure — callers
    outside the Tailscale network should not know the GUI exists.
    None client (ASGI scope without client tuple) is also rejected.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client = request.client
        if client is None or not _is_tailscale_ip(client.host):
            return Response("Not Found", status_code=404, media_type="text/plain")
        return await call_next(request)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log HTTP method, path, and response status for every request.

    Logs request.url.path only — never the full URL or query string, which
    could contain auth tokens or other sensitive values.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        logger.info("%s %s %d", request.method, request.url.path, response.status_code)
        return response


def setup_gui(app: Any) -> None:
    """Wire GUI components into a FastAPI app.

    Call this from _create_app() after _register_routes() so API routes
    are registered before the GUI middleware wraps the stack.

    Middleware ordering (last add_middleware = outermost = first on request):
      1. add_middleware(TailscaleOnlyMiddleware) — inner, rejects non-Tailscale IPs
      2. add_middleware(RequestLoggingMiddleware) — outer, logs all requests including rejections
    """
    app.include_router(create_dashboard_router())
    app.include_router(create_restore_router())
    app.include_router(create_settings_router())
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    async def _404_handler(request: Request, exc: Exception) -> HTMLResponse:
        return _templates.TemplateResponse(
            request, "error.html", {"code": 404, "message": "Page not found"},
            status_code=404,
        )

    async def _500_handler(request: Request, exc: Exception) -> HTMLResponse:
        return _templates.TemplateResponse(
            request, "error.html", {"code": 500, "message": "An unexpected error occurred"},
            status_code=500,
        )

    app.add_exception_handler(404, _404_handler)
    app.add_exception_handler(500, _500_handler)

    app.add_middleware(TailscaleOnlyMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
