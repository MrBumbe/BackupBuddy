"""Unit tests for gatekeeper.fragmenter.profiles and gatekeeper.fragmenter.fragmenter."""

import hashlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gatekeeper.fragmenter.fragmenter import (
    FragmentationError,
    Fragmenter,
    _compute_sha256,
    _encrypt_tag,
    _entry_name,
    derive_metadata_key,
)
from gatekeeper.fragmenter.profiles import Profile, get_profile
from gatekeeper.tahoe.client import TahoeError


# ── Profile tests ─────────────────────────────────────────────────────────────

class TestProfiles(unittest.TestCase):

    def test_balanced_profile(self):
        p = get_profile("balanced")
        self.assertEqual(p.k, 3)
        self.assertEqual(p.n, 5)

    def test_secure_profile(self):
        p = get_profile("secure")
        self.assertEqual(p.k, 3)
        self.assertEqual(p.n, 7)

    def test_paranoid_profile(self):
        p = get_profile("paranoid")
        self.assertEqual(p.k, 3)
        self.assertEqual(p.n, 10)

    def test_unknown_profile_raises_value_error(self):
        with self.assertRaises(ValueError, msg="unknown"):
            get_profile("nonexistent")

    def test_adaptive_profile_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            get_profile("adaptive")
        self.assertIn("adaptive", str(ctx.exception))
        self.assertIn("task 1.11.1", str(ctx.exception))

    def test_profile_is_named_tuple(self):
        p = get_profile("balanced")
        self.assertIsInstance(p, Profile)
        self.assertEqual(p.k, p[0])
        self.assertEqual(p.n, p[1])


# ── derive_metadata_key tests ─────────────────────────────────────────────────

class TestDeriveMetadataKey(unittest.TestCase):

    def test_returns_32_bytes(self):
        key = derive_metadata_key("URI:DIR2:somecap")
        self.assertEqual(len(key), 32)

    def test_different_caps_give_different_keys(self):
        k1 = derive_metadata_key("URI:DIR2:cap1")
        k2 = derive_metadata_key("URI:DIR2:cap2")
        self.assertNotEqual(k1, k2)

    def test_same_cap_gives_same_key(self):
        cap = "URI:DIR2:deterministic"
        self.assertEqual(derive_metadata_key(cap), derive_metadata_key(cap))

    def test_metadata_key_differs_from_catalog_key(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        cap = "URI:DIR2:testcap"
        catalog_key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None,
            info=b"backupbuddy:catalog:v1",
        ).derive(cap.encode("utf-8"))
        self.assertNotEqual(derive_metadata_key(cap), catalog_key)


# ── Internal helper tests ─────────────────────────────────────────────────────

class TestInternalHelpers(unittest.TestCase):

    def test_compute_sha256_matches_hashlib(self, tmp_path=None):
        import tempfile, os
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content for sha256 verification")
            name = f.name
        try:
            expected = hashlib.sha256(b"test content for sha256 verification").hexdigest()
            self.assertEqual(_compute_sha256(name), expected)
        finally:
            os.unlink(name)

    def test_entry_name_is_32_hex_chars(self):
        name = _entry_name("agent-01", "/home/user/file.txt")
        self.assertEqual(len(name), 32)
        int(name, 16)  # must be valid hex

    def test_entry_name_same_inputs_same_output(self):
        self.assertEqual(
            _entry_name("agent-01", "/home/user/file.txt"),
            _entry_name("agent-01", "/home/user/file.txt"),
        )

    def test_entry_name_different_agent_different_output(self):
        self.assertNotEqual(
            _entry_name("agent-01", "/home/user/file.txt"),
            _entry_name("agent-02", "/home/user/file.txt"),
        )

    def test_entry_name_different_path_different_output(self):
        self.assertNotEqual(
            _entry_name("agent-01", "/path/a.txt"),
            _entry_name("agent-01", "/path/b.txt"),
        )

    def test_encrypt_tag_returns_base64_string(self):
        result = _encrypt_tag("hello world", b"\x42" * 32)
        import base64
        decoded = base64.b64decode(result)
        # nonce (12) + at-least-1-byte ciphertext + 16-byte GCM tag
        self.assertGreater(len(decoded), 12 + 16)

    def test_encrypt_tag_nonce_randomness(self):
        key = b"\x00" * 32
        r1 = _encrypt_tag("same value", key)
        r2 = _encrypt_tag("same value", key)
        self.assertNotEqual(r1, r2)  # random nonce makes each call unique


# ── Fragmenter constructor tests ──────────────────────────────────────────────

class TestFragmenterConstructor(unittest.TestCase):

    def _make_fragmenter(self, key=None):
        client = AsyncMock()
        catalog = MagicMock()
        return Fragmenter(
            tahoe_client=client,
            catalog_db=catalog,
            root_dir_ref="URI:DIR2:rootref",
            metadata_key=key or b"\x00" * 32,
        )

    def test_accepts_valid_32_byte_key(self):
        frag = self._make_fragmenter(b"\xab" * 32)
        self.assertIsNotNone(frag)

    def test_rejects_short_key(self):
        with self.assertRaises(ValueError, msg="32 bytes"):
            self._make_fragmenter(b"\x00" * 16)

    def test_rejects_long_key(self):
        with self.assertRaises(ValueError):
            self._make_fragmenter(b"\x00" * 64)


# ── fragment_and_upload tests ─────────────────────────────────────────────────

def _make_fragmenter_with_mocks():
    client = AsyncMock()
    client.upload = AsyncMock(return_value="URI:CHK:fakeref123")
    client.link_file = AsyncMock(return_value=None)
    catalog = MagicMock()
    catalog.insert_file = MagicMock(return_value=1)
    frag = Fragmenter(
        tahoe_client=client,
        catalog_db=catalog,
        root_dir_ref="URI:DIR2:rootref",
        metadata_key=b"\x00" * 32,
    )
    return frag, client, catalog


@pytest.mark.anyio
async def test_successful_upload_returns_file_ref(tmp_path):
    frag, client, catalog = _make_fragmenter_with_mocks()
    test_file = tmp_path / "hello.txt"
    test_file.write_bytes(b"hello world")

    result = await frag.fragment_and_upload(
        file_path=str(test_file),
        profile="balanced",
        agent="agent-01",
        original_path="/home/user/hello.txt",
    )

    assert result == "URI:CHK:fakeref123"


@pytest.mark.anyio
async def test_successful_upload_calls_tahoe_upload(tmp_path):
    frag, client, catalog = _make_fragmenter_with_mocks()
    test_file = tmp_path / "file.txt"
    test_file.write_bytes(b"content")

    await frag.fragment_and_upload(
        file_path=str(test_file),
        profile="balanced",
        agent="agent-01",
        original_path="/home/user/file.txt",
    )

    client.upload.assert_called_once_with(str(test_file))


@pytest.mark.anyio
async def test_successful_upload_calls_link_file(tmp_path):
    frag, client, catalog = _make_fragmenter_with_mocks()
    test_file = tmp_path / "file.txt"
    test_file.write_bytes(b"content")

    await frag.fragment_and_upload(
        file_path=str(test_file),
        profile="balanced",
        agent="agent-01",
        original_path="/home/user/file.txt",
    )

    client.link_file.assert_called_once()
    args = client.link_file.call_args
    # dir_ref, name, file_ref, metadata
    assert args.args[0] == "URI:DIR2:rootref"
    assert args.args[2] == "URI:CHK:fakeref123"
    meta = args.args[3]
    assert "original_path_enc" in meta
    assert "agent_enc" in meta
    assert "backed_up_at" in meta


@pytest.mark.anyio
async def test_successful_upload_inserts_catalog_record(tmp_path):
    frag, client, catalog = _make_fragmenter_with_mocks()
    test_file = tmp_path / "file.txt"
    test_file.write_bytes(b"content")

    await frag.fragment_and_upload(
        file_path=str(test_file),
        profile="balanced",
        agent="agent-01",
        original_path="/home/user/file.txt",
    )

    catalog.insert_file.assert_called_once()
    kw = catalog.insert_file.call_args.kwargs
    assert kw["profile"] == "balanced"
    assert kw["k"] == 3
    assert kw["n"] == 5
    assert kw["agent"] == "agent-01"
    assert kw["original_path"] == "/home/user/file.txt"
    assert kw["cap"] == "URI:CHK:fakeref123"
    # sha256 must match the file's actual content
    expected_hash = hashlib.sha256(b"content").hexdigest()
    assert kw["sha256"] == expected_hash


@pytest.mark.anyio
async def test_secure_profile_records_correct_kn(tmp_path):
    frag, client, catalog = _make_fragmenter_with_mocks()
    test_file = tmp_path / "file.txt"
    test_file.write_bytes(b"content")

    await frag.fragment_and_upload(
        file_path=str(test_file),
        profile="secure",
        agent="agent-01",
        original_path="/home/user/file.txt",
    )

    kw = catalog.insert_file.call_args.kwargs
    assert kw["k"] == 3
    assert kw["n"] == 7


@pytest.mark.anyio
async def test_paranoid_profile_records_correct_kn(tmp_path):
    frag, client, catalog = _make_fragmenter_with_mocks()
    test_file = tmp_path / "file.txt"
    test_file.write_bytes(b"content")

    await frag.fragment_and_upload(
        file_path=str(test_file),
        profile="paranoid",
        agent="agent-01",
        original_path="/home/user/file.txt",
    )

    kw = catalog.insert_file.call_args.kwargs
    assert kw["k"] == 3
    assert kw["n"] == 10


@pytest.mark.anyio
async def test_unknown_profile_raises_before_upload(tmp_path):
    frag, client, catalog = _make_fragmenter_with_mocks()
    test_file = tmp_path / "file.txt"
    test_file.write_bytes(b"content")

    with pytest.raises(ValueError, match="nonexistent"):
        await frag.fragment_and_upload(
            file_path=str(test_file),
            profile="nonexistent",
            agent="agent-01",
            original_path="/path/file.txt",
        )

    client.upload.assert_not_called()
    catalog.insert_file.assert_not_called()


@pytest.mark.anyio
async def test_tahoe_upload_error_raises_fragmentation_error(tmp_path):
    frag, client, catalog = _make_fragmenter_with_mocks()
    client.upload.side_effect = TahoeError("connection refused")
    test_file = tmp_path / "file.txt"
    test_file.write_bytes(b"content")

    with pytest.raises(FragmentationError, match="Upload failed"):
        await frag.fragment_and_upload(
            file_path=str(test_file),
            profile="balanced",
            agent="agent-01",
            original_path="/path/file.txt",
        )

    catalog.insert_file.assert_not_called()
    client.link_file.assert_not_called()


@pytest.mark.anyio
async def test_hash_mismatch_raises_fragmentation_error(tmp_path):
    """File modified between pre- and post-upload hash must raise FragmentationError."""
    frag, client, catalog = _make_fragmenter_with_mocks()
    test_file = tmp_path / "file.txt"
    test_file.write_bytes(b"original")

    call_count = [0]

    def fake_hash(path):
        call_count[0] += 1
        if call_count[0] == 1:
            return hashlib.sha256(b"original").hexdigest()
        return hashlib.sha256(b"modified").hexdigest()

    with patch(
        "gatekeeper.fragmenter.fragmenter._compute_sha256",
        side_effect=fake_hash,
    ):
        with pytest.raises(FragmentationError, match="changed during upload"):
            await frag.fragment_and_upload(
                file_path=str(test_file),
                profile="balanced",
                agent="agent-01",
                original_path="/path/file.txt",
            )

    catalog.insert_file.assert_not_called()
    client.link_file.assert_not_called()


@pytest.mark.anyio
async def test_link_file_error_raises_fragmentation_error(tmp_path):
    frag, client, catalog = _make_fragmenter_with_mocks()
    client.link_file.side_effect = TahoeError("directory not found")
    test_file = tmp_path / "file.txt"
    test_file.write_bytes(b"content")

    with pytest.raises(FragmentationError, match="link file"):
        await frag.fragment_and_upload(
            file_path=str(test_file),
            profile="balanced",
            agent="agent-01",
            original_path="/path/file.txt",
        )

    catalog.insert_file.assert_not_called()


@pytest.mark.anyio
async def test_metadata_tag_contains_encrypted_fields(tmp_path):
    """Metadata stored in Tahoe directory entry must not contain plaintext paths."""
    frag, client, catalog = _make_fragmenter_with_mocks()
    test_file = tmp_path / "secret.txt"
    test_file.write_bytes(b"content")

    await frag.fragment_and_upload(
        file_path=str(test_file),
        profile="balanced",
        agent="my-agent",
        original_path="/home/secret/path.txt",
    )

    meta = client.link_file.call_args.args[3]
    # Encrypted fields must not contain the plaintext values
    assert "/home/secret/path.txt" not in str(meta["original_path_enc"])
    assert "my-agent" not in str(meta["agent_enc"])
    # Timestamp must be a number (plaintext per ADR-008)
    assert isinstance(meta["backed_up_at"], float)
