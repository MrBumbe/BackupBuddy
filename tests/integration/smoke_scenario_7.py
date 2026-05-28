#!/usr/bin/env python3
"""
Smoke test Scenario 7: fragment corruption detection.

Deliberately corrupts ALL share files in both GK1 and GK2 storage
directories, then runs the nightly verifier and asserts that Layer 3
(test restore) detects the corruption via SHA-256 mismatch.

With the smoke test profile (k=1/n=2) and two storage nodes, corrupting
only one storage node's shares leaves k=1 good share in the other node —
enough for Tahoe to reconstruct successfully.  All shares across both nodes
must be corrupted so no good data remains.

Usage (called by smoke_test.sh after Scenarios 1 and 3):

    python smoke_scenario_7.py \\
        --gk-data-dir     /tmp/bb-smoke/gk1 \\
        --gk1-storage-dir /tmp/bb-smoke/gk1-storage \\
        --gk2-storage-dir /tmp/bb-smoke/gk2-storage \\
        --tahoe-url       http://127.0.0.1:8582 \\
        --restore-dir     /tmp/bb-smoke/restore

Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from gatekeeper.config import VerifyConfig
from gatekeeper.db.catalog import CatalogDB
from gatekeeper.db.cluster import ClusterDB
from gatekeeper.main import _derive_catalog_key
from gatekeeper.tahoe.client import TahoeClient
from gatekeeper.verify.nightly import NightlyVerifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _find_share_files(storage_dir: Path) -> list[Path]:
    """Find all Tahoe share files under storage_dir/shares/.

    Tahoe stores shares as integer-named files:
    <storage_dir>/shares/<2hex>/<storage_index>/<N>
    """
    shares_root = storage_dir / "shares"
    if not shares_root.exists():
        return []
    found = []
    for root, _dirs, files in os.walk(str(shares_root)):
        for fname in files:
            if fname.isdigit():
                fpath = Path(root) / fname
                try:
                    if fpath.stat().st_size > 0:
                        found.append(fpath)
                except OSError:
                    pass
    return found


def _corrupt_share(share_path: Path) -> None:
    """Overwrite 100 bytes at offset 500 with random garbage."""
    garbage = os.urandom(100)
    with open(share_path, "r+b") as fh:
        fh.seek(500)
        fh.write(garbage)


async def run_scenario_7(
    gk_data_dir: Path,
    gk1_storage_dir: Path,
    gk2_storage_dir: Path,
    tahoe_url: str,
    restore_dir: Path,
) -> bool:
    restore_dir.mkdir(parents=True, exist_ok=True)

    root_dir_cap_path = gk_data_dir / "root_dir.cap"
    catalog_db_path = gk_data_dir / "catalog.db"
    cluster_db_path = gk_data_dir / "cluster.db"

    # --- Step 1: Open catalog and cluster databases ---
    root_dir_cap = root_dir_cap_path.read_text(encoding="utf-8").strip()
    catalog_key = _derive_catalog_key(root_dir_cap)

    catalog = CatalogDB(str(catalog_db_path), catalog_key)
    cluster = ClusterDB(str(cluster_db_path))
    try:
        # --- Step 2: Confirm at least one restorable file exists ---
        all_files = catalog.get_all_files()
        restorable = [
            f for f in all_files
            if f.get("original_path") and f.get("agent")
        ]
        if not restorable:
            logger.error(
                "FAIL: no restorable files in catalog — run Scenario 1 first"
            )
            return False
        logger.info("Catalog contains %d restorable file(s)", len(restorable))

        # --- Step 3: Find all share files across both storage nodes ---
        gk1_shares = _find_share_files(gk1_storage_dir)
        gk2_shares = _find_share_files(gk2_storage_dir)
        all_shares = gk1_shares + gk2_shares

        if not all_shares:
            logger.error(
                "FAIL: no share files found in GK1=%s or GK2=%s — "
                "check that Scenario 1 completed successfully",
                gk1_storage_dir,
                gk2_storage_dir,
            )
            return False

        logger.info(
            "Found %d share file(s): %d in GK1, %d in GK2",
            len(all_shares),
            len(gk1_shares),
            len(gk2_shares),
        )

        # --- Step 4: Corrupt ALL shares — disaster simulated ---
        for share_path in all_shares:
            _corrupt_share(share_path)
            logger.info("Corrupted share: %s", share_path)
        logger.info(
            "All %d share file(s) corrupted — no good data remains in grid",
            len(all_shares),
        )

        # --- Step 5: Run nightly verifier ---
        alerts: list[tuple[str, str, str | None]] = []

        async def capture_alert(
            level: str, message: str, detail: str | None = None
        ) -> None:
            alerts.append((level, message, detail))

        verify_config = VerifyConfig(
            test_restore_enabled=True,
            test_restore_files=len(restorable),
            test_restore_path=str(restore_dir / "scenario7_verify"),
        )

        async with TahoeClient(tahoe_url) as tahoe:
            verifier = NightlyVerifier(
                verify_config=verify_config,
                catalog=catalog,
                cluster=cluster,
                tahoe=tahoe,
                root_dir_cap=root_dir_cap,
                send_alert=capture_alert,
            )
            result = await verifier.run()

        # --- Step 6: Assert that Layer 3 detected the corruption ---

        # Layer 4 always emits a "warning" alert in the smoke test environment
        # because no lifeboat has been distributed (cluster.get_last_lifeboat_status()
        # returns None).  Filter to error/critical to isolate corruption failures.
        error_alerts = [
            (level, msg, detail)
            for level, msg, detail in alerts
            if level in ("error", "critical")
        ]

        layer3_detected = (
            result.layer3 is not None
            and not result.layer3.ok
            and result.layer3.errors > 0
        )

        if not layer3_detected:
            logger.error(
                "FAIL: Layer 3 (test restore) did not detect corruption — "
                "layer3=%s all_alerts=%s",
                result.layer3,
                alerts,
            )
            return False

        logger.info(
            "PASS: Layer 3 detected corruption (layer3.errors=%d, "
            "error/critical alerts=%d)",
            result.layer3.errors,
            len(error_alerts),
        )

        if not error_alerts:
            logger.warning(
                "Layer 3 detected corruption but no error/critical alerts were raised — "
                "check NightlyVerifier._alert logic"
            )

        return True

    finally:
        catalog.close()
        cluster.close()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--gk-data-dir",     required=True, metavar="DIR")
    p.add_argument("--gk1-storage-dir", required=True, metavar="DIR")
    p.add_argument("--gk2-storage-dir", required=True, metavar="DIR")
    p.add_argument("--tahoe-url",       required=True, metavar="URL")
    p.add_argument("--restore-dir",     required=True, metavar="DIR")
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()
    ok = await run_scenario_7(
        gk_data_dir=Path(args.gk_data_dir).resolve(),
        gk1_storage_dir=Path(args.gk1_storage_dir).resolve(),
        gk2_storage_dir=Path(args.gk2_storage_dir).resolve(),
        tahoe_url=args.tahoe_url,
        restore_dir=Path(args.restore_dir).resolve(),
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(_main())
