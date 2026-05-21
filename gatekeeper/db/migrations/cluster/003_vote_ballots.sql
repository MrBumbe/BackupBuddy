-- Migration 003: vote ballot tracking and grace period columns.
--
-- vote_ballots — per-voter ballot records, prevents double-voting
-- votes.grace_extension_days — days to add when a grace_extension vote passes
-- members.grace_started_at — timestamp when grace period began
-- members.grace_days — total grace days granted (default: 7, extendable)

CREATE TABLE IF NOT EXISTS vote_ballots (
    vote_id       INTEGER NOT NULL REFERENCES votes(id),
    voter_node_id TEXT    NOT NULL,
    voted_at      REAL    NOT NULL,
    choice        INTEGER NOT NULL CHECK(choice IN (0, 1)),
    PRIMARY KEY (vote_id, voter_node_id)
);

CREATE INDEX IF NOT EXISTS idx_ballots_vote ON vote_ballots (vote_id);

ALTER TABLE votes   ADD COLUMN grace_extension_days INTEGER;
ALTER TABLE members ADD COLUMN grace_started_at     REAL;
ALTER TABLE members ADD COLUMN grace_days           INTEGER NOT NULL DEFAULT 7;
