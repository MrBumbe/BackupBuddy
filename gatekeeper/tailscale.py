"""
Tailscale interface detection and startup guard.

Tailscale uses the IANA CGNAT range 100.64.0.0/10 exclusively.
The gatekeeper must not start if no Tailscale interface is found.
"""

import ipaddress
import logging
import socket

import psutil

logger = logging.getLogger(__name__)

_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class TailscaleNotRunning(Exception):
    pass


def get_tailscale_ip() -> str | None:
    """Return the local Tailscale IPv4 address, or None if Tailscale is not active.

    Detects Tailscale by scanning all network interfaces for an IPv4 address
    in the 100.64.0.0/10 CGNAT block, which Tailscale exclusively occupies.
    Does not call the Tailscale API or CLI.
    """
    for _iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            try:
                ip = ipaddress.ip_address(addr.address)
            except ValueError:
                continue
            if ip in _TAILSCALE_CGNAT:
                return addr.address
    return None


def assert_tailscale_running() -> str:
    """Return the Tailscale IP or raise TailscaleNotRunning.

    Must be called at gatekeeper startup before any other component starts.
    If Tailscale is not running the gatekeeper must not start.
    """
    ip = get_tailscale_ip()
    if ip is None:
        logger.error("Tailscale is not running — gatekeeper cannot start")
        raise TailscaleNotRunning(
            "Tailscale is not running. Start Tailscale and try again."
        )
    return ip
