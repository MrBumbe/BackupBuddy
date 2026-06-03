"""Normal restore flow: file and folder restore with hash verification.

Design constraints:
  - TahoeClient and CatalogDB injected — no global imports for testability.
  - Temp directory created with 0700 permissions; cleaned up in all outcomes.
  - Hash mismatch: retry download once (Tahoe may pick different fragments);
    if still mismatched, raise RestoreIntegrityError and send alert.
  - send_alert is an injectable callable so tests can assert on alert calls.
    If None, failures are logged at ERROR level only.
  - No Tahoe internals (cap, FURL, shares) in any user-facing string or
    raised exception message.
  - Folder restore uses get_all_files() + Python-side prefix filter because
    catalog uses HMAC blind index (no prefix search possible on encrypted paths).
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Callable

from gatekeeper.db.catalog import CatalogDB
from gatekeeper.tahoe.client import TahoeClient, TahoeError

logger = logging.getLogger(__name__)

_MAX_RETRIES = 1  # one retry on hash mismatch before raising


# ── Exceptions ────────────────────────────────────────────────────────────────

class RestoreNotFoundError(Exception):
    """Raised when the requested file is not found in the catalog."""


class RestoreIntegrityError(Exception):
    """Raised when hash verification fails after all retry attempts."""


class RestoreError(Exception):
    """Raised for recoverable restore failures with a user-facing message."""


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class RestoreFileResult:
    success: bool
    dest_path: str | None = None
    sha256: str | None = None
    error: str | None = None


@dataclass
class RestoreFolderResult:
    files_restored: int = 0
    files_failed: int = 0
    results: list[RestoreFileResult] = field(default_factory=list)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _make_temp_dir() -> str:
    """Create a temp directory with 0700 permissions and return its path."""
    tmpdir = tempfile.mkdtemp(prefix="bb_restore_")
    if sys.platform != "win32":
        os.chmod(tmpdir, stat.S_IRWXU)
    return tmpdir


def _check_temp_dir_not_in_pool(tmpdir: str) -> None:
    """Log a warning if the temp dir overlaps with a storage pool path.

    Storage pool paths are excluded from backup; a temp dir inside them would
    cause restored files to be treated as fragments by the storage manager.
    This is an unusual configuration but worth detecting.
    """
    from gatekeeper.storage.pool import EXCLUDED_PATHS  # lazy import — pool may not be init'd in tests

    real_tmp = os.path.realpath(tmpdir)
    for pool_path in EXCLUDED_PATHS:
        if real_tmp.startswith(os.path.realpath(pool_path)):
            logger.warning(
                "Restore temp directory %s is inside storage pool path %s — "
                "this is unexpected and may cause conflicts",
                tmpdir, pool_path,
            )
            break


def _safe_move(src: str, dest: str, original_filename: str) -> str:
    """Move src to dest, creating parent directories as needed.

    If dest is an existing directory, writes the file inside it using
    original_filename (cp-like semantics). Returns the resolved dest path.
    Raises RestoreError on PermissionError.
    """
    if os.path.isdir(dest):
        dest = os.path.join(dest, original_filename)
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    try:
        os.replace(src, dest)
    except PermissionError:
        raise RestoreError(
            f"Cannot write to {dest!r}: permission denied. "
            "Ensure the destination is writable by the backup service user."
        )
    return dest


def _alert(send_alert: Callable | None, message: str) -> None:
    if send_alert is not None:
        try:
            send_alert("error", message)
        except Exception:
            logger.exception("send_alert raised an exception")
    else:
        logger.error("%s", message)


# ── Public API ────────────────────────────────────────────────────────────────

async def restore_file(
    original_path: str,
    agent: str,
    dest_path: str,
    *,
    catalog: CatalogDB,
    tahoe: TahoeClient,
    send_alert: Callable | None = None,
) -> RestoreFileResult:
    """Restore a single file from the cluster to dest_path.

    Steps:
      1. Look up file in catalog by original_path + agent.
      2. Download via Tahoe client to a 0700 temp directory.
      3. Verify SHA-256 against catalog entry.
      4. If mismatch: retry download once (Tahoe may use different fragments).
      5. If still mismatched: send alert, raise RestoreIntegrityError.
      6. On success: move file to dest_path, clean up temp dir.

    Raises:
        RestoreNotFoundError:  File not found in the catalog.
        RestoreIntegrityError: Hash mismatch after all retry attempts.
    """
    record = catalog.get_file_by_path(agent, original_path)
    if record is None:
        raise RestoreNotFoundError(
            f"File not found in catalog: agent={agent!r} path={original_path!r}"
        )

    return await _restore_from_record(record, agent, dest_path, tahoe=tahoe, send_alert=send_alert)


async def _restore_from_record(
    record: dict,
    agent: str,
    dest_path: str,
    *,
    tahoe: TahoeClient,
    send_alert: Callable | None = None,
) -> RestoreFileResult:
    """Download from Tahoe using a pre-loaded catalog record and verify hash."""
    file_ref: str = record["cap"]
    expected_sha256: str = record["sha256"]

    tmpdir = _make_temp_dir()
    try:
        _check_temp_dir_not_in_pool(tmpdir)

        tmp_file = os.path.join(tmpdir, "restore.tmp")
        actual_sha256 = await _download_with_retry(
            tahoe, file_ref, tmp_file, expected_sha256, agent, send_alert,
        )

        original_filename = os.path.basename(record["original_path"])
        resolved_dest = _safe_move(tmp_file, dest_path, original_filename)

        logger.info(
            "Restore complete: agent=%s dest=%s sha256=%.16s…",
            agent, resolved_dest, actual_sha256,
        )
        return RestoreFileResult(
            success=True,
            dest_path=resolved_dest,
            sha256=actual_sha256,
        )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def restore_folder(
    folder_path: str,
    agent: str,
    dest_path: str,
    *,
    catalog: CatalogDB,
    tahoe: TahoeClient,
    send_alert: Callable | None = None,
) -> RestoreFolderResult:
    """Restore all files under folder_path for an agent.

    Finds all catalog entries whose original_path starts with folder_path
    (for the given agent) and restores each to a mirrored path under dest_path.

    Note: catalog uses HMAC blind index so we must decrypt all records and
    filter in Python — acceptable for Phase 1 PoC scope.

    Returns a RestoreFolderResult with per-file outcomes.
    """
    real_folder = os.path.realpath(folder_path)

    all_files = catalog.get_all_files()
    matching = [
        r for r in all_files
        if r["agent"] == agent
        and r["original_path"] is not None
        and os.path.realpath(r["original_path"]).startswith(
            real_folder if real_folder.endswith(os.sep) else real_folder + os.sep
        )
    ]

    if not matching:
        logger.info(
            "No catalog entries found for agent=%s folder=%s",
            agent, folder_path,
        )
        return RestoreFolderResult()

    summary = RestoreFolderResult()

    for record in matching:
        original = record["original_path"]
        # Mirror the directory structure under dest_path.
        relative = os.path.relpath(original, real_folder)
        file_dest = os.path.join(dest_path, relative)

        try:
            result = await _restore_from_record(
                record,
                agent,
                file_dest,
                tahoe=tahoe,
                send_alert=send_alert,
            )
            summary.results.append(result)
            if result.success:
                summary.files_restored += 1
            else:
                summary.files_failed += 1
        except (RestoreNotFoundError, RestoreIntegrityError, TahoeError) as exc:
            logger.error(
                "Failed to restore file: agent=%s path=%s error=%s",
                agent, original, type(exc).__name__,
            )
            summary.files_failed += 1
            summary.results.append(
                RestoreFileResult(success=False, dest_path=file_dest, error=str(exc))
            )

    logger.info(
        "Folder restore finished: agent=%s folder=%s restored=%d failed=%d",
        agent, folder_path, summary.files_restored, summary.files_failed,
    )
    return summary


# ── Internal download helper ──────────────────────────────────────────────────

async def _download_with_retry(
    tahoe: TahoeClient,
    file_ref: str,
    tmp_file: str,
    expected_sha256: str,
    agent: str,
    send_alert: Callable | None,
) -> str:
    """Download file_ref to tmp_file; verify SHA-256; retry once on mismatch.

    Returns the verified SHA-256 hex digest.
    Raises RestoreIntegrityError if all attempts fail.
    Raises TahoeError if the download itself fails.
    """
    # Reconstructed records (ADR-008) have sha256="" — hash unknown.
    # Download and return the actual digest without comparing.
    if not expected_sha256:
        actual = await tahoe.download(file_ref, tmp_file)
        logger.warning(
            "Hash verification skipped: sha256 unknown for reconstructed record. "
            "agent=%s actual=%.16s…",
            agent, actual,
        )
        return actual

    for attempt in range(_MAX_RETRIES + 1):
        try:
            actual = await tahoe.download(file_ref, tmp_file)
        except TahoeError as exc:
            if attempt < _MAX_RETRIES:
                logger.warning(
                    "Download attempt %d failed for agent=%s; retrying. error=%s",
                    attempt + 1, agent, type(exc).__name__,
                )
                continue
            raise

        if actual == expected_sha256:
            return actual

        logger.warning(
            "Hash mismatch on attempt %d for agent=%s "
            "(expected=%.16s… got=%.16s…)",
            attempt + 1, agent, expected_sha256, actual,
        )

        if attempt < _MAX_RETRIES:
            # Remove temp file before retry so the next download starts clean.
            try:
                os.remove(tmp_file)
            except OSError:
                pass
            continue

        message = (
            f"Restored file failed integrity check after {_MAX_RETRIES + 1} "
            f"attempt(s). agent={agent!r}"
        )
        _alert(send_alert, message)
        raise RestoreIntegrityError(message)

    raise RestoreIntegrityError("Download loop exited without returning")
