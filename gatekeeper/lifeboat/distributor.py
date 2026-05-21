"""
Lifeboat distributor: push encrypted bundles to all registered agents on a schedule.

Distribution sequence per run:
  1. Create bundle (bundle.py)
  2. Verify bundle decrypts locally before sending
  3. POST encrypted bundle to each agent's lifeboat_url
  4. Record result in cluster.db lifeboat_status table

Runs on a repeating asyncio schedule (interval from LifeboatConfig).
A run is skipped rather than queued if the previous one is still in flight.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from gatekeeper.db.cluster import ClusterDB
from gatekeeper.lifeboat.bundle import create_bundle
from gatekeeper.lifeboat.crypto import IntegrityError, decrypt
from gatekeeper.lifeboat.keystore import KeyNotFoundError, load_key

logger = logging.getLogger(__name__)

_PUSH_TIMEOUT = 30.0  # seconds — per-agent HTTP timeout


@dataclass
class DistributionResult:
    agent_count: int
    success_count: int
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.agent_count == 0 or self.success_count == self.agent_count:
            return "ok"
        if self.success_count == 0:
            return "failed"
        return "partial"


class LifeboatDistributor:
    """Distribute encrypted lifeboat bundles to registered agents.

    Args:
        data_dir:         Gatekeeper data directory (e.g. ~/.backupbuddy).
        config_path:      Path to gatekeeper.cfg.
        catalog_conn:     Open SQLite connection to catalog.db.
        cluster_db:       ClusterDB instance for agent list and status writes.
        agent_token:      Bearer token sent when pushing to agent endpoints.
        interval_seconds: Distribution interval (from LifeboatConfig).
    """

    def __init__(
        self,
        data_dir: Path,
        config_path: Path,
        catalog_conn: sqlite3.Connection,
        cluster_db: ClusterDB,
        agent_token: str,
        interval_seconds: int = 3600,
    ) -> None:
        self._data_dir = data_dir
        self._config_path = config_path
        self._catalog_conn = catalog_conn
        self._cluster_db = cluster_db
        self._agent_token = agent_token
        self._interval_seconds = interval_seconds
        self._lock = asyncio.Lock()

    async def distribute(self) -> DistributionResult:
        """Create bundle and push to all agents with a registered lifeboat_url.

        Per-agent failures are logged but do not abort distribution to the
        remaining agents.  Returns a DistributionResult.
        """
        agents = [a for a in self._cluster_db.list_agents() if a.get("lifeboat_url")]
        result = DistributionResult(agent_count=len(agents), success_count=0)

        if not agents:
            logger.info("Lifeboat distribution: no agents with lifeboat_url registered")
            self._cluster_db.insert_lifeboat_status(
                distributed_at=time.time(),
                agent_count=0,
                success_count=0,
                status="ok",
            )
            return result

        try:
            bundle = create_bundle(
                self._data_dir, self._config_path, self._catalog_conn
            )
        except Exception as exc:
            logger.error("Lifeboat bundle creation failed: %s", exc)
            result.errors.append(f"bundle creation: {exc}")
            self._cluster_db.insert_lifeboat_status(
                distributed_at=time.time(),
                agent_count=result.agent_count,
                success_count=0,
                status="failed",
            )
            return result

        try:
            self._verify_bundle(bundle)
        except (IntegrityError, KeyNotFoundError) as exc:
            logger.error("Lifeboat bundle failed local verification: %s", exc)
            result.errors.append(f"local verify: {exc}")
            self._cluster_db.insert_lifeboat_status(
                distributed_at=time.time(),
                agent_count=result.agent_count,
                success_count=0,
                status="failed",
            )
            return result

        async with httpx.AsyncClient(timeout=_PUSH_TIMEOUT) as client:
            for agent in agents:
                agent_name = agent["agent_name"]
                lifeboat_url = agent["lifeboat_url"]
                try:
                    await self._push(client, lifeboat_url, bundle)
                    result.success_count += 1
                    logger.info("Lifeboat bundle pushed to agent '%s'", agent_name)
                except Exception as exc:
                    logger.error(
                        "Lifeboat push to agent '%s' failed: %s", agent_name, exc
                    )
                    result.errors.append(f"{agent_name}: {exc}")

        self._cluster_db.insert_lifeboat_status(
            distributed_at=time.time(),
            agent_count=result.agent_count,
            success_count=result.success_count,
            status=result.status,
        )
        logger.info(
            "Lifeboat distribution complete: %d/%d agents updated (status=%s)",
            result.success_count,
            result.agent_count,
            result.status,
        )
        return result

    async def _push(
        self,
        client: httpx.AsyncClient,
        lifeboat_url: str,
        bundle: bytes,
    ) -> None:
        resp = await client.post(
            lifeboat_url,
            content=bundle,
            headers={
                "Authorization": f"Bearer {self._agent_token}",
                "Content-Type": "application/octet-stream",
            },
        )
        resp.raise_for_status()

    def _verify_bundle(self, bundle: bytes) -> None:
        """Verify the encrypted bundle can be decrypted with the local lifeboat key."""
        key = load_key()
        decrypt(bundle, key)

    async def run_scheduler(self) -> None:
        """Run distribute() on a repeating schedule.

        Skips a cycle if the previous run is still in progress.
        Logs all errors — never raises, to avoid crashing the gatekeeper.
        """
        logger.info(
            "Lifeboat scheduler started (interval=%ds)", self._interval_seconds
        )
        while True:
            if self._lock.locked():
                logger.warning(
                    "Lifeboat distribution still in progress — skipping this cycle"
                )
            else:
                async with self._lock:
                    try:
                        await self.distribute()
                    except Exception as exc:
                        logger.error("Lifeboat distribution unexpected error: %s", exc)
            await asyncio.sleep(self._interval_seconds)
