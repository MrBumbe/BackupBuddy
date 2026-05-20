"""
Unit tests for gatekeeper/main.py.
"""

from gatekeeper.main import _derive_catalog_key


class TestDeriveCatalogKey:
    def test_returns_32_bytes(self):
        key = _derive_catalog_key("URI:DIR2:someexamplecapability")
        assert len(key) == 32

    def test_deterministic(self):
        cap = "URI:DIR2:someexamplecapability"
        assert _derive_catalog_key(cap) == _derive_catalog_key(cap)

    def test_different_caps_produce_different_keys(self):
        key1 = _derive_catalog_key("URI:DIR2:capabilityA")
        key2 = _derive_catalog_key("URI:DIR2:capabilityB")
        assert key1 != key2

    def test_empty_string_produces_32_bytes(self):
        key = _derive_catalog_key("")
        assert len(key) == 32

    def test_context_separation_from_raw_sha256(self):
        import hashlib
        cap = "URI:DIR2:someexamplecapability"
        derived = _derive_catalog_key(cap)
        raw_sha256 = hashlib.sha256(cap.encode()).digest()
        assert derived != raw_sha256
