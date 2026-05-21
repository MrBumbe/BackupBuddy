"""Re-fragmentation worker: re-uploads files to the current k/n target.

Responsibilities:
  - Prioritise files: critical (unrestorable in current cluster) first,
    then non-critical sorted by age then size.
  - For each file: download to a secure temp dir, re-upload, overwrite the
    Tahoe directory entry via link_file (same entry_name as fragmenter.py),
    and update catalog.db with the new cap and current k/n.
  - Clean up temp files unconditionally, even on failure.

ADR-011: critical files bypass hysteresis/stability checks.
ADR-018: catalog.db records the active profile k/n at the time of re-upload.
SECURITY.md §6: log only operation names, sizes, agent IDs — never file paths.
SECURITY.md §8: temp dirs 0700, cleaned immediately after each file.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
import time as _time
from base64 import b64encode
from dataclasses import dataclass, field
from typing import Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gatekeeper.db.catalog import CatalogDB
from gatekeeper.tahoe.client import TahoeClient, TahoeError

logger = logging.getLogger(__name__)

_NONCE_SIZE = 12
_CHUNK_SIZE = 65536


# ── Helpers mirrored from fragmenter.py ──────────────────────────────────────
# These must stay in sync with fragmenter.py — they produce the Tahoe directory
# entry names and encrypted tags that the original fragmenter wrote.

def _entry_name(agent: str, original_path: str) -> str:
    """Return the stable 32-hex directory entry name for (agent, path).

    Mirrors fragmenter._entry_name exactly so link_file() overwrites the
    original entry rather than creating a duplicate.
    """
    return hashlib.sha256(f"{agent}:{original_path}".encode("utf-8")).hexdigest()[:32]


def _encrypt_tag(value: str, key: bytes) -> str:
    """AES-256-GCM encrypt a string; return base64-encoded nonce+ciphertext."""
    nonce = os.urandom(_NONCE_SIZE)
    ct = AESGCM(key).encrypt(nonce, value.encode("utf-8"), None)
    return b64encode(nonce + ct).decode("ascii")


def _compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Prioritisation ────────────────────────────────────────────────────────────

def prioritise_files(
    files: list[dict],
    current_cluster_size: int,
) -> tuple[list[dict], list[dict]]:
    """Split catalog records into (critical, non_critical) lists.

    Critical files: catalog.k > current_cluster_size, meaning the file
    cannot be restored with the current number of nodes.
    # TODO(1.13.2): replace this heuristic with actual fragment liveness data
    # from the verifier once task 1.13.2 is implemented.

    Non-critical list is sorted by backed_up_at ASC (oldest first), then
    size_bytes DESC (largest first within the same age) — ADR-011 priority order.
    """
    critical: list[dict] = []
    non_critical: list[dict] = []

    for f in files:
        if f["k"] > current_cluster_size:
            critical.append(f)
        else:
            non_critical.append(f)

    non_critical.sort(key=lambda f: (f["backed_up_at"], -f["size_bytes"]))
    return critical, non_critical


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class RebalanceResult:
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    failed_ids: list[int] = field(default_factory=list)


# ── Per-file re-fragmentation ─────────────────────────────────────────────────

async def _refrag_one(
    file_rec: dict,
    target_profile: str,
    target_k: int,
    target_n: int,
    tahoe_client: TahoeClient,
    catalog_db: CatalogDB,
    root_dir_ref: str,
    metadata_key: bytes,
    temp_dir: str,
) -> None:
    """Re-upload one catalog record to the current k/n target.

    Download → verify SHA-256 → re-upload → overwrite Tahoe directory entry
    → update catalog.db → remove temp file.

    The Tahoe directory entry is overwritten by calling link_file() with the
    same entry_name as the original fragmenter write (SHA-256 of agent:path).
    set_children replaces the entry in place; the old cap is dereferenced and
    Tahoe GC will reclaim the underlying shares.

    Raises TahoeError or ValueError on failure; temp file is always removed.
    """
    file_id = file_rec["id"]
    agent = file_rec["agent"]
    original_path = file_rec.get("original_path")
    old_cap = file_rec["cap"]
    expected_sha256 = file_rec["sha256"]

    if original_path is None:
        raise ValueError(f"file_id={file_id}: original_path is None, skipping re-link")

    dest_path = os.path.join(temp_dir, f"refrag_{file_id}")
    try:
        downloaded_sha256 = await tahoe_client.download(old_cap, dest_path)
    except TahoeError:
        raise

    if downloaded_sha256 != expected_sha256:
        raise ValueError(
            f"file_id={file_id}: SHA-256 mismatch after download "
            f"(expected={expected_sha256[:16]}… got={downloaded_sha256[:16]}…)"
        )

    try:
        new_cap = await tahoe_client.upload(dest_path)
    except TahoeError:
        raise
    finally:
        try:
            os.remove(dest_path)
        except OSError:
            pass

    backed_up_at = _time.time()
    tag = {
        "original_path_enc": _encrypt_tag(original_path, metadata_key),
        "agent_enc":         _encrypt_tag(agent, metadata_key),
        "backed_up_at":      backed_up_at,
    }
    entry = _entry_name(agent, original_path)

    await tahoe_client.link_file(root_dir_ref, entry, new_cap, tag)

    catalog_db.update_file(
        file_id,
        cap=new_cap,
        profile=target_profile,
        k=target_k,
        n=target_n,
        backed_up_at=backed_up_at,
    )

    logger.info(
        "Re-frag complete: file_id=%d agent=%s size=%d profile=%s k=%d n=%d",
        file_id, agent, file_rec["size_bytes"], target_profile, target_k, target_n,
    )


# ── Batch runner ──────────────────────────────────────────────────────────────

async def run_rebalance(
    files: list[dict],
    target_profile: str,
    target_k: int,
    target_n: int,
    tahoe_client: TahoeClient,
    catalog_db: CatalogDB,
    root_dir_ref: str,
    metadata_key: bytes,
    send_alert: Callable | None = None,
) -> RebalanceResult:
    """Re-upload a list of catalog records to the current target k/n.

    Files whose original_path is None are skipped (cannot re-link in Tahoe
    directory without a stable entry name).

    asyncio.CancelledError propagates immediately — do not catch it.
    Each file gets its own isolated temp directory (SECURITY.md §8).
    """
    result = RebalanceResult()

    for file_rec in files:
        if file_rec.get("original_path") is None:
            logger.debug("Skipping file_id=%d: original_path is None", file_rec["id"])
            result.skipped += 1
            continue

        result.processed += 1

        temp_dir = tempfile.mkdtemp(prefix="bb-rebalance-")
        if os.name != "nt":
            os.chmod(temp_dir, 0o700)

        try:
            await _refrag_one(
                file_rec=file_rec,
                target_profile=target_profile,
                target_k=target_k,
                target_n=target_n,
                tahoe_client=tahoe_client,
                catalog_db=catalog_db,
                root_dir_ref=root_dir_ref,
                metadata_key=metadata_key,
                temp_dir=temp_dir,
            )
            result.succeeded += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result.failed += 1
            result.failed_ids.append(file_rec["id"])
            logger.error(
                "Re-frag failed: file_id=%d agent=%s error=%s",
                file_rec["id"], file_rec.get("agent", "?"), type(exc).__name__,
            )
            if send_alert:
                try:
                    await send_alert(
                        f"Re-fragmentation failed for file_id={file_rec['id']}: {exc}"
                    )
                except Exception:
                    pass
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    return result
