-- Migration 001: create cluster state tables.
--
-- members     — registered cluster nodes and their resource accounting
-- invites     — single-use invite codes for cluster join
-- votes       — majority-vote records for removal and grace extension
-- orphan_tags — fragment cleanup tracking after node removal
--
-- No columns require encryption: cluster.db contains operational metadata,
-- not cryptographic key material.  File caps and original paths live in
-- catalog.db (see 001_catalog_init.sql in migrations/catalog/).

CREATE TABLE IF NOT EXISTS members (
    node_id            TEXT    PRIMARY KEY,
    display_name       TEXT    NOT NULL,
    tailscale_hostname TEXT    NOT NULL,
    joined_at          REAL    NOT NULL,
    contribution_bytes INTEGER NOT NULL DEFAULT 0,
    usage_bytes        INTEGER NOT NULL DEFAULT 0,
    profile            TEXT    NOT NULL,
    status             TEXT    NOT NULL DEFAULT 'active'
                       CHECK(status IN ('active', 'grace', 'removed'))
);

CREATE TABLE IF NOT EXISTS invites (
    code       TEXT    PRIMARY KEY,
    created_by TEXT    NOT NULL,
    created_at REAL    NOT NULL,
    expires_at REAL    NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0 CHECK(used    IN (0, 1)),
    revoked    INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0, 1))
);

CREATE TABLE IF NOT EXISTS votes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    vote_type      TEXT    NOT NULL
                   CHECK(vote_type IN ('removal', 'grace_extension')),
    target_node_id TEXT    NOT NULL,
    proposed_by    TEXT    NOT NULL,
    proposed_at    REAL    NOT NULL,
    closes_at      REAL    NOT NULL,
    votes_yes      INTEGER NOT NULL DEFAULT 0,
    votes_no       INTEGER NOT NULL DEFAULT 0,
    resolved       INTEGER NOT NULL DEFAULT 0 CHECK(resolved IN (0, 1))
);

CREATE TABLE IF NOT EXISTS orphan_tags (
    fragment_id      TEXT NOT NULL,
    owner_node_id    TEXT NOT NULL,
    created_at       REAL NOT NULL,
    marked_orphan_at REAL NOT NULL,
    cleaned_at       REAL,
    PRIMARY KEY (fragment_id, owner_node_id)
);

CREATE INDEX IF NOT EXISTS idx_invites_active  ON invites     (used, revoked, expires_at);
CREATE INDEX IF NOT EXISTS idx_votes_open      ON votes       (resolved, closes_at);
CREATE INDEX IF NOT EXISTS idx_orphans_pending ON orphan_tags (cleaned_at, marked_orphan_at);
