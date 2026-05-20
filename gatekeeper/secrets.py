"""
Encrypted secrets store for gatekeeper.

Stores SMTP passwords and webhook URLs encrypted at rest using AES-256-GCM.
The encryption key is derived from a machine-specific ID and a per-install
salt via HKDF-SHA256.  Neither the machine ID nor the salt is a secret on
its own — their combination binds the secrets to this specific installation
so they cannot be transferred to another machine by copying the file alone.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import platform
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

_NONCE_SIZE = 16  # bytes — AES-GCM nonce
_KEY_SIZE = 32    # bytes — AES-256


# ── Exceptions ─────────────────────────────────────────────────────────────────

class SecretsError(Exception):
    """Raised when a secret cannot be stored or retrieved."""


# ── Machine ID ─────────────────────────────────────────────────────────────────

def _read_machine_id() -> bytes:
    """Return a stable, machine-specific identifier for key derivation.

    Uses /etc/machine-id on Linux, the Windows MachineGuid registry value on
    Windows, and hostname as a last-resort fallback.  Monkeypatch this function
    in tests to simulate a different machine.
    """
    if sys.platform.startswith("linux"):
        mid = Path("/etc/machine-id")
        if mid.exists():
            return mid.read_bytes().strip()

    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            )
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            winreg.CloseKey(key)
            return value.encode("utf-8")
        except OSError:
            pass

    return platform.node().encode("utf-8")


# ── SecretsStore ───────────────────────────────────────────────────────────────

class SecretsStore:
    """Encrypted key-value store for sensitive credentials.

    All values are encrypted with AES-256-GCM before being written to disk.
    The encryption key is derived from the machine ID and a per-install salt
    so secrets are bound to this installation.

    Args:
        config_dir: Directory where secrets.enc and secrets.salt are stored.
                    Must be an absolute path.  Created if absent.
    """

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._secrets_file = config_dir / "secrets.enc"
        self._salt_file = config_dir / "secrets.salt"

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_secret(self, key: str, value: str) -> None:
        """Encrypt *value* and store it under *key*.  Logs key name only."""
        logger.info("Storing secret: %s", key)
        aesgcm = AESGCM(self._derive_key())
        nonce = os.urandom(_NONCE_SIZE)
        ciphertext = aesgcm.encrypt(nonce, value.encode("utf-8"), None)
        encoded = base64.b64encode(nonce + ciphertext).decode("ascii")

        data = self._load_raw()
        data[key] = encoded
        self._save_raw(data)

    def get_secret(self, key: str) -> str:
        """Decrypt and return the secret stored under *key*.  Logs key name only."""
        logger.debug("Fetching secret: %s", key)
        data = self._load_raw()
        if key not in data:
            raise SecretsError(f"Secret not found: {key!r}")

        raw = base64.b64decode(data[key])
        nonce, ciphertext = raw[:_NONCE_SIZE], raw[_NONCE_SIZE:]
        try:
            aesgcm = AESGCM(self._derive_key())
            return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
        except Exception as exc:
            raise SecretsError(
                f"Failed to decrypt secret {key!r}: {type(exc).__name__}"
            ) from exc

    def delete_secret(self, key: str) -> None:
        """Remove *key* from the secrets store.  No-op if key does not exist."""
        logger.info("Deleting secret: %s", key)
        data = self._load_raw()
        data.pop(key, None)
        self._save_raw(data)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _derive_key(self) -> bytes:
        """Derive a 256-bit AES key from the machine ID and per-install salt."""
        machine_id = _read_machine_id()
        salt = self._get_or_create_salt()
        hkdf = HKDF(
            algorithm=SHA256(),
            length=_KEY_SIZE,
            salt=salt,
            info=b"backupbuddy-secrets-v1",
        )
        return hkdf.derive(machine_id)

    def _get_or_create_salt(self) -> bytes:
        """Load the per-install salt, generating it on first use."""
        if self._salt_file.exists():
            return self._salt_file.read_bytes()
        self._config_dir.mkdir(parents=True, exist_ok=True)
        salt = os.urandom(32)
        self._salt_file.write_bytes(salt)
        try:
            os.chmod(self._salt_file, 0o600)
        except OSError:
            pass  # Windows — permissions enforced by NTFS ACLs instead
        return salt

    def _load_raw(self) -> dict[str, str]:
        if not self._secrets_file.exists():
            return {}
        try:
            return json.loads(self._secrets_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise SecretsError(
                f"Failed to read secrets file: {type(exc).__name__}"
            ) from exc

    def _save_raw(self, data: dict[str, str]) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._secrets_file.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(self._secrets_file, 0o600)
        except OSError:
            pass  # Windows
