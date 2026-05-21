"""Unit tests for gatekeeper.restore.restore."""

import hashlib
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from gatekeeper.restore.restore import (
    RestoreFileResult,
    RestoreFolderResult,
    RestoreIntegrityError,
    RestoreNotFoundError,
    _download_with_retry,
    _make_temp_dir,
    restore_file,
    restore_folder,
)
from gatekeeper.tahoe.client import TahoeError


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_catalog(record: dict | None = None) -> MagicMock:
    """Return a mock CatalogDB."""
    catalog = MagicMock()
    catalog.get_file_by_path.return_value = record
    return catalog


def _make_tahoe(download_sha: str | None = None, raises: Exception | None = None) -> AsyncMock:
    """Return a mock TahoeClient whose download() returns download_sha or raises."""
    tahoe = MagicMock()
    if raises is not None:
        tahoe.download = AsyncMock(side_effect=raises)
    else:
        tahoe.download = AsyncMock(return_value=download_sha)
    return tahoe


# ── _make_temp_dir ────────────────────────────────────────────────────────────

class TestMakeTempDir(unittest.TestCase):

    def test_creates_directory(self):
        tmpdir = _make_temp_dir()
        try:
            self.assertTrue(os.path.isdir(tmpdir))
        finally:
            os.rmdir(tmpdir)

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions not enforced on Windows")
    def test_permissions_0700(self):
        tmpdir = _make_temp_dir()
        try:
            mode = oct(os.stat(tmpdir).st_mode & 0o777)
            self.assertEqual(mode, oct(0o700))
        finally:
            os.rmdir(tmpdir)


# ── restore_file ─────────────────────────────────────────────────────────────

class TestRestoreFile(unittest.IsolatedAsyncioTestCase):

    async def test_successful_restore(self):
        content = b"important backup data"
        sha = _sha256(content)

        record = {"cap": "URI:CHK:abc", "sha256": sha}
        catalog = _make_catalog(record)

        async def fake_download(file_ref, dest_path):
            with open(dest_path, "wb") as f:
                f.write(content)
            return sha

        tahoe = MagicMock()
        tahoe.download = AsyncMock(side_effect=fake_download)

        with tempfile.TemporaryDirectory() as dest_dir:
            dest = os.path.join(dest_dir, "result.txt")
            result = await restore_file(
                "/home/user/file.txt", "agent-01", dest,
                catalog=catalog, tahoe=tahoe,
            )

        self.assertTrue(result.success)
        self.assertEqual(result.sha256, sha)

    async def test_file_not_in_catalog_raises_not_found(self):
        catalog = _make_catalog(None)
        tahoe = _make_tahoe()

        with self.assertRaises(RestoreNotFoundError):
            await restore_file(
                "/missing/file.txt", "agent-01", "/dest/file.txt",
                catalog=catalog, tahoe=tahoe,
            )

    async def test_hash_mismatch_retries_once_then_raises(self):
        record = {"cap": "URI:CHK:abc", "sha256": "expected_hash"}
        catalog = _make_catalog(record)

        # Always returns a different hash — integrity check always fails
        tahoe = _make_tahoe(download_sha="bad_hash")

        alerts = []

        with self.assertRaises(RestoreIntegrityError):
            await restore_file(
                "/home/user/file.txt", "agent-01", "/dest/file.txt",
                catalog=catalog, tahoe=tahoe,
                send_alert=lambda level, msg: alerts.append((level, msg)),
            )

        # download called twice: initial attempt + one retry
        self.assertEqual(tahoe.download.call_count, 2)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][0], "error")

    async def test_hash_match_on_retry_succeeds(self):
        content = b"retry succeeds"
        sha = _sha256(content)

        record = {"cap": "URI:CHK:abc", "sha256": sha}
        catalog = _make_catalog(record)

        call_count = 0

        async def flaky_download(file_ref, dest_path):
            nonlocal call_count
            call_count += 1
            with open(dest_path, "wb") as f:
                f.write(content)
            if call_count == 1:
                return "bad_hash"   # first attempt: wrong hash
            return sha              # second attempt: correct hash

        tahoe = MagicMock()
        tahoe.download = AsyncMock(side_effect=flaky_download)

        with tempfile.TemporaryDirectory() as dest_dir:
            dest = os.path.join(dest_dir, "retry_ok.txt")
            result = await restore_file(
                "/home/user/file.txt", "agent-01", dest,
                catalog=catalog, tahoe=tahoe,
            )

        self.assertTrue(result.success)
        self.assertEqual(call_count, 2)

    async def test_tahoe_error_propagates(self):
        record = {"cap": "URI:CHK:abc", "sha256": "sha"}
        catalog = _make_catalog(record)
        tahoe = _make_tahoe(raises=TahoeError("cluster unavailable"))

        with self.assertRaises(TahoeError):
            await restore_file(
                "/home/user/file.txt", "agent-01", "/dest/file.txt",
                catalog=catalog, tahoe=tahoe,
            )

    async def test_temp_files_cleaned_up_on_success(self):
        content = b"cleanup test"
        sha = _sha256(content)

        record = {"cap": "URI:CHK:abc", "sha256": sha}
        catalog = _make_catalog(record)

        created_tmpdirs: list[str] = []

        original_make_temp_dir = _make_temp_dir

        async def fake_download(file_ref, dest_path):
            with open(dest_path, "wb") as f:
                f.write(content)
            return sha

        tahoe = MagicMock()
        tahoe.download = AsyncMock(side_effect=fake_download)

        import gatekeeper.restore.restore as restore_mod

        original = restore_mod._make_temp_dir

        def patched_make_temp_dir():
            d = original()
            created_tmpdirs.append(d)
            return d

        with tempfile.TemporaryDirectory() as dest_dir:
            dest = os.path.join(dest_dir, "cleanup.txt")
            with patch.object(restore_mod, "_make_temp_dir", patched_make_temp_dir):
                await restore_file(
                    "/home/user/file.txt", "agent-01", dest,
                    catalog=catalog, tahoe=tahoe,
                )

        for d in created_tmpdirs:
            self.assertFalse(os.path.exists(d), f"Temp dir {d} was not cleaned up")

    async def test_temp_files_cleaned_up_on_failure(self):
        record = {"cap": "URI:CHK:abc", "sha256": "expected"}
        catalog = _make_catalog(record)
        tahoe = _make_tahoe(download_sha="wrong")

        import gatekeeper.restore.restore as restore_mod

        original = restore_mod._make_temp_dir
        created_tmpdirs: list[str] = []

        def patched_make_temp_dir():
            d = original()
            created_tmpdirs.append(d)
            return d

        with patch.object(restore_mod, "_make_temp_dir", patched_make_temp_dir):
            with self.assertRaises(RestoreIntegrityError):
                await restore_file(
                    "/home/user/file.txt", "agent-01", "/dest/file.txt",
                    catalog=catalog, tahoe=tahoe,
                )

        for d in created_tmpdirs:
            self.assertFalse(os.path.exists(d), f"Temp dir {d} was not cleaned up")

    async def test_no_tahoe_terms_in_error_messages(self):
        record = {"cap": "URI:CHK:abc", "sha256": "expected"}
        catalog = _make_catalog(record)
        tahoe = _make_tahoe(download_sha="wrong")

        alerts = []
        with self.assertRaises(RestoreIntegrityError) as ctx:
            await restore_file(
                "/home/user/file.txt", "agent-01", "/dest/file.txt",
                catalog=catalog, tahoe=tahoe,
                send_alert=lambda level, msg: alerts.append(msg),
            )

        for forbidden in ("FURL", "cap", "shares", "k-of-n", "erasure"):
            for msg_source in [str(ctx.exception)] + alerts:
                self.assertNotIn(
                    forbidden.lower(), msg_source.lower(),
                    f"Tahoe term {forbidden!r} in message: {msg_source!r}",
                )

    async def test_send_alert_none_does_not_crash(self):
        record = {"cap": "URI:CHK:abc", "sha256": "expected"}
        catalog = _make_catalog(record)
        tahoe = _make_tahoe(download_sha="wrong")

        with self.assertRaises(RestoreIntegrityError):
            await restore_file(
                "/home/user/file.txt", "agent-01", "/dest/file.txt",
                catalog=catalog, tahoe=tahoe,
                send_alert=None,
            )

    async def test_dest_path_parent_created_if_missing(self):
        content = b"nested dest test"
        sha = _sha256(content)

        record = {"cap": "URI:CHK:abc", "sha256": sha}
        catalog = _make_catalog(record)

        async def fake_download(file_ref, dest_path):
            with open(dest_path, "wb") as f:
                f.write(content)
            return sha

        tahoe = MagicMock()
        tahoe.download = AsyncMock(side_effect=fake_download)

        with tempfile.TemporaryDirectory() as dest_dir:
            # dest_path has a non-existent subdirectory
            dest = os.path.join(dest_dir, "subdir", "nested", "file.txt")
            result = await restore_file(
                "/home/user/file.txt", "agent-01", dest,
                catalog=catalog, tahoe=tahoe,
            )

        self.assertTrue(result.success)


# ── restore_folder ────────────────────────────────────────────────────────────

class TestRestoreFolder(unittest.IsolatedAsyncioTestCase):

    def _catalog_with_files(self, files: list[dict]) -> MagicMock:
        catalog = MagicMock()
        catalog.get_all_files.return_value = files
        return catalog

    async def test_restores_matching_files(self):
        sha = _sha256(b"folder content")
        files = [
            {"cap": "URI:1", "sha256": sha, "agent": "agent-01",
             "original_path": "/data/docs/report.pdf"},
            {"cap": "URI:2", "sha256": sha, "agent": "agent-01",
             "original_path": "/data/docs/notes.txt"},
            {"cap": "URI:3", "sha256": sha, "agent": "agent-01",
             "original_path": "/other/file.txt"},  # outside folder — excluded
        ]
        catalog = self._catalog_with_files(files)

        async def fake_download(file_ref, dest_path):
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "wb") as f:
                f.write(b"folder content")
            return sha

        tahoe = MagicMock()
        tahoe.download = AsyncMock(side_effect=fake_download)

        with tempfile.TemporaryDirectory() as dest_dir:
            result = await restore_folder(
                "/data/docs", "agent-01", dest_dir,
                catalog=catalog, tahoe=tahoe,
            )

        self.assertEqual(result.files_restored, 2)
        self.assertEqual(result.files_failed, 0)
        self.assertEqual(len(result.results), 2)

    async def test_skips_files_for_other_agents(self):
        sha = _sha256(b"x")
        files = [
            {"cap": "URI:1", "sha256": sha, "agent": "agent-01",
             "original_path": "/data/file.txt"},
            {"cap": "URI:2", "sha256": sha, "agent": "agent-02",
             "original_path": "/data/file.txt"},  # different agent
        ]
        catalog = self._catalog_with_files(files)

        async def fake_download(file_ref, dest_path):
            os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
            with open(dest_path, "wb") as f:
                f.write(b"x")
            return sha

        tahoe = MagicMock()
        tahoe.download = AsyncMock(side_effect=fake_download)

        with tempfile.TemporaryDirectory() as dest_dir:
            result = await restore_folder(
                "/data", "agent-01", dest_dir,
                catalog=catalog, tahoe=tahoe,
            )

        self.assertEqual(result.files_restored, 1)

    async def test_empty_folder_returns_zero_counts(self):
        catalog = self._catalog_with_files([])
        tahoe = _make_tahoe()

        result = await restore_folder(
            "/data/empty", "agent-01", "/dest",
            catalog=catalog, tahoe=tahoe,
        )

        self.assertEqual(result.files_restored, 0)
        self.assertEqual(result.files_failed, 0)
        self.assertEqual(result.results, [])

    async def test_failed_file_counted_not_fatal(self):
        sha_good = _sha256(b"good")
        sha_bad = "wrong_hash"

        files = [
            {"cap": "URI:ok", "sha256": sha_good, "agent": "agent-01",
             "original_path": "/data/ok.txt"},
            {"cap": "URI:bad", "sha256": "expected", "agent": "agent-01",
             "original_path": "/data/bad.txt"},
        ]
        catalog = self._catalog_with_files(files)

        async def selective_download(file_ref, dest_path):
            os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
            if file_ref == "URI:ok":
                with open(dest_path, "wb") as f:
                    f.write(b"good")
                return sha_good
            with open(dest_path, "wb") as f:
                f.write(b"corrupted")
            return sha_bad

        tahoe = MagicMock()
        tahoe.download = AsyncMock(side_effect=selective_download)

        with tempfile.TemporaryDirectory() as dest_dir:
            result = await restore_folder(
                "/data", "agent-01", dest_dir,
                catalog=catalog, tahoe=tahoe,
            )

        self.assertEqual(result.files_restored, 1)
        self.assertEqual(result.files_failed, 1)

    async def test_files_with_none_path_skipped(self):
        sha = _sha256(b"x")
        files = [
            {"cap": "URI:1", "sha256": sha, "agent": "agent-01",
             "original_path": None},  # ADR-008 call-home edge case
            {"cap": "URI:2", "sha256": sha, "agent": "agent-01",
             "original_path": "/data/file.txt"},
        ]
        catalog = self._catalog_with_files(files)

        async def fake_download(file_ref, dest_path):
            os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
            with open(dest_path, "wb") as f:
                f.write(b"x")
            return sha

        tahoe = MagicMock()
        tahoe.download = AsyncMock(side_effect=fake_download)

        with tempfile.TemporaryDirectory() as dest_dir:
            result = await restore_folder(
                "/data", "agent-01", dest_dir,
                catalog=catalog, tahoe=tahoe,
            )

        self.assertEqual(result.files_restored, 1)


# ── _download_with_retry ──────────────────────────────────────────────────────

class TestDownloadWithRetry(unittest.IsolatedAsyncioTestCase):

    async def test_success_on_first_attempt(self):
        sha = _sha256(b"content")
        tahoe = _make_tahoe(download_sha=sha)
        with tempfile.TemporaryDirectory() as d:
            result = await _download_with_retry(tahoe, "ref", os.path.join(d, "f"), sha, "ag", None)
        self.assertEqual(result, sha)
        self.assertEqual(tahoe.download.call_count, 1)

    async def test_tahoe_error_on_first_attempt_retries(self):
        sha = _sha256(b"content")
        call_count = 0

        with tempfile.TemporaryDirectory() as d:
            tmp_path = os.path.join(d, "f.tmp")

            async def flaky_download(ref, path):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise TahoeError("transient failure")
                with open(path, "wb") as f:
                    f.write(b"content")
                return sha

            tahoe = MagicMock()
            tahoe.download = AsyncMock(side_effect=flaky_download)

            result = await _download_with_retry(tahoe, "ref", tmp_path, sha, "ag", None)

        self.assertEqual(result, sha)
        self.assertEqual(call_count, 2)

    async def test_tahoe_error_on_both_attempts_raises(self):
        tahoe = _make_tahoe(raises=TahoeError("persistent failure"))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(TahoeError):
                await _download_with_retry(tahoe, "ref", os.path.join(d, "f"), "sha", "ag", None)
        self.assertEqual(tahoe.download.call_count, 2)

    async def test_hash_mismatch_on_both_raises_integrity_error(self):
        tahoe = _make_tahoe(download_sha="wrong")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RestoreIntegrityError):
                await _download_with_retry(tahoe, "ref", os.path.join(d, "f"), "expected", "ag", None)
        self.assertEqual(tahoe.download.call_count, 2)

    async def test_alert_called_exactly_once_on_integrity_failure(self):
        tahoe = _make_tahoe(download_sha="wrong")
        alerts = []
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RestoreIntegrityError):
                await _download_with_retry(
                    tahoe, "ref", os.path.join(d, "f"), "expected", "ag",
                    lambda level, msg: alerts.append(msg),
                )
        self.assertEqual(len(alerts), 1)


if __name__ == "__main__":
    unittest.main()
