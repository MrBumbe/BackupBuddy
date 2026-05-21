"""Rebalance scheduler: nightly check-and-run for re-fragmentation.

Implements the ADR-011 policy:
  - Non-critical files only run when cluster is stable for stability_days
    AND the size change exceeds the hysteresis_nodes threshold.
  - Critical files (unrestorable in current cluster) always run regardless.
  - At most daily_rebalance_pct% of the catalog is processed per night.
  - The run loop sleeps until the configured rebalance_time each day.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import datetime, time, timezone
from typing import Callable

from gatekeeper.config import FragmentationConfig, RebalanceConfig
from gatekeeper.db.catalog import CatalogDB
from gatekeeper.db.cluster import ClusterDB
from gatekeeper.fragmenter.adaptive import compute_adaptive_kn
from gatekeeper.fragmenter.profiles import get_profile
from gatekeeper.rebalance.worker import RebalanceResult, prioritise_files, run_rebalance
from gatekeeper.tahoe.client import TahoeClient

logger = logging.getLogger(__name__)


def _seconds_until(target: time) -> float:
    """Return seconds until the next occurrence of *target* local time."""
    now = datetime.now()
    candidate = now.replace(
        hour=target.hour,
        minute=target.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= now:
        candidate = candidate.replace(day=candidate.day + 1)
    return (candidate - now).total_seconds()


def _resolve_target_kn(
    node_count: int,
    frag_config: FragmentationConfig,
) -> tuple[str, int, int]:
    """Return (profile_name, k, n) for the current cluster size."""
    if frag_config.profile == "adaptive":
        k, n = compute_adaptive_kn(node_count, frag_config.adaptive)
        return ("adaptive", k, n)
    profile = get_profile(frag_config.profile)
    return (frag_config.profile, profile.k, profile.n)


async def check_and_run(
    cluster_db: ClusterDB,
    catalog_db: CatalogDB,
    tahoe_client: TahoeClient,
    root_dir_ref: str,
    metadata_key: bytes,
    rebalance_config: RebalanceConfig,
    frag_config: FragmentationConfig,
    send_alert: Callable | None = None,
) -> RebalanceResult | None:
    """Evaluate ADR-011 conditions and run re-fragmentation if warranted.

    Returns a RebalanceResult if any work was done, or None if skipped.
    Critical files are always processed; non-critical files are gated on
    hysteresis and stability checks.
    """
    logger.info("Rebalance check started")

    active = cluster_db.list_members(status="active")
    grace = cluster_db.list_members(status="grace")
    node_count = len(active) + len(grace)

    if node_count < 1:
        logger.warning("Rebalance skipped: no active nodes")
        return None

    target_profile, target_k, target_n = _resolve_target_kn(node_count, frag_config)

    state = cluster_db.get_rebalance_state()
    now = _time.time()

    if state is None:
        logger.error("rebalance_state row missing — migration 004 may not have run")
        return None

    baseline = state["baseline_count"]
    stable_since = state["size_stable_since"]

    if baseline == 0:
        # First run: seed baseline without processing anything.
        cluster_db.update_rebalance_state(
            baseline_count=node_count,
            current_tracked_count=node_count,
            size_stable_since=now,
        )
        logger.info(
            "Rebalance: seeded baseline at node_count=%d, no files processed this run",
            node_count,
        )
        return None

    distance = abs(node_count - baseline)
    days_stable = (now - stable_since) / 86400.0

    if node_count != state["current_tracked_count"]:
        cluster_db.update_rebalance_state(
            current_tracked_count=node_count,
            size_stable_since=now,
        )
        stable_since = now
        days_stable = 0.0
        logger.info(
            "Rebalance: cluster size changed to %d, stability timer reset",
            node_count,
        )

    within_hysteresis = distance <= rebalance_config.hysteresis_nodes
    not_yet_stable = days_stable < rebalance_config.stability_days

    all_files = catalog_db.get_all_files()
    critical_files, non_critical_files = prioritise_files(all_files, node_count)

    files_to_process: list[dict] = []

    if critical_files:
        logger.info(
            "Rebalance: %d critical file(s) will be processed (bypass hysteresis/stability)",
            len(critical_files),
        )
        files_to_process.extend(critical_files)

    if within_hysteresis:
        logger.info(
            "Rebalance: cluster distance=%d <= hysteresis=%d — non-critical files skipped",
            distance, rebalance_config.hysteresis_nodes,
        )
    elif not_yet_stable:
        logger.info(
            "Rebalance: days_stable=%.1f < stability_days=%d — non-critical files skipped",
            days_stable, rebalance_config.stability_days,
        )
    else:
        pct = rebalance_config.daily_rebalance_pct
        limit = max(1, len(all_files) * pct // 100)
        batch = non_critical_files[:limit]
        logger.info(
            "Rebalance: processing %d/%d non-critical files (%d%% limit, distance=%d, days_stable=%.1f)",
            len(batch), len(non_critical_files), pct, distance, days_stable,
        )
        files_to_process.extend(batch)

    if not files_to_process:
        logger.info("Rebalance check complete: no files to process")
        return None

    cluster_db.update_rebalance_state(in_progress=1)
    try:
        result = await run_rebalance(
            files=files_to_process,
            target_profile=target_profile,
            target_k=target_k,
            target_n=target_n,
            tahoe_client=tahoe_client,
            catalog_db=catalog_db,
            root_dir_ref=root_dir_ref,
            metadata_key=metadata_key,
            send_alert=send_alert,
        )
    finally:
        cluster_db.update_rebalance_state(in_progress=0)

    cluster_db.update_rebalance_state(
        baseline_count=node_count,
        last_run_at=_time.time(),
    )

    logger.info(
        "Rebalance complete: processed=%d succeeded=%d failed=%d skipped=%d",
        result.processed, result.succeeded, result.failed, result.skipped,
    )

    if result.failed and send_alert:
        try:
            await send_alert(
                f"Rebalance run completed with {result.failed} failure(s). "
                f"file_ids={result.failed_ids}"
            )
        except Exception:
            pass

    return result


async def run_scheduler(
    cluster_db: ClusterDB,
    catalog_db: CatalogDB,
    tahoe_client: TahoeClient,
    root_dir_ref: str,
    metadata_key: bytes,
    rebalance_config: RebalanceConfig,
    frag_config: FragmentationConfig,
    send_alert: Callable | None = None,
) -> None:
    """Loop forever: sleep until rebalance_time, then call check_and_run.

    asyncio.CancelledError propagates — this is how the caller shuts down
    the scheduler cleanly.
    """
    logger.info(
        "Rebalance scheduler started (daily at %s)",
        rebalance_config.rebalance_time,
    )

    while True:
        delay = _seconds_until(rebalance_config.rebalance_time)
        logger.debug("Rebalance scheduler sleeping %.0f seconds", delay)
        await asyncio.sleep(delay)

        logger.info("Rebalance scheduler woke up")
        try:
            await check_and_run(
                cluster_db=cluster_db,
                catalog_db=catalog_db,
                tahoe_client=tahoe_client,
                root_dir_ref=root_dir_ref,
                metadata_key=metadata_key,
                rebalance_config=rebalance_config,
                frag_config=frag_config,
                send_alert=send_alert,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Rebalance scheduler run failed: %s", type(exc).__name__)
            if send_alert:
                try:
                    await send_alert(f"Rebalance scheduler error: {exc}")
                except Exception:
                    pass
