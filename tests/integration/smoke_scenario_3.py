#!/usr/bin/env python3
"""
Smoke test Scenario 3: lifeboat bundle restore.

Verifies that after catalog.db and root_dir.cap are destroyed and restored
from a lifeboat bundle, a previously backed-up file can still be restored
with SHA-256 verification passing.

The GK1 gatekeeper daemon is intentionally left running during this scenario:
its Tahoe gateway is needed for restore_file().  After catalog.db is deleted,
the daemon's open fds refer to the original (now-unlinked) inode — those
writes go to the orphaned inode and do not affect the fresh catalog.db
restored from the bundle.

Usage (called by smoke_test.sh after Scenario 1):

    python smoke_scenario_3.py \
        --gk-data-dir /tmp/bb-smoke/gk1 \
        --key-path    /tmp/bb-smoke/gk1-lifeboat.key \
        --tahoe-url   http://127.0.0.1:8582 \
        --restore-dir /tmp/bb-smoke/restore

Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import logging
import os
import sqlite3
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from gatekeeper.db.catalog import CatalogDB
from gatekeeper.lifeboat.bundle import create_bundle, extract_bundle
from gatekeeper.main import _derive_catalog_key
from gatekeeper.restore.restore import restore_file
from gatekeeper.tahoe.client import TahoeClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def run_scenario_3(
    gk_data_dir: Path,
    key_path: Path,
    tahoe_url: str,
    restore_dir: Path,
) -> bool:
    restore_dir.mkdir(parents=True, exist_ok=True)
    config_path = gk_data_dir / "gatekeeper.cfg"
    catalog_db_path = gk_data_dir / "catalog.db"
    root_dir_cap_path = gk_data_dir / "root_dir.cap"

    # --- Step 1: Load lifeboat key ---
    lifeboat_key = key_path.read_bytes()
    if len(lifeboat_key) != 32:
        logger.error("FAIL: lifeboat key must be exactly 32 bytes (got %d)", len(lifeboat_key))
        return False
    logger.info("Lifeboat key loaded from %s", key_path)

    # --- Step 2: Derive catalog key from root_dir.cap ---
    root_dir_cap = root_dir_cap_path.read_text(encoding="utf-8").strip()
    catalog_key = _derive_catalog_key(root_dir_cap)

    # --- Step 3: Confirm at least one restorable file exists before destroying catalog ---
    pre_conn = CatalogDB(str(catalog_db_path), catalog_key)
    try:
        all_files = pre_conn.get_all_files()
    finally:
        pre_conn.close()

    restorable = [
        f for f in all_files
        if f.get("original_path") and f.get("sha256") and f["sha256"] != ""
    ]
    if not restorable:
        logger.error("FAIL: no restorable files in catalog before bundle creation")
        return False
    target = restorable[0]
    logger.info(
        "Catalog has %d file(s); will verify restore of agent=%s after bundle restore",
        len(restorable), target["agent"],
    )

    # --- Step 4: Create lifeboat bundle from current GK1 state ---
    raw_conn = sqlite3.connect(str(catalog_db_path))
    try:
        bundle_bytes = create_bundle(
            data_dir=gk_data_dir,
            config_path=config_path,
            catalog_conn=raw_conn,
            key=lifeboat_key,
        )
    finally:
        raw_conn.close()
    logger.info("Lifeboat bundle created (%d bytes)", len(bundle_bytes))

    # --- Step 5: Simulate disaster — delete catalog.db and root_dir.cap ---
    # WAL and SHM files are also deleted: orphaned WAL files from the running daemon
    # would confuse SQLite when it opens the fresh catalog.db written from the bundle.
    for suffix in ("", "-wal", "-shm"):
        Path(str(catalog_db_path) + suffix).unlink(missing_ok=True)
    root_dir_cap_path.unlink(missing_ok=True)
    logger.info("catalog.db (+ WAL/SHM) and root_dir.cap deleted — disaster simulated")

    # --- Step 6: Decrypt bundle and write restored files to disk ---
    payload = extract_bundle(bundle_bytes, key=lifeboat_key)
    logger.info("Bundle decrypted (version=%d)", payload["version"])

    restored_root_dir_cap = payload["root_dir_cap"]
    root_dir_cap_path.write_text(restored_root_dir_cap, encoding="utf-8")

    catalog_db_bytes = base64.b64decode(payload["catalog_db_b64"])
    catalog_db_path.write_bytes(catalog_db_bytes)
    if sys.platform != "win32":
        os.chmod(catalog_db_path, stat.S_IRUSR | stat.S_IWUSR)
    logger.info(
        "root_dir.cap and catalog.db restored from bundle (%d catalog bytes)",
        len(catalog_db_bytes),
    )

    # --- Step 7: Open restored catalog and verify it is not empty ---
    restored_catalog_key = _derive_catalog_key(restored_root_dir_cap)
    check_conn = CatalogDB(str(catalog_db_path), restored_catalog_key)
    try:
        restored_files = check_conn.get_all_files()
    finally:
        check_conn.close()

    if not restored_files:
        logger.error("FAIL: restored catalog.db is empty")
        return False
    logger.info("Restored catalog.db contains %d file(s)", len(restored_files))

    # --- Step 8: Restore target file from Tahoe using the restored catalog ---
    dest_path = str(restore_dir / "scenario3_restored.bin")
    restored_catalog = CatalogDB(str(catalog_db_path), restored_catalog_key)
    try:
        async with TahoeClient(tahoe_url) as tahoe:
            result = await restore_file(
                original_path=target["original_path"],
                agent=target["agent"],
                dest_path=dest_path,
                catalog=restored_catalog,
                tahoe=tahoe,
            )
    finally:
        restored_catalog.close()

    if not result.success:
        logger.error(
            "FAIL: restore_file returned failure after lifeboat restore: %s",
            result.error,
        )
        return False

    # --- Step 9: Verify SHA-256 against catalog's stored hash ---
    restored_sha256 = _sha256_file(Path(dest_path))
    if restored_sha256 != target["sha256"]:
        logger.error(
            "FAIL: SHA-256 mismatch after lifeboat restore — catalog=%s restored=%s",
            target["sha256"], restored_sha256,
        )
        return False

    logger.info(
        "PASS: file restored and hash-verified after lifeboat restore (sha256=%s…)",
        restored_sha256[:16],
    )
    return True


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--gk-data-dir", required=True, metavar="DIR")
    p.add_argument("--key-path",    required=True, metavar="FILE")
    p.add_argument("--tahoe-url",   required=True, metavar="URL")
    p.add_argument("--restore-dir", required=True, metavar="DIR")
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()
    ok = await run_scenario_3(
        gk_data_dir=Path(args.gk_data_dir).resolve(),
        key_path=Path(args.key_path).resolve(),
        tahoe_url=args.tahoe_url,
        restore_dir=Path(args.restore_dir).resolve(),
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(_main())
