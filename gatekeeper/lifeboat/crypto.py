"""
AES-256-GCM encryption and decryption for lifeboat bundles.

The runtime lifeboat key is a raw 32-byte random key — no passphrase
involved at this layer.  The recovery-kit passphrase path (Argon2id) is
handled separately in gatekeeper/lifeboat/recovery_kit.py (task 1.8.2).

Wire format: nonce (16 bytes) || AES-GCM ciphertext (includes 16-byte tag)
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_SIZE = 16  # bytes — random per-encryption nonce
_KEY_SIZE = 32    # bytes — AES-256 requires a 32-byte key


# ── Exceptions ─────────────────────────────────────────────────────────────────

class IntegrityError(Exception):
    """Raised when decryption fails due to a wrong key or tampered data."""


# ── Encrypt / Decrypt ──────────────────────────────────────────────────────────

def encrypt(data: bytes, key: bytes) -> bytes:
    """Encrypt *data* with AES-256-GCM.

    Returns nonce (16 bytes) prepended to the ciphertext (which includes the
    AES-GCM authentication tag appended by the library).

    Args:
        data: Plaintext bytes to encrypt.
        key:  32-byte AES-256 key from the keystore.

    Raises:
        ValueError: If *key* is not exactly 32 bytes.
    """
    if len(key) != _KEY_SIZE:
        raise ValueError(f"Key must be {_KEY_SIZE} bytes, got {len(key)}")
    nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, data, None)
    return nonce + ciphertext


def decrypt(data: bytes, key: bytes) -> bytes:
    """Decrypt AES-256-GCM *data* produced by :func:`encrypt`.

    Args:
        data: Nonce || ciphertext bytes as produced by :func:`encrypt`.
        key:  32-byte AES-256 key from the keystore.

    Raises:
        ValueError:      If *key* is not exactly 32 bytes.
        IntegrityError:  If decryption fails (wrong key, truncated or
                         tampered ciphertext, or failed authentication tag).
    """
    if len(key) != _KEY_SIZE:
        raise ValueError(f"Key must be {_KEY_SIZE} bytes, got {len(key)}")
    if len(data) <= _NONCE_SIZE:
        raise IntegrityError("Ciphertext is too short to be valid")
    nonce = data[:_NONCE_SIZE]
    ciphertext = data[_NONCE_SIZE:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise IntegrityError(
            "Decryption failed — wrong key or tampered data"
        ) from exc
