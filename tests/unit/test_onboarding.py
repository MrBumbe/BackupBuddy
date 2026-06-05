"""Unit tests for gatekeeper/gui/routes/onboarding.py.

Covers:
  - _cascade_join raises RuntimeError before calling initiate_join
    when state.storage_paths is empty (ISSUE-006 guard)
  - _cascade_new_cluster regenerates invite when state carries a stale code
    that is absent from cluster.db (ISSUE-001 / 1.19.10 fix)
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from gatekeeper.gui.wizard_state import WizardState


class TestCascadeJoinStoragePathGuard(unittest.IsolatedAsyncioTestCase):
    """_cascade_join must validate storage_paths before consuming the invite."""

    async def test_empty_storage_paths_raises_before_initiate_join(self) -> None:
        from gatekeeper.gui.routes.onboarding import _cascade_join

        state = WizardState(
            role="join",
            node_name="test-node",
            node_display_name="Test Node",
            storage_paths=[],  # step 3 was skipped
            invite_code="apple-mango-3",
            gatekeeper_url="http://100.64.0.2:8080",
            profile="adaptive",
        )

        with patch(
            "gatekeeper.gui.routes.onboarding.initiate_join",
            new_callable=AsyncMock,
        ) as mock_join:
            with self.assertRaises(RuntimeError) as ctx:
                await _cascade_join(
                    data_dir=Path("/tmp/fake-data-dir"),
                    config_path=Path("/tmp/fake-config/gatekeeper.cfg"),
                    state=state,
                    smtp_password="",
                    webhook_url="",
                )

            mock_join.assert_not_called()
            self.assertIn("step 3", str(ctx.exception))


class TestCascadeNewClusterStaleInviteCode(unittest.IsolatedAsyncioTestCase):
    """Stale first_invite_code in state must not skip invite generation (ISSUE-001)."""

    async def test_stale_invite_code_regenerated_when_not_in_db(self) -> None:
        from gatekeeper.gui.routes.onboarding import _cascade_new_cluster

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            config_path = data_dir / "gatekeeper.cfg"
            lifeboat_key_path = data_dir / "lifeboat.key"

            # Pre-create artifacts so cascade skips Tahoe bootstrap and recovery kit.
            (data_dir / "root_dir.cap").write_text("URI:DIR2:fake-cap", encoding="utf-8")
            (data_dir / "recovery_kit.enc").write_bytes(b"fake-kit")
            # Pre-create lifeboat key so generate_key() is not called.
            lifeboat_key_path.write_bytes(b"fake-lifeboat-key")

            state = WizardState(
                role="new",
                node_name="test-node",
                node_display_name="Test Node",
                storage_paths=[tmpdir],
                profile="adaptive",
                # Stale invite code — present in state but NOT in the fresh cluster.db.
                first_invite_code="stale-bolt-7",
            )

            with (
                patch(
                    "gatekeeper.gui.routes.onboarding.DEFAULT_KEY_PATH",
                    lifeboat_key_path,
                ),
                patch(
                    "gatekeeper.gui.routes.onboarding._write_gatekeeper_cfg",
                ),
                patch(
                    "gatekeeper.gui.routes.onboarding.SecretsStore",
                    return_value=MagicMock(),
                ),
            ):
                await _cascade_new_cluster(
                    data_dir=data_dir,
                    config_path=config_path,
                    state=state,
                    smtp_password="",
                    webhook_url="",
                    passphrase="test-passphrase",
                )

            self.assertNotEqual(
                state.first_invite_code,
                "stale-bolt-7",
                "Cascade must generate a new invite when stale code is absent from cluster.db",
            )
            self.assertNotEqual(state.first_invite_code, "")

            # Verify the new code is actually present in cluster.db.
            from gatekeeper.db.cluster import ClusterDB

            db = ClusterDB(str(data_dir / "cluster.db"))
            row = db.get_invite(state.first_invite_code)
            db.close()
            self.assertIsNotNone(
                row,
                "New invite code must be persisted to cluster.db",
            )
