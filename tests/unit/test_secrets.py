"""
Unit tests for gatekeeper/secrets.py.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gatekeeper.secrets import SecretsError, SecretsStore


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path: Path) -> SecretsStore:
    return SecretsStore(config_dir=tmp_path)


# ── set_secret / get_secret round-trip ────────────────────────────────────────

class TestRoundTrip:
    def test_set_and_get_returns_original_value(self, store: SecretsStore) -> None:
        store.set_secret("smtp_password", "hunter2")
        assert store.get_secret("smtp_password") == "hunter2"

    def test_set_and_get_webhook_url(self, store: SecretsStore) -> None:
        store.set_secret("webhook_url", "https://hooks.example.com/token/abc123")
        assert store.get_secret("webhook_url") == "https://hooks.example.com/token/abc123"

    def test_multiple_secrets_stored_independently(self, store: SecretsStore) -> None:
        store.set_secret("smtp_password", "pass1")
        store.set_secret("webhook_url", "https://example.com/hook")
        assert store.get_secret("smtp_password") == "pass1"
        assert store.get_secret("webhook_url") == "https://example.com/hook"

    def test_overwrite_updates_existing_key(self, store: SecretsStore) -> None:
        store.set_secret("smtp_password", "old_password")
        store.set_secret("smtp_password", "new_password")
        assert store.get_secret("smtp_password") == "new_password"

    def test_empty_string_value_round_trips(self, store: SecretsStore) -> None:
        store.set_secret("empty", "")
        assert store.get_secret("empty") == ""

    def test_unicode_value_round_trips(self, store: SecretsStore) -> None:
        store.set_secret("unicode_key", "pässwörd_åäö")
        assert store.get_secret("unicode_key") == "pässwörd_åäö"


# ── Secrets file permissions ───────────────────────────────────────────────────

class TestFilePermissions:
    @pytest.mark.skipif(
        __import__("sys").platform == "win32",
        reason="POSIX permissions not enforced on Windows",
    )
    def test_secrets_file_is_0600(self, store: SecretsStore, tmp_path: Path) -> None:
        store.set_secret("smtp_password", "secret")
        secrets_file = tmp_path / "secrets.enc"
        mode = oct(secrets_file.stat().st_mode & 0o777)
        assert mode == oct(0o600), f"Expected 0600, got {mode}"

    @pytest.mark.skipif(
        __import__("sys").platform == "win32",
        reason="POSIX permissions not enforced on Windows",
    )
    def test_salt_file_is_0600(self, store: SecretsStore, tmp_path: Path) -> None:
        store.set_secret("smtp_password", "secret")
        salt_file = tmp_path / "secrets.salt"
        mode = oct(salt_file.stat().st_mode & 0o777)
        assert mode == oct(0o600), f"Expected 0600, got {mode}"


# ── Ciphertext is not plaintext ────────────────────────────────────────────────

class TestNotPlaintext:
    def test_secrets_file_does_not_contain_plaintext_value(
        self, store: SecretsStore, tmp_path: Path
    ) -> None:
        store.set_secret("smtp_password", "hunter2")
        raw = (tmp_path / "secrets.enc").read_bytes()
        assert b"hunter2" not in raw

    def test_secrets_file_does_not_contain_plaintext_url(
        self, store: SecretsStore, tmp_path: Path
    ) -> None:
        store.set_secret("webhook_url", "https://secret.example.com/hook")
        raw = (tmp_path / "secrets.enc").read_bytes()
        assert b"https://secret.example.com/hook" not in raw

    def test_secrets_file_is_valid_json_of_encoded_blobs(
        self, store: SecretsStore, tmp_path: Path
    ) -> None:
        store.set_secret("k", "v")
        data = json.loads((tmp_path / "secrets.enc").read_text())
        assert isinstance(data, dict)
        assert "k" in data
        assert isinstance(data["k"], str)  # base64-encoded ciphertext

    def test_two_encryptions_of_same_value_produce_different_ciphertexts(
        self, store: SecretsStore, tmp_path: Path
    ) -> None:
        """Each call uses a random nonce — same plaintext must not produce same ciphertext."""
        store.set_secret("k", "same_value")
        first = json.loads((tmp_path / "secrets.enc").read_text())["k"]
        store.set_secret("k", "same_value")
        second = json.loads((tmp_path / "secrets.enc").read_text())["k"]
        assert first != second


# ── Error cases ────────────────────────────────────────────────────────────────

class TestErrors:
    def test_get_missing_key_raises_secrets_error(self, store: SecretsStore) -> None:
        with pytest.raises(SecretsError, match="not found"):
            store.get_secret("no_such_key")

    def test_wrong_machine_id_raises_on_decrypt(
        self, store: SecretsStore
    ) -> None:
        """Secrets are bound to the machine: a different ID must fail to decrypt."""
        store.set_secret("smtp_password", "secret_value")

        with patch(
            "gatekeeper.secrets._read_machine_id",
            return_value=b"different-machine-id-9999",
        ):
            with pytest.raises(SecretsError):
                store.get_secret("smtp_password")

    def test_corrupted_secrets_file_raises(self, tmp_path: Path) -> None:
        store = SecretsStore(config_dir=tmp_path)
        (tmp_path / "secrets.enc").write_text("not json at all", encoding="utf-8")
        with pytest.raises(SecretsError, match="Failed to read"):
            store.get_secret("any_key")

    def test_truncated_ciphertext_raises(self, tmp_path: Path) -> None:
        store = SecretsStore(config_dir=tmp_path)
        store.set_secret("k", "v")
        data = json.loads((tmp_path / "secrets.enc").read_text())
        data["k"] = "dGhpcyBpcyBub3QgZW5vdWdoIGJ5dGVz"  # too short
        (tmp_path / "secrets.enc").write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(SecretsError):
            store.get_secret("k")


# ── delete_secret ─────────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_removes_key(self, store: SecretsStore) -> None:
        store.set_secret("k", "v")
        store.delete_secret("k")
        with pytest.raises(SecretsError, match="not found"):
            store.get_secret("k")

    def test_delete_nonexistent_key_is_noop(self, store: SecretsStore) -> None:
        store.delete_secret("never_set")  # must not raise

    def test_delete_leaves_other_keys_intact(self, store: SecretsStore) -> None:
        store.set_secret("a", "alpha")
        store.set_secret("b", "beta")
        store.delete_secret("a")
        assert store.get_secret("b") == "beta"


# ── Salt persistence ───────────────────────────────────────────────────────────

class TestSalt:
    def test_salt_is_created_on_first_use(self, tmp_path: Path) -> None:
        store = SecretsStore(config_dir=tmp_path)
        assert not (tmp_path / "secrets.salt").exists()
        store.set_secret("k", "v")
        assert (tmp_path / "secrets.salt").exists()

    def test_same_salt_used_across_instances(self, tmp_path: Path) -> None:
        store_a = SecretsStore(config_dir=tmp_path)
        store_a.set_secret("k", "value")

        store_b = SecretsStore(config_dir=tmp_path)
        assert store_b.get_secret("k") == "value"

    def test_different_config_dirs_use_different_salts(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        store_a = SecretsStore(config_dir=dir_a)
        store_a.set_secret("k", "value")

        store_b = SecretsStore(config_dir=dir_b)
        with pytest.raises(SecretsError):
            store_b.get_secret("k")


# ── Logging behaviour ─────────────────────────────────────────────────────────

class TestLogging:
    def test_set_secret_logs_key_name(
        self, store: SecretsStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging
        with caplog.at_level(logging.INFO, logger="gatekeeper.secrets"):
            store.set_secret("smtp_password", "hunter2")
        assert any("smtp_password" in r.message for r in caplog.records)
        assert all("hunter2" not in r.message for r in caplog.records)

    def test_set_secret_does_not_log_value(
        self, store: SecretsStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging
        with caplog.at_level(logging.DEBUG, logger="gatekeeper.secrets"):
            store.set_secret("smtp_password", "super_secret_1234")
        assert all("super_secret_1234" not in r.message for r in caplog.records)
