"""
Unit tests for gatekeeper/gui/app.py.

Covers:
  - _is_tailscale_ip(): IP classification helper
  - AccessControlMiddleware: route-aware access control (ADR-023)
  - RequestLoggingMiddleware: log method + path + status, never query string
  - setup_gui(): full integration — routes, static files, middleware
"""
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from gatekeeper.gui.app import (
    AccessControlMiddleware,
    RequestLoggingMiddleware,
    _is_tailscale_ip,
    setup_gui,
)

_TAILSCALE_IP = "100.64.0.1"
_LAN_IP = "192.168.1.5"
_PUBLIC_IP = "8.8.8.8"


# ── _is_tailscale_ip ──────────────────────────────────────────────────────────

class TestIsTailscaleIp:
    def test_lower_bound_accepted(self):
        assert _is_tailscale_ip("100.64.0.1") is True

    def test_mid_range_accepted(self):
        assert _is_tailscale_ip("100.100.50.200") is True

    def test_upper_bound_accepted(self):
        assert _is_tailscale_ip("100.127.255.254") is True

    def test_lan_192_rejected(self):
        assert _is_tailscale_ip("192.168.1.1") is False

    def test_lan_10_rejected(self):
        assert _is_tailscale_ip("10.0.0.1") is False

    def test_loopback_rejected(self):
        assert _is_tailscale_ip("127.0.0.1") is False

    def test_public_ip_rejected(self):
        assert _is_tailscale_ip("8.8.8.8") is False

    def test_empty_string_rejected(self):
        assert _is_tailscale_ip("") is False

    def test_garbage_rejected(self):
        assert _is_tailscale_ip("not-an-ip") is False

    def test_just_outside_range_rejected(self):
        # 100.128.0.0 is outside 100.64.0.0/10
        assert _is_tailscale_ip("100.128.0.0") is False


# ── AccessControlMiddleware ───────────────────────────────────────────────────

def _app_with_access_control(
    *,
    gui_on_lan: bool = True,
    gui_on_tailscale: bool = False,
    setup_required: bool = False,
) -> FastAPI:
    app = FastAPI()
    app.state.setup_required = setup_required

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    @app.get("/api/status")
    async def api_status():
        return {"status": "ok"}

    @app.get("/api/cluster/sync")
    async def cluster_sync():
        return {"synced": True}

    @app.get("/api/verify/status")
    async def verify_status():
        return {"verify": "ok"}

    app.add_middleware(
        AccessControlMiddleware,
        gui_on_lan=gui_on_lan,
        gui_on_tailscale=gui_on_tailscale,
    )
    return app


class TestAccessControlMiddleware:
    # ── service routes — always require Tailscale ─────────────────────────────

    def test_api_status_allowed_from_tailscale(self):
        client = TestClient(_app_with_access_control(), client=(_TAILSCALE_IP, 9999))
        assert client.get("/api/status").status_code == 200

    def test_api_status_blocked_from_lan(self):
        client = TestClient(_app_with_access_control(), client=(_LAN_IP, 9999))
        assert client.get("/api/status").status_code == 404

    def test_cluster_route_allowed_from_tailscale(self):
        client = TestClient(_app_with_access_control(), client=(_TAILSCALE_IP, 9999))
        assert client.get("/api/cluster/sync").status_code == 200

    def test_cluster_route_blocked_from_lan(self):
        client = TestClient(_app_with_access_control(), client=(_LAN_IP, 9999))
        assert client.get("/api/cluster/sync").status_code == 404

    def test_verify_route_allowed_from_tailscale(self):
        client = TestClient(_app_with_access_control(), client=(_TAILSCALE_IP, 9999))
        assert client.get("/api/verify/status").status_code == 200

    def test_verify_route_blocked_from_lan(self):
        client = TestClient(_app_with_access_control(), client=(_LAN_IP, 9999))
        assert client.get("/api/verify/status").status_code == 404

    # ── GUI routes — default (gui_on_lan=True, gui_on_tailscale=False) ────────

    def test_gui_route_allowed_from_lan_by_default(self):
        client = TestClient(_app_with_access_control(), client=(_LAN_IP, 9999))
        assert client.get("/ping").status_code == 200

    def test_gui_route_blocked_from_tailscale_by_default(self):
        client = TestClient(_app_with_access_control(), client=(_TAILSCALE_IP, 9999))
        assert client.get("/ping").status_code == 404

    # ── GUI routes — both enabled ─────────────────────────────────────────────

    def test_gui_allowed_from_lan_when_both_true(self):
        app = _app_with_access_control(gui_on_lan=True, gui_on_tailscale=True)
        client = TestClient(app, client=(_LAN_IP, 9999))
        assert client.get("/ping").status_code == 200

    def test_gui_allowed_from_tailscale_when_both_true(self):
        app = _app_with_access_control(gui_on_lan=True, gui_on_tailscale=True)
        client = TestClient(app, client=(_TAILSCALE_IP, 9999))
        assert client.get("/ping").status_code == 200

    # ── GUI routes — Tailscale only ───────────────────────────────────────────

    def test_gui_allowed_from_tailscale_when_tailscale_only(self):
        app = _app_with_access_control(gui_on_lan=False, gui_on_tailscale=True)
        client = TestClient(app, client=(_TAILSCALE_IP, 9999))
        assert client.get("/ping").status_code == 200

    def test_gui_blocked_from_lan_when_tailscale_only(self):
        app = _app_with_access_control(gui_on_lan=False, gui_on_tailscale=True)
        client = TestClient(app, client=(_LAN_IP, 9999))
        assert client.get("/ping").status_code == 404

    # ── both disabled ─────────────────────────────────────────────────────────

    def test_gui_blocked_from_lan_when_both_false(self):
        app = _app_with_access_control(gui_on_lan=False, gui_on_tailscale=False)
        client = TestClient(app, client=(_LAN_IP, 9999))
        assert client.get("/ping").status_code == 404

    def test_gui_blocked_from_tailscale_when_both_false(self):
        app = _app_with_access_control(gui_on_lan=False, gui_on_tailscale=False)
        client = TestClient(app, client=(_TAILSCALE_IP, 9999))
        assert client.get("/ping").status_code == 404

    def test_service_route_still_works_when_gui_disabled(self):
        # Cluster API must function even when GUI is disabled
        app = _app_with_access_control(gui_on_lan=False, gui_on_tailscale=False)
        client = TestClient(app, client=(_TAILSCALE_IP, 9999))
        assert client.get("/api/cluster/sync").status_code == 200

    # ── setup mode bypass ─────────────────────────────────────────────────────

    def test_setup_mode_bypasses_all_checks(self):
        app = _app_with_access_control(
            gui_on_lan=False, gui_on_tailscale=False, setup_required=True
        )
        client = TestClient(app, client=(_LAN_IP, 9999))
        assert client.get("/ping").status_code == 200

    def test_setup_mode_allows_tailscale_too(self):
        app = _app_with_access_control(setup_required=True)
        client = TestClient(app, client=(_TAILSCALE_IP, 9999))
        assert client.get("/ping").status_code == 200

    # ── response body must not leak information ───────────────────────────────

    def test_blocked_response_body_does_not_leak_detail(self):
        app = _app_with_access_control(gui_on_lan=False, gui_on_tailscale=False)
        resp = TestClient(app, client=(_LAN_IP, 9999)).get("/ping")
        assert resp.status_code == 404
        assert "middleware" not in resp.text.lower()
        assert "tailscale" not in resp.text.lower()


# ── RequestLoggingMiddleware ──────────────────────────────────────────────────

def _app_with_logging_middleware() -> FastAPI:
    app = FastAPI()

    @app.get("/search")
    async def search(q: str = ""):
        return {"q": q}

    app.add_middleware(RequestLoggingMiddleware)
    return app


class TestRequestLoggingMiddleware:
    def test_logs_method_and_path(self, caplog):
        app = _app_with_logging_middleware()
        with caplog.at_level(logging.INFO, logger="gatekeeper.gui.app"):
            TestClient(app).get("/search")
        assert any(
            "GET" in r.message and "/search" in r.message
            for r in caplog.records
        )

    def test_logs_status_code(self, caplog):
        app = _app_with_logging_middleware()
        with caplog.at_level(logging.INFO, logger="gatekeeper.gui.app"):
            TestClient(app).get("/search")
        assert any("200" in r.message for r in caplog.records)

    def test_does_not_log_query_string(self, caplog):
        app = _app_with_logging_middleware()
        with caplog.at_level(logging.INFO, logger="gatekeeper.gui.app"):
            TestClient(app).get("/search?q=secret_token")
        our_records = [r for r in caplog.records if r.name == "gatekeeper.gui.app"]
        for record in our_records:
            assert "secret_token" not in record.message

    def test_logs_rejected_requests(self, caplog):
        """Logging middleware is outermost — logs even requests rejected by inner middleware."""
        app = FastAPI()
        app.state.setup_required = False

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        app.add_middleware(
            AccessControlMiddleware, gui_on_lan=False, gui_on_tailscale=False
        )
        app.add_middleware(RequestLoggingMiddleware)

        with caplog.at_level(logging.INFO, logger="gatekeeper.gui.app"):
            TestClient(app, client=(_LAN_IP, 9999)).get("/ping")

        assert any("404" in r.message for r in caplog.records)


# ── setup_gui integration ─────────────────────────────────────────────────────

def _make_gui_app(
    setup_required: bool = False,
    gui_on_lan: bool = True,
    gui_on_tailscale: bool = False,
) -> FastAPI:
    """Minimal FastAPI app with setup_gui applied, no full lifespan."""
    app = FastAPI()
    app.state.setup_required = setup_required
    setup_gui(app, gui_on_lan=gui_on_lan, gui_on_tailscale=gui_on_tailscale)
    return app


class TestSetupGui:
    def test_root_returns_html_for_lan_ip_default(self):
        """Default config: GUI accessible from LAN."""
        client = TestClient(_make_gui_app(), client=(_LAN_IP, 9999))
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_root_blocked_for_tailscale_ip_by_default(self):
        """Default config: GUI not accessible from Tailscale."""
        client = TestClient(_make_gui_app(), client=(_TAILSCALE_IP, 9999))
        assert client.get("/").status_code == 404

    def test_root_accessible_from_tailscale_when_enabled(self):
        client = TestClient(
            _make_gui_app(gui_on_lan=True, gui_on_tailscale=True),
            client=(_TAILSCALE_IP, 9999),
        )
        assert client.get("/").status_code == 200

    def test_static_css_served_from_lan(self):
        client = TestClient(_make_gui_app(), client=(_LAN_IP, 9999))
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    def test_static_not_accessible_from_tailscale_by_default(self):
        client = TestClient(_make_gui_app(), client=(_TAILSCALE_IP, 9999))
        assert client.get("/static/style.css").status_code == 404

    def test_index_shows_setup_badge_when_not_configured(self):
        client = TestClient(_make_gui_app(setup_required=True), client=(_LAN_IP, 9999))
        resp = client.get("/")
        assert resp.status_code == 200
        assert "setup" in resp.text.lower()

    def test_index_shows_running_badge_when_operational(self):
        client = TestClient(_make_gui_app(setup_required=False), client=(_LAN_IP, 9999))
        resp = client.get("/")
        assert resp.status_code == 200
        assert "running" in resp.text.lower()
