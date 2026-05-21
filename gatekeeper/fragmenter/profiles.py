"""
Fragmentation profile definitions.

Maps human-readable profile names to Tahoe-LAFS (k, n) parameters:
  k = shares.needed  — minimum fragments required to restore the file
  n = shares.total   — total fragments created and distributed

ADR-006: profiles are the only user-facing abstraction for k/n.
Tahoe terminology (shares.needed, shares.happy, shares.total) must
never appear in user-facing output.

ADR-018: k/n is a node-level setting in tahoe.cfg, not a per-upload
parameter. All uploads at any point in time use the same k/n.
"""

from typing import NamedTuple


class Profile(NamedTuple):
    k: int  # shares needed for reconstruction
    n: int  # total shares created


PROFILES: dict[str, Profile] = {
    "balanced": Profile(k=3, n=5),
    "secure":   Profile(k=3, n=7),
    "paranoid": Profile(k=3, n=10),
}

# "adaptive" is intentionally absent — k/n is computed at runtime from cluster
# size by gatekeeper/fragmenter/adaptive.py.

_VALID_NAMES = frozenset(PROFILES)


def get_profile(name: str) -> Profile:
    """Return the (k, n) Profile for the given profile name.

    Raises ValueError for unknown names, including "adaptive"
    (adaptive k/n is resolved by task 1.11.1, not this module).
    """
    try:
        return PROFILES[name]
    except KeyError:
        if name == "adaptive":
            raise ValueError(
                "adaptive profile: k/n resolved at runtime by adaptive.py"
            )
        raise ValueError(
            f"Unknown profile {name!r}. Valid non-adaptive profiles: {sorted(_VALID_NAMES)}"
        )
