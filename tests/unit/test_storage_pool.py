"""Unit tests for gatekeeper/storage/pool.py."""

from __future__ import annotations

import os
import stat
import sys
import threading
from pathlib import Path

import pytest

from gatekeeper.config import StoragePoolEntry
from gatekeeper.storage import pool as pool_module
from gatekeeper.storage.pool import (
    PoolPathError,
    QuotaExceeded,
    StoragePoolManager,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _entry(path: str, quota_bytes: int) -> StoragePoolEntry:
    return StoragePoolEntry(path=path, quota_bytes=quota_bytes)


def _make_files(directory: Path, sizes: list[int]) -> None:
    """Create files with the given sizes inside *directory*."""
    for i, size in enumerate(sizes):
        f = directory / f"fragment_{i}.blob"
        f.write_bytes(b"\x00" * size)


# ── Startup validation ────────────────────────────────────────────────────────

def test_empty_entries_raises():
    with pytest.raises(PoolPathError, match="at least one path"):
        StoragePoolManager([])


def test_non_existent_path_raises(tmp_path):
    missing = str(tmp_path / "does_not_exist")
    with pytest.raises(PoolPathError, match="not a directory"):
        StoragePoolManager([_entry(missing, 1024 ** 3)])


def test_file_instead_of_directory_raises(tmp_path):
    f = tmp_path / "not_a_dir.txt"
    f.write_text("hello")
    with pytest.raises(PoolPathError, match="not a directory"):
        StoragePoolManager([_entry(str(f), 1024 ** 3)])


@pytest.mark.skipif(sys.platform == "win32", reason="chmod not reliable on Windows")
def test_non_writable_path_raises(tmp_path):
    pool_dir = tmp_path / "readonly"
    pool_dir.mkdir()
    os.chmod(pool_dir, stat.S_IRUSR | stat.S_IXUSR)  # r-x, no write
    try:
        with pytest.raises(PoolPathError, match="not writable"):
            StoragePoolManager([_entry(str(pool_dir), 1024 ** 3)])
    finally:
        os.chmod(pool_dir, stat.S_IRWXU)


# ── Exclusion set ─────────────────────────────────────────────────────────────

def test_excluded_paths_instance_attribute(tmp_path):
    d = tmp_path / "pool"
    d.mkdir()
    manager = StoragePoolManager([_entry(str(d), 1024 ** 3)])
    assert os.path.realpath(str(d)) in manager.excluded_paths


def test_excluded_paths_is_frozenset(tmp_path):
    d = tmp_path / "pool"
    d.mkdir()
    manager = StoragePoolManager([_entry(str(d), 1024 ** 3)])
    assert isinstance(manager.excluded_paths, frozenset)


def test_excluded_paths_module_level_updated(tmp_path):
    d = tmp_path / "pool"
    d.mkdir()
    manager = StoragePoolManager([_entry(str(d), 1024 ** 3)])
    # Module-level EXCLUDED_PATHS must contain the resolved path after init
    assert os.path.realpath(str(d)) in pool_module.EXCLUDED_PATHS
    assert manager.excluded_paths == pool_module.EXCLUDED_PATHS


def test_excluded_paths_multiple_entries(tmp_path):
    d1 = tmp_path / "pool1"
    d2 = tmp_path / "pool2"
    d1.mkdir()
    d2.mkdir()
    manager = StoragePoolManager([
        _entry(str(d1), 1024 ** 3),
        _entry(str(d2), 500 * 1024 ** 2),
    ])
    assert os.path.realpath(str(d1)) in manager.excluded_paths
    assert os.path.realpath(str(d2)) in manager.excluded_paths
    assert len(manager.excluded_paths) == 2


def test_excluded_paths_resolved_through_symlink(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real_dir)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available on this platform")

    manager = StoragePoolManager([_entry(str(link), 1024 ** 3)])
    assert os.path.realpath(str(real_dir)) in manager.excluded_paths
    # The symlink itself should not appear — realpath resolved it
    assert str(link) not in manager.excluded_paths


# ── Initial disk usage from filesystem ───────────────────────────────────────

def test_initial_used_bytes_from_filesystem(tmp_path):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    _make_files(pool_dir, [1000, 2000, 3000])

    quota = 1024 ** 3
    manager = StoragePoolManager([_entry(str(pool_dir), quota)])
    usage = manager.get_usage()[0]

    assert usage["used_bytes"] == 6000
    assert usage["free_bytes"] == quota - 6000


def test_empty_pool_zero_used(tmp_path):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    manager = StoragePoolManager([_entry(str(pool_dir), 1024 ** 3)])
    assert manager.get_usage()[0]["used_bytes"] == 0


# ── get_target_path ───────────────────────────────────────────────────────────

def test_get_target_path_single_path(tmp_path):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    quota = 1024 ** 3
    manager = StoragePoolManager([_entry(str(pool_dir), quota)])
    target = manager.get_target_path(512)
    assert target == os.path.realpath(str(pool_dir))


def test_get_target_path_selects_most_free(tmp_path):
    d1 = tmp_path / "pool1"
    d2 = tmp_path / "pool2"
    d1.mkdir()
    d2.mkdir()

    quota = 1024 ** 3
    manager = StoragePoolManager([
        _entry(str(d1), quota),
        _entry(str(d2), quota),
    ])
    # Fill d1 so d2 has more free space
    manager.register_fragment(os.path.realpath(str(d1)), 600 * 1024 ** 2)

    target = manager.get_target_path(100 * 1024 ** 2)
    assert target == os.path.realpath(str(d2))


def test_get_target_path_quota_exceeded_raises(tmp_path):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    quota = 500  # 500 bytes
    manager = StoragePoolManager([_entry(str(pool_dir), quota)])

    with pytest.raises(QuotaExceeded):
        manager.get_target_path(1000)  # needs more than quota


def test_get_target_path_exactly_at_quota_boundary(tmp_path):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    quota = 1000
    manager = StoragePoolManager([_entry(str(pool_dir), quota)])

    # Exactly quota bytes available — should succeed
    target = manager.get_target_path(1000)
    assert target == os.path.realpath(str(pool_dir))

    # One byte over — should fail
    with pytest.raises(QuotaExceeded):
        manager.get_target_path(1001)


def test_get_target_path_all_full_raises(tmp_path):
    d1 = tmp_path / "pool1"
    d2 = tmp_path / "pool2"
    d1.mkdir()
    d2.mkdir()
    quota = 1000
    manager = StoragePoolManager([
        _entry(str(d1), quota),
        _entry(str(d2), quota),
    ])
    manager.register_fragment(os.path.realpath(str(d1)), quota)
    manager.register_fragment(os.path.realpath(str(d2)), quota)

    with pytest.raises(QuotaExceeded):
        manager.get_target_path(1)


# ── register_fragment / remove_fragment ───────────────────────────────────────

def test_register_fragment_increases_used(tmp_path):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    manager = StoragePoolManager([_entry(str(pool_dir), 1024 ** 3)])
    real = os.path.realpath(str(pool_dir))

    manager.register_fragment(real, 500_000)
    assert manager.get_usage()[0]["used_bytes"] == 500_000


def test_remove_fragment_decreases_used(tmp_path):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    manager = StoragePoolManager([_entry(str(pool_dir), 1024 ** 3)])
    real = os.path.realpath(str(pool_dir))

    manager.register_fragment(real, 500_000)
    manager.remove_fragment(real, 200_000)
    assert manager.get_usage()[0]["used_bytes"] == 300_000


def test_remove_fragment_floors_at_zero(tmp_path):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    manager = StoragePoolManager([_entry(str(pool_dir), 1024 ** 3)])
    real = os.path.realpath(str(pool_dir))

    # Remove more than used — should floor at zero, not go negative
    manager.remove_fragment(real, 999_999_999)
    assert manager.get_usage()[0]["used_bytes"] == 0


def test_register_fragment_round_trip(tmp_path):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    quota = 1024 ** 3
    manager = StoragePoolManager([_entry(str(pool_dir), quota)])
    real = os.path.realpath(str(pool_dir))

    manager.register_fragment(real, 1_000_000)
    manager.remove_fragment(real, 1_000_000)
    assert manager.get_usage()[0]["used_bytes"] == 0


def test_register_unknown_path_raises(tmp_path):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    manager = StoragePoolManager([_entry(str(pool_dir), 1024 ** 3)])

    with pytest.raises(ValueError, match="not in storage pool"):
        manager.register_fragment("/not/a/pool/path", 100)


def test_remove_unknown_path_raises(tmp_path):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    manager = StoragePoolManager([_entry(str(pool_dir), 1024 ** 3)])

    with pytest.raises(ValueError, match="not in storage pool"):
        manager.remove_fragment("/not/a/pool/path", 100)


# ── Thread safety ──────────────────────────────────────────────────────────────

def test_concurrent_register_is_thread_safe(tmp_path):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    quota = 10 * 1024 ** 3
    manager = StoragePoolManager([_entry(str(pool_dir), quota)])
    real = os.path.realpath(str(pool_dir))

    n_threads = 50
    size_each = 1000

    def worker():
        manager.register_fragment(real, size_each)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert manager.get_usage()[0]["used_bytes"] == n_threads * size_each


# ── get_usage ─────────────────────────────────────────────────────────────────

def test_get_usage_returns_all_paths(tmp_path):
    d1 = tmp_path / "pool1"
    d2 = tmp_path / "pool2"
    d1.mkdir()
    d2.mkdir()
    manager = StoragePoolManager([
        _entry(str(d1), 1000),
        _entry(str(d2), 2000),
    ])
    usage = manager.get_usage()
    assert len(usage) == 2
    quotas = {u["quota_bytes"] for u in usage}
    assert quotas == {1000, 2000}


def test_get_usage_free_bytes_correct(tmp_path):
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    quota = 10_000
    manager = StoragePoolManager([_entry(str(pool_dir), quota)])
    real = os.path.realpath(str(pool_dir))

    manager.register_fragment(real, 3_000)
    usage = manager.get_usage()[0]
    assert usage["used_bytes"] == 3_000
    assert usage["free_bytes"] == 7_000
