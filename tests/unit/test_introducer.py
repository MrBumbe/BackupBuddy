"""Unit tests for gatekeeper.tahoe.introducer.IntroducerNode."""

import asyncio
import os
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_furl_file(basedir: Path) -> None:
    """Create a minimal introducer directory structure with a FURL file."""
    (basedir / "private").mkdir(parents=True, exist_ok=True)
    (basedir / "tahoe.cfg").write_text("[node]\nnickname = test-introducer\n")
    (basedir / "private" / "introducer.furl").write_text(
        "pb://fakefurlhash@127.0.0.1:12345/fakeswissnum\n"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIntroducerNodeCreate(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.basedir = Path(self.tmpdir) / "introducer"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("gatekeeper.tahoe.introducer._find_tahoe", return_value="/fake/tahoe")
    @patch("subprocess.run")
    def test_create_calls_tahoe_create_introducer(self, mock_run, _mock_find):
        mock_run.return_value = MagicMock(returncode=0)
        from gatekeeper.tahoe.introducer import IntroducerNode
        node = IntroducerNode(str(self.basedir))
        node.create()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("create-introducer", args)
        self.assertIn(str(self.basedir), args)

    @patch("gatekeeper.tahoe.introducer._find_tahoe", return_value="/fake/tahoe")
    @patch("subprocess.run")
    def test_create_is_idempotent(self, mock_run, _mock_find):
        _make_furl_file(self.basedir)
        from gatekeeper.tahoe.introducer import IntroducerNode
        node = IntroducerNode(str(self.basedir))
        node.create()
        mock_run.assert_not_called()

    @patch("gatekeeper.tahoe.introducer._find_tahoe", return_value="/fake/tahoe")
    @patch("subprocess.run")
    def test_create_raises_on_failure(self, mock_run, _mock_find):
        mock_run.return_value = MagicMock(returncode=1, stderr="error: bad thing")
        from gatekeeper.tahoe.introducer import IntroducerNode
        node = IntroducerNode(str(self.basedir))
        with self.assertRaises(RuntimeError):
            node.create()


class TestIntroducerNodeFurl(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.basedir = Path(self.tmpdir) / "introducer"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("gatekeeper.tahoe.introducer._find_tahoe", return_value="/fake/tahoe")
    def test_furl_is_non_empty_string(self, _mock_find):
        _make_furl_file(self.basedir)
        from gatekeeper.tahoe.introducer import IntroducerNode
        node = IntroducerNode(str(self.basedir))
        furl = node._read_furl()
        self.assertIsInstance(furl, str)
        self.assertTrue(len(furl) > 0)

    @patch("gatekeeper.tahoe.introducer._find_tahoe", return_value="/fake/tahoe")
    def test_furl_raises_if_file_missing(self, _mock_find):
        self.basedir.mkdir(parents=True)
        from gatekeeper.tahoe.introducer import IntroducerNode
        node = IntroducerNode(str(self.basedir))
        with self.assertRaises(RuntimeError):
            node._read_furl()


class TestIntroducerNodeStartStop(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.basedir = Path(self.tmpdir) / "introducer"
        _make_furl_file(self.basedir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("gatekeeper.tahoe.introducer._find_tahoe", return_value="/fake/tahoe")
    async def test_start_returns_furl_string(self, _mock_find):
        from gatekeeper.tahoe.introducer import IntroducerNode

        fake_stderr = AsyncMock()
        fake_stderr.readline = AsyncMock(
            return_value=b"introducer running\n"
        )
        fake_process = MagicMock()
        fake_process.returncode = None
        fake_process.stderr = fake_stderr
        fake_process.wait = AsyncMock()
        fake_process.terminate = MagicMock()

        with patch(
            "asyncio.create_subprocess_exec", return_value=fake_process
        ):
            node = IntroducerNode(str(self.basedir))
            furl = await node.start()

        self.assertIsInstance(furl, str)
        self.assertTrue(len(furl) > 0)
        self.assertTrue(node.is_running())

    @patch("gatekeeper.tahoe.introducer._find_tahoe", return_value="/fake/tahoe")
    async def test_stop_terminates_process(self, _mock_find):
        from gatekeeper.tahoe.introducer import IntroducerNode

        fake_process = MagicMock()
        fake_process.returncode = None
        fake_process.terminate = MagicMock()
        fake_process.wait = AsyncMock(return_value=None)

        node = IntroducerNode(str(self.basedir))
        node._process = fake_process

        await node.stop()

        fake_process.terminate.assert_called_once()
        self.assertFalse(node.is_running())

    @patch("gatekeeper.tahoe.introducer._find_tahoe", return_value="/fake/tahoe")
    async def test_stop_is_idempotent_when_not_running(self, _mock_find):
        from gatekeeper.tahoe.introducer import IntroducerNode
        node = IntroducerNode(str(self.basedir))
        # Should not raise even when no process is running
        await node.stop()
        self.assertFalse(node.is_running())


if __name__ == "__main__":
    unittest.main()
