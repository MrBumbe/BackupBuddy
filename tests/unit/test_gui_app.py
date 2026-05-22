"""
Unit tests for gatekeeper/gui/app.py.

Covers:
  - _is_tailscale_ip(): IP classification helper
  - TailscaleOnlyMiddleware: allow Tailscale, reject everything else
  - RequestLoggingMiddleware: log method + path + status, never query string
  - setup_gui(): full integration — routes, static files, middleware
"""
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from gatekeeper.gui.app import (
    RequestLoggingMiddleware,
    TailscaleOnlyMiddleware,
    _is_tailscale_ip,
    setup_gui,
)


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


# ── TailscaleOnlyMiddleware ───────────────────────────────────────────────────

def _app_with_tailscale_middleware() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    app.add_middleware(TailscaleOnlyMiddleware)
    return app


class TestTailscaleOnlyMiddleware:
    def test_tailscale_ip_allowed(self):
        client = TestClient(_app_with_tailscale_middleware(), client=("100.64.0.1", 9999))
        assert client.get("/ping").status_code == 200

    def test_tailscale_ip_upper_range_allowed(self):
        client = TestClient(_app_with_tailscale_middleware(), client=("100.127.0.1", 9999))
        assert client.get("/ping").status_code == 200

    def test_lan_ip_blocked(self):
        client = TestClient(_app_with_tailscale_middleware(), client=("192.168.1.5", 9999))
        assert client.get("/ping").status_code == 404

    def test_private_10_blocked(self):
        client = TestClient(_app_with_tailscale_middleware(), client=("10.0.0.5", 9999))
        assert client.get("/ping").status_code == 404

    def test_loopback_blocked(self):
        client = TestClient(_app_with_tailscale_middleware(), client=("127.0.0.1", 9999))
        assert client.get("/ping").status_code == 404

    def test_public_ip_blocked(self):
        client = TestClient(_app_with_tailscale_middleware(), client=("8.8.8.8", 9999))
        assert client.get("/ping").status_code == 404

    def test_blocked_response_body_does_not_leak_detail(self):
        client = TestClient(_app_with_tailscale_middleware(), client=("1.2.3.4", 9999))
        resp = client.get("/ping")
        assert resp.status_code == 404
        assert "tailscale" not in resp.text.lower()
        assert "middleware" not in resp.text.lower()


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

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        app.add_middleware(TailscaleOnlyMiddleware)
        app.add_middleware(RequestLoggingMiddleware)

        with caplog.at_level(logging.INFO, logger="gatekeeper.gui.app"):
            TestClient(app, client=("10.0.0.1", 9999)).get("/ping")

        assert any("404" in r.message for r in caplog.records)


# ── setup_gui integration ─────────────────────────────────────────────────────

def _make_gui_app(setup_required: bool = False) -> FastAPI:
    """Minimal FastAPI app with setup_gui applied, no full lifespan."""
    app = FastAPI()
    app.state.setup_required = setup_required
    setup_gui(app)
    return app


class TestSetupGui:
    def test_root_returns_html_for_tailscale_ip(self):
        client = TestClient(_make_gui_app(), client=("100.64.0.1", 9999))
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_root_blocked_for_non_tailscale_ip(self):
        client = TestClient(_make_gui_app(), client=("192.168.0.1", 9999))
        assert client.get("/").status_code == 404

    def test_static_css_served(self):
        client = TestClient(_make_gui_app(), client=("100.64.0.1", 9999))
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    def test_static_not_accessible_from_non_tailscale(self):
        client = TestClient(_make_gui_app(), client=("10.0.0.1", 9999))
        assert client.get("/static/style.css").status_code == 404

    def test_index_shows_setup_badge_when_not_configured(self):
        client = TestClient(_make_gui_app(setup_required=True), client=("100.64.0.1", 9999))
        resp = client.get("/")
        assert resp.status_code == 200
        assert "setup" in resp.text.lower()

    def test_index_shows_running_badge_when_operational(self):
        client = TestClient(_make_gui_app(setup_required=False), client=("100.64.0.1", 9999))
        resp = client.get("/")
        assert resp.status_code == 200
        assert "running" in resp.text.lower()
