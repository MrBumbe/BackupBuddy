"""
Runtime lifeboat key management.

The gatekeeper uses a locally stored random 32-byte key (AES-256) to encrypt
lifeboat bundles.  This key lives at DEFAULT_KEY_PATH on the gatekeeper host and
is read at startup — no user input required.

Key lifecycle:
  - First setup  → call generate_key() once.  Key is written to disk (0600).
  - Every restart → call load_key().  If the file is missing, startup aborts.

A missing key file is treated as an error, not a first-run indicator.
Auto-generating a key on a missing file would silently prevent bundle
decryption with the old key and give false confidence.

The passphrase-based recovery kit (task 1.8.2) is a separate mechanism
handled in gatekeeper/lifeboat/recovery_kit.py.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_KEY_PATH = Path("/etc/backup-buddy/lifeboat.key")
_KEY_SIZE = 32  # bytes — AES-256


def _resolve_key_path(key_path: Path) -> Path:
    """Return key_path, substituting BACKUPBUDDY_LIFEBOAT_KEY_PATH when key_path
    is the module default.  This allows two gatekeepers on the same host (e.g.
    smoke tests) to use different key files without changing production code paths.
    """
    if key_path == DEFAULT_KEY_PATH:
        env_override = os.environ.get("BACKUPBUDDY_LIFEBOAT_KEY_PATH")
        if env_override:
            return Path(env_override)
    return key_path


# ── Exceptions ─────────────────────────────────────────────────────────────────

class KeystoreError(Exception):
    """Base exception for keystore failures."""


class KeyNotFoundError(KeystoreError):
    """Raised when the lifeboat key file is absent at startup."""


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_key(key_path: Path = DEFAULT_KEY_PATH) -> bytes:
    """Generate a new 32-byte random key and write it to *key_path*.

    Called once during first-run setup.  Overwrites any existing file at
    *key_path* — do not call on an established installation.

    File is created with permissions 0600 (POSIX).  On Windows the permission
    call is skipped; NTFS ACLs are relied on instead.

    Args:
        key_path: Destination path for the key file.  Defaults to
                  ``/etc/backup-buddy/lifeboat.key`` (overridable via
                  ``BACKUPBUDDY_LIFEBOAT_KEY_PATH`` env var).

    Returns:
        The freshly generated 32-byte key.
    """
    key_path = _resolve_key_path(key_path)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(_KEY_SIZE)
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass  # Windows — rely on NTFS ACLs
    logger.info("Lifeboat key written to %s", key_path)
    return key


def load_key(key_path: Path = DEFAULT_KEY_PATH) -> bytes:
    """Read the runtime lifeboat key from *key_path*.

    Called at every gatekeeper startup.  A missing key file is a critical
    error that must abort startup — it means either the key was never
    generated (wrong host or fresh install without running generate_key) or
    the file was lost.

    Args:
        key_path: Path to the key file.  Defaults to
                  ``/etc/backup-buddy/lifeboat.key`` (overridable via
                  ``BACKUPBUDDY_LIFEBOAT_KEY_PATH`` env var).

    Returns:
        The 32-byte key stored in the file.

    Raises:
        KeyNotFoundError: If *key_path* does not exist.
        KeystoreError:    If the file exists but cannot be read or is the
                          wrong length.
    """
    key_path = _resolve_key_path(key_path)
    if not key_path.exists():
        logger.critical(
            "Lifeboat key not found at %s — cannot start. "
            "Run the first-setup wizard to generate a key.",
            key_path,
        )
        raise KeyNotFoundError(f"Lifeboat key not found: {key_path}")

    try:
        key = key_path.read_bytes()
    except OSError as exc:
        raise KeystoreError(
            f"Failed to read lifeboat key from {key_path}: {exc}"
        ) from exc

    if len(key) != _KEY_SIZE:
        raise KeystoreError(
            f"Lifeboat key at {key_path} is {len(key)} bytes, expected {_KEY_SIZE}"
        )

    return key
