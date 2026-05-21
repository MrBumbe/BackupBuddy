"""
Recovery kit creation and extraction for disaster recovery.

The recovery kit is a passphrase-encrypted bundle containing the critical
material needed to rebuild a gatekeeper from scratch after total hardware loss:
  - node_privkey  — the Tahoe-LAFS node identity key (string, from tahoe.cfg)
  - root_dir_cap  — the master capability for the Tahoe file tree (string)

Wire format: salt (16 bytes) || nonce (16 bytes) || AES-GCM ciphertext
Key derivation: Argon2id(passphrase, salt, time_cost=3, memory_cost=65536, parallelism=4)
Encryption: AES-256-GCM

The passphrase is entered exactly twice over the system's lifetime:
  1. At first setup, when this kit is created.
  2. At full disaster recovery, when extract_recovery_kit() is called.

The passphrase is never written to disk, never logged, and never transmitted.

Callers (e.g. the onboarding wizard) are responsible for:
  - Sourcing node_privkey and root_dir_cap from the Tahoe node configuration.
  - Presenting the resulting bytes to the user as a downloadable .enc file.
  - Requiring the user to confirm they have saved the file before proceeding.
"""

from __future__ import annotations

import json
import os

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gatekeeper.lifeboat.crypto import IntegrityError

_SALT_SIZE = 16   # bytes — random per-encryption salt
_NONCE_SIZE = 16  # bytes — random per-encryption nonce (matches crypto.py)
_KEY_SIZE = 32    # bytes — AES-256
_MIN_DATA_LEN = _SALT_SIZE + _NONCE_SIZE + 16  # 16 = minimum AES-GCM tag


# ── Public API ──────────────────────────────────────────────────────────────────

def create_recovery_kit(
    passphrase: str,
    node_privkey: str,
    root_dir_cap: str,
) -> bytes:
    """Encrypt *node_privkey* and *root_dir_cap* into a recovery kit bundle.

    The passphrase is used to derive a 32-byte AES-256 key via Argon2id.
    A new random salt is generated on every call — two kits for the same
    inputs will differ (different salt and nonce).

    Args:
        passphrase:   User-supplied passphrase.  Never logged or persisted.
        node_privkey: Tahoe-LAFS node identity key string (from tahoe.cfg).
        root_dir_cap: Root directory capability string for the Tahoe file tree.

    Returns:
        Encrypted bundle: salt (16) || nonce (16) || AES-GCM ciphertext.
    """
    salt = os.urandom(_SALT_SIZE)
    key = _derive_key(passphrase, salt)

    payload = json.dumps({
        "node_privkey": node_privkey,
        "root_dir_cap": root_dir_cap,
    }).encode()

    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, payload, None)

    return salt + nonce + ciphertext


def extract_recovery_kit(data: bytes, passphrase: str) -> dict:
    """Decrypt and deserialize a recovery kit bundle.

    Args:
        data:       Bytes as produced by :func:`create_recovery_kit`.
        passphrase: The passphrase that was used when the kit was created.

    Returns:
        Dict with keys ``"node_privkey"`` and ``"root_dir_cap"`` (both str).

    Raises:
        IntegrityError: If *data* is too short, the passphrase is wrong,
                        or the ciphertext has been tampered with.
    """
    if len(data) < _MIN_DATA_LEN:
        raise IntegrityError("Recovery kit data is too short to be valid")

    salt = data[:_SALT_SIZE]
    nonce = data[_SALT_SIZE:_SALT_SIZE + _NONCE_SIZE]
    ciphertext = data[_SALT_SIZE + _NONCE_SIZE:]

    key = _derive_key(passphrase, salt)

    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise IntegrityError(
            "Recovery kit decryption failed — wrong passphrase or tampered data"
        ) from exc

    return json.loads(plaintext)


# ── Internal ────────────────────────────────────────────────────────────────────

def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a 32-byte AES key from *passphrase* using Argon2id.

    Parameters match ADR-007: time_cost=3, memory_cost=65536 KiB, parallelism=4.
    The passphrase is never passed to logging — callers must not log it either.
    """
    return hash_secret_raw(
        secret=passphrase.encode(),
        salt=salt,
        time_cost=3,
        memory_cost=65536,
        parallelism=4,
        hash_len=_KEY_SIZE,
        type=Type.ID,
    )
