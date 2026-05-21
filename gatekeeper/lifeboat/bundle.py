"""
Lifeboat bundle: serialize and encrypt gatekeeper disaster-recovery data.

Bundle contents (JSON, then AES-256-GCM encrypted with the runtime lifeboat key):
  - version        — int, format version (currently 1)
  - node_privkey   — Tahoe storage node private key text
  - root_dir_cap   — mutable root directory capability
  - catalog_db_b64 — base64-encoded WAL-consistent SQLite snapshot
  - gatekeeper_cfg — raw INI config text

The passphrase-based recovery kit (Argon2id) is a separate path in recovery_kit.py.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import tempfile
from pathlib import Path

from gatekeeper.lifeboat.crypto import decrypt, encrypt
from gatekeeper.lifeboat.keystore import load_key

logger = logging.getLogger(__name__)

_BUNDLE_VERSION = 1
_NODE_PRIVKEY_RELPATH = Path("tahoe") / "storage_node" / "private" / "node.privkey"


def create_bundle(
    data_dir: Path,
    config_path: Path,
    catalog_conn: sqlite3.Connection,
    *,
    key: bytes | None = None,
) -> bytes:
    """Create an encrypted lifeboat bundle from current gatekeeper state.

    Args:
        data_dir:     Gatekeeper data directory (e.g. ~/.backupbuddy).
        config_path:  Path to gatekeeper.cfg.
        catalog_conn: Open SQLite connection to catalog.db; a WAL-safe snapshot
                      is taken via Connection.backup() — the live DB is not read.
        key:          32-byte encryption key.  If None, loaded from keystore.

    Returns:
        Encrypted bundle bytes (nonce || ciphertext).

    Raises:
        FileNotFoundError: If node.privkey or root_dir.cap cannot be read.
        KeyNotFoundError:  If the lifeboat key is missing and key is None.
        ValueError:        If key is provided but is not 32 bytes.
    """
    node_privkey = (data_dir / _NODE_PRIVKEY_RELPATH).read_text(encoding="utf-8").strip()
    root_dir_cap = (data_dir / "root_dir.cap").read_text(encoding="utf-8").strip()
    gatekeeper_cfg = config_path.read_text(encoding="utf-8")
    catalog_snapshot = _snapshot_db(catalog_conn)

    payload = json.dumps({
        "version": _BUNDLE_VERSION,
        "node_privkey": node_privkey,
        "root_dir_cap": root_dir_cap,
        "catalog_db_b64": base64.b64encode(catalog_snapshot).decode("ascii"),
        "gatekeeper_cfg": gatekeeper_cfg,
    }).encode("utf-8")

    if key is None:
        key = load_key()

    logger.info("Creating lifeboat bundle (%d plaintext bytes)", len(payload))
    return encrypt(payload, key)


def extract_bundle(
    encrypted_data: bytes,
    *,
    key: bytes | None = None,
) -> dict:
    """Decrypt and parse a lifeboat bundle produced by create_bundle().

    Args:
        encrypted_data: Bundle bytes as returned by create_bundle().
        key:            32-byte decryption key.  If None, loaded from keystore.

    Returns:
        Dict with keys: version, node_privkey, root_dir_cap,
        catalog_db_b64, gatekeeper_cfg.

    Raises:
        IntegrityError:   If decryption fails (wrong key or tampered data).
        KeyNotFoundError: If the lifeboat key is missing and key is None.
        ValueError:       If key is provided but is not 32 bytes, or JSON is malformed.
    """
    if key is None:
        key = load_key()

    payload_bytes = decrypt(encrypted_data, key)
    return json.loads(payload_bytes.decode("utf-8"))


def _snapshot_db(conn: sqlite3.Connection) -> bytes:
    """Return a WAL-consistent binary snapshot of *conn* as raw bytes."""
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        backup_conn = sqlite3.connect(tmp_path)
        try:
            conn.backup(backup_conn)
        finally:
            backup_conn.close()
        return Path(tmp_path).read_bytes()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
