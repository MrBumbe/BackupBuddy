"""Unit tests for gatekeeper.rebalance.worker and .scheduler."""

import asyncio
import os
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gatekeeper.rebalance.worker import (
    RebalanceResult,
    _entry_name,
    prioritise_files,
    run_rebalance,
)
from gatekeeper.rebalance.scheduler import (
    _resolve_target_kn,
    _seconds_until,
    check_and_run,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _file(
    file_id: int,
    k: int = 3,
    n: int = 5,
    backed_up_at: float = 1000.0,
    size_bytes: int = 1024,
    agent: str = "agent-01",
    original_path: str = "/data/file.txt",
    cap: str = "cap:abc",
    sha256: str = "a" * 64,
    profile: str = "balanced",
) -> dict:
    return {
        "id": file_id,
        "k": k,
        "n": n,
        "backed_up_at": backed_up_at,
        "size_bytes": size_bytes,
        "agent": agent,
        "original_path": original_path,
        "cap": cap,
        "sha256": sha256,
        "profile": profile,
    }


def _make_cluster_db(
    active_count: int = 3,
    grace_count: int = 0,
    state: dict | None = None,
) -> MagicMock:
    db = MagicMock()
    db.list_members.side_effect = lambda status=None: (
        [{"node_id": f"n{i}"} for i in range(active_count)] if status == "active"
        else [{"node_id": f"g{i}"} for i in range(grace_count)] if status == "grace"
        else []
    )
    default_state = {
        "id": 1,
        "baseline_count": active_count + grace_count,
        "current_tracked_count": active_count + grace_count,
        "size_stable_since": time.time() - (8 * 86400),  # 8 days ago
        "last_run_at": None,
        "in_progress": 0,
    }
    db.get_rebalance_state.return_value = state if state is not None else default_state
    return db


def _make_frag_config(profile: str = "balanced") -> MagicMock:
    cfg = MagicMock()
    cfg.profile = profile
    cfg.adaptive = MagicMock()
    cfg.adaptive.ratio = 0.33
    cfg.adaptive.min_k = 1
    cfg.adaptive.max_n = 20
    return cfg


def _make_rebalance_config(
    stability_days: int = 7,
    hysteresis_nodes: int = 2,
    daily_rebalance_pct: int = 3,
) -> MagicMock:
    cfg = MagicMock()
    cfg.stability_days = stability_days
    cfg.hysteresis_nodes = hysteresis_nodes
    cfg.daily_rebalance_pct = daily_rebalance_pct
    cfg.rebalance_time = MagicMock()
    cfg.rebalance_time.hour = 3
    cfg.rebalance_time.minute = 30
    return cfg


# ── _entry_name ───────────────────────────────────────────────────────────────

class TestEntryName(unittest.TestCase):

    def test_stable_across_calls(self):
        a = _entry_name("agent-01", "/home/user/file.txt")
        b = _entry_name("agent-01", "/home/user/file.txt")
        self.assertEqual(a, b)

    def test_different_agents_differ(self):
        a = _entry_name("agent-01", "/home/user/file.txt")
        b = _entry_name("agent-02", "/home/user/file.txt")
        self.assertNotEqual(a, b)

    def test_different_paths_differ(self):
        a = _entry_name("agent-01", "/path/a.txt")
        b = _entry_name("agent-01", "/path/b.txt")
        self.assertNotEqual(a, b)

    def test_length_is_32_hex(self):
        result = _entry_name("agent-01", "/file.txt")
        self.assertEqual(len(result), 32)


# ── prioritise_files ──────────────────────────────────────────────────────────

class TestPrioritiseFiles(unittest.TestCase):

    def test_critical_when_k_exceeds_cluster(self):
        f = _file(1, k=5, n=7)
        critical, non_critical = prioritise_files([f], current_cluster_size=4)
        self.assertIn(f, critical)
        self.assertNotIn(f, non_critical)

    def test_non_critical_when_k_within_cluster(self):
        f = _file(1, k=3, n=5)
        critical, non_critical = prioritise_files([f], current_cluster_size=5)
        self.assertNotIn(f, critical)
        self.assertIn(f, non_critical)

    def test_exactly_at_cluster_size_is_non_critical(self):
        # k == current_cluster_size is still restorable
        f = _file(1, k=3, n=5)
        critical, non_critical = prioritise_files([f], current_cluster_size=3)
        self.assertIn(f, non_critical)
        self.assertNotIn(f, critical)

    def test_non_critical_sorted_oldest_first(self):
        f1 = _file(1, backed_up_at=2000.0)
        f2 = _file(2, backed_up_at=1000.0)
        _, non_critical = prioritise_files([f1, f2], current_cluster_size=5)
        self.assertEqual(non_critical[0]["id"], 2)  # older first

    def test_non_critical_sorted_largest_first_within_same_age(self):
        f1 = _file(1, backed_up_at=1000.0, size_bytes=100)
        f2 = _file(2, backed_up_at=1000.0, size_bytes=500)
        _, non_critical = prioritise_files([f1, f2], current_cluster_size=5)
        self.assertEqual(non_critical[0]["id"], 2)  # larger first

    def test_empty_input(self):
        critical, non_critical = prioritise_files([], current_cluster_size=5)
        self.assertEqual(critical, [])
        self.assertEqual(non_critical, [])


# ── run_rebalance ─────────────────────────────────────────────────────────────

class TestRunRebalance(unittest.IsolatedAsyncioTestCase):

    def _make_deps(self, sha256: str = "a" * 64):
        tahoe = AsyncMock()
        tahoe.download = AsyncMock(return_value=sha256)
        tahoe.upload = AsyncMock(return_value="cap:new")
        tahoe.link_file = AsyncMock()
        catalog_db = MagicMock()
        return tahoe, catalog_db

    async def test_successful_refrag_updates_catalog(self):
        files = [_file(1, sha256="a" * 64)]
        tahoe, catalog_db = self._make_deps(sha256="a" * 64)

        result = await run_rebalance(
            files=files,
            target_profile="secure",
            target_k=3,
            target_n=7,
            tahoe_client=tahoe,
            catalog_db=catalog_db,
            root_dir_ref="dir:root",
            metadata_key=os.urandom(32),
        )

        self.assertEqual(result.succeeded, 1)
        self.assertEqual(result.failed, 0)
        catalog_db.update_file.assert_called_once()
        call_kwargs = catalog_db.update_file.call_args
        self.assertEqual(call_kwargs[0][0], 1)  # file_id positional

    async def test_skips_files_without_original_path(self):
        f = _file(1)
        f["original_path"] = None
        tahoe, catalog_db = self._make_deps()

        result = await run_rebalance(
            files=[f],
            target_profile="balanced",
            target_k=3,
            target_n=5,
            tahoe_client=tahoe,
            catalog_db=catalog_db,
            root_dir_ref="dir:root",
            metadata_key=os.urandom(32),
        )

        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.processed, 0)
        tahoe.download.assert_not_called()

    async def test_download_failure_counts_as_failed(self):
        from gatekeeper.tahoe.client import TahoeError
        files = [_file(1)]
        tahoe, catalog_db = self._make_deps()
        tahoe.download = AsyncMock(side_effect=TahoeError("network error"))

        result = await run_rebalance(
            files=files,
            target_profile="balanced",
            target_k=3,
            target_n=5,
            tahoe_client=tahoe,
            catalog_db=catalog_db,
            root_dir_ref="dir:root",
            metadata_key=os.urandom(32),
        )

        self.assertEqual(result.failed, 1)
        self.assertEqual(result.succeeded, 0)
        self.assertIn(1, result.failed_ids)

    async def test_upload_failure_counts_as_failed(self):
        from gatekeeper.tahoe.client import TahoeError
        files = [_file(1, sha256="a" * 64)]
        tahoe, catalog_db = self._make_deps(sha256="a" * 64)
        tahoe.upload = AsyncMock(side_effect=TahoeError("upload error"))

        result = await run_rebalance(
            files=files,
            target_profile="balanced",
            target_k=3,
            target_n=5,
            tahoe_client=tahoe,
            catalog_db=catalog_db,
            root_dir_ref="dir:root",
            metadata_key=os.urandom(32),
        )

        self.assertEqual(result.failed, 1)
        self.assertEqual(result.succeeded, 0)

    async def test_sha256_mismatch_counts_as_failed(self):
        files = [_file(1, sha256="a" * 64)]
        tahoe, catalog_db = self._make_deps(sha256="b" * 64)  # different hash

        result = await run_rebalance(
            files=files,
            target_profile="balanced",
            target_k=3,
            target_n=5,
            tahoe_client=tahoe,
            catalog_db=catalog_db,
            root_dir_ref="dir:root",
            metadata_key=os.urandom(32),
        )

        self.assertEqual(result.failed, 1)
        catalog_db.update_file.assert_not_called()

    async def test_cancelled_error_propagates(self):
        tahoe, catalog_db = self._make_deps()
        tahoe.download = AsyncMock(side_effect=asyncio.CancelledError())

        with self.assertRaises(asyncio.CancelledError):
            await run_rebalance(
                files=[_file(1)],
                target_profile="balanced",
                target_k=3,
                target_n=5,
                tahoe_client=tahoe,
                catalog_db=catalog_db,
                root_dir_ref="dir:root",
                metadata_key=os.urandom(32),
            )

    async def test_temp_dir_cleaned_on_success(self):
        created_dirs = []
        original_mkdtemp = __import__("tempfile").mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = original_mkdtemp(**kwargs)
            created_dirs.append(d)
            return d

        files = [_file(1, sha256="a" * 64)]
        tahoe, catalog_db = self._make_deps(sha256="a" * 64)

        with patch("gatekeeper.rebalance.worker.tempfile.mkdtemp", side_effect=tracking_mkdtemp):
            await run_rebalance(
                files=files,
                target_profile="balanced",
                target_k=3,
                target_n=5,
                tahoe_client=tahoe,
                catalog_db=catalog_db,
                root_dir_ref="dir:root",
                metadata_key=os.urandom(32),
            )

        for d in created_dirs:
            self.assertFalse(os.path.exists(d), f"Temp dir {d!r} was not cleaned up")

    async def test_temp_dir_cleaned_on_failure(self):
        from gatekeeper.tahoe.client import TahoeError
        created_dirs = []
        original_mkdtemp = __import__("tempfile").mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = original_mkdtemp(**kwargs)
            created_dirs.append(d)
            return d

        tahoe, catalog_db = self._make_deps()
        tahoe.download = AsyncMock(side_effect=TahoeError("fail"))

        with patch("gatekeeper.rebalance.worker.tempfile.mkdtemp", side_effect=tracking_mkdtemp):
            await run_rebalance(
                files=[_file(1)],
                target_profile="balanced",
                target_k=3,
                target_n=5,
                tahoe_client=tahoe,
                catalog_db=catalog_db,
                root_dir_ref="dir:root",
                metadata_key=os.urandom(32),
            )

        for d in created_dirs:
            self.assertFalse(os.path.exists(d), f"Temp dir {d!r} was not cleaned up on failure")

    async def test_send_alert_called_on_failure(self):
        from gatekeeper.tahoe.client import TahoeError
        tahoe, catalog_db = self._make_deps()
        tahoe.download = AsyncMock(side_effect=TahoeError("fail"))
        alerts = []

        async def capture_alert(msg):
            alerts.append(msg)

        await run_rebalance(
            files=[_file(1)],
            target_profile="balanced",
            target_k=3,
            target_n=5,
            tahoe_client=tahoe,
            catalog_db=catalog_db,
            root_dir_ref="dir:root",
            metadata_key=os.urandom(32),
            send_alert=capture_alert,
        )

        self.assertTrue(any("fail" in a.lower() or "file_id=1" in a for a in alerts))


# ── _resolve_target_kn ────────────────────────────────────────────────────────

class TestResolveTargetKn(unittest.TestCase):

    def test_fixed_profile(self):
        cfg = _make_frag_config("balanced")
        profile, k, n = _resolve_target_kn(5, cfg)
        self.assertEqual(profile, "balanced")
        self.assertEqual(k, 3)
        self.assertEqual(n, 5)

    def test_adaptive_profile(self):
        cfg = _make_frag_config("adaptive")
        profile, k, n = _resolve_target_kn(6, cfg)
        self.assertEqual(profile, "adaptive")
        self.assertGreater(k, 0)
        self.assertGreaterEqual(n, k)


# ── check_and_run ─────────────────────────────────────────────────────────────

class TestCheckAndRun(unittest.IsolatedAsyncioTestCase):

    def _common_mocks(self):
        tahoe = AsyncMock()
        tahoe.download = AsyncMock(return_value="a" * 64)
        tahoe.upload = AsyncMock(return_value="cap:new")
        tahoe.link_file = AsyncMock()
        catalog_db = MagicMock()
        catalog_db.get_all_files.return_value = []
        return tahoe, catalog_db

    async def test_seeds_baseline_on_first_run_and_returns_none(self):
        cluster_db = _make_cluster_db(active_count=3, state={
            "id": 1,
            "baseline_count": 0,
            "current_tracked_count": 0,
            "size_stable_since": 0.0,
            "last_run_at": None,
            "in_progress": 0,
        })
        tahoe, catalog_db = self._common_mocks()

        result = await check_and_run(
            cluster_db=cluster_db,
            catalog_db=catalog_db,
            tahoe_client=tahoe,
            root_dir_ref="dir:root",
            metadata_key=os.urandom(32),
            rebalance_config=_make_rebalance_config(),
            frag_config=_make_frag_config(),
        )

        self.assertIsNone(result)
        cluster_db.update_rebalance_state.assert_called_once()

    async def test_within_hysteresis_skips_non_critical(self):
        # 3 nodes, baseline 3, distance=0 <= hysteresis=2 => skip non-critical
        cluster_db = _make_cluster_db(active_count=3)
        tahoe, catalog_db = self._common_mocks()
        catalog_db.get_all_files.return_value = [_file(1, k=3, n=5)]

        result = await check_and_run(
            cluster_db=cluster_db,
            catalog_db=catalog_db,
            tahoe_client=tahoe,
            root_dir_ref="dir:root",
            metadata_key=os.urandom(32),
            rebalance_config=_make_rebalance_config(hysteresis_nodes=2),
            frag_config=_make_frag_config(),
        )

        # No critical files, within hysteresis → nothing processed
        self.assertIsNone(result)
        tahoe.download.assert_not_called()

    async def test_not_yet_stable_skips_non_critical(self):
        # Stability timer was reset today — days_stable < stability_days
        cluster_db = _make_cluster_db(active_count=5, state={
            "id": 1,
            "baseline_count": 3,
            "current_tracked_count": 5,
            "size_stable_since": time.time() - 1 * 86400,  # 1 day ago
            "last_run_at": None,
            "in_progress": 0,
        })
        tahoe, catalog_db = self._common_mocks()
        catalog_db.get_all_files.return_value = [_file(1, k=3, n=5)]

        result = await check_and_run(
            cluster_db=cluster_db,
            catalog_db=catalog_db,
            tahoe_client=tahoe,
            root_dir_ref="dir:root",
            metadata_key=os.urandom(32),
            rebalance_config=_make_rebalance_config(stability_days=7, hysteresis_nodes=2),
            frag_config=_make_frag_config(),
        )

        self.assertIsNone(result)
        tahoe.download.assert_not_called()

    async def test_critical_files_processed_despite_hysteresis(self):
        # Critical file: k=5 > node_count=3
        cluster_db = _make_cluster_db(active_count=3)
        tahoe, catalog_db = self._common_mocks()
        tahoe.download = AsyncMock(return_value="a" * 64)
        catalog_db.get_all_files.return_value = [_file(1, k=5, n=7, sha256="a" * 64)]

        result = await check_and_run(
            cluster_db=cluster_db,
            catalog_db=catalog_db,
            tahoe_client=tahoe,
            root_dir_ref="dir:root",
            metadata_key=os.urandom(32),
            rebalance_config=_make_rebalance_config(hysteresis_nodes=2),
            frag_config=_make_frag_config(),
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.processed, 1)

    async def test_full_run_when_distance_exceeds_hysteresis_and_stable(self):
        # Cluster grew from 3 to 7 (distance=4 > hysteresis=2), stable 8 days
        cluster_db = _make_cluster_db(active_count=7, state={
            "id": 1,
            "baseline_count": 3,
            "current_tracked_count": 7,
            "size_stable_since": time.time() - 8 * 86400,
            "last_run_at": None,
            "in_progress": 0,
        })
        tahoe, catalog_db = self._common_mocks()
        tahoe.download = AsyncMock(return_value="a" * 64)
        files = [_file(i, sha256="a" * 64) for i in range(1, 11)]
        catalog_db.get_all_files.return_value = files

        result = await check_and_run(
            cluster_db=cluster_db,
            catalog_db=catalog_db,
            tahoe_client=tahoe,
            root_dir_ref="dir:root",
            metadata_key=os.urandom(32),
            rebalance_config=_make_rebalance_config(
                stability_days=7, hysteresis_nodes=2, daily_rebalance_pct=30
            ),
            frag_config=_make_frag_config("balanced"),
        )

        self.assertIsNotNone(result)
        self.assertGreater(result.processed, 0)

    async def test_returns_none_when_no_nodes(self):
        cluster_db = _make_cluster_db(active_count=0, grace_count=0)
        tahoe, catalog_db = self._common_mocks()

        result = await check_and_run(
            cluster_db=cluster_db,
            catalog_db=catalog_db,
            tahoe_client=tahoe,
            root_dir_ref="dir:root",
            metadata_key=os.urandom(32),
            rebalance_config=_make_rebalance_config(),
            frag_config=_make_frag_config(),
        )

        self.assertIsNone(result)
