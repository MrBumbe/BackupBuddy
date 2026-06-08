"""
File watcher with stability detection for BackupBuddy agent.

A file is considered stable (ready to upload) when ALL of:
  1. mtime has not changed for stability_seconds
  2. size has not changed between two consecutive scans
  3. No other process holds an open file handle

Stable files are placed on an asyncio.Queue for the upload worker.

SECURITY: File names and paths from the backup scope are NEVER logged
(SECURITY.md §6). Only counts, sizes, and operation status are logged.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import psutil

logger = logging.getLogger(__name__)


@dataclass
class _FileState:
    mtime: float
    size: int
    stable_since: float | None = field(default=None)


class FileWatcher:
    """Recursively watches backup_paths for stable files and queues them.

    Args:
        backup_paths: Directories to watch (absolute, validated by config).
        stability_seconds: Idle time required before a file is stable.
        exclude_patterns: fnmatch patterns matched against file basenames.
        excluded_pool_paths: Storage pool paths — never queued.
            In Phase 1 the agent cannot import the gatekeeper storage module
            (they run on different machines). The gatekeeper is expected to
            provide these paths after registration. Default: empty frozenset.
            TODO: wire up from registration response once agent API supports it.
        catalog_check: Optional callable(path, mtime, size) → bool returning
            True if the file is already in the gatekeeper catalog at this exact
            mtime/size. Files that return True are skipped.
            Defaults to always False (never skip).
            TODO: implement via gatekeeper_client once catalog query API exists.
        queue: asyncio Queue to place stable file paths on.
        scan_interval: Seconds between full-directory scans (default 60).
    """

    def __init__(
        self,
        backup_paths: list[str],
        stability_seconds: int,
        exclude_patterns: list[str],
        excluded_pool_paths: frozenset[str] = frozenset(),
        catalog_check: Callable[[str, float, int], bool] | None = None,
        queue: asyncio.Queue[str] | None = None,
        scan_interval: float = 60.0,
    ) -> None:
        self._backup_paths = [os.path.realpath(p) for p in backup_paths]
        self._stability_seconds = stability_seconds
        self._exclude_patterns = exclude_patterns
        self._excluded_pool_paths = frozenset(
            os.path.realpath(p) for p in excluded_pool_paths
        )
        self._catalog_check = catalog_check or (lambda path, mtime, size: False)
        self.queue: asyncio.Queue[str] = queue if queue is not None else asyncio.Queue()
        self._scan_interval = scan_interval
        self._state: dict[str, _FileState] = {}
        self._queued: set[str] = set()
        # _queued is written from _scan_once (thread-pool thread) and dequeue
        # (event-loop thread). _queued_lock guards all writes; individual set
        # reads are atomic under CPython's GIL so no lock is needed for them.
        self._queued_lock = threading.Lock()

    # ── Public interface ──────────────────────────────────────────────────────

    def dequeue(self, path: str) -> None:
        """Remove *path* from the queued set so the next scan re-evaluates it.

        Called from the upload worker after a failed upload so the file is
        retried on the next scan cycle. No-op if *path* is not in the set.
        """
        with self._queued_lock:
            self._queued.discard(path)

    async def run(self) -> None:
        """Run the watcher loop indefinitely. Call from an asyncio task."""
        _set_low_priority()
        logger.info(
            "File watcher started — paths: %d, stability: %ds, patterns: %d",
            len(self._backup_paths),
            self._stability_seconds,
            len(self._exclude_patterns),
        )
        while True:
            try:
                # _scan_once runs in a thread; it returns ready paths so we can
                # put them on the asyncio.Queue from the event loop thread
                # (asyncio.Queue is not thread-safe — NEVER call put_nowait from a worker thread).
                ready = await asyncio.to_thread(self._scan_once)
                for path in ready:
                    self.queue.put_nowait(path)
            except Exception:
                logger.exception("File watcher scan error — continuing")
            await asyncio.sleep(self._scan_interval)

    # ── Internal scan ─────────────────────────────────────────────────────────

    def _scan_once(self) -> list[str]:
        """Walk all backup paths and update stability state. Blocking — run in thread.

        Returns a list of newly-stable file paths ready for upload.
        Must NOT touch self.queue — see run() for the thread-safety rationale.
        """
        now = time.monotonic()
        seen: set[str] = set()
        ready: list[str] = []

        for root in self._backup_paths:
            try:
                for dirpath, _dirs, filenames in os.walk(root, followlinks=False):
                    for name in filenames:
                        full = os.path.join(dirpath, name)
                        real = os.path.realpath(full)
                        seen.add(real)
                        if self._check_file(real, name, now):
                            ready.append(real)
            except OSError:
                logger.warning("Watcher could not scan a backup path — skipping")

        # Drop state for files that disappeared.
        gone = set(self._state) - seen
        for path in gone:
            del self._state[path]
            with self._queued_lock:
                self._queued.discard(path)

        return ready

    def _check_file(self, real: str, basename: str, now: float) -> bool:
        """Evaluate one file. Returns True if the file just became stable."""
        if real in self._queued:
            return False
        if self._is_excluded(basename):
            return False
        if self._is_in_pool(real):
            return False

        try:
            stat = os.stat(real)
        except OSError:
            return False

        mtime = stat.st_mtime
        size = stat.st_size

        prev = self._state.get(real)
        if prev is None or prev.mtime != mtime or prev.size != size:
            # File changed — reset stability clock.
            self._state[real] = _FileState(mtime=mtime, size=size, stable_since=now)
            return False

        # mtime and size unchanged; check if stability window has elapsed.
        if prev.stable_since is None:
            self._state[real] = _FileState(mtime=mtime, size=size, stable_since=now)

        elapsed = now - (self._state[real].stable_since or now)
        if elapsed < self._stability_seconds:
            return False

        # Stability window passed — check open handles.
        if _has_open_handles(real):
            return False

        # Check catalog: skip if already backed up at this exact version.
        if self._catalog_check(real, mtime, size):
            with self._queued_lock:
                self._queued.add(real)
            return False

        with self._queued_lock:
            self._queued.add(real)
        return True

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_excluded(self, basename: str) -> bool:
        return any(fnmatch.fnmatch(basename, pat) for pat in self._exclude_patterns)

    def _is_in_pool(self, real_path: str) -> bool:
        for pool_path in self._excluded_pool_paths:
            if real_path == pool_path or real_path.startswith(pool_path + os.sep):
                return True
        return False


# ── OS helpers ────────────────────────────────────────────────────────────────

def _has_open_handles(path: str) -> bool:
    """Return True if any process has an open handle to *path*."""
    try:
        for proc in psutil.process_iter(["open_files"]):
            try:
                for f in proc.info.get("open_files") or []:
                    if os.path.realpath(f.path) == path:
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return False


def _set_low_priority() -> None:
    """Lower the watcher process to nice+19 / ionice idle. Best-effort."""
    try:
        os.nice(19)
    except (AttributeError, PermissionError):
        pass
    try:
        import subprocess
        subprocess.run(
            ["ionice", "-c", "3", "-p", str(os.getpid())],
            check=False,
            capture_output=True,
        )
    except (FileNotFoundError, OSError):
        pass
