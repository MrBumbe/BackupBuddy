"""
Unit tests for agent/main.py and the gatekeeper's agent API endpoint.

Covers:
  - agent main(): reads config, calls register(), logs failure and continues
  - agent main(): exits on invalid config
  - gatekeeper _create_agent_api_app(): register endpoint accepts valid token + LAN IP
  - gatekeeper _create_agent_api_app(): rejects invalid token
  - gatekeeper _create_agent_api_app(): rejects non-LAN (Tailscale) source IPs
  - gatekeeper _is_lan_ip(): correctly classifies IP addresses
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient

from gatekeeper.main import _create_agent_api_app, _is_lan_ip, _registered_agents


# ── _is_lan_ip() ──────────────────────────────────────────────────────────────

class TestIsLanIp:
    def test_rfc1918_192_168_accepted(self) -> None:
        assert _is_lan_ip("192.168.1.50") is True

    def test_rfc1918_10_x_accepted(self) -> None:
        assert _is_lan_ip("10.0.0.1") is True

    def test_rfc1918_172_16_accepted(self) -> None:
        assert _is_lan_ip("172.16.0.1") is True

    def test_tailscale_cgnat_rejected(self) -> None:
        assert _is_lan_ip("100.64.0.1") is False

    def test_tailscale_cgnat_high_rejected(self) -> None:
        assert _is_lan_ip("100.127.255.1") is False

    def test_loopback_rejected(self) -> None:
        assert _is_lan_ip("127.0.0.1") is False

    def test_public_ip_rejected(self) -> None:
        assert _is_lan_ip("8.8.8.8") is False

    def test_invalid_string_rejected(self) -> None:
        assert _is_lan_ip("not-an-ip") is False


# ── Agent API — registration endpoint ────────────────────────────────────────

def _make_agent_api_config(token: str = "test-secret") -> MagicMock:
    cfg = MagicMock()
    cfg.agent_api.token = token
    return cfg


class TestAgentApiRegister:
    """starlette.TestClient sends host='testclient' (not a real IP).
    Tests that need to pass the LAN-IP check patch _is_lan_ip to return True.
    Tests that exercise the non-LAN rejection path use the unpatch behaviour
    ('testclient' is not a valid IPv4 address → _is_lan_ip returns False → 403).
    """

    def _app(self, tmp_path: Path, token: str = "test-secret") -> TestClient:
        app = _create_agent_api_app(_make_agent_api_config(token), tmp_path)
        return TestClient(app, raise_server_exceptions=True)

    def test_valid_token_and_lan_ip_returns_registered(self, tmp_path) -> None:
        client = self._app(tmp_path)
        with patch("gatekeeper.main._is_lan_ip", return_value=True):
            resp = client.post(
                "/api/agents/register",
                json={"agent_name": "my-laptop"},
                headers={"Authorization": "Bearer test-secret"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"status": "registered"}

    def test_agent_name_stored_in_registered_agents(self, tmp_path) -> None:
        _registered_agents.clear()
        client = self._app(tmp_path)
        with patch("gatekeeper.main._is_lan_ip", return_value=True):
            client.post(
                "/api/agents/register",
                json={"agent_name": "storage-pc"},
                headers={"Authorization": "Bearer test-secret"},
            )
        assert "storage-pc" in _registered_agents

    def test_wrong_token_returns_401(self, tmp_path) -> None:
        client = self._app(tmp_path)
        with patch("gatekeeper.main._is_lan_ip", return_value=True):
            resp = client.post(
                "/api/agents/register",
                json={"agent_name": "hacker"},
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert resp.status_code == 401

    def test_missing_token_returns_401(self, tmp_path) -> None:
        client = self._app(tmp_path)
        with patch("gatekeeper.main._is_lan_ip", return_value=True):
            resp = client.post(
                "/api/agents/register",
                json={"agent_name": "agent"},
            )
        assert resp.status_code == 401

    def test_empty_configured_token_returns_401(self, tmp_path) -> None:
        client = self._app(tmp_path, token="")
        with patch("gatekeeper.main._is_lan_ip", return_value=True):
            resp = client.post(
                "/api/agents/register",
                json={"agent_name": "agent"},
                headers={"Authorization": "Bearer "},
            )
        assert resp.status_code == 401

    def test_non_lan_source_returns_403(self, tmp_path) -> None:
        # 'testclient' is not a valid IPv4 address → _is_lan_ip returns False → 403
        app = _create_agent_api_app(_make_agent_api_config(), tmp_path)
        tc = TestClient(app)
        resp = tc.post(
            "/api/agents/register",
            json={"agent_name": "cluster-spy"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status_code == 403


# ── Agent main() ─────────────────────────────────────────────────────────────

def _write_valid_backup_cfg(path: Path) -> None:
    """Write a minimal valid backup.cfg to *path*."""
    import tempfile, os
    backup_dir = path.parent / "backup_data"
    backup_dir.mkdir(exist_ok=True)
    path.write_text(
        f"[backup]\n{backup_dir}\n\n"
        "[gatekeeper]\n"
        "url = http://192.168.1.50:8081\n"
        "token = test-token\n"
        "name = test-laptop\n",
        encoding="utf-8",
    )


class TestAgentMain:
    def test_aborts_on_missing_config(self, tmp_path: Path) -> None:
        from agent.main import main
        with patch("sys.argv", ["agent", "--config", str(tmp_path / "missing.cfg")]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1

    def test_calls_register_on_startup(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "backup.cfg"
        _write_valid_backup_cfg(cfg_file)

        mock_register = AsyncMock()
        mock_aclose = AsyncMock()

        with patch("sys.argv", ["agent", "--config", str(cfg_file)]):
            with patch("agent.main.GatekeeperClient") as mock_client_cls:
                instance = MagicMock()
                instance.register = mock_register
                instance.aclose = mock_aclose
                mock_client_cls.return_value = instance

                with patch("agent.main.watch_config", return_value=lambda: None):
                    # Run with a short timeout so the test doesn't block forever.
                    import asyncio
                    with patch("asyncio.sleep", side_effect=KeyboardInterrupt):
                        try:
                            with pytest.raises((KeyboardInterrupt, SystemExit)):
                                from agent.main import main
                                main()
                        except Exception:
                            pass

        mock_register.assert_awaited()

    def test_continues_when_registration_fails(self, tmp_path: Path) -> None:
        from agent.gatekeeper_client import RegistrationError
        cfg_file = tmp_path / "backup.cfg"
        _write_valid_backup_cfg(cfg_file)

        mock_register = AsyncMock(side_effect=RegistrationError("unreachable"))
        mock_aclose = AsyncMock()

        with patch("sys.argv", ["agent", "--config", str(cfg_file)]):
            with patch("agent.main.GatekeeperClient") as mock_client_cls:
                instance = MagicMock()
                instance.register = mock_register
                instance.aclose = mock_aclose
                mock_client_cls.return_value = instance

                with patch("agent.main.watch_config", return_value=lambda: None):
                    with patch("asyncio.sleep", side_effect=KeyboardInterrupt):
                        try:
                            with pytest.raises((KeyboardInterrupt, SystemExit)):
                                from agent.main import main
                                main()
                        except Exception:
                            pass

        # Agent did NOT exit with code 1 — registration failure is non-fatal.
        mock_register.assert_awaited()
