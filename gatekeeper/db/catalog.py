"""Catalog database access layer.

Stores per-file backup metadata in SQLite (WAL mode, permissions 0600).

DESIGN NOTE (2026-05-20): This module is crypto-aware (Alt A design decision).
It accepts plaintext cap/original_path strings, encrypts them internally with
AES-256-GCM, and decrypts on read.  The key is derived from root_dir.cap context
by the caller (fragmenter) and passed at CatalogDB.__init__.

If this design is revised (e.g. moving encryption to the fragmenter, Alt B), the
following must change together: this module, 001_catalog_init.sql, the fragmenter
(gatekeeper/fragmenter/), and every call-site that constructs CatalogDB.
See project history for the 2026-05-20 design flag and the ADR-008 NULL note.
"""

import hashlib
import hmac as _hmac
import logging
import os
import re
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations" / "catalog"
_NONCE_SIZE = 12  # bytes — AES-GCM standard 96-bit nonce

# Columns that update_file() is allowed to touch.  Column names are
# whitelisted here so they can be used in a dynamic SET clause safely.
_UPDATABLE_FIELDS = frozenset({
    "cap", "sha256", "original_path", "agent",
    "backed_up_at", "size_bytes", "profile", "k", "n",
})


def _derive_keys(master_key: bytes) -> tuple[bytes, bytes]:
    enc_key = hashlib.sha256(master_key + b"\x00enc").digest()
    idx_key = hashlib.sha256(master_key + b"\x00idx").digest()
    return enc_key, idx_key


def _encrypt(plaintext: str, enc_key: bytes) -> bytes:
    nonce = os.urandom(_NONCE_SIZE)
    ct = AESGCM(enc_key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ct


def _decrypt(ciphertext: bytes, enc_key: bytes) -> str:
    nonce = ciphertext[:_NONCE_SIZE]
    ct = ciphertext[_NONCE_SIZE:]
    return AESGCM(enc_key).decrypt(nonce, ct, None).decode("utf-8")


def _path_hmac(path: str, idx_key: bytes) -> bytes:
    return _hmac.new(idx_key, path.encode("utf-8"), hashlib.sha256).digest()


class CatalogDB:
    """SQLite-backed catalog of backed-up files.

    All cap and original_path values are encrypted at rest.
    Call close() when done, or use as a context manager.
    """

    def __init__(self, db_path: str, key: bytes) -> None:
        """Open (or create) the catalog database.

        Args:
            db_path: Path to the SQLite file.  Created if absent.
            key:     32-byte master key derived from root_dir.cap context.
        """
        if len(key) != 32:
            raise ValueError("key must be exactly 32 bytes")

        self._enc_key, self._idx_key = _derive_keys(key)

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        # 0600: owner read/write only.  No-op on Windows; enforced on Linux.
        if sys.platform != "win32":
            os.chmod(db_path, stat.S_IRUSR | stat.S_IWUSR)

        self._run_migrations()
        logger.info("CatalogDB opened at %s", db_path)

    # ------------------------------------------------------------------
    # Migration runner
    # ------------------------------------------------------------------

    def _run_migrations(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER NOT NULL DEFAULT 0)"
        )
        if self._conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
            self._conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        self._conn.commit()

        current: int = self._conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0]

        migration_files = sorted(
            f for f in _MIGRATIONS_DIR.glob("*.sql")
            if re.match(r"^\d+_", f.name)
        )

        for mf in migration_files:
            num = int(mf.name.split("_")[0])
            if num <= current:
                continue
            logger.info("Applying catalog migration %s", mf.name)
            self._conn.executescript(mf.read_text(encoding="utf-8"))
            self._conn.execute("UPDATE schema_version SET version = ?", (num,))
            self._conn.commit()
            logger.info("Catalog schema now at version %d", num)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def insert_file(
        self,
        cap: str,
        sha256: str,
        original_path: str | None,
        agent: str,
        backed_up_at: float,
        size_bytes: int,
        profile: str,
        k: int,
        n: int,
    ) -> int:
        """Insert a new file record.  Returns the new row id."""
        enc_cap = _encrypt(cap, self._enc_key)
        enc_path = _encrypt(original_path, self._enc_key) if original_path is not None else None
        hmac_val = _path_hmac(original_path, self._idx_key) if original_path is not None else None

        cursor = self._conn.execute(
            "INSERT INTO files "
            "(cap, sha256, original_path, path_hmac, agent, "
            "backed_up_at, size_bytes, profile, k, n) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (enc_cap, sha256, enc_path, hmac_val, agent,
             backed_up_at, size_bytes, profile, k, n),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_file_by_path(self, agent: str, path: str) -> dict | None:
        """Return the most recent record for agent+path, or None."""
        hmac_val = _path_hmac(path, self._idx_key)
        row = self._conn.execute(
            "SELECT * FROM files "
            "WHERE agent = ? AND path_hmac = ? "
            "ORDER BY backed_up_at DESC LIMIT 1",
            (agent, hmac_val),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_all_files(self) -> list[dict]:
        """Return all file records, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM files ORDER BY backed_up_at DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_files_since(self, timestamp: float) -> list[dict]:
        """Return all file records backed up after timestamp, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM files WHERE backed_up_at > ? ORDER BY backed_up_at DESC",
            (timestamp,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update_file(self, file_id: int, **fields: Any) -> None:
        """Update one or more fields on a file record by id.

        Encrypted fields (cap, original_path) are re-encrypted automatically.
        Unknown field names raise ValueError.
        """
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"Unknown fields: {unknown}")
        if not fields:
            return

        params: dict[str, Any] = {}
        for field, value in fields.items():
            if field == "cap" and value is not None:
                params["cap"] = _encrypt(value, self._enc_key)
            elif field == "original_path":
                params["original_path"] = _encrypt(value, self._enc_key) if value is not None else None
                params["path_hmac"] = _path_hmac(value, self._idx_key) if value is not None else None
            else:
                params[field] = value

        # Column names come exclusively from _UPDATABLE_FIELDS whitelist above —
        # no user input can reach the column-name portion of this query.
        set_clause = ", ".join(f"{col} = ?" for col in params)
        self._conn.execute(  # noqa: S608
            f"UPDATE files SET {set_clause} WHERE id = ?",  # noqa: S608
            (*params.values(), file_id),
        )
        self._conn.commit()

    def delete_file(self, file_id: int) -> None:
        """Delete a file record by id.  No-op if id does not exist."""
        self._conn.execute(
            "DELETE FROM files WHERE id = ?",
            (file_id,),
        )
        self._conn.commit()

    def get_last_backup_per_agent(self) -> list[dict]:
        """Return the most recent backup timestamp and file count per agent.

        Uses only plaintext columns — no decryption required.
        """
        rows = self._conn.execute(
            "SELECT agent, MAX(backed_up_at) AS last_backup_at, COUNT(*) AS file_count "
            "FROM files GROUP BY agent"
        ).fetchall()
        return [dict(r) for r in rows]

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the raw SQLite connection (e.g. for WAL-safe backups in bundle.py)."""
        return self._conn

    def close(self) -> None:
        self._conn.close()
        logger.info("CatalogDB closed")

    def __enter__(self) -> "CatalogDB":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["cap"] = _decrypt(d["cap"], self._enc_key)
        if d["original_path"] is not None:
            d["original_path"] = _decrypt(d["original_path"], self._enc_key)
        d.pop("path_hmac", None)
        return d
