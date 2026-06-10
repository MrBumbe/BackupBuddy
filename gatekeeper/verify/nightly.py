"""
Nightly verification job for the gatekeeper.

Four layers run in order once per day at verify.daily_check_time:

  Layer 1 — root_dir.cap: verify the Tahoe file tree is accessible.
  Layer 2 — catalog vs cluster: verify each file cap exists and has
             sufficient shares; flag under-replicated files for rebalance.
  Layer 3 — test restore: randomly restore N files and verify SHA-256.
  Layer 4 — lifeboat: check bundle age and attempt decrypt from an agent.

Failure in any layer is logged and alerted but does not prevent
the remaining layers from running.

Injectable dependencies (catalog, cluster, tahoe, send_alert) allow
all layers to be tested in isolation without touching real infrastructure.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import random
import shutil
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from gatekeeper.config import VerifyConfig
from gatekeeper.db.catalog import CatalogDB
from gatekeeper.db.cluster import ClusterDB
from gatekeeper.lifeboat.bundle import extract_bundle
from gatekeeper.lifeboat.crypto import IntegrityError
from gatekeeper.lifeboat.keystore import KeyNotFoundError, load_key
from gatekeeper.restore.restore import (
    RestoreIntegrityError,
    RestoreNotFoundError,
    restore_file,
)
from gatekeeper.tahoe.client import TahoeClient, TahoeError

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = 30.0  # seconds — lifeboat bundle GET from agent

# Maps alert level → VerifyConfig attribute that gates sending it
_LEVEL_TO_NOTIFY_FLAG: dict[str, str] = {
    "info":     "notify_on_success",
    "warning":  "notify_on_warning",
    "error":    "notify_on_failure",
    "critical": "notify_on_corrupt",
}


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class LayerResult:
    ok: bool
    warnings: int = 0
    errors: int = 0
    detail: str = ""


@dataclass
class VerifyResult:
    layer1: LayerResult | None = None
    layer2: LayerResult | None = None
    layer3: LayerResult | None = None
    layer4: LayerResult | None = None

    @property
    def overall_ok(self) -> bool:
        return all(
            r is None or r.ok
            for r in (self.layer1, self.layer2, self.layer3, self.layer4)
        )


# ── NightlyVerifier ───────────────────────────────────────────────────────────

class NightlyVerifier:
    """Runs four verification layers once per day at verify.daily_check_time.

    Args:
        verify_config: The [verify] section of GatekeeperConfig.
        catalog:       CatalogDB instance.
        cluster:       ClusterDB instance.
        tahoe:         TahoeClient instance.
        root_dir_cap:  Active root directory capability string (in-memory).
        agent_token:   Bearer token used when fetching lifeboat bundles from agents.
        send_alert:    Async callable (level, message, detail=None). If None,
                       failures are logged at ERROR level only.
    """

    def __init__(
        self,
        verify_config: VerifyConfig,
        catalog: CatalogDB,
        cluster: ClusterDB,
        tahoe: TahoeClient,
        root_dir_cap: str,
        agent_token: str | None = None,
        send_alert: Callable | None = None,
    ) -> None:
        self._verify = verify_config
        self._catalog = catalog
        self._cluster = cluster
        self._tahoe = tahoe
        self._root_dir_cap = root_dir_cap
        self._agent_token = agent_token
        self._send_alert = send_alert
        self._is_running: bool = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(self, triggered_by: str = "scheduler") -> VerifyResult:
        """Run all four verification layers and return a combined VerifyResult.

        Each layer is isolated — an exception in one layer does not prevent
        the others from running.
        """
        logger.info("Nightly verification started (triggered_by=%s)", triggered_by)
        result = VerifyResult()

        for layer_num, layer_fn in [
            (1, self._layer1_root_dir_cap),
            (2, self._layer2_catalog_vs_cluster),
            (3, self._layer3_test_restore),
            (4, self._layer4_lifeboat),
        ]:
            try:
                layer_result = await layer_fn()
            except Exception:
                logger.exception(
                    "Nightly verify layer %d raised unexpectedly", layer_num
                )
                layer_result = LayerResult(
                    ok=False, errors=1, detail="unexpected exception"
                )
            setattr(result, f"layer{layer_num}", layer_result)

        if result.overall_ok:
            logger.info("Nightly verification completed — all layers passed")
            if self._verify.notify_on_success:
                await self._alert("info", "Nightly verification passed — all checks OK")
        else:
            logger.warning(
                "Nightly verification completed — one or more layers failed"
            )

        # Persist result — store counts only, never raw exception strings or cap material.
        detail_json = json.dumps({
            f"layer{i + 1}": {"ok": lr.ok, "warnings": lr.warnings, "errors": lr.errors}
            for i, lr in enumerate(
                [result.layer1, result.layer2, result.layer3, result.layer4]
            )
            if lr is not None
        })
        try:
            self._cluster.insert_verify_run(
                run_at=time.time(),
                result="passed" if result.overall_ok else "failed",
                detail_json=detail_json,
                triggered_by=triggered_by,
            )
        except Exception:
            logger.exception("Failed to persist verify run result to cluster DB")

        return result

    async def run_scheduler(self) -> None:
        """Run verify() once per day at verify.daily_check_time.

        Skips a cycle if the previous run is still in progress.
        Never raises — all errors are logged.
        """
        logger.info(
            "Nightly verify scheduler started (daily at %s)",
            self._verify.daily_check_time,
        )
        while True:
            now = datetime.datetime.now()
            target = datetime.datetime.combine(
                now.date(), self._verify.daily_check_time
            )
            if target <= now:
                target += datetime.timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())

            if self._is_running:
                logger.warning(
                    "Nightly verify still in progress — skipping this cycle"
                )
                continue

            self._is_running = True
            try:
                await self.run(triggered_by="scheduler")
            except Exception:
                logger.exception("Nightly verify unexpected error")
            finally:
                self._is_running = False

    # ── Layer 1 — root_dir.cap ────────────────────────────────────────────────

    async def _layer1_root_dir_cap(self) -> LayerResult:
        logger.info("Layer 1: verifying root_dir.cap accessibility")
        try:
            await self._tahoe.ls(self._root_dir_cap)
            logger.info("Layer 1: root_dir.cap accessible")
            return LayerResult(ok=True)
        except TahoeError as exc:
            logger.error(
                "Layer 1: root_dir.cap not accessible — %s", type(exc).__name__
            )
            await self._alert(
                "critical",
                "Nightly verify: root directory is not accessible in the storage cluster",
            )
            return LayerResult(ok=False, errors=1, detail=str(exc))

    # ── Layer 2 — catalog vs cluster ─────────────────────────────────────────

    async def _layer2_catalog_vs_cluster(self) -> LayerResult:
        logger.info("Layer 2: verifying catalog entries against storage cluster")
        files = self._catalog.get_all_files()
        if not files:
            logger.info("Layer 2: catalog is empty — nothing to verify")
            return LayerResult(ok=True, detail="catalog empty")

        under_replicated = 0
        inaccessible = 0

        for record in files:
            cap = record.get("cap")
            if not cap:
                continue

            check = await self._tahoe.check_cap(cap)
            if check is None:
                inaccessible += 1
                logger.warning(
                    "Layer 2: could not verify cap for agent=%s",
                    record.get("agent", "?"),
                )
                continue

            shares_good = check.get("shares_good", 0)
            shares_needed = check.get("shares_needed", 0)
            if shares_needed > 0 and shares_good < shares_needed:
                under_replicated += 1
                logger.warning(
                    "Layer 2: under-replicated file for agent=%s "
                    "(shares_good=%d shares_needed=%d) — flagged for rebalance",
                    record.get("agent", "?"),
                    shares_good,
                    shares_needed,
                )

        if inaccessible == 0 and under_replicated == 0:
            logger.info("Layer 2: all %d catalog entries verified OK", len(files))
            return LayerResult(ok=True)

        detail = f"{inaccessible} inaccessible, {under_replicated} under-replicated"
        if inaccessible > 0:
            await self._alert(
                "error",
                f"Nightly verify: {inaccessible} file(s) could not be found in the storage cluster",
                detail,
            )
        if under_replicated > 0:
            await self._alert(
                "warning",
                f"Nightly verify: {under_replicated} file(s) are under-replicated and queued for rebalance",
                detail,
            )

        return LayerResult(
            ok=False,
            warnings=under_replicated,
            errors=inaccessible,
            detail=detail,
        )

    # ── Layer 3 — test restore ────────────────────────────────────────────────

    async def _layer3_test_restore(self) -> LayerResult:
        if not self._verify.test_restore_enabled:
            logger.info("Layer 3: test restore disabled — skipped")
            return LayerResult(ok=True, detail="disabled")

        logger.info("Layer 3: test restore starting")
        candidates = [
            r for r in self._catalog.get_all_files()
            if r.get("original_path") is not None and r.get("agent")
        ]
        if not candidates:
            logger.info("Layer 3: no restorable files in catalog — skipped")
            return LayerResult(ok=True, detail="no files")

        sample = random.sample(
            candidates,
            min(self._verify.test_restore_files, len(candidates)),
        )

        os.makedirs(self._verify.test_restore_path, exist_ok=True)
        restore_base = tempfile.mkdtemp(
            prefix="bb_nightly_verify_",
            dir=self._verify.test_restore_path,
        )
        if sys.platform != "win32":
            os.chmod(restore_base, stat.S_IRWXU)

        failures = 0
        try:
            for i, record in enumerate(sample):
                dest = os.path.join(restore_base, f"file_{i}.tmp")
                try:
                    await restore_file(
                        record["original_path"],
                        record["agent"],
                        dest,
                        catalog=self._catalog,
                        tahoe=self._tahoe,
                    )
                    logger.info(
                        "Layer 3: test restore OK for agent=%s", record["agent"]
                    )
                except (RestoreNotFoundError, RestoreIntegrityError) as exc:
                    failures += 1
                    logger.error(
                        "Layer 3: test restore failed for agent=%s — %s",
                        record["agent"],
                        type(exc).__name__,
                    )
                    await self._alert(
                        "error",
                        "Nightly verify: test restore failed for a file",
                        f"agent={record['agent']} error={type(exc).__name__}",
                    )
                except TahoeError as exc:
                    failures += 1
                    logger.error(
                        "Layer 3: download error for agent=%s — %s",
                        record["agent"],
                        type(exc).__name__,
                    )
                    await self._alert(
                        "error",
                        "Nightly verify: test restore failed due to a storage cluster error",
                        f"agent={record['agent']}",
                    )
        finally:
            shutil.rmtree(restore_base, ignore_errors=True)

        if failures:
            return LayerResult(
                ok=False,
                errors=failures,
                detail=f"{failures}/{len(sample)} failed",
            )
        logger.info("Layer 3: all %d test restores passed", len(sample))
        return LayerResult(ok=True)

    # ── Layer 4 — lifeboat ────────────────────────────────────────────────────

    async def _layer4_lifeboat(self) -> LayerResult:
        logger.info("Layer 4: lifeboat age and decrypt check")

        status = self._cluster.get_last_lifeboat_status()
        if status is None:
            logger.warning("Layer 4: no lifeboat distribution on record")
            await self._alert(
                "warning",
                "Nightly verify: no lifeboat bundle has been distributed yet",
            )
            return LayerResult(ok=False, warnings=1, detail="never distributed")

        age_seconds = time.time() - status["distributed_at"]
        max_age_seconds = self._verify.lifeboat_max_age_hours * 3600
        if age_seconds > max_age_seconds:
            hours = age_seconds / 3600
            logger.warning(
                "Layer 4: lifeboat is %.1f h old (max %d h)",
                hours,
                self._verify.lifeboat_max_age_hours,
            )
            await self._alert(
                "warning",
                (
                    f"Nightly verify: lifeboat bundle is overdue "
                    f"({hours:.1f} h old, max {self._verify.lifeboat_max_age_hours} h)"
                ),
            )
            return LayerResult(ok=False, warnings=1, detail=f"age={hours:.1f}h")

        agents = [a for a in self._cluster.list_agents() if a.get("lifeboat_url")]
        if not agents:
            logger.warning("Layer 4: no agents with lifeboat_url registered")
            await self._alert(
                "warning",
                "Nightly verify: no agent has a lifeboat URL registered",
            )
            return LayerResult(
                ok=False, warnings=1, detail="no agents with lifeboat_url"
            )

        agent = agents[0]

        try:
            key = load_key()
        except (KeyNotFoundError, Exception) as exc:
            logger.error(
                "Layer 4: lifeboat key unavailable — %s", type(exc).__name__
            )
            await self._alert(
                "critical",
                "Nightly verify: lifeboat key is unavailable — cannot verify bundle",
            )
            return LayerResult(ok=False, errors=1, detail=str(exc))

        try:
            bundle_bytes = await self._fetch_lifeboat(agent["lifeboat_url"])
        except Exception as exc:
            logger.error(
                "Layer 4: failed to fetch lifeboat from agent '%s' — %s",
                agent["agent_name"],
                type(exc).__name__,
            )
            await self._alert(
                "critical",
                "Nightly verify: could not retrieve lifeboat bundle from agent",
            )
            return LayerResult(ok=False, errors=1, detail=str(exc))

        try:
            bundle_data = extract_bundle(bundle_bytes, key=key)
        except IntegrityError:
            logger.error(
                "Layer 4: lifeboat bundle decrypt failed for agent '%s'",
                agent["agent_name"],
            )
            await self._alert(
                "critical",
                "Nightly verify: lifeboat bundle decryption failed — data may be corrupted",
            )
            return LayerResult(ok=False, errors=1, detail="decrypt failed")

        bundle_cap = bundle_data.get("root_dir_cap", "")
        if bundle_cap != self._root_dir_cap:
            logger.error(
                "Layer 4: lifeboat root_dir_cap mismatch for agent '%s'",
                agent["agent_name"],
            )
            await self._alert(
                "critical",
                "Nightly verify: lifeboat bundle contains a stale root directory reference",
            )
            return LayerResult(ok=False, errors=1, detail="root_dir_cap mismatch")

        logger.info(
            "Layer 4: lifeboat OK (age=%.1f h, decrypt verified)",
            age_seconds / 3600,
        )
        return LayerResult(ok=True)

    async def _fetch_lifeboat(self, lifeboat_url: str) -> bytes:
        headers = {}
        if self._agent_token:
            headers["Authorization"] = f"Bearer {self._agent_token}"
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            resp = await client.get(lifeboat_url, headers=headers)
            resp.raise_for_status()
            return resp.content

    # ── Alert helper ──────────────────────────────────────────────────────────

    async def _alert(
        self, level: str, message: str, detail: str | None = None
    ) -> None:
        flag_name = _LEVEL_TO_NOTIFY_FLAG.get(level)
        if flag_name and not getattr(self._verify, flag_name, True):
            logger.debug(
                "verify: suppressed %s alert (notify disabled in config)", level
            )
            return
        if self._send_alert is None:
            logger.error("Verification alert: %s", message)
            return
        try:
            await self._send_alert(level, message, detail)
        except Exception:
            logger.exception("verify: send_alert raised an exception")
