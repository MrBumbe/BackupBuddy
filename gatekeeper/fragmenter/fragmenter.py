"""
Fragmenter: uploads files to the Tahoe grid and records them in catalog.db.

Responsibilities:
  - SHA-256 verification before and after upload (CLAUDE.md hash pattern)
  - Encrypted metadata tag on each Tahoe directory entry (ADR-008 call-home)
  - catalog.db insertion on success

ADR-018: the active fragmentation profile is a node-level Tahoe setting.
All uploads in a session use the k/n configured at startup.  The fragmenter
reads k/n from profiles.py for catalog recording and metadata; it does not
change the Tahoe node configuration at upload time.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time as _time
from base64 import b64encode

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from gatekeeper.db.catalog import CatalogDB
from gatekeeper.fragmenter.profiles import get_profile
from gatekeeper.tahoe.client import TahoeClient, TahoeError

logger = logging.getLogger(__name__)

_NONCE_SIZE = 12   # AES-GCM standard 96-bit nonce
_CHUNK_SIZE = 65536


# ── Exceptions ────────────────────────────────────────────────────────────────

class FragmentationError(Exception):
    """Raised when a file changes during upload or the upload fails."""


# ── Key derivation ────────────────────────────────────────────────────────────

def derive_metadata_key(root_dir_cap: str) -> bytes:
    """Derive a 32-byte AES-256-GCM key for Tahoe directory metadata tags.

    Uses a different HKDF info string than the catalog key so the two
    encryption contexts are cryptographically separated even though both
    derive from root_dir.cap.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"backupbuddy:metadata_tag:v1",
    )
    return hkdf.derive(root_dir_cap.encode("utf-8"))


# ── Internal helpers ──────────────────────────────────────────────────────────

def _compute_sha256(path: str) -> str:
    """Return the hex SHA-256 digest of a file, reading in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def _encrypt_tag(value: str, key: bytes) -> str:
    """AES-256-GCM encrypt a string; return base64-encoded nonce+ciphertext."""
    nonce = os.urandom(_NONCE_SIZE)
    ct = AESGCM(key).encrypt(nonce, value.encode("utf-8"), None)
    return b64encode(nonce + ct).decode("ascii")


def _entry_name(agent: str, original_path: str) -> str:
    """Return a stable 32-hex directory entry name for an (agent, path) pair.

    Using SHA-256(agent:path) ensures each unique file per agent gets its
    own entry; re-uploading the same file overwrites the entry (idempotent).
    """
    return hashlib.sha256(f"{agent}:{original_path}".encode("utf-8")).hexdigest()[:32]


# ── Fragmenter ────────────────────────────────────────────────────────────────

class Fragmenter:
    """Uploads files to the Tahoe grid and records them in catalog.db.

    All dependencies are injected so that unit tests can supply mocks
    without touching real Tahoe nodes or databases.

    Args:
        tahoe_client:  Async Tahoe HTTP client (gatekeeper/tahoe/client.py).
        catalog_db:    Backup catalog database (gatekeeper/db/catalog.py).
        root_dir_ref:  Read-write Tahoe directory cap for the backup tree root.
                       Used to link uploaded files with their metadata tags.
        metadata_key:  32-byte AES-256-GCM key for encrypting directory entry
                       metadata (ADR-008).  Must be derived from root_dir.cap
                       via derive_metadata_key(), not from the catalog key.
    """

    def __init__(
        self,
        tahoe_client: TahoeClient,
        catalog_db: CatalogDB,
        root_dir_ref: str,
        metadata_key: bytes,
    ) -> None:
        if len(metadata_key) != 32:
            raise ValueError("metadata_key must be exactly 32 bytes")
        self._client = tahoe_client
        self._catalog = catalog_db
        self._root_dir_ref = root_dir_ref
        self._metadata_key = metadata_key

    async def fragment_and_upload(
        self,
        file_path: str,
        profile: str,
        agent: str,
        original_path: str,
    ) -> str:
        """Upload a file to the Tahoe grid and record it in catalog.db.

        Steps:
          1. Resolve k/n from profile (ValueError if unknown or adaptive).
          2. Compute SHA-256 before upload.
          3. Upload file via Tahoe client (Tahoe applies erasure coding with
             k/n configured at node level per ADR-018).
          4. Compute SHA-256 after upload (re-reads file from disk).
          5. Raise FragmentationError if hashes differ (file changed mid-upload).
          6. Link file into root directory with encrypted metadata (ADR-008).
          7. Insert record into catalog.db.

        Args:
            file_path:     Absolute path to the file on the local filesystem.
            profile:       Fragmentation profile name ("balanced", "secure",
                           "paranoid").  "adaptive" is not supported here —
                           see gatekeeper/fragmenter/adaptive.py (task 1.11.1).
            agent:         Agent name (identifies which device owns this file).
            original_path: Original path as reported by the agent; stored
                           encrypted in both catalog.db and the Tahoe directory
                           entry for call-home reconstruction.

        Returns:
            The opaque Tahoe file reference (stored as cap in catalog.db).

        Raises:
            ValueError:          Unknown profile name.
            FragmentationError:  File changed during upload, or upload failed.
        """
        kn = get_profile(profile)  # raises ValueError for unknown / adaptive

        hash_before = await asyncio.to_thread(_compute_sha256, file_path)
        logger.debug(
            "Upload starting: agent=%s profile=%s hash=%.16s…",
            agent, profile, hash_before,
        )

        try:
            file_ref = await self._client.upload(file_path)
        except TahoeError as exc:
            raise FragmentationError(f"Upload failed: {exc}") from exc

        hash_after = await asyncio.to_thread(_compute_sha256, file_path)

        if hash_before != hash_after:
            # File was modified while we were uploading it.  The Tahoe cap
            # may represent partial or inconsistent content.  Discard it and
            # let the queue_worker re-queue the file.
            logger.warning(
                "File changed during upload — discarding. "
                "agent=%s hash_before=%.16s… hash_after=%.16s…",
                agent, hash_before, hash_after,
            )
            raise FragmentationError("File changed during upload — retry queued")

        backed_up_at = _time.time()

        # Build encrypted metadata tag for ADR-008 call-home reconstruction.
        # Only the holder of root_dir.cap (from which metadata_key is derived)
        # can decrypt these tags — consistent with the zero-knowledge design.
        tag = {
            "original_path_enc": _encrypt_tag(original_path, self._metadata_key),
            "agent_enc":         _encrypt_tag(agent, self._metadata_key),
            "backed_up_at":      backed_up_at,
        }
        entry_name = _entry_name(agent, original_path)

        try:
            await self._client.link_file(
                self._root_dir_ref, entry_name, file_ref, tag
            )
        except TahoeError as exc:
            raise FragmentationError(
                f"Failed to link file into root directory: {exc}"
            ) from exc

        file_size = os.path.getsize(file_path)
        self._catalog.insert_file(
            cap=file_ref,
            sha256=hash_before,
            original_path=original_path,
            agent=agent,
            backed_up_at=backed_up_at,
            size_bytes=file_size,
            profile=profile,
            k=kn.k,
            n=kn.n,
        )

        logger.info(
            "Upload complete: agent=%s size=%d profile=%s k=%d n=%d",
            agent, file_size, profile, kn.k, kn.n,
        )
        return file_ref
