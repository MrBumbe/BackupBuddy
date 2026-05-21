"""Invite code generation, validation, revocation, and consumption.

Policy layer over ClusterDB — all SQL is handled by ClusterDB methods.
"""

import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

EXPIRY_SECONDS: int = 48 * 3600

_WORDLIST_PATH = Path(__file__).parent / "wordlist.txt"


@dataclass
class InviteCode:
    code: str
    created_by: str
    created_at: float
    expires_at: float
    used: bool
    revoked: bool


def _load_words(wordlist_path: Path = _WORDLIST_PATH) -> list[str]:
    try:
        text = wordlist_path.read_text(encoding="utf-8")
        return [w.strip() for w in text.splitlines() if w.strip()]
    except FileNotFoundError:
        return []


def _generate_code(wordlist_path: Path = _WORDLIST_PATH) -> str:
    words = _load_words(wordlist_path)
    if words:
        w1 = secrets.choice(words)
        w2 = secrets.choice(words)
        n = secrets.randbelow(9) + 1
        return f"{w1}-{w2}-{n}"
    logger.warning("Invite wordlist not found — using fallback format")
    return f"bb-{secrets.token_hex(4)}"


def generate_invite(
    db,
    created_by: str,
    *,
    wordlist_path: Path = _WORDLIST_PATH,
) -> InviteCode:
    """Generate a new invite code, store it, and return an InviteCode record."""
    code = _generate_code(wordlist_path)
    now = time.time()
    expires = now + EXPIRY_SECONDS
    db.insert_invite(code=code, created_by=created_by, created_at=now, expires_at=expires)
    logger.info("Invite code created by %s, expires in 48h", created_by)
    return InviteCode(
        code=code,
        created_by=created_by,
        created_at=now,
        expires_at=expires,
        used=False,
        revoked=False,
    )


def validate_invite(db, code: str) -> InviteCode | None:
    """Return the InviteCode if valid; None if not found, revoked, used, or expired."""
    row = db.get_invite(code)
    if row is None:
        return None
    if row["revoked"]:
        return None
    if row["used"]:
        return None
    if time.time() > row["expires_at"]:
        return None
    return InviteCode(
        code=row["code"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        used=bool(row["used"]),
        revoked=bool(row["revoked"]),
    )


def revoke_invite(db, code: str, revoked_by: str) -> None:
    """Mark a code as revoked. Raises ValueError if not found or already used."""
    row = db.get_invite(code)
    if row is None:
        raise ValueError(f"Invite code not found: {code}")
    if row["used"]:
        raise ValueError("Cannot revoke a used invite code")
    db.update_invite(code, revoked=1)
    logger.info("Invite code revoked by %s", revoked_by)


def consume_invite(db, code: str) -> InviteCode:
    """Mark a valid code as used and return it. Raises ValueError if invalid."""
    invite = validate_invite(db, code)
    if invite is None:
        raise ValueError("Invalid, expired, or already-used invite code")
    db.update_invite(code, used=1)
    invite.used = True
    return invite
