-- Migration 001: create the files catalog table.
--
-- cap and original_path are stored as AES-256-GCM ciphertext.
-- path_hmac is a HMAC-SHA256 blind index that allows lookup by original_path
-- without decrypting every row (see catalog.py for key derivation).
--
-- DESIGN NOTE (2026-05-20): CatalogDB is crypto-aware (Alt A) — it accepts
-- plaintext values and encrypts them internally.  If this decision is revised,
-- the encryption columns (cap, original_path, path_hmac) and all callers must
-- be updated together.  See project history for the 2026-05-20 design flag.
--
-- original_path and path_hmac allow NULL to handle the call-home reconstruction
-- scenario (ADR-008) where a file entry exists in Tahoe but its metadata tag is
-- missing or unreadable.  NOT NULL was considered but rejected on 2026-05-20.

CREATE TABLE IF NOT EXISTS files (
    id            INTEGER  PRIMARY KEY AUTOINCREMENT,
    cap           BLOB     NOT NULL,
    sha256        TEXT     NOT NULL,
    original_path BLOB,
    path_hmac     BLOB,
    agent         TEXT     NOT NULL,
    backed_up_at  REAL     NOT NULL,
    size_bytes    INTEGER  NOT NULL,
    profile       TEXT     NOT NULL,
    k             INTEGER  NOT NULL,
    n             INTEGER  NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_lookup ON files (agent, path_hmac);
CREATE INDEX IF NOT EXISTS idx_files_since  ON files (backed_up_at);
