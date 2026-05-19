"""Unit tests for gatekeeper.tahoe.storage_node.StorageNode."""

import configparser
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_FAKE_FURL = "pb://fakefurlhash@127.0.0.1:12345/fakeswissnum"


def _make_node_dir(basedir: Path) -> None:
    """Create a minimal existing node directory with a tahoe.cfg."""
    basedir.mkdir(parents=True, exist_ok=True)
    (basedir / "tahoe.cfg").write_text("[node]\nnickname = test-storage-node\n")


class TestStorageNodeCreate(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.basedir = Path(self.tmpdir) / "storage-node"
        self.storage_dir = Path(self.tmpdir) / "storage-pool"
        self.storage_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    @patch("subprocess.run")
    def test_create_calls_tahoe_create_node(self, mock_run, _mock_find):
        mock_run.return_value = MagicMock(returncode=0)
        from gatekeeper.tahoe.storage_node import StorageNode
        node = StorageNode(str(self.basedir), str(self.storage_dir))
        # tahoe.cfg does not exist yet, so create-node should be called
        node.create(_FAKE_FURL)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("create-node", args)
        self.assertIn(str(self.basedir), args)

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    @patch("subprocess.run")
    def test_create_is_idempotent_when_node_exists(self, mock_run, _mock_find):
        """create() must not call tahoe create-node if tahoe.cfg already exists."""
        _make_node_dir(self.basedir)
        from gatekeeper.tahoe.storage_node import StorageNode
        node = StorageNode(str(self.basedir), str(self.storage_dir))
        node.create(_FAKE_FURL)
        mock_run.assert_not_called()

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    @patch("subprocess.run")
    def test_create_raises_on_tahoe_failure(self, mock_run, _mock_find):
        mock_run.return_value = MagicMock(returncode=1)
        from gatekeeper.tahoe.storage_node import StorageNode
        node = StorageNode(str(self.basedir), str(self.storage_dir))
        with self.assertRaises(RuntimeError):
            node.create(_FAKE_FURL)


class TestStorageNodeConfigure(unittest.TestCase):
    """
    Verify that _configure() writes all required settings to tahoe.cfg.
    These are the settings Tahoe reads at startup to connect and serve storage.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.basedir = Path(self.tmpdir) / "storage-node"
        self.storage_dir = Path(self.tmpdir) / "storage-pool"
        self.storage_dir.mkdir()
        _make_node_dir(self.basedir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _read_cfg(self) -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        cfg.read(str(self.basedir / "tahoe.cfg"))
        return cfg

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    def test_configure_writes_introducer_furl(self, _mock_find):
        from gatekeeper.tahoe.storage_node import StorageNode
        StorageNode(str(self.basedir), str(self.storage_dir)).create(_FAKE_FURL)
        # idempotent path — _configure was called; now verify the written value
        node = StorageNode(str(self.basedir), str(self.storage_dir))
        node._configure(_FAKE_FURL)
        self.assertEqual(self._read_cfg().get("client", "introducer.furl"), _FAKE_FURL)

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    def test_configure_enables_storage(self, _mock_find):
        from gatekeeper.tahoe.storage_node import StorageNode
        StorageNode(str(self.basedir), str(self.storage_dir))._configure(_FAKE_FURL)
        self.assertEqual(self._read_cfg().get("storage", "enabled"), "true")

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    def test_configure_sets_storage_dir_to_absolute_path(self, _mock_find):
        from gatekeeper.tahoe.storage_node import StorageNode
        StorageNode(str(self.basedir), str(self.storage_dir))._configure(_FAKE_FURL)
        written = self._read_cfg().get("storage", "storage_dir")
        self.assertTrue(written.startswith("/") or (len(written) > 1 and written[1] == ":"),
                        "storage_dir must be an absolute path")
        self.assertEqual(written, str(self.storage_dir.resolve()))

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    def test_configure_sets_reserved_space(self, _mock_find):
        quota = 5 * 1024 ** 3  # 5 GB in bytes
        from gatekeeper.tahoe.storage_node import StorageNode
        StorageNode(str(self.basedir), str(self.storage_dir),
                    reserved_space=quota)._configure(_FAKE_FURL)
        self.assertEqual(int(self._read_cfg().get("storage", "reserved_space")), quota)

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    def test_configure_sets_node_nickname(self, _mock_find):
        from gatekeeper.tahoe.storage_node import StorageNode
        StorageNode(str(self.basedir), str(self.storage_dir),
                    nickname="anders-node")._configure(_FAKE_FURL)
        self.assertEqual(self._read_cfg().get("node", "nickname"), "anders-node")

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    def test_configure_sets_web_port(self, _mock_find):
        from gatekeeper.tahoe.storage_node import StorageNode
        StorageNode(str(self.basedir), str(self.storage_dir),
                    web_port=3456)._configure(_FAKE_FURL)
        written = self._read_cfg().get("node", "web.port")
        self.assertIn("3456", written)
        self.assertIn("127.0.0.1", written)

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    def test_configure_is_idempotent(self, _mock_find):
        """Calling _configure twice must produce consistent config."""
        from gatekeeper.tahoe.storage_node import StorageNode
        node = StorageNode(str(self.basedir), str(self.storage_dir))
        node._configure(_FAKE_FURL)
        node._configure(_FAKE_FURL)
        cfg = self._read_cfg()
        self.assertEqual(cfg.get("client", "introducer.furl"), _FAKE_FURL)
        self.assertEqual(cfg.get("storage", "enabled"), "true")


class TestStorageNodeStartStop(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.basedir = Path(self.tmpdir) / "storage-node"
        self.storage_dir = Path(self.tmpdir) / "storage-pool"
        self.storage_dir.mkdir()
        _make_node_dir(self.basedir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    async def test_start_returns_when_client_running_logged(self, _mock_find):
        """start() must succeed when Tahoe emits 'client running'."""
        from gatekeeper.tahoe.storage_node import StorageNode

        fake_stderr = AsyncMock()
        fake_stderr.readline = AsyncMock(return_value=b"client running\n")
        fake_process = MagicMock()
        fake_process.returncode = None
        fake_process.stderr = fake_stderr
        fake_process.wait = AsyncMock()
        fake_process.terminate = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=fake_process):
            node = StorageNode(str(self.basedir), str(self.storage_dir))
            await node.start()

        self.assertTrue(node.is_running())

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    async def test_start_raises_if_not_created(self, _mock_find):
        """start() must raise RuntimeError when tahoe.cfg does not exist."""
        from gatekeeper.tahoe.storage_node import StorageNode
        empty = Path(self.tmpdir) / "empty-node"
        empty.mkdir()
        node = StorageNode(str(empty), str(self.storage_dir))
        with self.assertRaises(RuntimeError):
            await node.start()

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    async def test_start_is_idempotent_when_already_running(self, _mock_find):
        """start() must not spawn a second subprocess if the node is running."""
        from gatekeeper.tahoe.storage_node import StorageNode

        fake_process = MagicMock()
        fake_process.returncode = None

        node = StorageNode(str(self.basedir), str(self.storage_dir))
        node._process = fake_process

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            await node.start()
            mock_exec.assert_not_called()

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    async def test_stop_terminates_process(self, _mock_find):
        from gatekeeper.tahoe.storage_node import StorageNode

        fake_process = MagicMock()
        fake_process.returncode = None
        fake_process.terminate = MagicMock()
        fake_process.wait = AsyncMock(return_value=None)

        node = StorageNode(str(self.basedir), str(self.storage_dir))
        node._process = fake_process
        await node.stop()

        fake_process.terminate.assert_called_once()
        self.assertFalse(node.is_running())

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    async def test_stop_is_idempotent_when_not_running(self, _mock_find):
        from gatekeeper.tahoe.storage_node import StorageNode
        node = StorageNode(str(self.basedir), str(self.storage_dir))
        await node.stop()
        self.assertFalse(node.is_running())

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    async def test_stop_kills_on_timeout(self, _mock_find):
        """stop() must kill the process if terminate() does not produce clean exit."""
        import asyncio
        from gatekeeper.tahoe.storage_node import StorageNode

        fake_process = MagicMock()
        fake_process.returncode = None
        fake_process.terminate = MagicMock()
        fake_process.kill = MagicMock()
        fake_process.wait = AsyncMock(return_value=None)

        node = StorageNode(str(self.basedir), str(self.storage_dir))
        node._process = fake_process

        async def simulate_timeout(coro, timeout):
            # Close the coroutine properly to avoid unawaited-coroutine warnings.
            coro.close()
            raise asyncio.TimeoutError()

        with patch("asyncio.wait_for", new=simulate_timeout):
            await node.stop()

        fake_process.kill.assert_called_once()
        self.assertFalse(node.is_running())


class TestStorageNodeIsRunning(unittest.TestCase):

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    def test_is_running_false_with_no_process(self, _mock_find):
        from gatekeeper.tahoe.storage_node import StorageNode
        tmpdir = tempfile.mkdtemp()
        try:
            node = StorageNode(tmpdir, tmpdir)
            self.assertFalse(node.is_running())
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    def test_is_running_false_when_process_has_exited(self, _mock_find):
        from gatekeeper.tahoe.storage_node import StorageNode
        tmpdir = tempfile.mkdtemp()
        try:
            fake_process = MagicMock()
            fake_process.returncode = 0  # process exited
            node = StorageNode(tmpdir, tmpdir)
            node._process = fake_process
            self.assertFalse(node.is_running())
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    def test_is_running_true_when_process_is_alive(self, _mock_find):
        from gatekeeper.tahoe.storage_node import StorageNode
        tmpdir = tempfile.mkdtemp()
        try:
            fake_process = MagicMock()
            fake_process.returncode = None  # still running
            node = StorageNode(tmpdir, tmpdir)
            node._process = fake_process
            self.assertTrue(node.is_running())
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
