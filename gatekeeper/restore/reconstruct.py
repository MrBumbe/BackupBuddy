"""Catalog reconstruction from the Tahoe file tree (ADR-008).

Used when catalog.db is missing or corrupt. Traverses the Tahoe directory
referenced by root_dir.cap, decrypts per-file metadata tags, and inserts
records into a fresh catalog.db.

Sentinel values for fields unknown after reconstruction:
  sha256  = ""        — signals "hash unknown"; restore.py skips verification
  profile = "unknown" — signals reconstructed record
  k = 0, n = 0       — signals unknown fragmentation parameters
  agent   = "unknown" — used when agent_enc tag is unreadable

Files with unreadable metadata are still inserted with original_path = None
so their caps are at least known and files can potentially be restored.
"""

from __future__ import annotations

import asyncio
import logging
from base64 import b64decode

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gatekeeper.db.catalog import CatalogDB
from gatekeeper.fragmenter.fragmenter import derive_metadata_key
from gatekeeper.tahoe.client import TahoeClient

logger = logging.getLogger(__name__)

_NONCE_SIZE = 12
_UNKNOWN_SHA256 = ""
_UNKNOWN_SENTINEL = "unknown"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _decrypt_tag(b64_value: str, key: bytes) -> str:
    """Decrypt a base64-encoded AES-256-GCM tag written by the fragmenter."""
    raw = b64decode(b64_value)
    nonce = raw[:_NONCE_SIZE]
    ct = raw[_NONCE_SIZE:]
    return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")


# ── Public API ────────────────────────────────────────────────────────────────

async def reconstruct_catalog(
    root_dir_cap: str,
    *,
    catalog: CatalogDB,
    tahoe: TahoeClient,
    progress_queue: asyncio.Queue | None = None,
) -> int:
    """Rebuild catalog.db by traversing the Tahoe file tree.

    For each file linked in the root directory, decrypts its ADR-008
    metadata tag to recover original_path, agent, and backed_up_at.
    Inserts a record into catalog with sentinel values for unknown fields
    (sha256="", profile="unknown", k=0, n=0).

    If a metadata tag is missing or unreadable, the file is still inserted
    with original_path=None so its cap is preserved for manual recovery.

    Args:
        root_dir_cap:   Tahoe cap for the root backup directory. Used both
                        as the directory reference for traversal and as IKM
                        for metadata tag key derivation (via derive_metadata_key).
        catalog:        Open CatalogDB instance to insert records into.
        tahoe:          Async TahoeClient for directory listing.
        progress_queue: Optional; a dict {"files_processed": n} is put after
                        each file so the GUI can display progress.

    Returns:
        Number of file records inserted.

    Raises:
        TahoeError: If the root directory cannot be listed.
    """
    metadata_key = derive_metadata_key(root_dir_cap)
    entries = await tahoe.ls_with_metadata(root_dir_cap)

    logger.info(
        "Catalog reconstruction started: %d entries in root directory",
        len(entries),
    )

    count = 0
    for entry in entries:
        tag = entry["metadata"]
        original_path: str | None = None
        agent = _UNKNOWN_SENTINEL
        backed_up_at = 0.0

        try:
            if "original_path_enc" in tag:
                original_path = _decrypt_tag(tag["original_path_enc"], metadata_key)
            if "agent_enc" in tag:
                agent = _decrypt_tag(tag["agent_enc"], metadata_key)
            if "backed_up_at" in tag:
                backed_up_at = float(tag["backed_up_at"])
        except Exception:
            logger.warning(
                "Metadata tag unreadable for entry %.16s — "
                "inserting with original_path=None",
                entry["name"],
            )
            original_path = None

        catalog.insert_file(
            cap=entry["file_ref"],
            sha256=_UNKNOWN_SHA256,
            original_path=original_path,
            agent=agent,
            backed_up_at=backed_up_at,
            size_bytes=entry["size"],
            profile=_UNKNOWN_SENTINEL,
            k=0,
            n=0,
        )
        count += 1

        if progress_queue is not None:
            await progress_queue.put({"files_processed": count})

    logger.info(
        "Catalog reconstruction complete: %d records inserted",
        count,
    )
    return count
