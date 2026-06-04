"""Unit tests for the storage server cache mechanism (task 1.18.10).

Tests cover:
  - _parse_furl_locations(): extracts host/port from Foolscap FURLs
  - StorageNode._check_introducer_reachable(): TCP reachability check
  - StorageNode._count_cached_servers(): reads server count from servers.yaml
  - StorageNode.start() warning behaviour when introducer is unreachable
"""

import asyncio
import configparser
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

_FAKE_FURL = "pb://fakefurlhash@192.168.1.60:34267/introducer"
_TCP_FURL = "pb://fakefurlhash@tcp:192.168.1.60:34267/introducer"
_MULTI_FURL = "pb://fakefurlhash@192.168.1.60:34267,192.168.1.70:34268/introducer"


def _make_node_dir(basedir: Path, introducer_furl: str = "") -> None:
    basedir.mkdir(parents=True, exist_ok=True)
    cfg_lines = "[node]\nnickname = test\n"
    if introducer_furl:
        cfg_lines += f"\n[client]\nintroducer.furl = {introducer_furl}\n"
    (basedir / "tahoe.cfg").write_text(cfg_lines)


def _write_servers_yaml(basedir: Path, servers: dict) -> None:
    private = basedir / "private"
    private.mkdir(exist_ok=True)
    with open(private / "servers.yaml", "w") as f:
        yaml.dump({"storage": servers}, f)


class TestParseFurlLocations(unittest.TestCase):

    def setUp(self):
        # Import after patching _find_tahoe to avoid errors on machines without tahoe
        with patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe"):
            from gatekeeper.tahoe.storage_node import _parse_furl_locations
            self._parse = _parse_furl_locations

    def test_standard_host_port(self):
        result = self._parse(_FAKE_FURL)
        self.assertEqual(result, [("192.168.1.60", 34267)])

    def test_tcp_prefix_stripped(self):
        result = self._parse(_TCP_FURL)
        self.assertEqual(result, [("192.168.1.60", 34267)])

    def test_multiple_hints(self):
        result = self._parse(_MULTI_FURL)
        self.assertEqual(result, [
            ("192.168.1.60", 34267),
            ("192.168.1.70", 34268),
        ])

    def test_empty_string_returns_empty(self):
        self.assertEqual(self._parse(""), [])

    def test_malformed_furl_returns_empty(self):
        self.assertEqual(self._parse("pb://nodeid"), [])
        self.assertEqual(self._parse("not_a_furl"), [])

    def test_tailscale_ip(self):
        furl = "pb://fakehash@100.64.1.5:34267/storage"
        result = self._parse(furl)
        self.assertEqual(result, [("100.64.1.5", 34267)])


class TestCheckIntroducerReachable(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.basedir = Path(self.tmpdir) / "storage-node"
        self.storage_dir = Path(self.tmpdir) / "storage-pool"
        self.storage_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_node(self):
        with patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe"):
            from gatekeeper.tahoe.storage_node import StorageNode
            return StorageNode(str(self.basedir), str(self.storage_dir))

    def _cfg_with_furl(self, furl: str) -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        cfg.read_dict({"client": {"introducer.furl": furl}})
        return cfg

    async def test_returns_true_when_no_furl_configured(self):
        node = self._make_node()
        cfg = configparser.ConfigParser()
        result = await node._check_introducer_reachable(cfg)
        self.assertTrue(result)

    async def test_returns_true_when_furl_empty(self):
        node = self._make_node()
        cfg = self._cfg_with_furl("")
        result = await node._check_introducer_reachable(cfg)
        self.assertTrue(result)

    async def test_returns_true_when_unparseable_furl(self):
        node = self._make_node()
        cfg = self._cfg_with_furl("not_a_furl")
        result = await node._check_introducer_reachable(cfg)
        self.assertTrue(result)

    async def test_returns_true_when_connection_succeeds(self):
        node = self._make_node()
        cfg = self._cfg_with_furl(_FAKE_FURL)

        fake_writer = MagicMock()
        fake_writer.close = MagicMock()
        fake_writer.wait_closed = AsyncMock()

        with patch("asyncio.open_connection", return_value=(AsyncMock(), fake_writer)):
            result = await node._check_introducer_reachable(cfg)

        self.assertTrue(result)

    async def test_returns_false_when_all_hints_timeout(self):
        node = self._make_node()
        cfg = self._cfg_with_furl(_FAKE_FURL)

        async def _timeout(coro, timeout):
            coro.close()  # prevent unawaited-coroutine warning
            raise asyncio.TimeoutError()

        with patch("asyncio.wait_for", side_effect=_timeout):
            result = await node._check_introducer_reachable(cfg)

        self.assertFalse(result)

    async def test_returns_false_when_connection_refused(self):
        node = self._make_node()
        cfg = self._cfg_with_furl(_FAKE_FURL)

        async def _open_conn_refused(*_a, **_kw):
            raise OSError("Connection refused")

        with patch("asyncio.open_connection", side_effect=_open_conn_refused):
            result = await node._check_introducer_reachable(cfg)

        self.assertFalse(result)

    async def test_multi_hint_succeeds_on_second_hint(self):
        """Should return True when the first hint fails but the second succeeds."""
        node = self._make_node()
        cfg = self._cfg_with_furl(_MULTI_FURL)

        call_count = 0

        fake_writer = MagicMock()
        fake_writer.close = MagicMock()
        fake_writer.wait_closed = AsyncMock()

        async def _mixed(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("Connection refused")
            return (AsyncMock(), fake_writer)

        with patch("asyncio.open_connection", side_effect=_mixed):
            result = await node._check_introducer_reachable(cfg)

        self.assertTrue(result)
        self.assertEqual(call_count, 2)


class TestCountCachedServers(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.basedir = Path(self.tmpdir) / "storage-node"
        self.storage_dir = Path(self.tmpdir) / "storage-pool"
        self.storage_dir.mkdir()
        self.basedir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_node(self):
        with patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe"):
            from gatekeeper.tahoe.storage_node import StorageNode
            return StorageNode(str(self.basedir), str(self.storage_dir))

    def test_returns_zero_when_file_absent(self):
        node = self._make_node()
        self.assertEqual(node._count_cached_servers(), 0)

    def test_returns_count_for_populated_yaml(self):
        node = self._make_node()
        _write_servers_yaml(self.basedir, {
            "v0-server1": {"ann": {"anonymous-storage-FURL": "pb://a@host:1/s"}},
            "v0-server2": {"ann": {"anonymous-storage-FURL": "pb://b@host:2/s"}},
        })
        self.assertEqual(node._count_cached_servers(), 2)

    def test_returns_zero_for_corrupt_yaml(self):
        node = self._make_node()
        private = self.basedir / "private"
        private.mkdir(exist_ok=True)
        (private / "servers.yaml").write_text("not: valid: yaml: !!python/object/apply: evil")
        # corrupt yaml should not raise
        result = node._count_cached_servers()
        self.assertIsInstance(result, int)

    def test_returns_zero_for_empty_storage_section(self):
        node = self._make_node()
        _write_servers_yaml(self.basedir, {})
        self.assertEqual(node._count_cached_servers(), 0)


class TestStartWarnsWhenIntroducerUnreachable(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.basedir = Path(self.tmpdir) / "storage-node"
        self.storage_dir = Path(self.tmpdir) / "storage-pool"
        self.storage_dir.mkdir()
        _make_node_dir(self.basedir, introducer_furl=_FAKE_FURL)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    async def test_warning_logged_with_cache_count_when_servers_cached(self, _mf):
        from gatekeeper.tahoe.storage_node import StorageNode

        _write_servers_yaml(self.basedir, {
            "v0-s1": {"ann": {"anonymous-storage-FURL": "pb://a@h:1/s"}},
            "v0-s2": {"ann": {"anonymous-storage-FURL": "pb://b@h:2/s"}},
            "v0-s3": {"ann": {"anonymous-storage-FURL": "pb://c@h:3/s"}},
        })

        fake_process = MagicMock()
        fake_process.returncode = None
        fake_process.wait = AsyncMock()
        fake_process.terminate = MagicMock()

        fake_writer = MagicMock()
        fake_writer.close = MagicMock()
        fake_writer.wait_closed = AsyncMock()

        async def _conn_refused(*_a, **_kw):
            raise OSError("Connection refused")

        async def _conn_success_for_web_port(*args, **kwargs):
            # Only succeed for the web port (127.0.0.1), not for the introducer
            if args and "127.0.0.1" in str(args):
                return (AsyncMock(), fake_writer)
            raise OSError("Connection refused")

        node = StorageNode(str(self.basedir), str(self.storage_dir))

        with patch("asyncio.create_subprocess_exec", return_value=fake_process), \
             patch.object(node, "_check_introducer_reachable", AsyncMock(return_value=False)), \
             patch("asyncio.open_connection", return_value=(AsyncMock(), fake_writer)), \
             self.assertLogs("gatekeeper.tahoe.storage_node", level="WARNING") as log_cm:
            await node.start()

        warning_messages = [r for r in log_cm.output if "WARNING" in r]
        self.assertTrue(
            any("cached server list" in m and "3 servers" in m for m in warning_messages),
            f"Expected cache warning with count, got: {warning_messages}",
        )

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    async def test_warning_logged_when_no_cache_exists(self, _mf):
        from gatekeeper.tahoe.storage_node import StorageNode

        fake_process = MagicMock()
        fake_process.returncode = None
        fake_process.wait = AsyncMock()
        fake_process.terminate = MagicMock()

        fake_writer = MagicMock()
        fake_writer.close = MagicMock()
        fake_writer.wait_closed = AsyncMock()

        node = StorageNode(str(self.basedir), str(self.storage_dir))

        with patch("asyncio.create_subprocess_exec", return_value=fake_process), \
             patch.object(node, "_check_introducer_reachable", AsyncMock(return_value=False)), \
             patch("asyncio.open_connection", return_value=(AsyncMock(), fake_writer)), \
             self.assertLogs("gatekeeper.tahoe.storage_node", level="WARNING") as log_cm:
            await node.start()

        warning_messages = [r for r in log_cm.output if "WARNING" in r]
        self.assertTrue(
            any("no server cache" in m for m in warning_messages),
            f"Expected no-cache warning, got: {warning_messages}",
        )

    @patch("gatekeeper.tahoe.storage_node._find_tahoe", return_value="/fake/tahoe")
    async def test_no_warning_when_introducer_reachable(self, _mf):
        from gatekeeper.tahoe.storage_node import StorageNode

        fake_process = MagicMock()
        fake_process.returncode = None
        fake_process.wait = AsyncMock()
        fake_process.terminate = MagicMock()

        fake_writer = MagicMock()
        fake_writer.close = MagicMock()
        fake_writer.wait_closed = AsyncMock()

        node = StorageNode(str(self.basedir), str(self.storage_dir))

        import logging
        with patch("asyncio.create_subprocess_exec", return_value=fake_process), \
             patch.object(node, "_check_introducer_reachable", AsyncMock(return_value=True)), \
             patch("asyncio.open_connection", return_value=(AsyncMock(), fake_writer)):
            with self.assertLogs("gatekeeper.tahoe.storage_node", level="WARNING") as log_cm:
                # Trigger at least one log so assertLogs does not fail on empty
                logging.getLogger("gatekeeper.tahoe.storage_node").warning("sentinel")
                await node.start()

        cache_warnings = [
            r for r in log_cm.output
            if "cached server list" in r or "no server cache" in r
        ]
        self.assertEqual(cache_warnings, [], f"Unexpected cache warning: {cache_warnings}")


if __name__ == "__main__":
    unittest.main()
