#!/usr/bin/env python3
"""
Trigger the NightlyVerifier on a running gatekeeper node.

Opens catalog.db, cluster.db, and root_dir.cap from the data directory,
runs all four verification layers, and prints results as JSON.

Exit code: 0 if all layers pass, 1 otherwise.
Note: Layer 4 returns ok=False with warnings when no lifeboat has been
distributed yet — this is expected in new clusters (not an error condition).

Usage:
  /opt/backup-buddy/.venv/bin/python3 /tmp/run_nightly_verify.py
  /opt/backup-buddy/.venv/bin/python3 /tmp/run_nightly_verify.py \\
      --data-dir /var/lib/backup-buddy --test-restore-files 50
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, "/opt/backup-buddy")

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from gatekeeper.config import VerifyConfig
from gatekeeper.db.catalog import CatalogDB
from gatekeeper.db.cluster import ClusterDB
from gatekeeper.verify.nightly import NightlyVerifier
from gatekeeper.tahoe.client import TahoeClient


def _derive_catalog_key(root_dir_cap: str) -> bytes:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"backupbuddy:catalog:v1",
    )
    return hkdf.derive(root_dir_cap.encode("utf-8"))


async def _run(data_dir: Path, tahoe_port: int, test_restore_files: int) -> dict:
    root_cap_path = data_dir / "root_dir.cap"
    if not root_cap_path.exists():
        print("ERROR: root_dir.cap not found — gatekeeper not yet onboarded",
              file=sys.stderr)
        sys.exit(1)

    root_dir_cap = root_cap_path.read_text(encoding="utf-8").strip()
    catalog_key = _derive_catalog_key(root_dir_cap)

    catalog_db = CatalogDB(str(data_dir / "catalog.db"), catalog_key)
    cluster_db = ClusterDB(str(data_dir / "cluster.db"))

    alerts: list[dict] = []

    async def send_alert(level: str, message: str, detail: str | None = None) -> None:
        entry = {"level": level, "message": message, "detail": detail}
        alerts.append(entry)
        print(f"ALERT [{level.upper()}] {message}", flush=True)
        if detail:
            print(f"  detail: {detail}", flush=True)

    verify_config = VerifyConfig(
        test_restore_enabled=True,
        test_restore_files=test_restore_files,
    )

    try:
        async with TahoeClient(f"http://127.0.0.1:{tahoe_port}") as tahoe:
            verifier = NightlyVerifier(
                verify_config=verify_config,
                catalog=catalog_db,
                cluster=cluster_db,
                tahoe=tahoe,
                root_dir_cap=root_dir_cap,
                send_alert=send_alert,
            )
            result = await verifier.run()
    finally:
        catalog_db.close()
        cluster_db.close()

    def _lr(r):
        if r is None:
            return None
        return {
            "ok": r.ok,
            "warnings": r.warnings,
            "errors": r.errors,
            "detail": r.detail,
        }

    return {
        "overall_ok": result.overall_ok,
        "layer1": _lr(result.layer1),
        "layer2": _lr(result.layer2),
        "layer3": _lr(result.layer3),
        "layer4": _lr(result.layer4),
        "alerts": alerts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run NightlyVerifier on a gatekeeper node"
    )
    parser.add_argument("--data-dir", default="/var/lib/backup-buddy")
    parser.add_argument("--tahoe-port", type=int, default=3456)
    parser.add_argument(
        "--test-restore-files",
        type=int,
        default=50,
        help="number of files to sample in Layer 3 test restore (default: 50)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )

    summary = asyncio.run(
        _run(Path(args.data_dir), args.tahoe_port, args.test_restore_files)
    )

    # Print structured result on stdout so the caller can parse it
    print("VERIFY_RESULT:" + json.dumps(summary), flush=True)
    sys.exit(0 if summary["overall_ok"] else 1)


if __name__ == "__main__":
    main()
