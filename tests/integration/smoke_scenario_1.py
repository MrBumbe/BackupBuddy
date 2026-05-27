#!/usr/bin/env python3
"""
Smoke test Scenario 1: file backed up by agent, restored, SHA-256 verified.

Usage (called by smoke_test.sh after GK1 and GK2 are running):

    python smoke_scenario_1.py \
        --agent-api-url http://192.168.1.50:8581 \
        --agent-api-token test-token-123 \
        --agent-name smoke-agent \
        --gk-data-dir /tmp/bb-smoke/gk1 \
        --tahoe-url http://127.0.0.1:8582 \
        --lan-ip 192.168.1.50 \
        --restore-dir /tmp/bb-smoke/restore

Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx

# Allow running from any cwd — project root is two levels up.
sys.path.insert(0, str(Path(__file__).parents[2]))

from gatekeeper.db.catalog import CatalogDB
from gatekeeper.main import _derive_catalog_key
from gatekeeper.restore.restore import RestoreNotFoundError, restore_file
from gatekeeper.tahoe.client import TahoeClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0   # seconds between catalog polls
_POLL_TIMEOUT  = 120   # seconds to wait for file to appear in catalog


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _create_test_file(tmpdir: Path) -> tuple[Path, str]:
    """Create a small test file and return (path, sha256)."""
    content = b"BackupBuddy smoke test payload\n" + os.urandom(256)
    path = tmpdir / "smoke_test_input.bin"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    logger.info("Test file created: %s (%d bytes, sha256=%s…)", path, len(content), digest[:16])
    return path, digest


async def _register_agent(
    client: httpx.AsyncClient,
    base_url: str,
    agent_name: str,
) -> None:
    resp = await client.post(
        f"{base_url}/api/agents/register",
        json={"agent_name": agent_name},
    )
    resp.raise_for_status()
    logger.info("Agent '%s' registered: %s", agent_name, resp.json())


async def _send_file(
    client: httpx.AsyncClient,
    base_url: str,
    file_path: Path,
    agent_name: str,
) -> None:
    metadata = json.dumps({
        "original_path": str(file_path),
        "agent_name": agent_name,
    })
    data = file_path.read_bytes()
    resp = await client.post(
        f"{base_url}/api/agents/fragments",
        content=data,
        headers={"X-Fragment-Metadata": metadata},
    )
    resp.raise_for_status()
    logger.info("File queued by gatekeeper: %s", resp.json())


def _poll_catalog(
    catalog_db_path: str,
    catalog_key: bytes,
    agent: str,
    original_path: str,
    timeout: float,
) -> dict | None:
    """Poll catalog.db until the file appears or timeout expires."""
    deadline = time.monotonic() + timeout
    logger.info("Polling catalog.db for agent=%s path=%s", agent, original_path)
    while time.monotonic() < deadline:
        db = CatalogDB(catalog_db_path, catalog_key)
        try:
            record = db.get_file_by_path(agent, original_path)
        finally:
            db.close()
        if record is not None:
            logger.info("File found in catalog (sha256=%s…)", record["sha256"][:16])
            return record
        time.sleep(_POLL_INTERVAL)
    return None


async def run_scenario_1(
    agent_api_url: str,
    agent_api_token: str,
    agent_name: str,
    gk_data_dir: Path,
    tahoe_url: str,
    lan_ip: str,
    restore_dir: Path,
) -> bool:
    restore_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bb_smoke_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        test_file, original_sha256 = _create_test_file(tmpdir)

        # Agent API calls must originate from a LAN IP (not loopback, not Tailscale)
        transport = httpx.AsyncHTTPTransport(local_address=lan_ip)
        async with httpx.AsyncClient(
            transport=transport,
            headers={"Authorization": f"Bearer {agent_api_token}"},
            timeout=30.0,
        ) as http:
            await _register_agent(http, agent_api_url, agent_name)
            await _send_file(http, agent_api_url, test_file, agent_name)

        # Read root_dir.cap to derive catalog key
        root_dir_cap = (gk_data_dir / "root_dir.cap").read_text(encoding="utf-8").strip()
        catalog_key = _derive_catalog_key(root_dir_cap)
        catalog_db_path = str(gk_data_dir / "catalog.db")

        record = _poll_catalog(
            catalog_db_path, catalog_key,
            agent_name, str(test_file),
            timeout=_POLL_TIMEOUT,
        )
        if record is None:
            logger.error(
                "FAIL: file did not appear in catalog within %ds", _POLL_TIMEOUT
            )
            return False

        # Restore the file via TahoeClient
        restore_path = str(restore_dir / "restored_smoke_test.bin")
        async with TahoeClient(tahoe_url) as tahoe:
            catalog_db = CatalogDB(catalog_db_path, catalog_key)
            try:
                result = await restore_file(
                    original_path=str(test_file),
                    agent=agent_name,
                    dest_path=restore_path,
                    catalog=catalog_db,
                    tahoe=tahoe,
                )
            finally:
                catalog_db.close()

        if not result.success:
            logger.error("FAIL: restore_file returned failure: %s", result.error)
            return False

        # Verify SHA-256
        restored_sha256 = _sha256_file(Path(restore_path))
        if restored_sha256 != original_sha256:
            logger.error(
                "FAIL: SHA-256 mismatch — original=%s restored=%s",
                original_sha256, restored_sha256,
            )
            return False

        logger.info(
            "PASS: file restored and verified (sha256=%s…)", restored_sha256[:16]
        )
        return True


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--agent-api-url",   required=True, metavar="URL")
    p.add_argument("--agent-api-token", required=True, metavar="TOKEN")
    p.add_argument("--agent-name",      default="smoke-agent", metavar="NAME")
    p.add_argument("--gk-data-dir",     required=True, metavar="DIR")
    p.add_argument("--tahoe-url",       required=True, metavar="URL")
    p.add_argument("--lan-ip",          required=True, metavar="IP",
                   help="LAN IP to bind outgoing HTTP requests to (not loopback/Tailscale)")
    p.add_argument("--restore-dir",     required=True, metavar="DIR")
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()
    ok = await run_scenario_1(
        agent_api_url=args.agent_api_url,
        agent_api_token=args.agent_api_token,
        agent_name=args.agent_name,
        gk_data_dir=Path(args.gk_data_dir).resolve(),
        tahoe_url=args.tahoe_url,
        lan_ip=args.lan_ip,
        restore_dir=Path(args.restore_dir).resolve(),
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(_main())
