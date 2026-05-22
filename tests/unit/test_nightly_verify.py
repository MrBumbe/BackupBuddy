"""
Unit tests for gatekeeper/verify/nightly.py.

Each layer is tested in isolation using mocks for all external dependencies.
The combined run test verifies inter-layer isolation (failure in one layer
does not prevent others from running).
"""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from gatekeeper.config import VerifyConfig
from gatekeeper.lifeboat.crypto import IntegrityError
from gatekeeper.lifeboat.keystore import KeyNotFoundError
from gatekeeper.restore.restore import RestoreIntegrityError, RestoreNotFoundError
from gatekeeper.tahoe.client import TahoeError
from gatekeeper.verify.nightly import LayerResult, NightlyVerifier, VerifyResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _default_config(**overrides) -> VerifyConfig:
    defaults = dict(
        test_restore_enabled=True,
        test_restore_files=2,
        test_restore_path="/tmp/bb_test_verify/",
        lifeboat_max_age_hours=6,
        notify_on_success=False,
        notify_on_warning=True,
        notify_on_failure=True,
        notify_on_corrupt=True,
    )
    defaults.update(overrides)
    return VerifyConfig(**defaults)


def _make_verifier(
    *,
    config: VerifyConfig | None = None,
    catalog: MagicMock | None = None,
    cluster: MagicMock | None = None,
    tahoe: MagicMock | None = None,
    root_dir_cap: str = "URI:DIR2-RW:abc123",
    agent_token: str | None = "token",
    send_alert: AsyncMock | None = None,
) -> NightlyVerifier:
    return NightlyVerifier(
        verify_config=config or _default_config(),
        catalog=catalog or MagicMock(),
        cluster=cluster or MagicMock(),
        tahoe=tahoe or MagicMock(),
        root_dir_cap=root_dir_cap,
        agent_token=agent_token,
        send_alert=send_alert,
    )


def _file_record(
    agent: str = "node-a",
    original_path: str = "/home/user/docs/file.txt",
    cap: str = "URI:CHK:aaa",
    sha256: str = "abc123",
) -> dict:
    return {
        "agent": agent,
        "original_path": original_path,
        "cap": cap,
        "sha256": sha256,
    }


def _healthy_check() -> dict:
    return {"accessible": True, "shares_good": 7, "shares_needed": 3}


def _agent_record(
    agent_name: str = "node-a",
    lifeboat_url: str = "http://100.64.0.2:8081/lifeboat",
) -> dict:
    return {"agent_name": agent_name, "lifeboat_url": lifeboat_url}


# ── VerifyResult ──────────────────────────────────────────────────────────────

class TestVerifyResult(unittest.TestCase):

    def test_overall_ok_all_pass(self):
        r = VerifyResult(
            layer1=LayerResult(ok=True),
            layer2=LayerResult(ok=True),
            layer3=LayerResult(ok=True),
            layer4=LayerResult(ok=True),
        )
        self.assertTrue(r.overall_ok)

    def test_overall_ok_one_fails(self):
        r = VerifyResult(
            layer1=LayerResult(ok=True),
            layer2=LayerResult(ok=False),
            layer3=LayerResult(ok=True),
            layer4=LayerResult(ok=True),
        )
        self.assertFalse(r.overall_ok)

    def test_overall_ok_none_layers(self):
        r = VerifyResult()
        self.assertTrue(r.overall_ok)


# ── Layer 1 — root_dir.cap ────────────────────────────────────────────────────

class TestLayer1RootDirCap(unittest.IsolatedAsyncioTestCase):

    async def test_accessible(self):
        tahoe = MagicMock()
        tahoe.ls = AsyncMock(return_value=[("file.txt", "URI:CHK:aaa")])
        send_alert = AsyncMock()
        v = _make_verifier(tahoe=tahoe, send_alert=send_alert)

        result = await v._layer1_root_dir_cap()

        self.assertTrue(result.ok)
        send_alert.assert_not_called()

    async def test_inaccessible_sends_critical_alert(self):
        tahoe = MagicMock()
        tahoe.ls = AsyncMock(side_effect=TahoeError("not found"))
        send_alert = AsyncMock()
        v = _make_verifier(tahoe=tahoe, send_alert=send_alert)

        result = await v._layer1_root_dir_cap()

        self.assertFalse(result.ok)
        self.assertEqual(result.errors, 1)
        send_alert.assert_awaited_once()
        level, message, *_ = send_alert.call_args.args
        self.assertEqual(level, "critical")

    async def test_inaccessible_with_no_send_alert(self):
        tahoe = MagicMock()
        tahoe.ls = AsyncMock(side_effect=TahoeError("not found"))
        v = _make_verifier(tahoe=tahoe, send_alert=None)

        result = await v._layer1_root_dir_cap()

        self.assertFalse(result.ok)


# ── Layer 2 — catalog vs cluster ─────────────────────────────────────────────

class TestLayer2CatalogVsCluster(unittest.IsolatedAsyncioTestCase):

    async def test_all_healthy(self):
        catalog = MagicMock()
        catalog.get_all_files.return_value = [_file_record(), _file_record(cap="URI:CHK:bbb")]
        tahoe = MagicMock()
        tahoe.check_cap = AsyncMock(return_value=_healthy_check())
        send_alert = AsyncMock()
        v = _make_verifier(catalog=catalog, tahoe=tahoe, send_alert=send_alert)

        result = await v._layer2_catalog_vs_cluster()

        self.assertTrue(result.ok)
        send_alert.assert_not_called()

    async def test_empty_catalog(self):
        catalog = MagicMock()
        catalog.get_all_files.return_value = []
        send_alert = AsyncMock()
        v = _make_verifier(catalog=catalog, send_alert=send_alert)

        result = await v._layer2_catalog_vs_cluster()

        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "catalog empty")
        send_alert.assert_not_called()

    async def test_inaccessible_cap_sends_error(self):
        catalog = MagicMock()
        catalog.get_all_files.return_value = [_file_record()]
        tahoe = MagicMock()
        tahoe.check_cap = AsyncMock(return_value=None)
        send_alert = AsyncMock()
        v = _make_verifier(catalog=catalog, tahoe=tahoe, send_alert=send_alert)

        result = await v._layer2_catalog_vs_cluster()

        self.assertFalse(result.ok)
        self.assertEqual(result.errors, 1)
        levels = [call.args[0] for call in send_alert.call_args_list]
        self.assertIn("error", levels)

    async def test_under_replicated_sends_warning(self):
        catalog = MagicMock()
        catalog.get_all_files.return_value = [_file_record()]
        tahoe = MagicMock()
        tahoe.check_cap = AsyncMock(
            return_value={"accessible": True, "shares_good": 2, "shares_needed": 3}
        )
        send_alert = AsyncMock()
        v = _make_verifier(catalog=catalog, tahoe=tahoe, send_alert=send_alert)

        result = await v._layer2_catalog_vs_cluster()

        self.assertFalse(result.ok)
        self.assertEqual(result.warnings, 1)
        levels = [call.args[0] for call in send_alert.call_args_list]
        self.assertIn("warning", levels)

    async def test_mixed_results(self):
        catalog = MagicMock()
        catalog.get_all_files.return_value = [
            _file_record(cap="URI:CHK:aaa"),  # inaccessible
            _file_record(cap="URI:CHK:bbb"),  # under-replicated
            _file_record(cap="URI:CHK:ccc"),  # healthy
        ]
        tahoe = MagicMock()
        tahoe.check_cap = AsyncMock(side_effect=[
            None,
            {"accessible": True, "shares_good": 1, "shares_needed": 3},
            _healthy_check(),
        ])
        send_alert = AsyncMock()
        v = _make_verifier(catalog=catalog, tahoe=tahoe, send_alert=send_alert)

        result = await v._layer2_catalog_vs_cluster()

        self.assertFalse(result.ok)
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.warnings, 1)
        self.assertEqual(send_alert.await_count, 2)

    async def test_shares_needed_zero_skips_check(self):
        """shares_needed=0 means k is unknown — don't flag as under-replicated."""
        catalog = MagicMock()
        catalog.get_all_files.return_value = [_file_record()]
        tahoe = MagicMock()
        tahoe.check_cap = AsyncMock(
            return_value={"accessible": True, "shares_good": 0, "shares_needed": 0}
        )
        send_alert = AsyncMock()
        v = _make_verifier(catalog=catalog, tahoe=tahoe, send_alert=send_alert)

        result = await v._layer2_catalog_vs_cluster()

        self.assertTrue(result.ok)
        send_alert.assert_not_called()

    async def test_notify_disabled_suppresses_alert(self):
        catalog = MagicMock()
        catalog.get_all_files.return_value = [_file_record()]
        tahoe = MagicMock()
        tahoe.check_cap = AsyncMock(return_value=None)
        send_alert = AsyncMock()
        config = _default_config(notify_on_failure=False)
        v = _make_verifier(config=config, catalog=catalog, tahoe=tahoe, send_alert=send_alert)

        result = await v._layer2_catalog_vs_cluster()

        self.assertFalse(result.ok)
        send_alert.assert_not_called()


# ── Layer 3 — test restore ────────────────────────────────────────────────────

class TestLayer3TestRestore(unittest.IsolatedAsyncioTestCase):

    async def test_disabled(self):
        config = _default_config(test_restore_enabled=False)
        send_alert = AsyncMock()
        v = _make_verifier(config=config, send_alert=send_alert)

        result = await v._layer3_test_restore()

        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "disabled")
        send_alert.assert_not_called()

    async def test_no_files(self):
        catalog = MagicMock()
        catalog.get_all_files.return_value = []
        send_alert = AsyncMock()
        v = _make_verifier(catalog=catalog, send_alert=send_alert)

        result = await v._layer3_test_restore()

        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "no files")
        send_alert.assert_not_called()

    async def test_successful_restore(self):
        catalog = MagicMock()
        catalog.get_all_files.return_value = [_file_record()]
        send_alert = AsyncMock()
        v = _make_verifier(catalog=catalog, send_alert=send_alert)

        with patch("gatekeeper.verify.nightly.restore_file", new_callable=AsyncMock):
            result = await v._layer3_test_restore()

        self.assertTrue(result.ok)
        send_alert.assert_not_called()

    async def test_integrity_failure_sends_error_alert(self):
        catalog = MagicMock()
        catalog.get_all_files.return_value = [_file_record()]
        send_alert = AsyncMock()
        v = _make_verifier(catalog=catalog, send_alert=send_alert)

        with patch(
            "gatekeeper.verify.nightly.restore_file",
            new_callable=AsyncMock,
            side_effect=RestoreIntegrityError("hash mismatch"),
        ):
            result = await v._layer3_test_restore()

        self.assertFalse(result.ok)
        self.assertEqual(result.errors, 1)
        send_alert.assert_awaited_once()
        self.assertEqual(send_alert.call_args.args[0], "error")

    async def test_not_found_failure_sends_error_alert(self):
        catalog = MagicMock()
        catalog.get_all_files.return_value = [_file_record()]
        send_alert = AsyncMock()
        v = _make_verifier(catalog=catalog, send_alert=send_alert)

        with patch(
            "gatekeeper.verify.nightly.restore_file",
            new_callable=AsyncMock,
            side_effect=RestoreNotFoundError("not in catalog"),
        ):
            result = await v._layer3_test_restore()

        self.assertFalse(result.ok)
        self.assertEqual(result.errors, 1)
        send_alert.assert_awaited_once()
        self.assertEqual(send_alert.call_args.args[0], "error")

    async def test_tahoe_error_sends_error_alert(self):
        catalog = MagicMock()
        catalog.get_all_files.return_value = [_file_record()]
        send_alert = AsyncMock()
        v = _make_verifier(catalog=catalog, send_alert=send_alert)

        with patch(
            "gatekeeper.verify.nightly.restore_file",
            new_callable=AsyncMock,
            side_effect=TahoeError("download failed"),
        ):
            result = await v._layer3_test_restore()

        self.assertFalse(result.ok)
        self.assertEqual(result.errors, 1)
        send_alert.assert_awaited_once()
        self.assertEqual(send_alert.call_args.args[0], "error")

    async def test_temp_dir_cleaned_up_on_failure(self):
        """Temp dir must be removed even when a restore raises."""
        import tempfile

        catalog = MagicMock()
        catalog.get_all_files.return_value = [_file_record()]
        created_dirs: list[str] = []

        real_mkdtemp = tempfile.mkdtemp

        def capture_mkdtemp(**kwargs):
            d = real_mkdtemp(**kwargs)
            created_dirs.append(d)
            return d

        v = _make_verifier(catalog=catalog)

        with (
            patch("gatekeeper.verify.nightly.tempfile.mkdtemp", side_effect=capture_mkdtemp),
            patch(
                "gatekeeper.verify.nightly.restore_file",
                new_callable=AsyncMock,
                side_effect=RestoreIntegrityError("hash mismatch"),
            ),
        ):
            await v._layer3_test_restore()

        for d in created_dirs:
            self.assertFalse(
                os.path.exists(d),
                f"Temp dir {d} was not cleaned up",
            )

    async def test_files_without_original_path_excluded(self):
        """Reconstructed records with original_path=None must not be selected."""
        catalog = MagicMock()
        catalog.get_all_files.return_value = [
            {"agent": "node-a", "original_path": None, "cap": "URI:CHK:aaa", "sha256": "x"},
        ]
        send_alert = AsyncMock()
        v = _make_verifier(catalog=catalog, send_alert=send_alert)

        with patch("gatekeeper.verify.nightly.restore_file", new_callable=AsyncMock) as mock_rf:
            result = await v._layer3_test_restore()

        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "no files")
        mock_rf.assert_not_called()


# ── Layer 4 — lifeboat ────────────────────────────────────────────────────────

class TestLayer4Lifeboat(unittest.IsolatedAsyncioTestCase):

    def _make_good_status(self, age_seconds: float = 3600.0) -> dict:
        return {"distributed_at": time.time() - age_seconds}

    async def test_ok(self):
        cluster = MagicMock()
        cluster.get_last_lifeboat_status.return_value = self._make_good_status(3600)
        cluster.list_agents.return_value = [_agent_record()]

        bundle_data = {"root_dir_cap": "URI:DIR2-RW:abc123"}
        send_alert = AsyncMock()
        v = _make_verifier(
            cluster=cluster,
            root_dir_cap="URI:DIR2-RW:abc123",
            send_alert=send_alert,
        )

        with (
            patch("gatekeeper.verify.nightly.load_key", return_value=b"\x00" * 32),
            patch(
                "gatekeeper.verify.nightly.NightlyVerifier._fetch_lifeboat",
                new_callable=AsyncMock,
                return_value=b"encrypted-bundle",
            ),
            patch("gatekeeper.verify.nightly.extract_bundle", return_value=bundle_data),
        ):
            result = await v._layer4_lifeboat()

        self.assertTrue(result.ok)
        send_alert.assert_not_called()

    async def test_no_status_sends_warning(self):
        cluster = MagicMock()
        cluster.get_last_lifeboat_status.return_value = None
        send_alert = AsyncMock()
        v = _make_verifier(cluster=cluster, send_alert=send_alert)

        result = await v._layer4_lifeboat()

        self.assertFalse(result.ok)
        self.assertEqual(result.warnings, 1)
        self.assertEqual(result.detail, "never distributed")
        send_alert.assert_awaited_once()
        self.assertEqual(send_alert.call_args.args[0], "warning")

    async def test_stale_lifeboat_sends_warning(self):
        config = _default_config(lifeboat_max_age_hours=6)
        cluster = MagicMock()
        cluster.get_last_lifeboat_status.return_value = self._make_good_status(
            age_seconds=7 * 3600
        )
        send_alert = AsyncMock()
        v = _make_verifier(config=config, cluster=cluster, send_alert=send_alert)

        result = await v._layer4_lifeboat()

        self.assertFalse(result.ok)
        self.assertEqual(result.warnings, 1)
        send_alert.assert_awaited_once()
        self.assertEqual(send_alert.call_args.args[0], "warning")

    async def test_no_agents_sends_warning(self):
        cluster = MagicMock()
        cluster.get_last_lifeboat_status.return_value = self._make_good_status(3600)
        cluster.list_agents.return_value = []
        send_alert = AsyncMock()
        v = _make_verifier(cluster=cluster, send_alert=send_alert)

        result = await v._layer4_lifeboat()

        self.assertFalse(result.ok)
        self.assertEqual(result.warnings, 1)
        send_alert.assert_awaited_once()
        self.assertEqual(send_alert.call_args.args[0], "warning")

    async def test_key_unavailable_sends_critical(self):
        cluster = MagicMock()
        cluster.get_last_lifeboat_status.return_value = self._make_good_status(3600)
        cluster.list_agents.return_value = [_agent_record()]
        send_alert = AsyncMock()
        v = _make_verifier(cluster=cluster, send_alert=send_alert)

        with patch(
            "gatekeeper.verify.nightly.load_key",
            side_effect=KeyNotFoundError("missing"),
        ):
            result = await v._layer4_lifeboat()

        self.assertFalse(result.ok)
        self.assertEqual(result.errors, 1)
        send_alert.assert_awaited_once()
        self.assertEqual(send_alert.call_args.args[0], "critical")

    async def test_fetch_failure_sends_critical(self):
        cluster = MagicMock()
        cluster.get_last_lifeboat_status.return_value = self._make_good_status(3600)
        cluster.list_agents.return_value = [_agent_record()]
        send_alert = AsyncMock()
        v = _make_verifier(cluster=cluster, send_alert=send_alert)

        with (
            patch("gatekeeper.verify.nightly.load_key", return_value=b"\x00" * 32),
            patch(
                "gatekeeper.verify.nightly.NightlyVerifier._fetch_lifeboat",
                new_callable=AsyncMock,
                side_effect=Exception("connection refused"),
            ),
        ):
            result = await v._layer4_lifeboat()

        self.assertFalse(result.ok)
        self.assertEqual(result.errors, 1)
        send_alert.assert_awaited_once()
        self.assertEqual(send_alert.call_args.args[0], "critical")

    async def test_decrypt_failure_sends_critical(self):
        cluster = MagicMock()
        cluster.get_last_lifeboat_status.return_value = self._make_good_status(3600)
        cluster.list_agents.return_value = [_agent_record()]
        send_alert = AsyncMock()
        v = _make_verifier(cluster=cluster, send_alert=send_alert)

        with (
            patch("gatekeeper.verify.nightly.load_key", return_value=b"\x00" * 32),
            patch(
                "gatekeeper.verify.nightly.NightlyVerifier._fetch_lifeboat",
                new_callable=AsyncMock,
                return_value=b"garbage",
            ),
            patch(
                "gatekeeper.verify.nightly.extract_bundle",
                side_effect=IntegrityError("bad key"),
            ),
        ):
            result = await v._layer4_lifeboat()

        self.assertFalse(result.ok)
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.detail, "decrypt failed")
        send_alert.assert_awaited_once()
        self.assertEqual(send_alert.call_args.args[0], "critical")

    async def test_cap_mismatch_sends_critical(self):
        cluster = MagicMock()
        cluster.get_last_lifeboat_status.return_value = self._make_good_status(3600)
        cluster.list_agents.return_value = [_agent_record()]
        send_alert = AsyncMock()
        v = _make_verifier(
            cluster=cluster,
            root_dir_cap="URI:DIR2-RW:CURRENT",
            send_alert=send_alert,
        )

        bundle_data = {"root_dir_cap": "URI:DIR2-RW:STALE"}
        with (
            patch("gatekeeper.verify.nightly.load_key", return_value=b"\x00" * 32),
            patch(
                "gatekeeper.verify.nightly.NightlyVerifier._fetch_lifeboat",
                new_callable=AsyncMock,
                return_value=b"encrypted-bundle",
            ),
            patch("gatekeeper.verify.nightly.extract_bundle", return_value=bundle_data),
        ):
            result = await v._layer4_lifeboat()

        self.assertFalse(result.ok)
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.detail, "root_dir_cap mismatch")
        send_alert.assert_awaited_once()
        self.assertEqual(send_alert.call_args.args[0], "critical")

    async def test_notify_on_corrupt_false_suppresses_alert(self):
        cluster = MagicMock()
        cluster.get_last_lifeboat_status.return_value = self._make_good_status(3600)
        cluster.list_agents.return_value = [_agent_record()]
        config = _default_config(notify_on_corrupt=False)
        send_alert = AsyncMock()
        v = _make_verifier(config=config, cluster=cluster, send_alert=send_alert)

        with (
            patch("gatekeeper.verify.nightly.load_key", return_value=b"\x00" * 32),
            patch(
                "gatekeeper.verify.nightly.NightlyVerifier._fetch_lifeboat",
                new_callable=AsyncMock,
                return_value=b"garbage",
            ),
            patch(
                "gatekeeper.verify.nightly.extract_bundle",
                side_effect=IntegrityError("bad key"),
            ),
        ):
            result = await v._layer4_lifeboat()

        self.assertFalse(result.ok)
        send_alert.assert_not_called()


# ── Combined run ──────────────────────────────────────────────────────────────

class TestNightlyVerifierRun(unittest.IsolatedAsyncioTestCase):

    async def test_all_layers_pass(self):
        v = _make_verifier()
        v._layer1_root_dir_cap = AsyncMock(return_value=LayerResult(ok=True))
        v._layer2_catalog_vs_cluster = AsyncMock(return_value=LayerResult(ok=True))
        v._layer3_test_restore = AsyncMock(return_value=LayerResult(ok=True))
        v._layer4_lifeboat = AsyncMock(return_value=LayerResult(ok=True))

        result = await v.run()

        self.assertTrue(result.overall_ok)
        v._layer1_root_dir_cap.assert_awaited_once()
        v._layer2_catalog_vs_cluster.assert_awaited_once()
        v._layer3_test_restore.assert_awaited_once()
        v._layer4_lifeboat.assert_awaited_once()

    async def test_layer_failure_does_not_block_others(self):
        """All four layers must run even when one raises an unexpected exception."""
        v = _make_verifier()
        v._layer1_root_dir_cap = AsyncMock(side_effect=RuntimeError("kaboom"))
        v._layer2_catalog_vs_cluster = AsyncMock(return_value=LayerResult(ok=True))
        v._layer3_test_restore = AsyncMock(return_value=LayerResult(ok=True))
        v._layer4_lifeboat = AsyncMock(return_value=LayerResult(ok=True))

        result = await v.run()

        self.assertFalse(result.overall_ok)
        self.assertIsNotNone(result.layer1)
        self.assertFalse(result.layer1.ok)
        v._layer2_catalog_vs_cluster.assert_awaited_once()
        v._layer3_test_restore.assert_awaited_once()
        v._layer4_lifeboat.assert_awaited_once()

    async def test_all_layers_fail(self):
        v = _make_verifier()
        v._layer1_root_dir_cap = AsyncMock(return_value=LayerResult(ok=False, errors=1))
        v._layer2_catalog_vs_cluster = AsyncMock(return_value=LayerResult(ok=False, errors=2))
        v._layer3_test_restore = AsyncMock(return_value=LayerResult(ok=False, errors=1))
        v._layer4_lifeboat = AsyncMock(return_value=LayerResult(ok=False, errors=1))

        result = await v.run()

        self.assertFalse(result.overall_ok)

    async def test_success_alert_sent_when_notify_on_success_true(self):
        config = _default_config(notify_on_success=True)
        send_alert = AsyncMock()
        v = _make_verifier(config=config, send_alert=send_alert)
        v._layer1_root_dir_cap = AsyncMock(return_value=LayerResult(ok=True))
        v._layer2_catalog_vs_cluster = AsyncMock(return_value=LayerResult(ok=True))
        v._layer3_test_restore = AsyncMock(return_value=LayerResult(ok=True))
        v._layer4_lifeboat = AsyncMock(return_value=LayerResult(ok=True))

        await v.run()

        send_alert.assert_awaited_once()
        self.assertEqual(send_alert.call_args.args[0], "info")

    async def test_success_alert_suppressed_when_notify_on_success_false(self):
        config = _default_config(notify_on_success=False)
        send_alert = AsyncMock()
        v = _make_verifier(config=config, send_alert=send_alert)
        v._layer1_root_dir_cap = AsyncMock(return_value=LayerResult(ok=True))
        v._layer2_catalog_vs_cluster = AsyncMock(return_value=LayerResult(ok=True))
        v._layer3_test_restore = AsyncMock(return_value=LayerResult(ok=True))
        v._layer4_lifeboat = AsyncMock(return_value=LayerResult(ok=True))

        await v.run()

        send_alert.assert_not_called()
