"""
Storage pool manager: path selection, quota enforcement, and backup exclusion.

EXCLUDED_PATHS is set once at manager initialisation and imported by the watcher
and any other module that needs to check whether a path is in the storage pool.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Sequence

from gatekeeper.config import StoragePoolEntry

logger = logging.getLogger(__name__)

# Set once by StoragePoolManager.__init__ — immutable frozenset after that.
# Watcher and fragmenter import this directly: `from gatekeeper.storage.pool import EXCLUDED_PATHS`
EXCLUDED_PATHS: frozenset[str] = frozenset()


class QuotaExceeded(Exception):
    """Raised when no storage pool path has sufficient quota for a fragment."""


class PoolPathError(ValueError):
    """Raised when a storage pool path fails startup validation."""


class StoragePoolManager:
    """Manages fragment storage across multiple local paths with hard quotas.

    Thread-safe: all state mutations are protected by an internal lock.

    Usage note: _used_bytes is initialised from the filesystem on startup and
    updated in-memory for register/remove calls.  If fragments are deleted
    out-of-band (e.g. by the orphan cleanup job in task 1.10.2) without calling
    remove_fragment(), the counter drifts until the next restart.  Orphan cleanup
    must call remove_fragment() to keep the counter accurate.
    """

    def __init__(self, entries: Sequence[StoragePoolEntry]) -> None:
        global EXCLUDED_PATHS

        if not entries:
            raise PoolPathError("Storage pool must have at least one path")

        self._lock = threading.Lock()
        self._entries: list[StoragePoolEntry] = []
        self._used_bytes: dict[str, int] = {}

        for entry in entries:
            real = os.path.realpath(entry.path)

            if not os.path.isdir(real):
                raise PoolPathError(
                    f"Storage pool path is not a directory: {entry.path!r}"
                )
            if not os.access(real, os.W_OK):
                raise PoolPathError(
                    f"Storage pool path is not writable: {entry.path!r}"
                )

            normalized = StoragePoolEntry(path=real, quota_bytes=entry.quota_bytes)
            self._entries.append(normalized)
            used = self._compute_used_bytes(real)
            self._used_bytes[real] = used
            logger.info(
                "Storage pool path registered: %s — %d bytes used, %d bytes quota",
                real,
                used,
                entry.quota_bytes,
            )

        # Build immutable exclusion set with realpath-resolved paths.
        # The module-level name is replaced once so importers get the live set.
        self.excluded_paths: frozenset[str] = frozenset(
            e.path for e in self._entries
        )
        EXCLUDED_PATHS = self.excluded_paths

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _compute_used_bytes(path: str) -> int:
        """Sum file sizes under *path* without following symlinks."""
        total = 0
        for dirpath, _dirs, files in os.walk(path, followlinks=False):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass  # file removed between walk and stat — harmless skip
        return total

    # ── Public API ────────────────────────────────────────────────────────────

    def get_target_path(self, size_bytes: int) -> str:
        """Return the pool path with the most remaining quota that fits *size_bytes*.

        Raises QuotaExceeded if no path has enough quota remaining.
        """
        with self._lock:
            best: str | None = None
            best_free: int = -1
            for entry in self._entries:
                free = entry.quota_bytes - self._used_bytes[entry.path]
                if free >= size_bytes and free > best_free:
                    best = entry.path
                    best_free = free

        if best is None:
            raise QuotaExceeded(
                f"No storage pool path has {size_bytes} bytes of quota remaining"
            )
        return best

    def register_fragment(self, path: str, size_bytes: int) -> None:
        """Record that *size_bytes* of quota has been consumed at *path*."""
        real = os.path.realpath(path)
        with self._lock:
            if real not in self._used_bytes:
                raise ValueError(f"Path not in storage pool: {path!r}")
            self._used_bytes[real] += size_bytes

    def remove_fragment(self, path: str, size_bytes: int) -> None:
        """Record that *size_bytes* of quota has been released at *path*."""
        real = os.path.realpath(path)
        with self._lock:
            if real not in self._used_bytes:
                raise ValueError(f"Path not in storage pool: {path!r}")
            self._used_bytes[real] = max(0, self._used_bytes[real] - size_bytes)

    def get_usage(self) -> list[dict]:
        """Return a usage snapshot per path (for monitoring and GUI)."""
        with self._lock:
            return [
                {
                    "path": entry.path,
                    "quota_bytes": entry.quota_bytes,
                    "used_bytes": self._used_bytes[entry.path],
                    "free_bytes": max(
                        0, entry.quota_bytes - self._used_bytes[entry.path]
                    ),
                }
                for entry in self._entries
            ]
