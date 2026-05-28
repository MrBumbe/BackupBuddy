"""Unit tests for gatekeeper.tahoe.client.TahoeClient."""

import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

_NODE_URL = "http://127.0.0.1:3456"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int, text: str = "", json_body=None) -> MagicMock:
    """Build a fake httpx.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_body is not None:
        resp.json = MagicMock(return_value=json_body)
    return resp


def _make_stream_response(status_code: int, content: bytes) -> MagicMock:
    """Build a fake streaming response for download tests."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""

    async def _aiter_bytes(chunk_size=65536):
        offset = 0
        while offset < len(content):
            yield content[offset:offset + chunk_size]
            offset += chunk_size

    resp.aiter_bytes = _aiter_bytes

    class _FakeStream:
        async def __aenter__(self_inner):
            return resp

        async def __aexit__(self_inner, *_):
            pass

    return _FakeStream()


# ---------------------------------------------------------------------------
# Upload tests
# ---------------------------------------------------------------------------

class TestTahoeClientUpload(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.test_file = Path(self.tmpdir) / "test.bin"
        self.test_file.write_bytes(b"hello tahoe")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def test_upload_returns_ref(self):
        from gatekeeper.tahoe.client import TahoeClient

        fake_ref = "URI:CHK:abc123"
        fake_resp = _make_response(200, text=fake_ref)

        async with TahoeClient(_NODE_URL) as client:
            client._http.put = AsyncMock(return_value=fake_resp)
            ref = await client.upload(str(self.test_file))

        self.assertEqual(ref, fake_ref)

    async def test_upload_calls_put_uri(self):
        from gatekeeper.tahoe.client import TahoeClient

        fake_resp = _make_response(200, text="URI:CHK:xyz")

        async with TahoeClient(_NODE_URL) as client:
            client._http.put = AsyncMock(return_value=fake_resp)
            await client.upload(str(self.test_file))

        call_url = client._http.put.call_args[0][0]
        self.assertTrue(call_url.endswith("/uri"), f"Expected PUT /uri, got {call_url!r}")

    async def test_upload_raises_on_error(self):
        from gatekeeper.tahoe.client import TahoeClient, TahoeError

        fake_resp = _make_response(500, text="server error")

        async with TahoeClient(_NODE_URL) as client:
            client._http.put = AsyncMock(return_value=fake_resp)
            with self.assertRaises(TahoeError):
                await client.upload(str(self.test_file))

    async def test_upload_accepts_metadata_without_error(self):
        """metadata param is accepted at this layer even though it's not stored here."""
        from gatekeeper.tahoe.client import TahoeClient

        fake_resp = _make_response(200, text="URI:CHK:abc")

        async with TahoeClient(_NODE_URL) as client:
            client._http.put = AsyncMock(return_value=fake_resp)
            ref = await client.upload(str(self.test_file), metadata={"path": "/home/user/file.txt"})

        self.assertEqual(ref, "URI:CHK:abc")


# ---------------------------------------------------------------------------
# Download tests
# ---------------------------------------------------------------------------

class TestTahoeClientDownload(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def test_download_writes_content_and_returns_sha256(self):
        from gatekeeper.tahoe.client import TahoeClient

        content = b"restored file content"
        expected_hash = hashlib.sha256(content).hexdigest()
        dest = str(Path(self.tmpdir) / "out.bin")

        fake_stream = _make_stream_response(200, content)

        async with TahoeClient(_NODE_URL) as client:
            client._http.stream = MagicMock(return_value=fake_stream)
            digest = await client.download("URI:CHK:abc123", dest)

        self.assertEqual(digest, expected_hash)
        self.assertEqual(Path(dest).read_bytes(), content)

    async def test_download_raises_on_404(self):
        from gatekeeper.tahoe.client import TahoeClient, TahoeError

        fake_stream = _make_stream_response(404, b"")

        async with TahoeClient(_NODE_URL) as client:
            client._http.stream = MagicMock(return_value=fake_stream)
            with self.assertRaises(TahoeError):
                await client.download("URI:CHK:missing", str(Path(self.tmpdir) / "x"))

    async def test_download_url_encodes_ref(self):
        """Refs containing colons and slashes must be URL-encoded in the path."""
        from gatekeeper.tahoe.client import TahoeClient

        content = b"data"
        dest = str(Path(self.tmpdir) / "out.bin")
        fake_stream = _make_stream_response(200, content)

        async with TahoeClient(_NODE_URL) as client:
            client._http.stream = MagicMock(return_value=fake_stream)
            await client.download("URI:CHK:abc/def:123", dest)

        call_url = client._http.stream.call_args[0][1]
        self.assertNotIn("URI:CHK:abc/def:123", call_url,
                         "Raw ref must not appear un-encoded in the URL")


# ---------------------------------------------------------------------------
# Upload → download round-trip (hash match)
# ---------------------------------------------------------------------------

class TestTahoeClientRoundTrip(unittest.IsolatedAsyncioTestCase):
    """
    Upload a file, then download it — the SHA-256 must match.
    Mocks the HTTP layer so no real Tahoe node is required.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.src = Path(self.tmpdir) / "source.bin"
        self.src.write_bytes(b"important backup data\n" * 500)
        self.dest = str(Path(self.tmpdir) / "restored.bin")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def test_upload_download_hash_match(self):
        from gatekeeper.tahoe.client import TahoeClient

        content = self.src.read_bytes()
        expected_hash = hashlib.sha256(content).hexdigest()
        fake_ref = "URI:CHK:roundtrip123"

        upload_resp = _make_response(200, text=fake_ref)
        download_stream = _make_stream_response(200, content)

        async with TahoeClient(_NODE_URL) as client:
            client._http.put = AsyncMock(return_value=upload_resp)
            client._http.stream = MagicMock(return_value=download_stream)

            ref = await client.upload(str(self.src))
            digest = await client.download(ref, self.dest)

        self.assertEqual(ref, fake_ref)
        self.assertEqual(digest, expected_hash)
        self.assertEqual(Path(self.dest).read_bytes(), content)


# ---------------------------------------------------------------------------
# ls tests
# ---------------------------------------------------------------------------

class TestTahoeClientLs(unittest.IsolatedAsyncioTestCase):

    async def test_ls_returns_name_ref_pairs(self):
        from gatekeeper.tahoe.client import TahoeClient

        dir_json = [
            "dirnode",
            {
                "children": {
                    "notes.txt": ["filenode", {"ro_uri": "URI:CHK:file1", "metadata": {}}],
                    "subdir": ["dirnode", {"rw_uri": "URI:DIR2:dir1", "metadata": {}}],
                }
            },
        ]
        fake_resp = _make_response(200, json_body=dir_json)

        async with TahoeClient(_NODE_URL) as client:
            client._http.get = AsyncMock(return_value=fake_resp)
            items = await client.ls("URI:DIR2:rootref")

        names = {name for name, _ in items}
        self.assertIn("notes.txt", names)
        self.assertIn("subdir", names)
        refs = {ref for _, ref in items}
        self.assertIn("URI:CHK:file1", refs)
        self.assertIn("URI:DIR2:dir1", refs)

    async def test_ls_raises_on_non_dirnode(self):
        from gatekeeper.tahoe.client import TahoeClient, TahoeError

        fake_resp = _make_response(200, json_body=["filenode", {}])

        async with TahoeClient(_NODE_URL) as client:
            client._http.get = AsyncMock(return_value=fake_resp)
            with self.assertRaises(TahoeError):
                await client.ls("URI:CHK:file_not_dir")

    async def test_ls_raises_on_http_error(self):
        from gatekeeper.tahoe.client import TahoeClient, TahoeError

        fake_resp = _make_response(410, text="gone")

        async with TahoeClient(_NODE_URL) as client:
            client._http.get = AsyncMock(return_value=fake_resp)
            with self.assertRaises(TahoeError):
                await client.ls("URI:DIR2:bad")

    async def test_ls_empty_directory(self):
        from gatekeeper.tahoe.client import TahoeClient

        dir_json = ["dirnode", {"children": {}}]
        fake_resp = _make_response(200, json_body=dir_json)

        async with TahoeClient(_NODE_URL) as client:
            client._http.get = AsyncMock(return_value=fake_resp)
            items = await client.ls("URI:DIR2:empty")

        self.assertEqual(items, [])


# ---------------------------------------------------------------------------
# mkdir tests
# ---------------------------------------------------------------------------

class TestTahoeClientMkdir(unittest.IsolatedAsyncioTestCase):

    async def test_mkdir_returns_ref(self):
        from gatekeeper.tahoe.client import TahoeClient

        fake_ref = "URI:DIR2:newdir123"
        fake_resp = _make_response(200, text=fake_ref)

        async with TahoeClient(_NODE_URL) as client:
            client._http.post = AsyncMock(return_value=fake_resp)
            ref = await client.mkdir()

        self.assertEqual(ref, fake_ref)

    async def test_mkdir_posts_to_uri_with_t_mkdir(self):
        from gatekeeper.tahoe.client import TahoeClient

        fake_resp = _make_response(200, text="URI:DIR2:x")

        async with TahoeClient(_NODE_URL) as client:
            client._http.post = AsyncMock(return_value=fake_resp)
            await client.mkdir()

        call_kwargs = client._http.post.call_args
        call_url = call_kwargs[0][0]
        call_params = call_kwargs[1].get("params", {})
        self.assertTrue(call_url.endswith("/uri"))
        self.assertEqual(call_params.get("t"), "mkdir")

    async def test_mkdir_raises_on_error(self):
        from gatekeeper.tahoe.client import TahoeClient, TahoeError

        fake_resp = _make_response(500, text="internal error")

        async with TahoeClient(_NODE_URL) as client:
            client._http.post = AsyncMock(return_value=fake_resp)
            with self.assertRaises(TahoeError):
                await client.mkdir()


# ---------------------------------------------------------------------------
# check_cap tests
# ---------------------------------------------------------------------------

class TestTahoeClientCheckCap(unittest.IsolatedAsyncioTestCase):

    async def test_healthy_file_returns_real_share_counts(self):
        from gatekeeper.tahoe.client import TahoeClient

        check_json = {
            "storage-index": "aabbcc",
            "summary": "Healthy",
            "results": {
                "healthy": True,
                "count-shares-good": 5,
                "count-shares-needed": 3,
                "count-shares-expected": 5,
            },
        }
        fake_resp = _make_response(200, json_body=check_json)

        async with TahoeClient(_NODE_URL) as client:
            client._http.post = AsyncMock(return_value=fake_resp)
            result = await client.check_cap("URI:CHK:abc123")

        self.assertIsNotNone(result)
        self.assertTrue(result["accessible"])
        self.assertEqual(result["shares_good"], 5)
        self.assertEqual(result["shares_needed"], 3)

    async def test_under_replicated_file_returns_low_share_count(self):
        """shares_good < shares_needed — caller can detect under-replication."""
        from gatekeeper.tahoe.client import TahoeClient

        check_json = {
            "storage-index": "aabbcc",
            "summary": "Not Healthy",
            "results": {
                "healthy": False,
                "count-shares-good": 2,
                "count-shares-needed": 3,
                "count-shares-expected": 5,
            },
        }
        fake_resp = _make_response(200, json_body=check_json)

        async with TahoeClient(_NODE_URL) as client:
            client._http.post = AsyncMock(return_value=fake_resp)
            result = await client.check_cap("URI:CHK:underrep")

        self.assertIsNotNone(result)
        self.assertEqual(result["shares_good"], 2)
        self.assertEqual(result["shares_needed"], 3)
        self.assertLess(result["shares_good"], result["shares_needed"])

    async def test_lit_file_defaults_to_one_of_one(self):
        """LIT files have no share counts — must default to 1/1 (always healthy)."""
        from gatekeeper.tahoe.client import TahoeClient

        check_json = {"storage-index": "", "results": {"healthy": True}}
        fake_resp = _make_response(200, json_body=check_json)

        async with TahoeClient(_NODE_URL) as client:
            client._http.post = AsyncMock(return_value=fake_resp)
            result = await client.check_cap("URI:LIT:abc")

        self.assertIsNotNone(result)
        self.assertEqual(result["shares_good"], 1)
        self.assertEqual(result["shares_needed"], 1)

    async def test_network_error_returns_none(self):
        """Any exception during the check returns None — caller treats as inaccessible."""
        from gatekeeper.tahoe.client import TahoeClient

        async with TahoeClient(_NODE_URL) as client:
            client._http.post = AsyncMock(side_effect=Exception("connection refused"))
            result = await client.check_cap("URI:CHK:bad")

        self.assertIsNone(result)

    async def test_http_error_returns_none(self):
        from gatekeeper.tahoe.client import TahoeClient

        fake_resp = _make_response(404, text="not found")

        async with TahoeClient(_NODE_URL) as client:
            client._http.post = AsyncMock(return_value=fake_resp)
            result = await client.check_cap("URI:CHK:missing")

        self.assertIsNone(result)

    async def test_uses_post_with_t_check_and_output_json(self):
        """check_cap must POST to /uri/<ref>?t=check&output=json."""
        from gatekeeper.tahoe.client import TahoeClient

        check_json = {"storage-index": "x", "results": {"healthy": True, "count-shares-good": 3, "count-shares-needed": 3}}
        fake_resp = _make_response(200, json_body=check_json)

        async with TahoeClient(_NODE_URL) as client:
            client._http.post = AsyncMock(return_value=fake_resp)
            await client.check_cap("URI:CHK:abc")

        call_args = client._http.post.call_args
        call_url = call_args[0][0]
        call_params = call_args[1].get("params", {})
        self.assertIn("/uri/", call_url)
        self.assertEqual(call_params.get("t"), "check")
        self.assertEqual(call_params.get("output"), "json")

    async def test_url_encodes_ref(self):
        """File refs with colons must be URL-encoded before use in the path."""
        from gatekeeper.tahoe.client import TahoeClient

        check_json = {"storage-index": "x", "results": {"healthy": True}}
        fake_resp = _make_response(200, json_body=check_json)

        async with TahoeClient(_NODE_URL) as client:
            client._http.post = AsyncMock(return_value=fake_resp)
            await client.check_cap("URI:CHK:abc:def")

        call_url = client._http.post.call_args[0][0]
        self.assertNotIn("URI:CHK:abc:def", call_url,
                         "Raw ref must not appear un-encoded in the URL")


if __name__ == "__main__":
    unittest.main()
