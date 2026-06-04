"""Unit tests for gatekeeper/gui/routes/onboarding.py.

Covers:
  - _cascade_join raises RuntimeError before calling initiate_join
    when state.storage_paths is empty (ISSUE-006 guard)
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
