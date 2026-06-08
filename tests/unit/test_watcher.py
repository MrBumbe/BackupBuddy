"""
Unit tests for agent/watcher.py — FileWatcher stability detection.

Security note: tests use tmp_path-based paths; no real user data involved.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.watcher import FileWatcher, _FileState, _has_open_handles


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_watcher(
    backup_paths: list[str],
    *,
    stability_seconds: int = 10,
    exclude_patterns: list[str] | None = None,
    excluded_pool_paths: frozenset[str] = frozenset(),
    catalog_check=None,
    scan_interval: float = 0.0,
) -> FileWatcher:
    return FileWatcher(
        backup_paths=backup_paths,
        stability_seconds=stability_seconds,
        exclude_patterns=exclude_patterns or [],
        excluded_pool_paths=excluded_pool_paths,
        catalog_check=catalog_check,
        scan_interval=scan_interval,
    )


def _touch(path: Path, content: str = "x") -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _scan(watcher: FileWatcher, now: float | None = None, *, real_handles: bool = False) -> list[str]:
    """Run _check_file for all files under watcher._backup_paths, return ready list.

    By default mocks _has_open_handles to False so tests run fast on Windows.
    Pass real_handles=True to use the real psutil check (slow).
    """
    now = now if now is not None else time.monotonic()

    def _do_scan() -> list[str]:
        ready: list[str] = []
        for root in watcher._backup_paths:
            for dirpath, _dirs, filenames in os.walk(root):
                for name in filenames:
                    real = os.path.realpath(os.path.join(dirpath, name))
                    if watcher._check_file(real, name, now):
                        ready.append(real)
        return ready

    if real_handles:
        return _do_scan()
    with patch("agent.watcher._has_open_handles", return_value=False):
        return _do_scan()


# ── _has_open_handles() ───────────────────────────────────────────────────────

class TestHasOpenHandles:
    def test_no_handles_on_closed_file(self, tmp_path: Path) -> None:
        f = _touch(tmp_path / "closed.txt")
        assert _has_open_handles(str(f)) is False

    def test_open_handle_detected(self, tmp_path: Path) -> None:
        f = _touch(tmp_path / "open.txt")
        with open(f, "r") as _fh:
            assert _has_open_handles(str(f)) is True


# ── Stability detection — _check_file() ──────────────────────────────────────

class TestStabilityDetection:
    def test_stable_file_queued_after_stability_window(self, tmp_path: Path) -> None:
        _touch(tmp_path / "doc.txt")
        watcher = _make_watcher([str(tmp_path)], stability_seconds=5)

        t0 = time.monotonic()
        # First scan — sets stable_since, not yet stable.
        assert _scan(watcher, t0) == []

        # Second scan after stability window — should be ready.
        ready = _scan(watcher, t0 + 6)
        assert len(ready) == 1

    def test_unstable_file_not_queued(self, tmp_path: Path) -> None:
        f = tmp_path / "active.txt"
        _touch(f, "initial content")
        watcher = _make_watcher([str(tmp_path)], stability_seconds=5)

        t0 = time.monotonic()
        assert _scan(watcher, t0) == []

        # Write content of different size so the watcher detects size change
        # even if the OS timestamp resolution hasn't advanced.
        _touch(f, "initial content — appended so size definitely differs")
        ready = _scan(watcher, t0 + 6)
        assert ready == [], "File that changed should not be queued"

    def test_file_with_open_handle_not_queued(self, tmp_path: Path) -> None:
        f = _touch(tmp_path / "locked.txt")
        watcher = _make_watcher([str(tmp_path)], stability_seconds=0)

        t0 = time.monotonic()
        _scan(watcher, t0)

        with open(f, "r"):
            ready = _scan(watcher, t0 + 1, real_handles=True)
            assert ready == [], "File with open handle should not be queued"

    def test_excluded_pattern_not_queued(self, tmp_path: Path) -> None:
        _touch(tmp_path / "notes.tmp")
        watcher = _make_watcher(
            [str(tmp_path)],
            stability_seconds=0,
            exclude_patterns=["*.tmp"],
        )
        t0 = time.monotonic()
        assert _scan(watcher, t0) == []
        assert _scan(watcher, t0 + 1) == []

    def test_pool_path_file_not_queued(self, tmp_path: Path) -> None:
        pool_dir = tmp_path / "pool"
        pool_dir.mkdir()
        _touch(pool_dir / "fragment.bin")
        watcher = _make_watcher(
            [str(pool_dir)],
            stability_seconds=0,
            excluded_pool_paths=frozenset([str(pool_dir)]),
        )
        t0 = time.monotonic()
        assert _scan(watcher, t0) == []
        assert _scan(watcher, t0 + 1) == []

    def test_already_queued_file_not_returned_twice(self, tmp_path: Path) -> None:
        _touch(tmp_path / "report.pdf")
        watcher = _make_watcher([str(tmp_path)], stability_seconds=0)

        t0 = time.monotonic()
        _scan(watcher, t0)
        first = _scan(watcher, t0 + 1)
        second = _scan(watcher, t0 + 2)

        assert len(first) == 1
        assert second == [], "Stable file should only appear once"

    def test_multiple_files_all_returned(self, tmp_path: Path) -> None:
        for i in range(3):
            _touch(tmp_path / f"file{i}.txt")
        watcher = _make_watcher([str(tmp_path)], stability_seconds=0)

        t0 = time.monotonic()
        _scan(watcher, t0)
        ready = _scan(watcher, t0 + 1)
        assert len(ready) == 3

    def test_catalog_hit_skips_file(self, tmp_path: Path) -> None:
        _touch(tmp_path / "known.pdf")
        watcher = _make_watcher(
            [str(tmp_path)],
            stability_seconds=0,
            catalog_check=lambda path, mtime, size: True,
        )
        t0 = time.monotonic()
        _scan(watcher, t0)
        assert _scan(watcher, t0 + 1) == [], "File known to catalog should not be returned"

    def test_exclude_case_preserved(self, tmp_path: Path) -> None:
        _touch(tmp_path / "Thumbs.db")
        watcher = _make_watcher(
            [str(tmp_path)],
            stability_seconds=0,
            exclude_patterns=["Thumbs.db"],
        )
        t0 = time.monotonic()
        assert _scan(watcher, t0) == []
        assert _scan(watcher, t0 + 1) == []

    def test_disappeared_file_state_cleared(self, tmp_path: Path) -> None:
        f = tmp_path / "temp.txt"
        _touch(f)
        watcher = _make_watcher([str(tmp_path)], stability_seconds=5)

        t0 = time.monotonic()
        _scan(watcher, t0)
        assert os.path.realpath(str(f)) in watcher._state

        f.unlink()
        with patch("agent.watcher._has_open_handles", return_value=False):
            watcher._scan_once()
        assert os.path.realpath(str(f)) not in watcher._state


# ── _scan_once() returns ready list ───────────────────────────────────────────

class TestScanOnce:
    def test_scan_once_returns_stable_files(self, tmp_path: Path) -> None:
        f = _touch(tmp_path / "ready.txt")
        watcher = _make_watcher([str(tmp_path)], stability_seconds=0)

        real = os.path.realpath(str(f))
        stat = os.stat(real)
        watcher._state[real] = _FileState(
            mtime=stat.st_mtime,
            size=stat.st_size,
            stable_since=time.monotonic() - 1,
        )

        with patch("agent.watcher._has_open_handles", return_value=False):
            ready = watcher._scan_once()
        assert real in ready

    def test_scan_once_does_not_touch_queue(self, tmp_path: Path) -> None:
        f = _touch(tmp_path / "file.txt")
        watcher = _make_watcher([str(tmp_path)], stability_seconds=0)

        real = os.path.realpath(str(f))
        stat = os.stat(real)
        watcher._state[real] = _FileState(
            mtime=stat.st_mtime,
            size=stat.st_size,
            stable_since=time.monotonic() - 1,
        )

        with patch("agent.watcher._has_open_handles", return_value=False):
            watcher._scan_once()
        assert watcher.queue.empty(), "_scan_once must not touch the queue"


# ── FileWatcher.dequeue() — re-queue after failed upload ─────────────────────

class TestDequeue:
    def test_dequeue_removes_path_from_queued(self, tmp_path: Path) -> None:
        f = _touch(tmp_path / "photo.jpg")
        watcher = _make_watcher([str(tmp_path)], stability_seconds=0)

        t0 = time.monotonic()
        _scan(watcher, t0)
        ready = _scan(watcher, t0 + 1)
        real = os.path.realpath(str(f))

        assert real in ready, "file must be queued before dequeue"
        assert real in watcher._queued

        watcher.dequeue(real)
        assert real not in watcher._queued

    def test_dequeue_noop_for_unknown_path(self, tmp_path: Path) -> None:
        watcher = _make_watcher([str(tmp_path)], stability_seconds=0)
        watcher.dequeue("/nonexistent/file.txt")  # must not raise

    def test_dequeue_allows_next_scan_to_requeue(self, tmp_path: Path) -> None:
        f = _touch(tmp_path / "report.docx")
        watcher = _make_watcher([str(tmp_path)], stability_seconds=0)
        real = os.path.realpath(str(f))

        t0 = time.monotonic()
        _scan(watcher, t0)
        first = _scan(watcher, t0 + 1)
        assert real in first, "file must appear on first stable scan"
        assert real in watcher._queued

        # Simulate failed upload: dequeue without clearing _state.
        watcher.dequeue(real)
        assert real not in watcher._queued

        # Next scan cycle: _state still holds the stable entry → file re-queued.
        second = _scan(watcher, t0 + 2)
        assert real in second, "file must be re-queued after dequeue"


# ── FileWatcher.run() — async integration ─────────────────────────────────────

class TestWatcherRun:
    @pytest.mark.anyio
    async def test_run_queues_stable_file(self, tmp_path: Path) -> None:
        """Integration: run() must queue a file that was stable before start."""
        f = _touch(tmp_path / "ready.txt")
        watcher = _make_watcher(
            [str(tmp_path)],
            stability_seconds=0,
            scan_interval=0.05,
        )

        # Pre-seed state so the file appears stable from the first scan.
        real = os.path.realpath(str(f))
        stat = os.stat(real)
        watcher._state[real] = _FileState(
            mtime=stat.st_mtime,
            size=stat.st_size,
            stable_since=time.monotonic() - 1,
        )

        with (
            patch("agent.watcher._set_low_priority"),
            patch("agent.watcher._has_open_handles", return_value=False),
        ):
            task = asyncio.create_task(watcher.run())
            # Give the watcher a few scan cycles to pick up the file.
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert not watcher.queue.empty()
        assert real == watcher.queue.get_nowait()
