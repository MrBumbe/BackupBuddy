"""
Unit tests for gatekeeper/lifeboat/crypto.py, keystore.py, and recovery_kit.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from gatekeeper.lifeboat.crypto import IntegrityError, decrypt, encrypt
from gatekeeper.lifeboat.recovery_kit import create_recovery_kit, extract_recovery_kit
from gatekeeper.lifeboat.keystore import (
    KeyNotFoundError,
    KeystoreError,
    generate_key,
    load_key,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

_KEY = os.urandom(32)  # shared test key — regenerated each test run
_MSG = b"BackupBuddy lifeboat test payload \x00\xff"


# ── crypto.py — encrypt ────────────────────────────────────────────────────────

class TestEncrypt:
    def test_returns_bytes(self):
        result = encrypt(_MSG, _KEY)
        assert isinstance(result, bytes)

    def test_output_longer_than_input(self):
        # nonce (16) + AES-GCM tag (16) overhead means output > input
        result = encrypt(_MSG, _KEY)
        assert len(result) > len(_MSG)

    def test_nonce_randomness(self):
        """Two consecutive encryptions of the same plaintext must produce different output."""
        a = encrypt(_MSG, _KEY)
        b = encrypt(_MSG, _KEY)
        assert a != b

    def test_nonce_is_first_16_bytes(self):
        """The first 16 bytes change between calls; the rest may overlap by chance."""
        a = encrypt(_MSG, _KEY)
        b = encrypt(_MSG, _KEY)
        assert a[:16] != b[:16]

    def test_wrong_key_length_raises(self):
        with pytest.raises(ValueError, match="32 bytes"):
            encrypt(_MSG, b"tooshort")

    def test_empty_plaintext_accepted(self):
        result = encrypt(b"", _KEY)
        assert isinstance(result, bytes)


# ── crypto.py — decrypt ────────────────────────────────────────────────────────

class TestDecrypt:
    def test_roundtrip(self):
        ciphertext = encrypt(_MSG, _KEY)
        assert decrypt(ciphertext, _KEY) == _MSG

    def test_empty_roundtrip(self):
        ciphertext = encrypt(b"", _KEY)
        assert decrypt(ciphertext, _KEY) == b""

    def test_wrong_key_raises_integrity_error(self):
        ciphertext = encrypt(_MSG, _KEY)
        wrong_key = os.urandom(32)
        with pytest.raises(IntegrityError):
            decrypt(ciphertext, wrong_key)

    def test_tampered_ciphertext_raises_integrity_error(self):
        ciphertext = bytearray(encrypt(_MSG, _KEY))
        ciphertext[-1] ^= 0xFF  # flip last byte (inside the GCM tag)
        with pytest.raises(IntegrityError):
            decrypt(bytes(ciphertext), _KEY)

    def test_tampered_nonce_raises_integrity_error(self):
        ciphertext = bytearray(encrypt(_MSG, _KEY))
        ciphertext[0] ^= 0x01  # flip a nonce byte
        with pytest.raises(IntegrityError):
            decrypt(bytes(ciphertext), _KEY)

    def test_truncated_input_raises_integrity_error(self):
        with pytest.raises(IntegrityError):
            decrypt(b"\x00" * 4, _KEY)  # shorter than nonce

    def test_wrong_key_length_raises(self):
        ciphertext = encrypt(_MSG, _KEY)
        with pytest.raises(ValueError, match="32 bytes"):
            decrypt(ciphertext, b"short")

    def test_output_is_not_key(self):
        ciphertext = encrypt(_MSG, _KEY)
        assert _KEY not in ciphertext


# ── keystore.py — generate_key ─────────────────────────────────────────────────

class TestGenerateKey:
    def test_returns_32_bytes(self, tmp_path):
        key_path = tmp_path / "lifeboat.key"
        key = generate_key(key_path)
        assert len(key) == 32

    def test_writes_to_disk(self, tmp_path):
        key_path = tmp_path / "lifeboat.key"
        key = generate_key(key_path)
        assert key_path.exists()
        assert key_path.read_bytes() == key

    def test_creates_parent_directory(self, tmp_path):
        key_path = tmp_path / "subdir" / "lifeboat.key"
        generate_key(key_path)
        assert key_path.exists()

    def test_two_calls_produce_different_keys(self, tmp_path):
        key_a = generate_key(tmp_path / "a.key")
        key_b = generate_key(tmp_path / "b.key")
        assert key_a != key_b

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions not enforced on Windows")
    def test_key_file_permissions(self, tmp_path):
        key_path = tmp_path / "lifeboat.key"
        generate_key(key_path)
        mode = oct(key_path.stat().st_mode & 0o777)
        assert mode == "0o600"


# ── keystore.py — load_key ─────────────────────────────────────────────────────

class TestLoadKey:
    def test_loads_correct_bytes(self, tmp_path):
        key_path = tmp_path / "lifeboat.key"
        generated = generate_key(key_path)
        loaded = load_key(key_path)
        assert loaded == generated

    def test_missing_file_raises_key_not_found_error(self, tmp_path):
        key_path = tmp_path / "does_not_exist.key"
        with pytest.raises(KeyNotFoundError):
            load_key(key_path)

    def test_missing_file_is_not_silently_generated(self, tmp_path):
        key_path = tmp_path / "absent.key"
        try:
            load_key(key_path)
        except KeyNotFoundError:
            pass
        assert not key_path.exists()

    def test_wrong_length_file_raises_keystore_error(self, tmp_path):
        key_path = tmp_path / "bad.key"
        key_path.write_bytes(b"\x00" * 16)  # 16 bytes, not 32
        with pytest.raises(KeystoreError):
            load_key(key_path)

    def test_key_not_found_error_is_subclass_of_keystore_error(self, tmp_path):
        key_path = tmp_path / "absent.key"
        with pytest.raises(KeystoreError):
            load_key(key_path)

    def test_roundtrip_via_generate_then_load(self, tmp_path):
        key_path = tmp_path / "lifeboat.key"
        original = generate_key(key_path)
        loaded = load_key(key_path)
        assert original == loaded
        assert len(loaded) == 32

    def test_loaded_key_works_with_crypto(self, tmp_path):
        """Keys from disk can be used directly with encrypt/decrypt."""
        key_path = tmp_path / "lifeboat.key"
        key = generate_key(key_path)
        loaded = load_key(key_path)
        ciphertext = encrypt(_MSG, loaded)
        assert decrypt(ciphertext, key) == _MSG


# ── recovery_kit.py ─────────────────────────────────────────────────────────────
#
# Argon2id with time_cost=3 and memory_cost=65536 KiB takes ~200–400 ms per call.
# We use a session-scoped fixture to derive the key and create one kit once, then
# reuse the resulting bytes across tests that only need to verify structure or
# tamper with the ciphertext.  Tests that *must* call create_recovery_kit
# independently (salt randomness, wrong-passphrase) each pay the full cost.

_PASSPHRASE = "correct-horse-battery-staple"
_NODE_PRIVKEY = "priv-v0-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa="
_ROOT_DIR_CAP = "URI:DIR2:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.fixture(scope="session")
def recovery_kit_bytes():
    """Create one kit for the session — avoid paying Argon2 cost repeatedly."""
    return create_recovery_kit(_PASSPHRASE, _NODE_PRIVKEY, _ROOT_DIR_CAP)


class TestCreateRecoveryKit:
    def test_returns_bytes(self, recovery_kit_bytes):
        assert isinstance(recovery_kit_bytes, bytes)

    def test_minimum_length(self, recovery_kit_bytes):
        # salt(16) + nonce(16) + AES-GCM tag(16) minimum
        assert len(recovery_kit_bytes) >= 48

    def test_salt_randomness(self):
        """Two kits with identical inputs must differ (independent random salts)."""
        kit_a = create_recovery_kit(_PASSPHRASE, _NODE_PRIVKEY, _ROOT_DIR_CAP)
        kit_b = create_recovery_kit(_PASSPHRASE, _NODE_PRIVKEY, _ROOT_DIR_CAP)
        assert kit_a != kit_b
        # Salts (first 16 bytes) differ
        assert kit_a[:16] != kit_b[:16]

    def test_payload_not_in_plaintext(self, recovery_kit_bytes):
        assert _NODE_PRIVKEY.encode() not in recovery_kit_bytes
        assert _ROOT_DIR_CAP.encode() not in recovery_kit_bytes
        assert _PASSPHRASE.encode() not in recovery_kit_bytes


class TestExtractRecoveryKit:
    def test_roundtrip(self, recovery_kit_bytes):
        result = extract_recovery_kit(recovery_kit_bytes, _PASSPHRASE)
        assert result["node_privkey"] == _NODE_PRIVKEY
        assert result["root_dir_cap"] == _ROOT_DIR_CAP

    def test_returns_dict_with_expected_keys(self, recovery_kit_bytes):
        result = extract_recovery_kit(recovery_kit_bytes, _PASSPHRASE)
        assert set(result.keys()) == {"node_privkey", "root_dir_cap"}

    def test_wrong_passphrase_raises_integrity_error(self, recovery_kit_bytes):
        with pytest.raises(IntegrityError):
            extract_recovery_kit(recovery_kit_bytes, "wrong-passphrase")

    def test_tampered_ciphertext_raises_integrity_error(self, recovery_kit_bytes):
        tampered = bytearray(recovery_kit_bytes)
        tampered[-1] ^= 0xFF
        with pytest.raises(IntegrityError):
            extract_recovery_kit(bytes(tampered), _PASSPHRASE)

    def test_tampered_nonce_raises_integrity_error(self, recovery_kit_bytes):
        tampered = bytearray(recovery_kit_bytes)
        tampered[16] ^= 0x01  # first nonce byte (after the 16-byte salt)
        with pytest.raises(IntegrityError):
            extract_recovery_kit(bytes(tampered), _PASSPHRASE)

    def test_tampered_salt_raises_integrity_error(self, recovery_kit_bytes):
        tampered = bytearray(recovery_kit_bytes)
        tampered[0] ^= 0x01  # first salt byte
        with pytest.raises(IntegrityError):
            extract_recovery_kit(bytes(tampered), _PASSPHRASE)

    def test_truncated_input_raises_integrity_error_without_argon2(self):
        # Data shorter than salt+nonce+tag must be rejected before Argon2 runs
        with pytest.raises(IntegrityError):
            extract_recovery_kit(b"\x00" * 10, _PASSPHRASE)

    def test_empty_passphrase_roundtrip(self):
        """Empty passphrase is technically valid — Argon2 handles it."""
        kit = create_recovery_kit("", _NODE_PRIVKEY, _ROOT_DIR_CAP)
        result = extract_recovery_kit(kit, "")
        assert result["node_privkey"] == _NODE_PRIVKEY

    def test_integrity_error_is_from_crypto_module(self, recovery_kit_bytes):
        """recovery_kit reuses crypto.IntegrityError — not a separate exception."""
        from gatekeeper.lifeboat.crypto import IntegrityError as CryptoIntegrityError
        with pytest.raises(CryptoIntegrityError):
            extract_recovery_kit(recovery_kit_bytes, "bad-passphrase")
