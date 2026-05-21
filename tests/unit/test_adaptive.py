"""Unit tests for gatekeeper/fragmenter/adaptive.py."""

import pytest

from gatekeeper.config import AdaptiveConfig
from gatekeeper.fragmenter.adaptive import compute_adaptive_kn, get_current_kn


DEFAULT = AdaptiveConfig()  # ratio=0.33, min_k=1, max_n=20


# ---------------------------------------------------------------------------
# compute_adaptive_kn — small cluster special cases
# ---------------------------------------------------------------------------

class TestSmallClusters:
    def test_single_node_all_of_n(self):
        assert compute_adaptive_kn(1, DEFAULT) == (1, 1)

    def test_two_nodes_all_of_n(self):
        assert compute_adaptive_kn(2, DEFAULT) == (2, 2)

    def test_zero_nodes_raises(self):
        with pytest.raises(ValueError, match="node_count"):
            compute_adaptive_kn(0, DEFAULT)

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            compute_adaptive_kn(-1, DEFAULT)


# ---------------------------------------------------------------------------
# compute_adaptive_kn — reference table from ADR-006a
# ---------------------------------------------------------------------------

class TestReferenceTable:
    def test_3_nodes(self):
        assert compute_adaptive_kn(3, DEFAULT) == (1, 3)

    def test_4_nodes(self):
        assert compute_adaptive_kn(4, DEFAULT) == (1, 4)

    def test_5_nodes(self):
        assert compute_adaptive_kn(5, DEFAULT) == (2, 5)

    def test_6_nodes(self):
        assert compute_adaptive_kn(6, DEFAULT) == (2, 6)

    def test_9_nodes(self):
        assert compute_adaptive_kn(9, DEFAULT) == (3, 9)

    def test_20_nodes(self):
        assert compute_adaptive_kn(20, DEFAULT) == (7, 20)


# ---------------------------------------------------------------------------
# compute_adaptive_kn — max_n boundary
# ---------------------------------------------------------------------------

class TestMaxNBoundary:
    def test_node_count_above_max_n_caps_n(self):
        k, n = compute_adaptive_kn(25, DEFAULT)
        assert n == 20  # capped at max_n
        assert k == 7

    def test_custom_max_n(self):
        cfg = AdaptiveConfig(ratio=0.33, min_k=1, max_n=10)
        k, n = compute_adaptive_kn(15, cfg)
        assert n == 10

    def test_n_equals_max_n_exactly(self):
        k, n = compute_adaptive_kn(20, DEFAULT)
        assert n == 20


# ---------------------------------------------------------------------------
# compute_adaptive_kn — min_k boundary
# ---------------------------------------------------------------------------

class TestMinKBoundary:
    def test_min_k_enforced_when_ratio_gives_lower(self):
        # ratio=0.01 on 6 nodes → round(0.06)=0, but min_k=1 floors it
        cfg = AdaptiveConfig(ratio=0.01, min_k=1, max_n=20)
        k, n = compute_adaptive_kn(6, cfg)
        assert k >= 1

    def test_custom_min_k_respected(self):
        cfg = AdaptiveConfig(ratio=0.10, min_k=3, max_n=20)
        k, n = compute_adaptive_kn(6, cfg)
        # round(6*0.10)=round(0.6)=1, but min_k=3 should floor it
        assert k == 3

    def test_k_never_exceeds_n(self):
        # Contrived: min_k > node_count (via capped n)
        cfg = AdaptiveConfig(ratio=0.99, min_k=1, max_n=3)
        k, n = compute_adaptive_kn(3, cfg)
        assert k <= n


# ---------------------------------------------------------------------------
# get_current_kn — uses injected db stub
# ---------------------------------------------------------------------------

class _FakeDB:
    def __init__(self, active: int, grace: int = 0):
        self._active = active
        self._grace = grace

    def list_members(self, status: str) -> list[dict]:
        count = self._active if status == "active" else self._grace
        return [{"node_id": f"node-{i}"} for i in range(count)]


class TestGetCurrentKn:
    def test_uses_active_plus_grace_count(self):
        db = _FakeDB(active=4, grace=2)
        k, n = get_current_kn(db, DEFAULT)
        # 6 nodes → k=2, n=6
        assert (k, n) == (2, 6)

    def test_only_active(self):
        db = _FakeDB(active=9, grace=0)
        assert get_current_kn(db, DEFAULT) == (3, 9)

    def test_only_grace_counts(self):
        db = _FakeDB(active=0, grace=3)
        assert get_current_kn(db, DEFAULT) == (1, 3)

    def test_single_active_node(self):
        db = _FakeDB(active=1, grace=0)
        assert get_current_kn(db, DEFAULT) == (1, 1)
