"""
GUI application setup — middleware, templates, static files, routes.

Registered components:
  TailscaleOnlyMiddleware  — rejects all requests from non-Tailscale IPs (404)
  RequestLoggingMiddleware — logs method + path + status (never query string)
  /static                  — CSS and assets, no external CDN
  /                        — dashboard placeholder (full content added in task 1.14.2)

All components are wired into the main FastAPI app via setup_gui(app).
"""
from __future__ import annotations

import ipaddress
import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from gatekeeper.tailscale import _TAILSCALE_CGNAT

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _is_tailscale_ip(ip_str: str) -> bool:
    """Return True if ip_str is an IPv4 address in the Tailscale CGNAT block (100.64.0.0/10)."""
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


def create_gui_router() -> APIRouter:
    """Return the APIRouter for GUI routes.

    Uses APIRouter (not a sub-app mount) so route handlers share app.state
    with the rest of the gatekeeper (catalog_db, cluster_db, etc.).
    """
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Any:
        setup_required = getattr(request.app.state, "setup_required", True)
        return _templates.TemplateResponse(
            request, "index.html", {"setup_required": setup_required}
        )

    return router


def setup_gui(app: Any) -> None:
    """Wire GUI components into a FastAPI app.

    Call this from _create_app() after _register_routes() so API routes
    are registered before the GUI middleware wraps the stack.

    Middleware ordering (last add_middleware = outermost = first on request):
      1. add_middleware(TailscaleOnlyMiddleware) — inner, rejects non-Tailscale IPs
      2. add_middleware(RequestLoggingMiddleware) — outer, logs all requests including rejections
    """
    app.include_router(create_gui_router())
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
