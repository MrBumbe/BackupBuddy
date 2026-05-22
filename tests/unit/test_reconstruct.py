"""Unit tests for gatekeeper.restore.reconstruct and related changes."""

import asyncio
import os
import tempfile
import time
import unittest
from base64 import b64encode
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gatekeeper.fragmenter.fragmenter import derive_metadata_key, _encrypt_tag
from gatekeeper.restore.reconstruct import (
    _decrypt_tag,
    reconstruct_catalog,
    _UNKNOWN_SENTINEL,
    _UNKNOWN_SHA256,
)
from gatekeeper.tahoe.client import TahoeError


# ── Helpers ───────────────────────────────────────────────────────────────────

_ROOT_DIR_CAP = "URI:DIR2:testcap12345678901234567890"
_METADATA_KEY = derive_metadata_key(_ROOT_DIR_CAP)


def _make_tag(original_path: str, agent: str, backed_up_at: float) -> dict:
    """Build a realistic metadata tag as the fragmenter would write it."""
    return {
        "original_path_enc": _encrypt_tag(original_path, _METADATA_KEY),
        "agent_enc": _encrypt_tag(agent, _METADATA_KEY),
        "backed_up_at": backed_up_at,
    }


def _make_entry(
    name: str,
    file_ref: str,
    size: int,
    tag: dict,
) -> dict:
    return {"name": name, "file_ref": file_ref, "metadata": tag, "size": size}


def _make_tahoe(entries: list[dict]) -> MagicMock:
    tahoe = MagicMock()
    tahoe.ls_with_metadata = AsyncMock(return_value=entries)
    return tahoe


def _make_catalog() -> MagicMock:
    catalog = MagicMock()
    catalog.insert_file = MagicMock()
    return catalog


# ── _decrypt_tag ──────────────────────────────────────────────────────────────

class TestDecryptTag(unittest.TestCase):

    def test_round_trip(self):
        key = os.urandom(32)
        plaintext = "/home/user/documents/report.pdf"
        b64 = _encrypt_tag(plaintext, key)
        self.assertEqual(_decrypt_tag(b64, key), plaintext)

    def test_wrong_key_raises(self):
        key = os.urandom(32)
        wrong_key = os.urandom(32)
        b64 = _encrypt_tag("hello", key)
        with self.assertRaises(Exception):
            _decrypt_tag(b64, wrong_key)

    def test_tampered_ciphertext_raises(self):
        key = os.urandom(32)
        import base64
        raw = base64.b64decode(_encrypt_tag("hello", key))
        tampered = raw[:12] + bytes([raw[12] ^ 0xFF]) + raw[13:]
        with self.assertRaises(Exception):
            _decrypt_tag(b64encode(tampered).decode(), key)


# ── reconstruct_catalog ───────────────────────────────────────────────────────

class TestReconstructCatalog(unittest.IsolatedAsyncioTestCase):

    async def test_empty_directory_returns_zero(self):
        catalog = _make_catalog()
        tahoe = _make_tahoe([])

        count = await reconstruct_catalog(
            _ROOT_DIR_CAP, catalog=catalog, tahoe=tahoe,
        )

        self.assertEqual(count, 0)
        catalog.insert_file.assert_not_called()

    async def test_single_file_with_valid_metadata(self):
        backed_up_at = time.time()
        tag = _make_tag("/home/alice/photo.jpg", "agent-01", backed_up_at)
        entries = [_make_entry("abc123", "URI:CHK:file1", 1024, tag)]

        catalog = _make_catalog()
        tahoe = _make_tahoe(entries)

        count = await reconstruct_catalog(
            _ROOT_DIR_CAP, catalog=catalog, tahoe=tahoe,
        )

        self.assertEqual(count, 1)
        catalog.insert_file.assert_called_once()
        kwargs = catalog.insert_file.call_args.kwargs
        self.assertEqual(kwargs["original_path"], "/home/alice/photo.jpg")
        self.assertEqual(kwargs["agent"], "agent-01")
        self.assertAlmostEqual(kwargs["backed_up_at"], backed_up_at, places=2)
        self.assertEqual(kwargs["cap"], "URI:CHK:file1")
        self.assertEqual(kwargs["sha256"], _UNKNOWN_SHA256)
        self.assertEqual(kwargs["profile"], _UNKNOWN_SENTINEL)
        self.assertEqual(kwargs["k"], 0)
        self.assertEqual(kwargs["n"], 0)
        self.assertEqual(kwargs["size_bytes"], 1024)

    async def test_multiple_files_all_inserted(self):
        now = time.time()
        entries = [
            _make_entry("e1", "URI:CHK:1", 100,
                        _make_tag("/data/a.txt", "agent-01", now)),
            _make_entry("e2", "URI:CHK:2", 200,
                        _make_tag("/data/b.txt", "agent-01", now)),
            _make_entry("e3", "URI:CHK:3", 300,
                        _make_tag("/data/c.txt", "agent-02", now)),
        ]
        catalog = _make_catalog()
        tahoe = _make_tahoe(entries)

        count = await reconstruct_catalog(
            _ROOT_DIR_CAP, catalog=catalog, tahoe=tahoe,
        )

        self.assertEqual(count, 3)
        self.assertEqual(catalog.insert_file.call_count, 3)

    async def test_missing_metadata_inserts_with_none_path(self):
        entries = [_make_entry("notagentry", "URI:CHK:notag", 512, {})]
        catalog = _make_catalog()
        tahoe = _make_tahoe(entries)

        count = await reconstruct_catalog(
            _ROOT_DIR_CAP, catalog=catalog, tahoe=tahoe,
        )

        self.assertEqual(count, 1)
        kwargs = catalog.insert_file.call_args.kwargs
        self.assertIsNone(kwargs["original_path"])
        self.assertEqual(kwargs["agent"], _UNKNOWN_SENTINEL)
        self.assertEqual(kwargs["backed_up_at"], 0.0)

    async def test_corrupted_metadata_inserts_with_none_path(self):
        bad_tag = {
            "original_path_enc": "not-valid-base64!!!",
            "agent_enc": "also-bad",
            "backed_up_at": 1234567890.0,
        }
        entries = [_make_entry("bad", "URI:CHK:bad", 256, bad_tag)]
        catalog = _make_catalog()
        tahoe = _make_tahoe(entries)

        count = await reconstruct_catalog(
            _ROOT_DIR_CAP, catalog=catalog, tahoe=tahoe,
        )

        self.assertEqual(count, 1)
        kwargs = catalog.insert_file.call_args.kwargs
        self.assertIsNone(kwargs["original_path"])

    async def test_wrong_key_metadata_inserts_with_none_path(self):
        wrong_cap = "URI:DIR2:wrongcap12345678901234567890"
        wrong_key = derive_metadata_key(wrong_cap)
        bad_tag = {
            "original_path_enc": _encrypt_tag("/secret/path", wrong_key),
            "agent_enc": _encrypt_tag("agent-01", wrong_key),
            "backed_up_at": 1234567890.0,
        }
        entries = [_make_entry("wrongkey", "URI:CHK:wk", 128, bad_tag)]
        catalog = _make_catalog()
        tahoe = _make_tahoe(entries)

        count = await reconstruct_catalog(
            _ROOT_DIR_CAP, catalog=catalog, tahoe=tahoe,
        )

        self.assertEqual(count, 1)
        kwargs = catalog.insert_file.call_args.kwargs
        self.assertIsNone(kwargs["original_path"])

    async def test_partial_metadata_path_decrypted_agent_fails(self):
        right_key = _METADATA_KEY
        wrong_cap = "URI:DIR2:wrongcap12345678901234567890"
        wrong_key = derive_metadata_key(wrong_cap)

        # original_path_enc uses correct key, agent_enc uses wrong key
        partial_tag = {
            "original_path_enc": _encrypt_tag("/home/user/doc.pdf", right_key),
            "agent_enc": _encrypt_tag("agent-01", wrong_key),
            "backed_up_at": 1234567890.0,
        }
        entries = [_make_entry("partial", "URI:CHK:partial", 64, partial_tag)]
        catalog = _make_catalog()
        tahoe = _make_tahoe(entries)

        count = await reconstruct_catalog(
            _ROOT_DIR_CAP, catalog=catalog, tahoe=tahoe,
        )

        self.assertEqual(count, 1)
        kwargs = catalog.insert_file.call_args.kwargs
        # Whole metadata treated as unreadable on any exception
        self.assertIsNone(kwargs["original_path"])

    async def test_progress_queue_receives_one_update_per_file(self):
        now = time.time()
        entries = [
            _make_entry("a", "URI:1", 10, _make_tag("/a", "ag", now)),
            _make_entry("b", "URI:2", 20, _make_tag("/b", "ag", now)),
        ]
        catalog = _make_catalog()
        tahoe = _make_tahoe(entries)

        q: asyncio.Queue = asyncio.Queue()
        count = await reconstruct_catalog(
            _ROOT_DIR_CAP, catalog=catalog, tahoe=tahoe, progress_queue=q,
        )

        self.assertEqual(count, 2)
        self.assertEqual(q.qsize(), 2)
        self.assertEqual(q.get_nowait(), {"files_processed": 1})
        self.assertEqual(q.get_nowait(), {"files_processed": 2})

    async def test_no_progress_queue_does_not_crash(self):
        entries = [
            _make_entry("x", "URI:X", 5, _make_tag("/x", "ag", 1.0)),
        ]
        catalog = _make_catalog()
        tahoe = _make_tahoe(entries)

        count = await reconstruct_catalog(
            _ROOT_DIR_CAP, catalog=catalog, tahoe=tahoe, progress_queue=None,
        )
        self.assertEqual(count, 1)

    async def test_tahoe_error_propagates(self):
        catalog = _make_catalog()
        tahoe = MagicMock()
        tahoe.ls_with_metadata = AsyncMock(side_effect=TahoeError("grid unreachable"))

        with self.assertRaises(TahoeError):
            await reconstruct_catalog(_ROOT_DIR_CAP, catalog=catalog, tahoe=tahoe)

        catalog.insert_file.assert_not_called()

    async def test_size_bytes_from_entry(self):
        now = time.time()
        entries = [_make_entry("sz", "URI:SZ", 98765, _make_tag("/f", "ag", now))]
        catalog = _make_catalog()
        tahoe = _make_tahoe(entries)

        await reconstruct_catalog(_ROOT_DIR_CAP, catalog=catalog, tahoe=tahoe)

        kwargs = catalog.insert_file.call_args.kwargs
        self.assertEqual(kwargs["size_bytes"], 98765)


# ── restore._download_with_retry: empty sha256 path ──────────────────────────

class TestDownloadWithRetryEmptySha256(unittest.IsolatedAsyncioTestCase):
    """Verifies the sentinel sha256="" path added to support reconstructed records."""

    async def test_empty_sha256_skips_hash_check(self):
        from gatekeeper.restore.restore import _download_with_retry
        from unittest.mock import AsyncMock, MagicMock

        actual_sha = "a" * 64  # any 64-char hex string

        tahoe = MagicMock()
        tahoe.download = AsyncMock(return_value=actual_sha)

        with tempfile.TemporaryDirectory() as d:
            result = await _download_with_retry(
                tahoe, "URI:CHK:ref", os.path.join(d, "f"),
                "", "agent-01", None,
            )

        self.assertEqual(result, actual_sha)
        # Only one download attempt — no retry needed when skipping hash check
        tahoe.download.assert_called_once()

    async def test_empty_sha256_tahoe_error_propagates(self):
        from gatekeeper.restore.restore import _download_with_retry
        from gatekeeper.tahoe.client import TahoeError

        tahoe = MagicMock()
        tahoe.download = AsyncMock(side_effect=TahoeError("network error"))

        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(TahoeError):
                await _download_with_retry(
                    tahoe, "URI:CHK:ref", os.path.join(d, "f"),
                    "", "agent-01", None,
                )


# ── ls_with_metadata ──────────────────────────────────────────────────────────

class TestLsWithMetadata(unittest.IsolatedAsyncioTestCase):
    """Unit tests for TahoeClient.ls_with_metadata()."""

    def _make_tahoe_client(self, json_response: dict) -> object:
        from unittest.mock import AsyncMock, MagicMock, patch
        from gatekeeper.tahoe.client import TahoeClient

        client = TahoeClient.__new__(TahoeClient)
        mock_http = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = json_response
        mock_http.get = AsyncMock(return_value=mock_response)
        client._http = mock_http
        client._node_url = "http://127.0.0.1:3456"
        return client

    async def test_returns_only_filenodes(self):
        json_data = ["dirnode", {"children": {
            "file.txt": ["filenode", {
                "ro_uri": "URI:CHK:abc",
                "metadata": {"x": 1},
                "size": 42,
            }],
            "subdir": ["dirnode", {
                "rw_uri": "URI:DIR2:xyz",
                "metadata": {},
            }],
        }}]
        client = self._make_tahoe_client(json_data)
        result = await client.ls_with_metadata("URI:DIR2:root")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "file.txt")
        self.assertEqual(result[0]["file_ref"], "URI:CHK:abc")
        self.assertEqual(result[0]["metadata"], {"x": 1})
        self.assertEqual(result[0]["size"], 42)

    async def test_missing_metadata_defaults_to_empty_dict(self):
        json_data = ["dirnode", {"children": {
            "nometa.txt": ["filenode", {
                "ro_uri": "URI:CHK:nm",
                "size": 10,
            }],
        }}]
        client = self._make_tahoe_client(json_data)
        result = await client.ls_with_metadata("URI:DIR2:root")

        self.assertEqual(result[0]["metadata"], {})

    async def test_empty_directory_returns_empty_list(self):
        json_data = ["dirnode", {"children": {}}]
        client = self._make_tahoe_client(json_data)
        result = await client.ls_with_metadata("URI:DIR2:root")

        self.assertEqual(result, [])

    async def test_not_a_dirnode_raises_tahoe_error(self):
        from gatekeeper.tahoe.client import TahoeError
        json_data = ["filenode", {}]
        client = self._make_tahoe_client(json_data)

        with self.assertRaises(TahoeError):
            await client.ls_with_metadata("URI:CHK:notadir")

    async def test_http_error_raises_tahoe_error(self):
        from gatekeeper.tahoe.client import TahoeClient, TahoeError
        client = TahoeClient.__new__(TahoeClient)
        mock_http = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "not found"
        mock_http.get = AsyncMock(return_value=mock_response)
        client._http = mock_http
        client._node_url = "http://127.0.0.1:3456"

        with self.assertRaises(TahoeError):
            await client.ls_with_metadata("URI:DIR2:missing")


if __name__ == "__main__":
    unittest.main()
