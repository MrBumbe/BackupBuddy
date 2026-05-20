"""
Unit tests for gatekeeper/tailscale.py.
"""

import logging
import socket
from unittest.mock import patch

import pytest

from gatekeeper.tailscale import TailscaleNotRunning, assert_tailscale_running, get_tailscale_ip


# ── Helpers ───────────────────────────────────────────────────────────────────

class _Addr:
    """Minimal stand-in for psutil._common.snicaddr."""

    def __init__(self, address: str, family: int = socket.AF_INET):
        self.address = address
        self.family = family


def _ipv4(address: str) -> _Addr:
    return _Addr(address, socket.AF_INET)


def _ipv6(address: str) -> _Addr:
    return _Addr(address, socket.AF_INET6)


_TAILSCALE_ONLY = {"tailscale0": [_ipv4("100.100.1.5")]}
_LAN_ONLY = {"eth0": [_ipv4("192.168.1.100")]}
_EMPTY = {}
_MIXED = {
    "eth0": [_ipv4("192.168.1.100")],
    "tailscale0": [_ipv4("100.100.1.5")],
}


# ── get_tailscale_ip ──────────────────────────────────────────────────────────

class TestGetTailscaleIp:
    def test_returns_tailscale_ip_when_running(self):
        with patch("psutil.net_if_addrs", return_value=_TAILSCALE_ONLY):
            assert get_tailscale_ip() == "100.100.1.5"

    def test_returns_none_when_not_running(self):
        with patch("psutil.net_if_addrs", return_value=_LAN_ONLY):
            assert get_tailscale_ip() is None

    def test_returns_none_on_no_interfaces(self):
        with patch("psutil.net_if_addrs", return_value=_EMPTY):
            assert get_tailscale_ip() is None

    def test_finds_tailscale_among_multiple_interfaces(self):
        with patch("psutil.net_if_addrs", return_value=_MIXED):
            assert get_tailscale_ip() == "100.100.1.5"

    def test_ignores_ipv6_addresses(self):
        ipv6_only = {"tailscale0": [_ipv6("fd7a:115c:a1e0::1")]}
        with patch("psutil.net_if_addrs", return_value=ipv6_only):
            assert get_tailscale_ip() is None

    def test_boundary_lowest_tailscale_address(self):
        with patch("psutil.net_if_addrs", return_value={"ts": [_ipv4("100.64.0.1")]}):
            assert get_tailscale_ip() == "100.64.0.1"

    def test_boundary_highest_tailscale_address(self):
        with patch("psutil.net_if_addrs", return_value={"ts": [_ipv4("100.127.255.254")]}):
            assert get_tailscale_ip() == "100.127.255.254"

    def test_address_just_below_range_excluded(self):
        # 100.63.255.255 is outside 100.64.0.0/10
        with patch("psutil.net_if_addrs", return_value={"eth0": [_ipv4("100.63.255.255")]}):
            assert get_tailscale_ip() is None

    def test_address_just_above_range_excluded(self):
        # 100.128.0.0 is outside 100.64.0.0/10
        with patch("psutil.net_if_addrs", return_value={"eth0": [_ipv4("100.128.0.1")]}):
            assert get_tailscale_ip() is None

    def test_loopback_excluded(self):
        with patch("psutil.net_if_addrs", return_value={"lo": [_ipv4("127.0.0.1")]}):
            assert get_tailscale_ip() is None


# ── assert_tailscale_running ──────────────────────────────────────────────────

class TestAssertTailscaleRunning:
    def test_returns_ip_when_running(self):
        with patch("psutil.net_if_addrs", return_value=_TAILSCALE_ONLY):
            assert assert_tailscale_running() == "100.100.1.5"

    def test_raises_when_not_running(self):
        with patch("psutil.net_if_addrs", return_value=_LAN_ONLY):
            with pytest.raises(TailscaleNotRunning):
                assert_tailscale_running()

    def test_logs_error_when_not_running(self, caplog):
        with patch("psutil.net_if_addrs", return_value=_LAN_ONLY):
            with caplog.at_level(logging.ERROR, logger="gatekeeper.tailscale"):
                with pytest.raises(TailscaleNotRunning):
                    assert_tailscale_running()
        assert any("not running" in r.message.lower() for r in caplog.records)

    def test_raises_on_no_interfaces(self):
        with patch("psutil.net_if_addrs", return_value=_EMPTY):
            with pytest.raises(TailscaleNotRunning):
                assert_tailscale_running()

    def test_error_message_is_user_friendly(self):
        with patch("psutil.net_if_addrs", return_value=_LAN_ONLY):
            with pytest.raises(TailscaleNotRunning, match="Tailscale"):
                assert_tailscale_running()
