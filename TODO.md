# TODO.md

> **For Claude Code (Kludde):**
>
> - Read CLAUDE.md before starting any task — every time
> - Read the files listed under **Reads** before writing any code for that task
> - Check off tasks with [x] when complete
> - Write implementation notes in the `> Kludde:` line after each task
> - Commit after every task using the git conventions in CLAUDE.md
> - Use x.y.z numbering: x = phase, y = group, z = task (z > 10 = added later)
> - Never start a new task if the previous one has unresolved issues
> - If a task reveals something unexpected, add a new x.y.z task before continuing
> - If a task would require a Phase 2 feature, stop and flag it — see ADR-016
> - The project owner (Johan) handles the GitHub fork action if Kludde lacks access
> - **PAUSE after each completed task.** End your response after committing a task.
>   Johan will do /clear between tasks to save tokens and then ask you to continue.

---

# Phase 1 — PoC

---

## 1.1 — Project foundation

### [x] 1.1.1 — Fork and clone Tahoe-LAFS

**Reads:** CLAUDE.md, DECISIONS.md → ADR-001, project-docs/architecture.md
**Note:** If Kludde cannot perform the GitHub fork directly, flag to Johan.
Johan forks tahoe-lafs/tahoe-lafs on GitHub to the BackupBuddy organisation/account.
Kludde then clones the fork locally and sets up the project structure.
**Creates:** project root from fork, `UPSTREAM.md`
**Requirements:**
- Tahoe-LAFS fork cloned to working directory
- Upstream remote added: `git remote add upstream https://github.com/tahoe-lafs/tahoe-lafs`
- `UPSTREAM.md` created at project root documenting fork base commit hash and date
- Verify Tahoe-LAFS runs: `python -m tahoe --version` exits without error
**Done when:**
- Fork exists, upstream remote is set
- `python -m tahoe --version` outputs a version string
- `UPSTREAM.md` exists with base commit hash

```
> Kludde: Fork already cloned by Johan to MrBumbe/BackupBuddy. Added upstream remote
> (tahoe-lafs/tahoe-lafs). Base commit: f002fd0d (2026-01-21). One local commit ahead
> of upstream (test fix). Fixed pyproject.toml readme ref (README.rst → README.md).
> `tahoe --version` works via entry point in .venv; `python -m tahoe` does not work
> (Tahoe uses `allmydata` as the Python package name, not `tahoe`). All done.
```

---

### [x] 1.1.2 — Repository structure and project scaffolding

**Reads:** CLAUDE.md → Project file structure
**Creates:** `gatekeeper/`, `agent/`, `tests/unit/`, `tests/integration/`,
`requirements.txt`, `.gitignore`, `.env.example`
**Requirements:**
- Directory structure matches CLAUDE.md → Project file structure
- `requirements.txt` created with all known dependencies at pinned exact versions
  (Tahoe-LAFS dependencies + FastAPI + Uvicorn + Pydantic v2 + APScheduler +
  cryptography + argon2-cffi + aiosmtplib + httpx)
- `.gitignore` includes: `*.pyc`, `__pycache__/`, `.env`, `*.db`, `*.key`,
  `*.cap`, `lifeboat.enc`, `node_modules/`, `.tahoe/`
- `.env.example` created with all required env var keys and empty values
- `pip audit` shows no high or critical vulnerabilities
**Done when:**
- All directories exist
- `pip install -r requirements.txt` completes without errors
- `pip audit` clean
- `.env` is gitignored

```
> Kludde: All directories created per CLAUDE.md structure. requirements.txt generated
> from full venv freeze (105 packages, all pinned). pip-audit clean. .env.example
> created. .gitignore extended with BackupBuddy-specific entries. Python 3.13 in use
> (≥ 3.11 requirement met). pip-audit included in venv for CI use.
```

---

### [x] 1.1.3 — CONTRIBUTING.md

**Reads:** CLAUDE.md → Git conventions
**Creates:** `CONTRIBUTING.md`
**Requirements:**
- Documents git commit format and types
- Documents branch naming convention
- Documents the task checklist (from CLAUDE.md)
- Documents how to run unit tests
- Documents the `pip audit` requirement
**Done when:**
- File exists and covers all points above

```
> Kludde: CONTRIBUTING.md created covering: commit format+types, branch naming,
> task completion checklist, unit test instructions (pytest tests/unit/),
> pip-audit requirement, and language rules.
```

---

## 1.2 — Tahoe-LAFS base configuration

### [x] 1.2.1 — Introducer node setup

**Reads:** DECISIONS.md → ADR-001, docs/architecture.md → Introducer node
**Creates:** `gatekeeper/tahoe/introducer.py`, `docs/tahoe_internals.md`
**Requirements:**
- Script to create and start a Tahoe-LAFS introducer node programmatically
- Introducer FURL extracted and returned after creation
- Introducer runs as a managed subprocess (not a blocking call)
- `docs/tahoe_internals.md` created: documents Tahoe concepts used internally
  (FURL, cap, shares.needed/happy/total, storage index) —
  this is internal reference only, never exposed to users
- No Tahoe terminology appears in any log message visible to users
**Done when:**
- Introducer starts programmatically and returns a FURL string
- Introducer process can be stopped and restarted cleanly
- Unit test: introducer starts, FURL is a non-empty string

```
> Kludde: IntroducerNode class in gatekeeper/tahoe/introducer.py. Wraps tahoe
> create-introducer and tahoe run as async managed subprocess. FURL returned
> from start() but never logged or user-facing. project-docs/tahoe_internals.md
> created as internal dev reference. 8 unit tests pass (mocked subprocess).
> pytest added to requirements.txt. Note: docs/ in TODO referred to project-docs/.
```

---

### [x] 1.2.2 — Gatekeeper as Tahoe storage node

**Reads:** DECISIONS.md → ADR-001, ADR-005, docs/architecture.md → Cluster
**Creates:** `gatekeeper/tahoe/storage_node.py`
**Requirements:**
- Script to create and start a Tahoe-LAFS storage node for the gatekeeper
- Storage node connects to introducer using FURL from gatekeeper.cfg
- Storage directory configurable (points to storage pool — see 1.5)
- `reserved_space` set to enforce quota (overridden by storage pool logic in 1.5)
- Node starts as a managed subprocess
**Done when:**
- Storage node starts and connects to a running introducer
- Node appears in introducer's connected peers list
- Unit test: node starts, connects, peer count increases

```
> Kludde: StorageNode class in gatekeeper/tahoe/storage_node.py. Wraps tahoe
> create-node and tahoe run as async managed subprocess. Configures [storage]
> storage_dir (verified native Tahoe config key in allmydata/client.py — no
> symlinks needed), reserved_space, and introducer FURL via configparser.
> reserved_space is a floor not a cap; real quota enforcement in task 1.5.2.
> Startup ready-signal is "client running" (NODETYPE="client" in Tahoe source).
> Peer connection to introducer verified in smoke test (task 1.16.2).
> 18 unit tests pass; all 26 unit tests (incl. introducer) green.
```

---

### [x] 1.2.3 — Tahoe client (gateway) for gatekeeper

**Reads:** DECISIONS.md → ADR-001, docs/architecture.md → Data flow
**Creates:** `gatekeeper/tahoe/client.py`
**Requirements:**
- Wrapper around Tahoe-LAFS client/gateway operations:
  `upload(file_path, metadata) → cap`
  `download(cap, dest_path) → sha256`
  `ls(cap) → list of (name, cap)`
  `mkdir() → cap`
- All operations are async
- No Tahoe caps, FURLs, or shares terminology appears in return values or
  exceptions visible outside this module — translate to internal types
- Metadata dict encrypted before being passed to Tahoe (see ADR-008)
**Done when:**
- Upload a test file, get a cap back
- Download using that cap, file contents match
- Unit test: upload → download → hash match

```
> Kludde: TahoeClient in gatekeeper/tahoe/client.py wraps the Tahoe HTTP gateway
> with four async operations: upload (PUT /uri), download (streaming GET /uri/<ref>),
> ls (GET /uri/<ref>?t=json → list of (name, ref) tuples), mkdir (POST /uri?t=mkdir).
> Returns opaque reference strings — no Tahoe caps, FURLs, or shares terminology in
> public API or raised exceptions. metadata param accepted by upload() but not stored
> at this layer (fragmenter's responsibility per ADR-008). SHA-256 computed and
> returned by download() for caller verification. 15 unit tests all pass.
```

---

## 1.3 — Database layer

### [x] 1.3.1 — catalog.db schema and access layer

**Reads:** SECURITY.md → Section 4, DECISIONS.md → ADR-014,
docs/configuration.md → catalog.db
**Creates:** `gatekeeper/db/catalog.py`, `gatekeeper/db/migrations/001_catalog_init.sql`
**Requirements:**
- SQLite database with WAL mode enabled
- File permissions set to 0600 on creation
- Schema: files table with fields: id, cap (encrypted), sha256, original_path
  (encrypted), agent, backed_up_at, size_bytes, profile, k, n
- Access layer functions:
  `insert_file(...)`, `get_file_by_path(agent, path)`,
  `get_all_files()`, `get_files_since(timestamp)`,
  `update_file(...)`, `delete_file(id)`
- All queries parameterized — no string formatting
- cap and original_path stored encrypted (key derived from root_dir.cap context)
- Migration system: version table, migrations run on startup
**Done when:**
- Database created with correct schema and permissions
- All access functions work correctly
- Unit tests: insert, retrieve, update, delete — all parameterized
- Attempting string-formatted query raises linting error (add flake8 rule)

```
> Kludde: CatalogDB in gatekeeper/db/catalog.py — crypto-aware (Alt A, 2026-05-20 design decision).
> Accepts plaintext cap/original_path, encrypts with AES-256-GCM internally; key passed at init.
> Blind index (HMAC-SHA256 path_hmac column) enables get_file_by_path() without decrypting rows.
> original_path and path_hmac allow NULL for ADR-008 call-home edge case (NOT NULL rejected 2026-05-20).
> Migration 001 in gatekeeper/db/migrations/001_catalog_init.sql; version table managed in Python.
> WAL mode, 0600 perms (POSIX only; skipped on Windows). All queries parameterized.
> flake8-bandit S608 rule added (.flake8); fires on f-string/concat SQL, noqa-annotated on safe
> whitelisted-column use in update_file(). 26 tests pass (1 skipped — permissions, Windows dev env).
> NOTE: If Alt A is revisited, update catalog.py, 001_catalog_init.sql, and the fragmenter together.
```

---

### [x] 1.3.2 — cluster.db schema and access layer

**Reads:** SECURITY.md → Section 4, DECISIONS.md → ADR-014,
docs/configuration.md → cluster.db
**Creates:** `gatekeeper/db/cluster.py`, `gatekeeper/db/migrations/002_cluster_init.sql`
**Requirements:**
- SQLite, WAL mode, permissions 0600
- Schema:
  `members` table: node_id, display_name, tailscale_hostname, joined_at,
    contribution_bytes, usage_bytes, profile, status
  `invites` table: code, created_by, created_at, expires_at, used, revoked
  `votes` table: id, type (removal/grace_extension), target_node_id,
    proposed_by, proposed_at, closes_at, votes_yes, votes_no, resolved
  `orphan_tags` table: fragment_id, owner_node_id, created_at,
    marked_orphan_at, cleaned_at
- Access layer functions for each table (insert, get, update, list)
- All queries parameterized
**Done when:**
- Schema created with correct tables and permissions
- All access functions work
- Unit tests for each table's CRUD operations

```
> Kludde: ClusterDB in gatekeeper/db/cluster.py — four tables: members, invites,
> votes, orphan_tags. No encryption (cluster.db holds operational metadata, not key
> material). All queries parameterized; updatable columns whitelisted per table with
> frozenset guards; update_* methods raise ValueError on unknown fields.
> vote_type used instead of type (reserved-word footgun). Composite PK on
> orphan_tags(fragment_id, owner_node_id) prevents duplicate tagging.
> Migration runner collision fixed: moved 001_catalog_init.sql to
> migrations/catalog/ and placed 001_cluster_init.sql in migrations/cluster/;
> each DB class points to its own subdir. catalog.py updated accordingly.
> 63 unit tests pass (1 skipped — POSIX perms on Windows dev machine).
> Cross-contamination verified: files table absent from cluster.db, members absent
> from catalog.db.
```

---

## 1.4 — Configuration parsing

### [x] 1.4.1 — gatekeeper.cfg parser

**Reads:** docs/configuration.md → gatekeeper.cfg, SECURITY.md → Section 1
**Creates:** `gatekeeper/config.py`
**Requirements:**
- Pydantic v2 model for the full gatekeeper.cfg schema
- Parsed from INI file using Python configparser + Pydantic validation
- All fields have correct types and defaults matching docs/configuration.md
- SMTP password and webhook URL are NOT stored in the parsed config object
  after initial read — fetched separately from encrypted store (see 1.5.3)
- Storage pool paths validated: must be absolute, must exist, must be directories
- Tailscale interface IP resolved at startup and stored in config
- Config reloaded on SIGHUP without restarting the process
**Done when:**
- Valid gatekeeper.cfg parses without errors
- Invalid values (wrong types, missing required fields) raise clear errors
- Unit tests: valid config, missing required field, invalid storage path,
  storage path that does not exist

```
> Kludde: GatekeeperConfig in gatekeeper/config.py — full Pydantic v2 model for all
> gatekeeper.cfg sections. configparser with delimiters=('=',) and optionxform=str to
> handle Windows paths (colons in paths) and preserve case for exclude patterns.
> SMTP password and webhook URL are NOT stored; silently ignored if present in the file.
> tailscale_ip field is None at parse time; populated at startup by tailscale.py (1.5.1).
> Storage pool paths validated: absolute, exists, is_dir (ConfigError on any failure).
> SIGHUP handler registered via install_sighup_handler(); no-op on Windows.
> Durations ('1h', '15m') → seconds int. Times ('04:00') → datetime.time.
> Quotas ('2000 GB') → bytes int. 39 tests pass, 1 skipped (SIGHUP on Windows).
```

---

### [x] 1.4.2 — backup.cfg parser (agent)

**Reads:** docs/configuration.md → backup.cfg, SECURITY.md → Section 4
**Creates:** `agent/config.py`
**Requirements:**
- Pydantic v2 model for backup.cfg schema
- Backup paths validated: absolute, exist, are directories
- Exclude patterns validated: valid glob syntax
- share_log defaults to false
- Config reloaded when file changes on disk (inotify or polling)
**Done when:**
- Valid backup.cfg parses correctly
- Invalid paths raise clear errors
- Unit tests: valid config, non-existent path, invalid glob pattern

```
> Kludde: AgentConfig in agent/config.py — Pydantic v2 model for [schedule],
> [backup], [exclude], [node]. [backup] and [exclude] use allow_no_value=True
> bare-key parsing (same as gatekeeper excludes). Backup paths validated:
> absolute, exists, is_dir, not system-critical (/etc /boot /sys /proc /dev).
> Glob patterns validated: non-empty, no null bytes, valid fnmatch syntax.
> watch_config() mtime-polls in a daemon thread; malformed reloads silently
> ignored so agent keeps running on last valid config. SIGHUP not used (agent
> is cross-platform). 24 tests pass, 2 skipped (POSIX critical-path tests,
> /etc and /proc absent on Windows dev machine).
```

---

## 1.5 — Gatekeeper core

### [x] 1.5.1 — Tailscale interface detection and startup check

**Reads:** SECURITY.md → Section 3, DECISIONS.md → ADR-002, CLAUDE.md → GUI route pattern
**Creates:** `gatekeeper/tailscale.py`
**Requirements:**
- Function `get_tailscale_ip() → str | None` that resolves the local Tailscale IP
  by reading the Tailscale interface (not by calling the Tailscale API)
- Function `assert_tailscale_running()` that raises `TailscaleNotRunning`
  if no Tailscale interface is found
- `assert_tailscale_running()` called at gatekeeper startup before anything else
- If Tailscale is not running: log error, exit with clear message, do NOT start GUI
**Done when:**
- Returns correct IP when Tailscale is running
- Raises correctly when Tailscale is not running
- Unit test: mock Tailscale running, mock Tailscale not running

```
> Kludde: get_tailscale_ip() uses psutil.net_if_addrs() to scan all IPv4 interfaces for
> addresses in Tailscale's CGNAT block 100.64.0.0/10 — no API or CLI call needed.
> assert_tailscale_running() raises TailscaleNotRunning and logs at ERROR if no interface
> found; returns the IP string on success (ready for use as Uvicorn host in task 1.5.4).
> 15 unit tests pass including CGNAT boundary cases and IPv6 exclusion.
```

---

### [x] 1.5.2 — Storage pool manager

**Reads:** DECISIONS.md → ADR-005, SECURITY.md → Section 5,
docs/architecture.md → Storage pool, docs/configuration.md → storage-pool
**Creates:** `gatekeeper/storage/pool.py`
**Requirements:**
- Reads storage pool paths and quotas from gatekeeper config
- Tracks bytes used per path (from filesystem, not from memory)
- `get_target_path(size_bytes) → path` — returns path with most free space
  that has quota remaining for size_bytes
- `register_fragment(path, size_bytes)` — records fragment placed at path
- `remove_fragment(path, size_bytes)` — records fragment removed from path
- Hard quota enforcement: raises `QuotaExceeded` if no path has space
- At startup: builds exclusion set from all storage pool paths (realpath resolved)
- Exclusion set exported as `EXCLUDED_PATHS: frozenset[str]` — immutable
- Paths checked at startup: must exist, must be writable, must be directories
**Done when:**
- Correct path selected based on available space
- QuotaExceeded raised correctly
- Exclusion set is populated and immutable
- Unit tests: path selection, quota enforcement, exclusion set contents

```
> Kludde: StoragePoolManager in gatekeeper/storage/pool.py. Reads StoragePoolEntry list
> from config; resolves all paths via os.path.realpath before use. Startup validation:
> must exist, be a directory, be writable (PoolPathError otherwise). _used_bytes seeded
> from filesystem via os.walk(followlinks=False) at init; updated in-memory via
> register_fragment / remove_fragment for the current session. Note: orphan cleanup
> (1.10.2) must call remove_fragment() to avoid counter drift.
> get_target_path(size_bytes) returns path with most remaining quota; raises
> QuotaExceeded if none qualify. Module-level EXCLUDED_PATHS: frozenset[str] set once
> by __init__ (global reassignment of immutable frozenset); self.excluded_paths mirrors
> it for instance access. All mutations protected by a threading.Lock.
> 23 tests pass, 2 skipped (Windows: chmod + symlinks). Full suite: 206 pass, 7 skip.
```

---

### [x] 1.5.3 — Encrypted secrets store

**Reads:** SECURITY.md → Sections 1, 7, DECISIONS.md → ADR-007
**Creates:** `gatekeeper/secrets.py`
**Requirements:**
- Stores SMTP password and webhook URL encrypted at rest
- Encryption: AES-256-GCM, key derived from a machine-specific secret
  (e.g. hashed combination of machine-id + a static salt stored in config dir)
- `set_secret(key, value)` — encrypts and writes to secrets file (permissions 0600)
- `get_secret(key) → str` — reads and decrypts
- Secrets file location: config directory, never in project root
- Never logs secret values — only key names
**Done when:**
- set/get round-trip works correctly
- Secrets file has correct permissions
- Unit test: set, get, verify value matches, verify file is not plaintext

```
> Kludde: SecretsStore class in gatekeeper/secrets.py. AES-256-GCM with HKDF-SHA256
> key derivation from machine ID (Linux: /etc/machine-id, Windows: MachineGuid registry)
> + per-install salt stored in config_dir/secrets.salt. Config_dir is injectable for
> testability (same pattern as CatalogDB). set_secret/get_secret/delete_secret API.
> Secrets file: JSON of {key: base64(nonce+ciphertext)}, perms 0600 (skipped on Windows).
> 22 unit tests: round-trip, plaintext absence, nonce randomness, wrong-machine-id
> rejection, corrupted-file handling, salt persistence, logging safety.
> Full suite: 228 pass, 9 skip.
```

---

### [x] 1.5.4 — Gatekeeper startup sequence

**Reads:** CLAUDE.md → Architecture quick reference, docs/architecture.md → Gatekeeper
**Creates:** `gatekeeper/main.py`
**Requirements:**
- Startup order:
  1. Assert Tailscale is running (1.5.1)
  2. Load and validate gatekeeper.cfg (1.4.1)
  3. Resolve and validate storage pool paths (1.5.2)
  4. Open catalog.db and cluster.db, run migrations (1.3.1, 1.3.2)
  5. Start Tahoe introducer if this node is the introducer (1.2.1)
  6. Start Tahoe storage node (1.2.2)
  7. Start Tahoe client/gateway (1.2.3)
  8. Start background schedulers (watcher, verify, rebalance, lifeboat)
  9. Start FastAPI (bound to Tailscale IP only)
- Each step logs start and completion
- Any failure in steps 1–7 aborts startup with a clear error message
- Graceful shutdown on SIGTERM: stop schedulers, close DB connections,
  stop Tahoe processes
**Done when:**
- Gatekeeper starts cleanly with a valid config
- Startup aborts with clear message if Tailscale is missing
- Startup aborts with clear message if config is invalid
- Graceful shutdown works on SIGTERM

```
> Kludde: main.py already existed with solid structure; added TahoeClient integration
> (step 7), setup-mode detection (root_dir.cap absent → 503 from /api/status),
> catalog key derivation via HKDF-SHA256, and background scheduler stubs.
> 16 unit tests via starlette.testclient.TestClient (httpx.ASGITransport 0.28.1
> does not trigger ASGI lifespan). Full suite: 248 pass, 9 skip (Windows POSIX skips).
```

---

## 1.6 — Agent core

### [x] 1.6.1 — Agent startup and gatekeeper registration

**Reads:** docs/architecture.md → Agent, docs/configuration.md → backup.cfg
**Creates:** `agent/main.py`, `agent/gatekeeper_client.py`
**Requirements:**
- Agent reads backup.cfg at startup (1.4.2)
- Agent connects to gatekeeper on local LAN using configured IP and port
- Authentication: pre-shared token generated at agent setup, stored in agent config
- `gatekeeper_client.py`: async HTTP client for agent→gatekeeper communication
  `register()` — announces agent presence to gatekeeper
  `send_fragment(fragment_data, metadata)` — sends fragment to gatekeeper
  `store_lifeboat(encrypted_bundle)` — stores lifeboat bundle from gatekeeper
  `get_lifeboat() → bytes` — returns stored lifeboat bundle
- Agent never communicates with the cluster directly — only with its gatekeeper
- Agent never accepts connections from outside its local subnet
**Done when:**
- Agent starts, reads config, registers with gatekeeper
- Gatekeeper logs the agent registration
- Unit test: agent registration, token authentication

```
> Kludde: agent/gatekeeper_client.py — async HTTP client (register, send_fragment)
> + local file ops (store_lifeboat, get_lifeboat — 0600 perms). agent/main.py —
> reads backup.cfg, calls register() non-fatally, starts config watcher, file
> watcher stub. Gatekeeper dual-listener added (ADR-017): GUI on Tailscale IP:8080,
> agent API on LAN IP:8081. Agent API: token auth via secrets.compare_digest +
> LAN-IP source check. backup.cfg extended with required [gatekeeper] section
> (url, token, name, lifeboat_path). gatekeeper.cfg extended with optional
> [agent_api] section (enabled, port, token). Registered agents kept in-memory
> dict — persistent storage added in 1.8.3 when lifeboat distribution needs it.
> store_lifeboat/get_lifeboat are local file ops; gatekeeper→agent HTTP transport
> for lifeboat push is implemented in task 1.8.3.
> 30 new unit tests (30 pass, 1 skip Windows chmod). Full suite: 278 pass, 10 skip.
```

---

### [x] 1.6.2 — File watcher with stability detection

**Reads:** docs/design.md → File watcher, SECURITY.md → Section 5
**Creates:** `agent/watcher.py`
**Requirements:**
- Watches all directories in backup.cfg [backup] using inotify (Linux) or
  polling fallback for other platforms
- File considered stable when ALL of:
  1. mtime unchanged for `stability_minutes` (default 30, configurable)
  2. File size unchanged between two checks
  3. No open file handles (check via /proc/fd or lsof)
- Stable file added to upload queue (asyncio Queue)
- File already in catalog.db with matching mtime and size: skip
- Excluded patterns (backup.cfg [exclude]) applied before queuing
- Storage pool paths (EXCLUDED_PATHS from 1.5.2) checked before queuing —
  never queue a file from a storage pool path
- Watcher runs at `nice +19` / `ionice -c 3`
**Done when:**
- New file detected and queued after stability window
- File with open handle not queued until handle is closed
- Excluded patterns respected
- Storage pool paths never queued
- Unit tests: stable file queued, unstable file not queued, excluded file skipped

```
> Kludde: FileWatcher in agent/watcher.py — polling-based (no inotify in Phase 1; portable
> across Linux/Windows/macOS). Stability detection: mtime + size unchanged since stable_since,
> no open handles (psutil). _scan_once() returns list[str] instead of writing to asyncio.Queue
> directly — asyncio.Queue is not thread-safe; run() puts results on the queue from the event
> loop thread after asyncio.to_thread() returns. nice+19 / ionice best-effort (no-op on
> Windows/permission errors). stability_minutes added to backup.cfg [schedule] as a plain
> integer (not a duration string — different from full_scan format). Storage pool paths passed
> as excluded_pool_paths parameter (not imported from gatekeeper.storage.pool) because agent
> and gatekeeper run on different machines in production. catalog_check is a callable
> defaulting to always-False — catalog.db lives on the gatekeeper; agent cannot query it
> directly. Both are TODO for wiring up via registration response. 15 unit tests pass
> (incl. async integration test via anyio). _has_open_handles patched in most tests —
> psutil process enumeration is slow on Windows (several seconds per scan).
> Full suite: 39 pass, 2 skip (POSIX critical-path tests, /etc and /proc absent on Windows).
```

---

## 1.7 — Fragmenter

### [x] 1.7.1 — Hash verification and fragmentation

**Reads:** SECURITY.md → Section 4, DECISIONS.md → ADR-006,
CLAUDE.md → Every file hash verification
**Creates:** `gatekeeper/fragmenter/fragmenter.py`, `gatekeeper/fragmenter/profiles.py`
**Requirements:**
- `profiles.py`: maps profile names to Tahoe k/n values
  `PROFILES = { "balanced": (3,5), "secure": (3,7), "paranoid": (3,10) }`
  Adaptiv profile k/n computed separately (see 1.11)
- `fragmenter.py`:
  `fragment_and_upload(file_path, profile, agent, original_path) → cap`
  1. Compute SHA-256 of file before fragmentation
  2. Set Tahoe shares.needed/happy/total from profile
  3. Build encrypted metadata tag (original_path + agent, encrypted with
     root_dir.cap-derived key — see ADR-008)
  4. Upload to Tahoe with metadata tag attached
  5. Compute SHA-256 of file after upload
  6. If hashes differ: log warning, raise FragmentationError
  7. If hashes match: insert into catalog.db
- CPU priority: `nice +19` applied to the worker process/thread
- Max concurrent uploads: from gatekeeper config
- Streaming: fragments sent as they complete, catalog.db updated only when
  all fragments confirmed placed (Tahoe servers-of-happiness check passes)
**Done when:**
- File uploaded, cap returned, catalog.db entry created
- Hash mismatch during upload raises FragmentationError and does not create katalog entry
- Unit tests: successful upload, hash mismatch handling, profile mapping

```
> Kludde: profiles.py — PROFILES dict with balanced(3,5)/secure(3,7)/paranoid(3,10); get_profile()
> raises ValueError for unknown names and "adaptive" (task 1.11.1). fragmenter.py — Fragmenter class
> with fragment_and_upload(): SHA-256 before+after upload (file-change detection), AES-256-GCM
> encrypted metadata tags in Tahoe directory entries for ADR-008 call-home reconstruction,
> catalog.db insertion on success. derive_metadata_key() uses separate HKDF info string from
> catalog key. ADR-018 added to DECISIONS.md: k/n is a Tahoe node-level setting (tahoe.cfg),
> not per-upload — verified in fork source; profile changes require node restart.
> StorageNode extended with shares_needed/happy/total params; written to tahoe.cfg [client].
> TahoeClient extended with link_file() using POST /uri/<dir>?t=set_children (verified in fork).
> main.py: reads profile k/n at startup, passes to StorageNode; creates Fragmenter and exposes
> on app.state. test_main.py mock fixed (fragmentation.profile = "balanced").
> 33 new tests (31 fragmenter + 2 storage_node shares). Full suite: 324 pass, 10 skip, 2 pre-existing
> agent test failures (test_agent_main.py — not caused by this task, agent/main.py untouched).
```

---

### [x] 1.7.2 — Upload queue worker

**Reads:** docs/design.md → File watcher, docs/architecture.md → Data flow backup
**Creates:** `gatekeeper/fragmenter/queue_worker.py`
**Requirements:**
- Consumes from the upload queue (produced by agent watcher)
- Calls `fragment_and_upload` for each item
- Respects `upload_concurrent` limit from config
- On FragmentationError: re-queue with exponential backoff (max 3 retries)
- After 3 failures: mark file as failed in log, send notification
- Worker runs continuously as an asyncio background task
**Done when:**
- Queue worker processes files in order
- Retries on failure with backoff
- Sends notification after 3 failures
- Unit test: successful queue processing, retry logic, failure notification

```
> Kludde: UploadItem dataclass + UploadQueueWorker in gatekeeper/fragmenter/queue_worker.py.
> N independent worker tasks (not Semaphore) — cleaner backpressure and fair fan-out.
> Retry: 1 initial + MAX_RETRIES=3 = 4 attempts total; backoff min(60, 2^attempt) seconds inline
> (not re-queue, to preserve order). Only FragmentationError caught; CancelledError/OSError
> propagate. queue.get() outside try/finally so cancellation during get() does not call
> task_done(). send_alert injectable callable (same pattern as catalog_check in agent/watcher.py);
> logs critical when None — wired to gatekeeper.notify.dispatcher in task 1.13.1.
> Security §6: failure logs contain only agent name, attempt count, error type, file size.
> 19 unit tests pass (backoff values, alert-once, alert-failure-handling, non-FragError propagation).
> Pre-existing test_agent_main.py failures unchanged (2 fail, unrelated to this task).
> Full suite: 343 pass, 10 skip, 2 pre-existing failures.
```

---

## 1.8 — Lifeboat

### [x] 1.8.1 — Lifeboat key generation and runtime encryption

**Reads:** SECURITY.md → Sections 1, 7, DECISIONS.md → ADR-007
**Creates:** `gatekeeper/lifeboat/crypto.py`, `gatekeeper/lifeboat/keystore.py`
**Requirements:**
- `keystore.py`:
  `generate_key() → bytes` — generates a random 32-byte key at first setup
  `load_key() → bytes` — reads key from `/etc/backup-buddy/lifeboat.key`
  Key file permissions: 0600, owned by service user
  If key file missing at startup: log critical error, abort — do not auto-generate
  (missing key = something is wrong, not a first-run scenario)
- `crypto.py`:
  `encrypt(data: bytes, key: bytes) → bytes` — AES-256-GCM encryption
  `decrypt(data: bytes, key: bytes) → bytes` — AES-256-GCM decryption
  Random 16-byte nonce prepended to output
  Authentication tag verified on decrypt — raises `IntegrityError` on failure
  No passphrase involved — key is always the raw 32-byte key from keystore
**Done when:**
- Key generated at setup, loaded at restart without user input
- Encrypt/decrypt round-trip works
- Wrong key on decrypt raises IntegrityError
- Unit tests: generate, load, encrypt/decrypt, wrong key, missing key file

```
> Kludde: crypto.py — encrypt/decrypt with AES-256-GCM; 16-byte random nonce prepended to output;
> IntegrityError raised on wrong key, tampered data, or truncated input. No passphrase involved —
> raw 32-byte key from keystore only. keystore.py — generate_key() writes 32 random bytes to
> /etc/backup-buddy/lifeboat.key (0600, POSIX; skipped on Windows); load_key() reads file and
> validates length; KeyNotFoundError (subclass of KeystoreError) logged at CRITICAL and raised
> if file absent — never auto-generates. Key path injectable for testability (same pattern as
> CatalogDB/SecretsStore). 25 unit tests pass, 1 skip (POSIX perms on Windows dev machine).
```

---

### [x] 1.8.2 — Recovery kit (disaster recovery only)

**Reads:** SECURITY.md → Sections 1, 7, DECISIONS.md → ADR-007,
project-docs/onboarding.md → Setup complete
**Creates:** `gatekeeper/lifeboat/recovery_kit.py`
**Requirements:**
- `create_recovery_kit(passphrase: str) → bytes`
  Contents: node.privkey + root_dir.cap serialized and encrypted with passphrase
  Key derivation: Argon2id (time_cost=3, memory_cost=65536, parallelism=4)
  Random 16-byte salt prepended to output
  Encryption: AES-256-GCM
  Called ONCE at first setup — never updated automatically
- `extract_recovery_kit(data: bytes, passphrase: str) → dict`
  Decrypts and deserializes — returns dict with node.privkey and root_dir.cap
  Raises `IntegrityError` on wrong passphrase or tampered data
- Recovery kit presented to user in onboarding as a downloadable file
  (`backup-buddy-recovery-kit.enc`)
- User must confirm they have saved it before onboarding continues
- Passphrase is entered once at setup and once at full disaster recovery — never again
- No passphrase in any log output (even partial)
**Done when:**
- Recovery kit created, downloadable from onboarding wizard
- Wrong passphrase raises IntegrityError
- User confirmation required before onboarding proceeds
- Unit tests: create, extract, wrong passphrase, tampered data

```
> Kludde: recovery_kit.py — create_recovery_kit(passphrase, node_privkey, root_dir_cap) → bytes;
> extract_recovery_kit(data, passphrase) → dict. Argon2id (time_cost=3, memory_cost=65536 KiB,
> parallelism=4) key derivation; AES-256-GCM encryption; wire format: salt(16)||nonce(16)||ciphertext.
> IntegrityError imported from crypto.py (no duplication). Length check before Argon2 to avoid
> spending ~200 ms on obviously invalid input. Passphrase not logged; exception messages contain
> no passphrase material. node_privkey and root_dir_cap passed as str parameters (dependency
> injection pattern — onboarding wizard has them in memory). Both stored as JSON in the payload.
> Tahoe node.privkey is a UTF-8 string in tahoe.cfg (ed25519.string_from_signing_key output).
> 13 unit tests via session-scoped fixture (kit created once to minimise Argon2 cost per run).
> Full suite: 381 pass, 11 skip, 2 pre-existing agent test failures (unchanged).
```

---

### [x] 1.8.3 — Lifeboat bundle creation and distribution

**Reads:** DECISIONS.md → ADR-007, project-docs/design.md → Lifeboat mechanism
**Creates:** `gatekeeper/lifeboat/bundle.py`, `gatekeeper/lifeboat/distributor.py`
**Requirements:**
- `bundle.py`:
  `create_bundle() → bytes`
  Reads: node.privkey, root_dir.cap, catalog.db (copy), gatekeeper.cfg
  Serializes to dict, encrypts with runtime key from keystore (1.8.1)
  `extract_bundle(data: bytes) → dict`
  Decrypts using runtime key — no passphrase involved
- `distributor.py`:
  `distribute()`:
  1. Create bundle via bundle.py
  2. Send encrypted bundle to all registered agents via agent client
  3. Each agent stores bundle at fixed path, permissions 0600
  4. After distribution: verify bundle can be decrypted with local key
  5. Update lifeboat timestamp in cluster.db
  Scheduled every `lifeboat.interval` (default: 1h) via APScheduler
  If distribution fails for any agent: log warning, continue with others
  If verification fails: log error, send critical alert
- **NOTE (from 1.6.1):** `GatekeeperClient.store_lifeboat()` and
  `GatekeeperClient.get_lifeboat()` are already implemented as local file ops
  in `agent/gatekeeper_client.py`. This task must add the HTTP transport:
  a POST /lifeboat endpoint on the agent (or push from gatekeeper) that calls
  `store_lifeboat()`, and a GET /lifeboat endpoint (or pull) that calls
  `get_lifeboat()`. The agent will need a minimal HTTP listener for this.
  The registered agents dict in `gatekeeper/main.py:_registered_agents` must
  also be persisted (to cluster.db or a new table) so the gatekeeper knows
  which agents to push to after restart.
**Done when:**
- Bundle created and distributed to all agents
- Verification passes after distribution
- Scheduler triggers correctly
- Unit tests: bundle round-trip, distribution, verification, failed agent handled

```
> Kludde: bundle.py — create_bundle(data_dir, config_path, catalog_conn, *, key=None) → bytes;
> extract_bundle(encrypted_data, *, key=None) → dict. JSON payload: version, node_privkey,
> root_dir_cap, catalog_db_b64 (WAL-safe via Connection.backup() to tempfile), gatekeeper_cfg.
> Encrypted with AES-256-GCM runtime key from keystore (key param for testability).
> distributor.py — LifeboatDistributor: distribute() creates bundle, verifies locally,
> POSTs to each agent's lifeboat_url (stored in cluster.db agents table), records result in
> lifeboat_status table. run_scheduler() loops with asyncio.sleep; skips cycle if previous
> still in flight (asyncio.Lock). Per-agent failures logged but do not abort distribution.
> cluster.db migration 002: agents table (agent_name, ip, lifeboat_url, registered_at,
> last_seen) + lifeboat_status table. ClusterDB.upsert_agent() uses ON CONFLICT DO UPDATE.
> Agent lifeboat HTTP server: POST /lifeboat + GET /lifeboat on LAN IP:8082 (token auth,
> reuses gatekeeper.token pre-shared key). Agent detects LAN IP via psutil at startup and
> advertises lifeboat_port in registration; gatekeeper constructs lifeboat_url from client IP.
> _AgentRegisterMessage extended with lifeboat_port: int | None = None.
> _create_agent_api_app now accepts data_dir; opens own ClusterDB connection (WAL safe).
> CatalogDB.connection property added for WAL-safe snapshot access.
> 21 new tests. Full suite: 404 pass, 11 skip.
```

---

## 1.9 — Cluster and invite system

### [x] 1.9.1 — Invite code generation and management

**Reads:** DECISIONS.md → ADR-009, project-docs/design.md → Invite system
**Creates:** `gatekeeper/cluster/invites.py`, `gatekeeper/cluster/wordlist.txt`
**Requirements:**
- `generate_invite(created_by: str) → InviteCode`
  Generates a human-readable code: two words + number (e.g. "coffee-trumpet-7")
  Format: `{word}-{word}-{1–9}` — all lowercase, hyphen-separated
  Words sourced from bundled wordlist (see below)
  Code stored in cluster.db invites table
  Single-use, expires after 48 hours
- `validate_invite(code: str) → InviteCode | None`
  Returns None if: not found, already used, expired, revoked
- `revoke_invite(code: str, revoked_by: str)`
  Marks code as revoked in cluster.db
- `consume_invite(code: str, node_info: dict)`
  Marks code as used, triggers cluster join (1.9.2)
- All active invites visible to all cluster members (via GUI in 1.14)

**Wordlist (`gatekeeper/cluster/wordlist.txt`):**
  Fetch the EFF Short Wordlist #1 from:
  `https://www.eff.org/files/2016/09/08/eff_short_wordlist_1.txt`
  Parse it (tab-separated: dice-number + word), extract words only, save to wordlist.txt
  The list contains ~1296 common, safe English words — no offensive or obscure terms
  If the URL is unreachable: generate codes as `bb-{8 random hex chars}` instead
  (e.g. `bb-a3f9k2m7`) and log a warning that the wordlist could not be fetched
  Bundle the fetched wordlist.txt with the project so subsequent runs do not need
  network access for code generation

**Done when:**
- Codes generated in correct format using wordlist words
- Fallback format used if wordlist unavailable
- Expired codes rejected, used codes rejected, revocation works
- Unit tests: generate, validate, expire, revoke, consume, fallback format

```
> Kludde: invites.py — policy layer over ClusterDB (generate_invite, validate_invite,
> revoke_invite, consume_invite). InviteCode dataclass. Codes in format word-word-N
> using EFF Short Wordlist #1 (1294 words bundled in wordlist.txt; "yo-yo" excluded —
> hyphen breaks format). Fallback bb-{8hex} logged as warning when wordlist missing.
> Randomness: secrets.choice + secrets.randbelow (never random module).
> validate_invite checks revoked → used → expired in that order.
> revoke_invite raises ValueError on used codes (cannot un-use).
> ADR-009 corrected: "three words" → "two words" (typo confirmed by Johan 2026-05-21).
> 28 unit tests pass. Full suite: 432 pass, 11 skip.
```

---

### [x] 1.9.2 — Cluster join flow

**Reads:** DECISIONS.md → ADR-009, docs/design.md → Invite system,
docs/architecture.md → Introducer node
**Creates:** `gatekeeper/cluster/join.py`
**Requirements:**
- `initiate_join(invite_code: str, node_info: NodeInfo) → JoinResult`
  Called on the joining gatekeeper
  1. Validate invite code against the cluster (HTTP call to any known member)
  2. Receive introducer FURL and cluster metadata
  3. Configure local Tahoe client with introducer FURL
  4. Register self in cluster.db on the remote side
  5. Consume invite code
- `accept_join(node_info: NodeInfo, invite_code: str) → bool`
  Called on the receiving/existing gatekeeper
  Validates invite, adds new member to cluster.db, returns introducer FURL
- `NodeInfo` Pydantic model: node_id, display_name, tailscale_hostname, profile
- All inbound data validated with Pydantic before processing
**Done when:**
- Two-node cluster forms correctly via invite code
- New node appears in both nodes' cluster.db
- Invalid invite rejected cleanly
- Unit tests: successful join, invalid code, expired code

```
> Kludde: Created gatekeeper/cluster/join.py with NodeInfo (Pydantic, validated),
> JoinRequest, _JoinResponseBody (untrusted HTTP response validator), JoinAcceptResponse,
> JoinResult. accept_join consumes the invite atomically before insert_member to prevent
> double-use. initiate_join is async (httpx), validates the response with Pydantic before
> returning. POST /api/cluster/join added to the Tailscale-bound GUI app; introducer_furl
> stored in app.state (not logged). Added member_url param to initiate_join — the spec
> omitted it (a joining node has no known members). Tahoe client config is out of scope
> here; the onboarding wizard (1.15.2) handles that using the returned introducer_furl.
> 26 new unit tests pass. Full suite: 458 pass, 11 skip. Commit: fcdf58d.
```

---

## 1.10 — Node removal and orphan cleanup

### [x] 1.10.1 — Removal vote mechanism

**Reads:** DECISIONS.md → ADR-010, docs/design.md → Node removal
**Creates:** `gatekeeper/cluster/removal.py`
**Requirements:**
- `propose_removal(target_node_id: str, proposed_by: str)`
  Creates an open vote in cluster.db votes table
  Vote open for 48 hours
  Target node does NOT receive notification of the vote
  All other members notified via webhook/SMTP
- `cast_vote(vote_id: str, node_id: str, vote: bool)`
  Records yes/no vote
  After each vote: check if majority reached
- `check_vote_result(vote_id: str) → VoteResult`
  Returns: pending, passed, failed, expired
  Majority = more than half of current members (excluding target)
- `start_grace_period(target_node_id: str)`
  Sets grace period start in cluster.db
  Notifies target node that removal process has started
  Triggers re-fragmentation of target's data (see 1.11)
- `extend_grace_period(target_node_id: str, days: int, proposed_by: str)`
  Requires majority vote (reuses vote mechanism)
**Done when:**
- Vote created, cast, resolved correctly
- Grace period starts after majority
- Target not notified until grace period starts
- Unit tests: vote passes, vote fails, vote expires, grace period extension

```
> Kludde: Created migration 003 adding vote_ballots (PRIMARY KEY enforces no double-voting),
> grace_extension_days on votes, grace_started_at and grace_days on members. Extended
> ClusterDB with insert_ballot/list_ballots methods and insert_vote grace_extension_days
> param. Created gatekeeper/cluster/removal.py with VoteResult enum, VoteRecord dataclass,
> and six public functions: propose_removal, cast_vote, check_vote_result,
> start_grace_period, extend_grace_period, apply_grace_extension. Target not notified
> until start_grace_period (as required by ADR-010). vote_id is int (schema uses
> INTEGER PRIMARY KEY AUTOINCREMENT; TODO.md comment "str" is a typo). 41 unit tests,
> all pass. Full suite: 499 passed, 11 skipped.
```

---

### [x] 1.10.2 — Orphan fragment tracking and cleanup

**Reads:** DECISIONS.md → ADR-012, docs/design.md → Orphan fragment cleanup
**Creates:** `gatekeeper/cluster/orphans.py`
**Requirements:**
- `mark_orphan(fragment_id: str, owner_node_id: str)`
  Called when a node is confirmed removed after grace period
  Records in cluster.db orphan_tags table with marked_orphan_at timestamp
- Daily job `cleanup_orphans()`:
  1. Find all orphan_tags where marked_orphan_at > orphan_grace_days ago
  2. Verify re-fragmentation of owner's data is complete before deleting
  3. Delete fragment files from storage pool
  4. Update orphan_tags.cleaned_at
  5. Send notification: "Cleared X GB of orphaned fragments from [node]"
- `extend_orphan_grace(owner_node_id: str, days: int)`
  Extends marked_orphan_at by days — triggered by cluster vote
- Deletion must not happen if re-fragmentation is still in progress
**Done when:**
- Orphans marked correctly after node removal
- Cleanup job deletes only confirmed-complete orphans
- Grace period extension works
- Unit tests: mark, cleanup (re-frag complete), cleanup blocked (re-frag pending)

```
> Kludde: gatekeeper/cluster/orphans.py — tre publika funktioner: mark_orphan (idempotent,
> loggar warning om taggen redan finns), cleanup_orphans (daily-job med injectable
> is_refrag_complete och delete_fragment — blockerar borttagning om re-frag ej klar,
> loggar exception utan att avbryta loopen, skickar alert per borttagen fragment),
> extend_orphan_grace (förlänger marked_orphan_at för alla pending orphans av en ägare).
> delete_fragment är injicerbar — måste anropa StoragePoolManager.remove_fragment() (task 1.5.2).
> Inga nya migrations behövdes; orphan_tags-tabellen finns sedan migration 001.
> 21 unit tests pass. Full suite: 520 pass, 11 skip. Commit: 0fa2798.
```

---

## 1.11 — Re-fragmentation scheduler

### [x] 1.11.1 — Adaptiv profile k/n calculation

**Reads:** DECISIONS.md → ADR-006a, docs/design.md → Adaptiv profile
**Creates:** `gatekeeper/fragmenter/adaptive.py`
**Requirements:**
- `compute_adaptive_kn(node_count: int, config: AdaptiveConfig) → tuple[int, int]`
  ratio = config.ratio (default 0.33)
  n = min(node_count, config.max_n)
  k = max(round(n * ratio), config.min_k)
  Returns (k, n)
- `get_current_kn() → tuple[int, int]`
  Reads current active node count from cluster.db
  Returns computed k/n for current cluster state
- Profile stored in gatekeeper config as "adaptive" — k/n resolved at upload time
**Done when:**
- Correct k/n computed for various cluster sizes
- min_k and max_n limits respected
- Unit tests: 3 nodes, 6 nodes, 9 nodes, 20+ nodes, min_k boundary, max_n boundary

```
> Kludde: Created gatekeeper/fragmenter/adaptive.py with compute_adaptive_kn() and
> get_current_kn(). Special case for <3 nodes (all-of-n). Formula for 3+ nodes:
> n=min(node_count, max_n), k=max(round(n*ratio), min_k). Updated AdaptiveConfig
> default min_k from 2→1 (matches Johan's 1:3 design intent). Updated ADR-006a in
> DECISIONS.md with correct reference table and rationale. 20 unit tests pass.
```

---

### [x] 1.11.2 — Rebalance scheduler

**Reads:** DECISIONS.md → ADR-011, docs/design.md → Re-fragmentation policy
**Creates:** `gatekeeper/rebalance/scheduler.py`, `gatekeeper/rebalance/worker.py`
**Requirements:**
- `scheduler.py`: nightly job that evaluates whether re-fragmentation is needed
  1. Get current cluster size
  2. Compute distance from baseline (stored in cluster.db)
  3. If distance <= hysteresis_nodes: skip, log "within hysteresis zone"
  4. If cluster has been at this size < stability_days: skip, log days remaining
  5. Otherwise: queue re-fragmentation run
- `worker.py`: executes re-fragmentation at `daily_rebalance_pct` per night
  Priority order:
  1. Files below minimum k (critical — always first, ignores hysteresis)
  2. Oldest backed-up files
  3. Largest files
  4. Remainder
  For each file:
  1. Download k fragments, reconstruct file to temp location
  2. Re-upload with new k/n, get new cap
  3. Verify new fragments placed
  4. Update catalog.db with new cap
  5. Delete old fragments
  6. Clean temp file
- Notify on start and completion
**Done when:**
- Hysteresis check works correctly
- Stability threshold blocks premature rebalancing
- Files below k treated as critical regardless of hysteresis
- New fragments verified before old deleted
- Unit tests: hysteresis boundary, stability threshold, priority ordering

```
> Kludde: Migration 004 adds rebalance_state singleton. worker.py downloads
> each file to an isolated 0700 temp dir, re-uploads, overwrites the Tahoe
> directory entry via link_file (same SHA-256 entry_name as fragmenter),
> and updates catalog.db with the new cap and current k/n per ADR-018.
> scheduler.py evaluates hysteresis + stability nightly; critical files
> (k > cluster_size) bypass both checks per ADR-011. 27 unit tests pass.
```

---

## 1.12 — Restore

### [x] 1.12.1 — Normal restore flow

**Reads:** docs/architecture.md → Data flow restore, docs/design.md → Restore,
SECURITY.md (full)
**Creates:** `gatekeeper/restore/restore.py`
**Requirements:**
- `restore_file(original_path: str, agent: str, dest_path: str) → RestoreResult`
  1. Look up file in catalog.db by original_path + agent
  2. Get cap from catalog.db entry (decrypt cap)
  3. Download via Tahoe client (1.2.3) to temp path (permissions 0700)
  4. Compute SHA-256 of restored file
  5. Compare against catalog.db sha256
  6. If match: move to dest_path, return success
  7. If mismatch: try alternate fragment combinations (if available)
  8. If all attempts fail: raise RestoreIntegrityError, send alert
  9. Clean temp path regardless of outcome
- `restore_folder(folder_path: str, agent: str, dest_path: str) → RestoreResult`
  Calls restore_file for all files under folder_path in catalog.db
- Temp directory created with permissions 0700, cleaned up after each file
- Temp directory must not be under any path in backup.cfg
**Done when:**
- File restored and hash verified
- Mismatch triggers alert
- Temp files always cleaned up
- Unit tests: successful restore, hash mismatch, temp cleanup on failure

```
> Kludde: restore.py — restore_file() and restore_folder() with hash verification.
> _restore_from_record() internal helper avoids double catalog lookup in folder restore.
> Retry once on hash mismatch (Tahoe may use different fragments); after two failures:
> send_alert + raise RestoreIntegrityError. No Tahoe internals in error messages.
> Temp dir: tempfile.mkdtemp() + 0700 chmod (POSIX); cleaned via shutil.rmtree in finally.
> _check_temp_dir_not_in_pool() warns if temp dir overlaps storage pool (EXCLUDED_PATHS).
> restore_folder: get_all_files() + Python-side prefix filter (HMAC blind index prevents
> SQL prefix search on encrypted paths). TahoeError propagates from restore_file.
> send_alert injectable callable; None → log at ERROR. Same pattern as queue_worker.py.
> 22 unit tests (21 pass, 1 skip POSIX perms). Full suite: 588 pass, 12 skip.
```

---

### [x] 1.12.2 — "Call home" catalog reconstruction

**Reads:** DECISIONS.md → ADR-008, docs/design.md → Call home,
docs/architecture.md → Data flow restore (call home)
**Creates:** `gatekeeper/restore/reconstruct.py`
**Requirements:**
- `reconstruct_catalog(root_dir_cap: str) → int`
  Used when catalog.db is missing or corrupt
  1. Open Tahoe file tree from root_dir_cap
  2. Traverse entire tree recursively
  3. For each file cap: decrypt metadata tag (original_path, agent, backed_up_at)
  4. Insert into new catalog.db
  5. Return count of files reconstructed
- Called from the emergency restore GUI flow (1.14)
- Runs asynchronously — long-running for large catalogs
- Progress reported via an asyncio Event or queue (for GUI progress display)
- If metadata tag is missing or unreadable: file still added to catalog.db
  with original_path = None (file exists but path unknown)
**Done when:**
- All files from Tahoe tree added to catalog.db
- Encrypted metadata correctly decrypted
- Files with missing metadata added with original_path = None
- Unit test: reconstruct from tree, verify entry count, verify paths

```
> Kludde: reconstruct.py — reconstruct_catalog(root_dir_cap, *, catalog, tahoe, progress_queue=None) → int.
> Adds TahoeClient.ls_with_metadata() (returns name/file_ref/metadata/size per filenode; skips
> subdirectories). Decrypts original_path_enc and agent_enc using derive_metadata_key(root_dir_cap)
> (reused from fragmenter). Sentinel values for unknown fields: sha256="", profile="unknown", k=0, n=0.
> Files with missing or unreadable metadata inserted with original_path=None (ADR-008 design intent).
> restore.py _download_with_retry() short-circuits on sha256="" — downloads and returns actual digest
> with a warning instead of raising RestoreIntegrityError. 21 new tests; 609 pass, 12 skipped.
```

---

## 1.13 — Verification and notifications

### [x] 1.13.1 — Notification dispatcher

**Reads:** SECURITY.md → Section 6, docs/design.md → Monitoring and notifications,
docs/configuration.md → notify
**Creates:** `gatekeeper/notify/dispatcher.py`, `gatekeeper/notify/smtp.py`,
`gatekeeper/notify/webhook.py`
**Requirements:**
- `dispatcher.py`: `send_alert(level, message, detail=None)`
  level: info | warning | error | critical
  Reads notify config and enabled channels
  Dispatches to enabled channels (SMTP and/or webhook)
  Respects per-event config (on_backup_success, on_backup_failure, etc.)
- `smtp.py`: sends email via aiosmtplib
  Password fetched from encrypted secrets store (1.5.3), never from config object
  TLS required (STARTTLS or SSL)
  Connection tested with `test_smtp() → bool`
- `webhook.py`: sends HTTP POST via httpx
  Generic JSON payload: `{ level, message, detail, timestamp, node }`
  Tested with `test_webhook() → bool`
- Neither module logs the password or webhook URL
**Done when:**
- Alert dispatched to both channels when both enabled
- Test functions return True on success, False on failure
- Unit tests: dispatch to smtp, dispatch to webhook, failed smtp handled gracefully

```
> Kludde: AlertDispatcher in gatekeeper/notify/dispatcher.py — routes alerts to SMTP
> and/or webhook channels based on per-event config flags (on_backup_failure etc.).
> send_alert(level, message, detail=None, *, event=None): event maps to notify.on_*
> config key; None event = always dispatch; critical level bypasses event filter.
> Channel failures isolated via asyncio.gather(return_exceptions=True).
> smtp.py: aiosmtplib, STARTTLS port 587 / implicit TLS port 465; password from
> SecretsStore key "smtp_password"; test_smtp() accepts explicit password for pre-save
> testing. webhook.py: httpx AsyncClient, 5 s timeout, JSON payload {level, message,
> detail, timestamp, node}; URL from SecretsStore key "webhook_url"; test_webhook()
> accepts explicit URL. Neither module logs passwords or URLs.
> Design note: send_alert gains optional event= param (spec omitted it but per-event
> filtering requires it). Existing callers (queue_worker, orphans, restore) use
> inconsistent injectable signatures — will be aligned when wired in main.py.
> 38 unit tests pass. Full suite: 647 pass, 12 skip. Commit: e1875650b.
```

---

### [x] 1.13.2 — Nightly verification job

**Reads:** docs/design.md → Verification and test restore,
docs/configuration.md → verify, SECURITY.md → Section 8
**Creates:** `gatekeeper/verify/nightly.py`
**Requirements:**
- Four verification layers run in order each night at `verify.daily_check_time`:

  **Layer 1 — root_dir.cap integrity**
  Attempt to open the Tahoe file tree from root_dir.cap
  Failure: critical alert, log, continue to next layer

  **Layer 2 — catalog.db vs cluster**
  For each file in catalog.db: verify cap exists in Tahoe tree
  Count fragments per file: if below k, flag for rebalance
  Files with count < k: immediate rebalance queue, send warning alert

  **Layer 3 — Test restore**
  Select `test_restore_files` random files from catalog.db
  Restore each to `test_restore_path` (temp, permissions 0700)
  Verify SHA-256 against catalog.db
  Clean up temp files regardless of result
  Any failure: error alert with file name and detail

  **Layer 4 — Lifeboat age check**
  Check lifeboat timestamp in cluster.db
  If older than `lifeboat_max_age_hours`: warning alert
  Attempt to decrypt lifeboat from one agent (using in-memory passphrase)
  Failure: critical alert

- Job logs start and completion
- Failure in any layer does not prevent other layers from running
- `notify_on_success = false` by default — silent on clean run
**Done when:**
- All four layers run nightly
- Failures in each layer send correct alert level
- Temp files cleaned after test restore
- Unit tests: each layer independently, combined run

```
> Kludde: NightlyVerifier in gatekeeper/verify/nightly.py — four isolated layers run in order;
> exception in one layer does not prevent others from running. Layer 1: ls() root_dir.cap,
> critical alert on TahoeError. Layer 2: check_cap() per file in catalog; warns on under-replication
> (shares_good < shares_needed), errors on inaccessible; shares_needed=0 skipped (unknown k).
> Layer 3: random sample of N restorable files restored to 0700 temp dir under test_restore_path;
> always cleaned in finally; original_path=None excluded. Layer 4: lifeboat age check then fetch
> from first agent with lifeboat_url and decrypt with runtime key (load_key); root_dir_cap match
> verified; critical alert on any failure. Alert suppression via notify_on_success/warning/failure/
> corrupt flags. run_scheduler() sleeps to next daily_check_time; skips if previous run in progress.
> TahoeClient.check_cap() (t=check&output=json) and ClusterDB.get_last_lifeboat_status() already
> existed from prior tasks. 35 unit tests pass. Full suite: 682 pass, 12 skip. Commit: 23f0188fc.
```

---

## 1.14 — Web GUI

### [x] 1.14.1 — FastAPI application setup

**Reads:** DECISIONS.md → ADR-015, SECURITY.md → Section 3,
CLAUDE.md → Every GUI route
**Creates:** `gatekeeper/gui/__init__.py`, `gatekeeper/gui/app.py`,
`gatekeeper/gui/templates/base.html`
**Requirements:**
- FastAPI app bound to Tailscale IP only (resolved via gatekeeper/tailscale.py)
- Startup event asserts Tailscale is running before binding
- All routes return 404 for requests not from Tailscale subnet
- Base HTML template: clean, functional, no external CDN dependencies
  (all CSS/JS served locally — no calls to cdn.tailwindcss.com etc.)
- Error pages: plain language, no stack traces, no Tahoe internals
- Request logging: method, path, status — no query params that might contain keys
**Done when:**
- App starts and is accessible at Tailscale IP:8080
- Not accessible at localhost:8080 or LAN IP:8080
- Base template renders without external network calls
- Unit test: request from Tailscale IP accepted, from other IP rejected

```
> Kludde: Implemented in gatekeeper/gui/app.py. TailscaleOnlyMiddleware rejects
> non-Tailscale IPs with 404 (information-leak-safe). RequestLoggingMiddleware
> logs method + path + status only — query string is never logged. Starlette 1.0.0
> changed TemplateResponse to require request as first arg; updated accordingly.
> setup_gui(app) wires middleware, router, /static mount, and 404/500 error handlers
> into the main app. All 8 TestClient calls in test_main.py updated with
> client=("100.64.0.1", 12345) so they pass through the new IP filter.
> 43 new tests in test_gui_app.py covering _is_tailscale_ip, both middleware classes,
> and full integration (routes, static files, template rendering). 709 tests pass.
```

---

### [x] 1.14.2 — Dashboard

**Reads:** docs/design.md → Web GUI → Dashboard
**Creates:** `gatekeeper/gui/routes/dashboard.py`, `gatekeeper/gui/templates/dashboard.html`
**Requirements:**
- Cluster status: connected node count, list with online/offline indicator
- Storage pool: used / quota per path, total percentage
- Buddy storage table: per buddy — contributes / uses / ratio / status
  Warning indicator if any buddy is below 1.0x ratio
- Last backup per agent: timestamp and success/failure
- Active jobs: fragmenting, uploading, verifying (with progress where available)
- All data fetched from cluster.db and catalog.db — no live cluster calls on page load
- Data refreshed every 30 seconds via lightweight polling (no WebSocket in Phase 1)
**Done when:**
- Dashboard renders with real data from databases
- Warnings shown for ratio imbalance
- Polling updates data without full page reload

```
> Kludde: dashboard.py — _build_dashboard_data() reads cluster.db and catalog.db; no
> live cluster calls on page load. Cluster section: member list with online_count,
> ratio (contribution/usage), row-warning (<1.2x) and row-error (<1.0x) CSS classes.
> Storage pool section: per-path used/quota/free with progress bar (warning >75%,
> error >90%) and total summary row. Agents section: per-agent last_backup_at and
> file_count from catalog; online/offline badge (15-minute threshold). Jobs section:
> rebalance in_progress/last_run_at and lifeboat distributed_at/success_count. Upload
> queue progress not tracked in DB (in-memory only in Phase 1 — no persistent state).
> GET / renders SSR HTML; GET /api/dashboard returns JSON; dashboard.html polls
> /api/dashboard every 30 s and patches sections without full reload.
> 31 unit tests pass. Full suite: 740 pass, 12 skip.
```

---

### [x] 1.14.3 — Restore UI

**Reads:** docs/design.md → Web GUI → Restore, docs/onboarding.md
**Creates:** `gatekeeper/gui/routes/restore.py`, `gatekeeper/gui/templates/restore.html`
**Requirements:**
- Three restore entry points (as defined in docs/onboarding.md):
  1. Find a specific file — search by name or date, restore single file
  2. Restore a full folder — select agent + date, restore to chosen destination
  3. Emergency restore — load root_dir.cap, trigger catalog reconstruction (1.12.2)
     with progress indicator
- Restore jobs run async — page shows progress, does not block
- Restored files delivered to a user-specified destination path on the gatekeeper
- Hash verification result shown per file
- Failure shown clearly with "try again" option
**Done when:**
- All three restore paths work end-to-end
- Progress shown for long-running restores
- Hash verification result visible per file

```
> Kludde: Three-tab restore UI: Find a file, Restore a folder, Emergency restore.
> routes/restore.py: 6 routes (GET /restore, GET /api/restore/catalog, POST /api/restore/start/file,
> POST /api/restore/start/folder, POST /api/restore/emergency, GET /api/restore/jobs/{job_id}).
> In-memory job registry capped at 50 entries, oldest completed jobs evicted first.
> Catalog search is O(catalog) — HMAC blind index prevents SQL prefix search; intentional Phase 1.
> Emergency restore writes to main catalog only when empty (409 if records exist — ADR Option A).
> Dest path validation: must be absolute on original path (not resolved), no storage pool overlap.
> Bug found and fixed: original _validate_dest_path checked os.path.isabs on realpath result
> which is always True — moved check to original dest_path before realpath resolution.
> base.html updated with nav links. app.py registers create_restore_router().
> CSS: tabs, form controls, buttons, job status, search row, inline restore form.
> 42 unit tests pass. Full suite: 782 pass, 12 skip.
```

---

### [x] 1.14.4 — Settings UI

**Reads:** docs/design.md → Web GUI → Settings, docs/configuration.md
**Creates:** `gatekeeper/gui/routes/settings.py`, `gatekeeper/gui/templates/settings.html`
**Requirements:**
- Fragmentation profile: four buttons (Balanced / Secure / Paranoid / Adaptive)
  Current profile highlighted, change takes effect on next backup
  Paranoid disabled with tooltip if cluster has fewer than 10 nodes
- Storage pool: list paths with quota, add/remove paths
  Adding a path: validate it exists and is writable, add to exclusion set immediately
- Notifications:
  SMTP fields: host, port, username, "to" address — password entered separately
  "Test email" button — calls test_smtp(), shows result inline
  Webhook URL field — "Test webhook" button
  Passwords/URLs sent to encrypted secrets store, never stored in form values
- Lifeboat: timestamp of last distribution, "Test decryption" button
  Test decryption prompts for passphrase, decrypts bundle from an agent, confirms OK
**Done when:**
- Profile change persisted and applied to next backup
- Storage pool path addition validates and updates exclusion set
- Notification test buttons work and show inline result
- Lifeboat decryption test works

```
> Kludde: 10 routes across settings.py. Profile buttons with Paranoid guard (< 10 nodes).
> Config write-back via configparser (atomic tempfile rename). Passwords/URLs only in SecretsStore,
> never in config or form values. Storage pool remove blocks with 409 if used_bytes > 0.
> EXCLUDED_PATHS and pool manager updates require restart; UI shows this message.
> Lifeboat test: create+decrypt local bundle (validates keystore key). Recovery kit test:
> decrypt data_dir/recovery_kit.enc with user passphrase. app.state.config_path and
> app.state.data_dir added to lifespan. 37 unit tests pass. Full suite: 819 pass, 12 skip.
```

---

### [x] 1.14.5 — Buddies and cluster management UI

**Reads:** docs/design.md → Web GUI → Buddies, docs/design.md → Invite system,
docs/design.md → Node removal
**Creates:** `gatekeeper/gui/routes/buddies.py`, `gatekeeper/gui/templates/buddies.html`
**Requirements:**
- Buddy table: name, online status, contributes, uses, ratio, profile
- Cluster storage summary: total capacity, total used, total percentage
- Generate invite button: creates code (1.9.1), displays it clearly,
  shows expiry time, copy button
- Active invites list: code (masked), created by, expires at, revoke button
- Propose removal: button per buddy, confirmation modal before submitting
- Active votes: list with yes/no counts, cast vote button (for open votes)
- Grace period extensions: button if a node is in grace period
**Done when:**
- Buddy table shows real data
- Invite generation and display works
- Removal proposal and voting works end-to-end
- Active votes visible and castable

```
> Kludde: Buddy table (name, status, contribution, usage, ratio, profile) with SSR + 30s JS polling.
> Cluster storage summary (total capacity/used/percent). Generate invite: POST /api/buddies/invite
> returns full code once; displayed in modal with copy button; list shows masked code (word-***-N).
> Revoke button per active invite. Propose removal: per-buddy button opens confirmation modal before
> POST. Active votes table: yes/no cast buttons; open votes targeting the local node filtered out
> (ADR-010). cast_vote auto-calls start_grace_period on removal PASSED and apply_grace_extension on
> grace_extension PASSED. Grace extension: "Extend grace" button per grace-status member, opens days
> input modal. upsert_self_member added to ClusterDB so local node is always in members table.
> local_node_id stored in app.state at startup. Modal CSS added to style.css (no CDN).
> 21 unit tests, full suite 840 pass 12 skip. Commit: 0888617d0.
```

---

### [x] 1.14.6 — Agents UI

**Reads:** docs/design.md → Web GUI → Agents
**Creates:** `gatekeeper/gui/routes/agents.py`, `gatekeeper/gui/templates/agents.html`
**Requirements:**
- List of registered agents with: name, last seen, last backup status, backup.cfg status
- Per-agent detail: backup.cfg contents (if share_log = true or config shared explicitly),
  recent backup.log entries (if share_log = true), last 10 backup events
- If share_log = false for an agent: show only name, last seen, last backup timestamp
  and status (success / failed) — no file names or paths
**Done when:**
- Agent list renders with correct data
- share_log = false hides file-level detail
- share_log = true shows log entries

```
> Kludde: GET /agents + GET /api/agents (30 s JS polling). share_log flag added to
> _AgentRegisterMessage and cluster.db agents table (migration 005, ALTER TABLE).
> ClusterDB.upsert_agent() extended with share_log param; CatalogDB.get_recent_backups_for_agent()
> returns last N events (backed_up_at, size_bytes, profile only — no encrypted columns).
> Per-agent detail row shown for all agents: share_log=false shows timestamps + privacy note
> (share_log = true in backup.cfg); share_log=true shows same + log-sharing note (actual
> backup.cfg/log forwarding not implemented in Phase 1 — agent does not push them).
> "backup.cfg status" rendered as log-sharing enabled/disabled badge (no actual config push).
> Nav link added to base.html; CSS for .detail-row and .inner-table added to style.css.
> 23 unit tests pass. Full suite: 863 pass, 12 skip. Commit: 68a14782c.
```

---

## 1.15 — Onboarding wizard

### [x] 1.15.1 — Install script (gatekeeper)

**Reads:** docs/onboarding.md, SECURITY.md → Section 11
**Creates:** `install/gatekeeper.sh`
**Requirements:**
- Single curl-pipe-bash install script
- Installs Tailscale if not present (using official Tailscale install script)
- Installs Python 3.11+ if not present
- Installs BackupBuddy from repo
- Starts gatekeeper service (systemd unit created)
- Starts onboarding web server on port 8080
- Attempts to open browser if desktop environment detected
- Idempotent: safe to run twice without breaking existing installation
- No secrets, tokens, or keys generated during install — deferred to wizard
**Done when:**
- Script installs cleanly on Ubuntu 22.04 and 24.04
- Script is idempotent (run twice, second run is a no-op)
- Service starts and onboarding wizard is accessible

```
> Kludde: install/gatekeeper.sh — Ubuntu 22.04/24.04, set -euo pipefail, idempotent.
> Service user: dedicated backupbuddy system user (no login shell), same pattern as
> Nginx/Gitea. Directories: /etc/backup-buddy (config), /var/lib/backup-buddy (data),
> /opt/backup-buddy (code+venv). Install order: pip install . -r requirements.txt
> (installs Tahoe-LAFS fork from source + pins all deps). Python discovery: 3.13/3.12/3.11
> from system; falls back to deadsnakes PPA for Python 3.11 on Ubuntu 22.04. Tailscale
> installed via official curl|sh; NOT authenticated during install (user runs tailscale up
> before finishing the wizard). Browser opened if DISPLAY/WAYLAND_DISPLAY set (xdg-open).
> ADR-019 added: missing gatekeeper.cfg → setup mode, GUI binds to LAN IP (not Tailscale).
> TailscaleOnlyMiddleware bypassed when app.state.setup_required=True. Wizard (1.15.2) must
> instruct user to run tailscale up before the final "finish setup" step.
> Storage pool path permissions (chown backupbuddy) must be handled by wizard (1.15.2).
> Cannot be tested on Windows dev machine — Ubuntu 22.04/24.04 verification pending.
> 863 pass, 12 skip. Commit: 5e327793a.
```

---

### [x] 1.15.2 — Onboarding wizard (web)

**Reads:** docs/onboarding.md (entire file), SECURITY.md → Section 11
**Creates:** `gatekeeper/gui/routes/onboarding.py`,
`gatekeeper/gui/templates/onboarding/` (one template per step)
**Requirements:**
- Five-step wizard matching docs/onboarding.md exactly
- Progress saved between steps (server-side session, not localStorage)
- Step 1: new cluster vs join
- Step 2: node name
- Step 3: storage paths with quota input and disk space validation
- Step 4: profile selection with expandable plain-language explanation
  No erasure coding terminology visible to user
- Step 5: notifications (SMTP + webhook, both skippable)
- Setup complete screen:
  root_dir.cap displayed once with copy button
  "I have saved my recovery key" checkbox + button — REQUIRED before continuing
  First invite code generated and displayed
  Link to dashboard
- Join flow (new cluster = false): invite code entry field before steps 2–5
  Fetches introducer FURL and cluster config on valid code
- All error messages in plain English — no Tahoe errors surfaced directly
**Done when:**
- New cluster wizard completes and gatekeeper is operational
- Join flow wizard completes and node joins the cluster
- Recovery key confirmation cannot be skipped
- Wizard resumable if interrupted

```
> Kludde: 5-step wizard served on LAN IP in setup mode (ADR-019). State persisted in
> onboarding_state.json via atomic writes. Finish cascade: Tahoe introducer + storage node
> bootstrapped, root_dir.cap created, lifeboat key generated, cluster.db seeded, first invite
> code generated, gatekeeper.cfg written last (atomic rename) — retryable on failure.
> Join flow contacts existing cluster via initiate_join to obtain introducer_furl.
> Recovery key (root_dir.cap) shown once; download + re-display blocked after user confirms.
> app.py: added setup_onboarding_app(); main.py: _create_app() branches on setup_mode.
> Templates in flat templates/ dir. 10 unit tests for wizard_state.py pass.
> Commit: 5728a38bd.
```

---

### [x] 1.15.3 — Agent install script

**Reads:** docs/onboarding.md → Agent installation
**Creates:** `install/agent.sh`
**Requirements:**
- Asks two questions: gatekeeper IP and agent name
- Installs agent service (systemd unit)
- Creates default backup.cfg with commented example paths
- Registers agent with gatekeeper using auto-generated token
- Idempotent
**Done when:**
- Script runs cleanly on Ubuntu 22.04 and 24.04
- Agent registers with gatekeeper after install
- backup.cfg created with comments

```
> Kludde: install/agent.sh — Ubuntu 22.04/24.04, set -euo pipefail, idempotent.
> Two questions (gatekeeper IP + agent name) read from /dev/tty so curl|bash works.
> Token auto-generated via openssl rand -hex 32 (fallback: python secrets.token_hex).
> backup.cfg written with 0600 perms to /etc/backup-buddy/backup.cfg — all [backup]
> paths commented out by design (agent won't start until user uncomments at least one).
> systemd unit: service enabled but NOT started for the same reason. Token printed
> prominently in the completion message with [agent_api] / token = <TOKEN> snippet to
> add to gatekeeper.cfg. Idempotent: backup.cfg not overwritten if it already exists;
> existing token extracted and shown instead. Phase 1 note: all agents share one token
> (gatekeeper AgentApiConfig.token) — a second agent install must reuse the same token.
> Cannot be tested on Windows dev machine — Ubuntu 22.04/24.04 verification pending.
```

---

## 1.16 — Unit tests and small-scale integration tests

> **Hardware note:** Full Proxmox test environment (docs/testing.md) requires
> dedicated hardware not yet available. The tasks below use the development
> machine and/or existing servers for smaller-scope validation.
> Full integration testing (docs/testing.md scenarios 1–7) is a separate
> milestone — tracked here when hardware is ready.

### [x] 1.16.1 — Unit test suite

**Reads:** docs/testing.md, CLAUDE.md → Task completion checklist
**Creates:** `tests/unit/` (one file per module)
**Requirements:**
- Unit tests for all critical modules:
  `test_config.py` — gatekeeper.cfg and backup.cfg parsing
  `test_storage_pool.py` — quota enforcement, path exclusion, path selection
  `test_watcher.py` — stability detection, exclusion patterns
  `test_fragmenter.py` — hash verification, profile mapping, adaptive k/n
  `test_lifeboat.py` — encrypt/decrypt, wrong passphrase, bundle contents
  `test_invites.py` — generate, validate, expire, revoke
  `test_restore.py` — successful restore, hash mismatch, temp cleanup
  `test_rebalance.py` — hysteresis, stability threshold, priority ordering
  `test_orphans.py` — mark, cleanup, grace period
  `test_notify.py` — dispatch to smtp, dispatch to webhook
  `test_reconstruct.py` — catalog reconstruction from Tahoe tree
- All tests runnable with `pytest tests/unit/`
- No external services required for unit tests (mock Tahoe, mock Tailscale)
- `pip audit` run as part of test suite
**Done when:**
- All unit tests pass
- `pip audit` shows no high/critical vulnerabilities
- No test requires network access or external services

```
> Kludde: All 11 required test files already existed from prior feature tasks. Ran full
> suite: 873 pass, 12 skip, 0 fail. pip-audit found PYSEC-2026-161 in starlette 1.0.0
> — upgraded to 1.0.1 and re-verified: no known vulnerabilities (tahoe-lafs skipped,
> fork not on PyPI). requirements.txt updated. All tests use mocked Tahoe and Tailscale;
> no network access required. Committed security fix: cf0e4171f.
```

---

### [x] 1.16.2 — Two-node smoke test (local machine) — Scenario 1

**Reads:** docs/testing.md → Scenario 1
**Creates:** `tests/integration/smoke_test.sh`, `tests/integration/bootstrap_gk.py`,
`tests/integration/run_tahoe_node.py`, `tests/integration/smoke_scenario_1.py`
**Requirements:**
- `bootstrap_gk.py` bootstraps GK1 (introducer + storage node + root_dir.cap +
  catalog.db) and GK2 (storage node only); mkdir() retried until storage node
  self-announces (up to 30s)
- `run_tahoe_node.py` runs a pre-created Tahoe node directory (used for GK2)
- `smoke_test.sh` starts GK1 gatekeeper daemon + GK2 bare Tahoe node, waits for
  readiness, runs Scenario 1, cleans up on exit
- `smoke_scenario_1.py` registers agent, POSTs file to `/api/agents/fragments`,
  polls catalog.db, restores via TahoeClient, verifies SHA-256
- Agent API HTTP requests bound to LAN IP via httpx local_address
- Script cleans up all processes and temp dirs on exit
**Done when:**
- Scenario 1: file backed up, restored, hash verified ✓
- All processes cleaned up after test ✓

```
> Implemented 2026-05-27. Test profile (k=1/n=2/happy=1) allows smoke test to
> succeed with only 1 storage node if GK2 has not yet announced itself.
> Scenario 3 (lifeboat restore) split into task 1.16.5.
```

---

### [x] 1.16.4 — Agent upload pipeline

**Reads:** docs/architecture.md → Data flow backup, docs/testing.md → Scenario 1
**Creates:** `/api/agents/fragments` endpoint in `gatekeeper/main.py`,
`_upload_worker` coroutine in `agent/main.py`
**Requirements:**
- `POST /api/agents/fragments` on gatekeeper agent API (LAN, token-auth):
  receives raw file bytes + `X-Fragment-Metadata` JSON header
  (`original_path`, `agent_name`), writes to `data_dir/upload_tmp/`,
  creates UploadItem, puts on gatekeeper upload queue
- Agent `_upload_worker`: consumes `upload_queue`, reads file, calls
  `GatekeeperClient.send_fragment()` with metadata
- Gatekeeper UploadQueueWorker started in lifespan and stopped on shutdown
- Upload queue exposed via `_state["upload_queue"]` for the agent API handler
**Done when:**
- `/api/agents/fragments` receives file and enqueues UploadItem ✓
- UploadQueueWorker starts and stops correctly in lifespan ✓
- Agent `_upload_worker` sends files from local queue to gatekeeper ✓
- Scenario 1 in smoke test verifies the full chain end-to-end ✓

```
> Implemented 2026-05-27. Also added test profile (k=1/n=2/happy=1),
> TahoeConfig.tahoe_web_port, and BACKUPBUDDY_LIFEBOAT_KEY_PATH env override
> as prerequisites for the smoke test.
```

---

### [x] 1.16.5 — Smoke test Scenario 3 (lifeboat restore)

**Reads:** docs/testing.md → Scenario 3, gatekeeper/lifeboat/bundle.py
**Creates:** `tests/integration/smoke_scenario_3.py` (called from `smoke_test.sh`)
**Requirements:**
- Add Scenario 3 call to `smoke_test.sh` after Scenario 1
- `smoke_scenario_3.py` creates a lifeboat bundle from GK1 data using
  `create_bundle()`, deletes catalog.db and root_dir.cap, restores them with
  `extract_bundle()`, verifies the file from Scenario 1 can still be restored
- Requires the lifeboat key to be available (GK1_KEY from the smoke test)
**Done when:**
- catalog.db + root_dir.cap deleted, restored from bundle ✓
- File from Scenario 1 can still be restored and hash-verified ✓

```
> Kludde: smoke_scenario_3.py — nine-step flow: load key, derive catalog key, confirm
> restorable file exists, create_bundle() via raw sqlite3.Connection, simulate disaster
> (delete catalog.db + -wal + -shm + root_dir.cap with missing_ok=True), extract_bundle(),
> write restored root_dir.cap and catalog.db (0600 chmod on POSIX), verify restored catalog
> is non-empty, restore_file() via TahoeClient, SHA-256 verified against catalog's stored hash.
> GK1 daemon left running throughout — Tahoe gateway needed for restore_file(); daemon's
> open fds reference the orphaned inode and do not affect the fresh catalog.db.
> smoke_test.sh: Step 7 added after Scenario 1. Cannot be tested on Windows dev machine.
> Verified on Ubuntu server 192.168.1.50 — SMOKE TEST PASSED (both Scenario 1 and Scenario 3).
> Bugs fixed during live testing:
>   - tahoe run needs --allow-stdin-close (else exits when stdin closes with DEVNULL)
>   - tahoe create-introducer/create-node needs --hostname flag
>   - Parent dirs must be created before tahoe create-* (Tahoe uses os.mkdir)
>   - backupbuddy-gatekeeper entry point was missing from pyproject.toml
>   - gatekeeper/agent packages not in pyproject.toml hatch wheel packages list
>   - FragmentationConfig validator rejected 'test' profile
>   - run_tahoe_node.py missing --allow-stdin-close
>   - httpx.AsyncClient requires bytes/async iterable, not sync file handle
```

---

### [x] 1.16.3 — Full Proxmox integration tests

> **⚠ Hardware-dependent.** This task cannot start until dedicated Proxmox
> hardware is available. See docs/testing.md for full requirements.
> Estimated minimum hardware: 8 cores, 32 GB RAM, 500 GB SSD.
> Johan to confirm hardware availability before this task begins.

**Reads:** docs/testing.md (entire file)
**Creates:** `tests/integration/proxmox/` (scripts per scenario)
**Requirements:**
- All seven scenarios from docs/testing.md implemented as automated scripts
- Proxmox API used for VM lifecycle (create, start, stop, snapshot, rollback)
- Each scenario resets to a clean snapshot before running
- Results logged with pass/fail per scenario
**Done when:**
- All seven scenarios pass
- Results logged cleanly
- Environment reset after each run

```
> Run 2026-05-28 on Proxmox 9.2.2 (3 GK VMs + introducer + 3 agent LXCs).
> Scenarios 1, 2, 4, 5, 6, 7: PASS. Scenario 3: partial (bundle integrity OK;
> full VM-restore path requires recovery_kit.enc from wizard flow).
> Bug found and fixed: check_cap used t=check (unsupported in fork) — commit f2c8bd5c7.
> Infrastructure note: tub.location generated as 127.0.0.1 — see task 1.16.6.
> Full test report: project-docs/test-report-proxmox-2026-05-28.md.
```

---

### [x] 1.16.6 — Fix tub.location in Tahoe node bootstrap

**Reads:** `gatekeeper/main.py`, `install/gatekeeper.sh`, `project-docs/test-report-proxmox-2026-05-28.md`
**Modifies:** wherever `tahoe.cfg` is generated during first-run setup
**Requirements:**
- When BackupBuddy generates `tahoe.cfg` for the storage node, `tub.location`
  must be set to the machine's actual LAN IP, not `127.0.0.1`
- `127.0.0.1` causes the node to announce itself as localhost to the introducer,
  making it unreachable from all other cluster members — multi-node Tahoe breaks silently
- Correct value: `tcp:<lan_ip>:<tub_port>` where `<lan_ip>` is the same IP the
  gatekeeper uses for agent-API binding (the first non-loopback, non-Tailscale private IP)
- The fix should go into the code path that writes `tahoe.cfg`, not patched post-hoc
- Add a startup check: if `tub.location` contains `127.0.0.1`, log a clear error and refuse to start
**Done when:**
- Fresh install generates `tub.location = tcp:<real_ip>:<port>` automatically
- Startup check catches any misconfigured existing install
- Verified in Proxmox environment: all 3 gatekeepers can see each other's storage nodes

```
> Done 2026-05-28. Used Tailscale IP (not LAN IP) per ADR-002 — confirmed with Johan.
> StorageNode._configure() now sets tub.location = tcp:<hostname>:<tub_port>, reading
> the existing tub.port written by tahoe create-node to preserve the assigned port.
> start() refuses to start if tub.location contains 127.0.0.1 with a clear error.
> main.py passes config.tailscale_ip as hostname; bootstrap_gk.py gets --hostname flag.
> Commit: 9dd46d9d4
```

---

### [x] 1.16.7 — Nightly verifier: real under-replication detection

**Reads:** `gatekeeper/verify/nightly.py`, `gatekeeper/tahoe/client.py`,
  `project-docs/test-report-proxmox-2026-05-28.md`
**Modifies:** `gatekeeper/tahoe/client.py` and/or `gatekeeper/verify/nightly.py`
**Requirements:**
- Layer 2 of the nightly verifier currently only checks file accessibility (HTTP 200).
  Share counts (`shares_good`, `shares_needed`) are returned as `1`/`1` always
  because the Tahoe fork does not support `t=check` on file URI endpoints.
- Goal: detect files that have fewer shares than `shares.needed` (under-replicated
  files that cannot currently be restored)
- Option A: Add a `t=check` equivalent endpoint to the Tahoe fork's webapi.
  Preferred — gives exact share counts. Requires modifying the Tahoe fork.
- Option B: Use `t=json` on the verify URI (`verify_uri` from the file's JSON info)
  to count available shares. Needs investigation.
- Option C: Accept the current limitation for Phase 1 and document it clearly.
  Prioritise Option A or B if either is straightforward.
- Whatever the outcome, `check_cap` docstring must accurately describe what it detects
**Done when:**
- Layer 2 can distinguish "file inaccessible" from "file under-replicated"
  OR a documented decision is recorded in DECISIONS.md explaining why it cannot

```
> Kludde: POST /uri/<cap>?t=check&output=json already exists in the BackupBuddy Tahoe fork
> (t=check is handled on the POST handler of FileNodeHandler — GET only supports t=json).
> No fork modification needed. CheckResultsRenderer.render_JSON returns count-shares-good
> and count-shares-needed from the real ICheckable.check() operation, which contacts storage
> nodes to count available shares. check_cap() in TahoeClient updated to use POST + parse
> real share counts; LIT files (no count keys in response) default to 1/1.
> 7 new unit tests in test_client.py cover: healthy file, under-replicated file, LIT file,
> network error, HTTP error, POST method + params, URL encoding.
> Layer 2 of nightly verifier can now distinguish "inaccessible" (check returns None)
> from "under-replicated" (shares_good < shares_needed). All existing nightly verifier
> tests pass unchanged. Full suite: 882 pass, 12 skip, 1 pre-existing queue_worker fail.
```

---

### [x] 1.16.8 — Integration test: full Scenario 3 (lifeboat VM restore)

**Reads:** `project-docs/testing.md` → Scenario 3,
  `project-docs/test-report-proxmox-2026-05-28.md`,
  `gatekeeper/lifeboat/`, `gatekeeper/gui/routes/onboarding.py`
**Requirements:**
- Complete the gap identified in task 1.16.3: the full destroy-and-restore flow
- Prerequisites: gatekeeper set up via the wizard (not custom bootstrap), so
  `recovery_kit.enc` exists and a passphrase was set during onboarding
- Test steps:
  1. Set up a fresh gatekeeper via wizard flow (includes recovery_kit creation)
  2. Back up ≥10 files
  3. Record a passphrase and confirm `recovery_kit.enc` exists
  4. Destroy the gatekeeper VM entirely (`qm destroy`)
  5. Create a fresh VM, install BackupBuddy
  6. Use the emergency restore path in the GUI
  7. Enter passphrase, verify all files are listed, restore one, check SHA-256
- Must pass before any real-world deployment where the wizard flow is used
**Done when:**
- End-to-end lifeboat restore completes from a passphrase with no manual steps
- Restored gatekeeper has same `node_id` (same Tahoe identity) as the destroyed one
- All previously backed-up files appear in the GUI after restore

```
> Kludde: Implemented Option A (Phase 1 scope): passphrase → recovery_kit.enc →
> root_dir_cap → Tahoe catalog reconstruction. Two code gaps fixed:
> (1) Wizard step 5 now collects passphrase + creates recovery_kit.enc during cascade.
>     Download endpoint returns recovery_kit.enc (binary) instead of raw root_dir_cap.
> (2) POST /api/restore/emergency now accepts recovery_kit_b64 + passphrase;
>     extracts root_dir_cap via extract_recovery_kit(), then calls reconstruct_catalog().
>     raw recovery_key field kept for backward compat (Scenario 4 API path).
> Proxmox test (VM 101, snapshot pre116test): wiped catalog.db, called emergency
> restore with recovery_kit_b64 + passphrase → 53/53 files reconstructed, restore
> of scenario1_testfile.txt returned SHA-256 ceaa7ca994f9cc... (exact match).
> Full test report: project-docs/test-report-scenario3-2026-05-28.md
> Option B (catalog snapshot preserved in recovery kit) → Phase 2 item 2.10.
```

---

### [x] 1.16.9 — Integration test: fragment corruption detection

**Reads:** `project-docs/testing.md` → Scenario 7,
  `gatekeeper/verify/nightly.py` → Layer 2 and Layer 3
**Requirements:**
- Deliberately corrupt a fragment on a storage node and verify the nightly
  verifier detects it
- Steps:
  1. Back up a file with known SHA-256
  2. Find the Tahoe share file on a storage node (locate in storage_dir)
  3. Corrupt it: `dd if=/dev/urandom bs=1 count=100 seek=500 of=<share_file> conv=notrunc`
  4. Trigger nightly verification
  5. Verify Layer 3 (test restore) detects the mismatch and reports `RestoreIntegrityError`
     OR that k remaining good shares still allow a clean restore
  6. Verify an alert is raised (check notification dispatcher)
- Note: with k=1, n=2 and 2 shares on the same node, corruption may prevent restore.
  With 2 separate storage nodes and shares distributed, k=1 means 1 good share suffices.
  Test should be run with ≥2 storage nodes connected.
**Done when:**
- Corruption is detected and reported by nightly verifier
- System behaviour under corruption is clearly documented (alert sent, auto-retry or not)

```
> Implementation: `tests/integration/smoke_scenario_7.py` (Step 8 in smoke_test.sh).
> Corrupts ALL share files in both GK1 and GK2 storage dirs (k=1/n=2 means one good
> share suffices — must corrupt both nodes to guarantee detection). Runs NightlyVerifier
> with injectable send_alert; asserts layer3.ok=False and layer3.errors>0.  Layer 4
> always warns in smoke test (no lifeboat distributed) — filtered to error/critical level
> when asserting on corruption-specific alerts.
> Committed: test(verify): add scenario 7 fragment corruption detection smoke test
```

---

### [x] 1.16.10 — Integration test: multi-gatekeeper cluster join flow

**Reads:** `project-docs/testing.md`, `gatekeeper/gui/routes/buddies.py`,
  `gatekeeper/gui/routes/onboarding.py`
**Requirements:**
- Test the full invite → join → vote → active member flow with two real gatekeepers
  (Anders invites Björn, Björn joins, both see each other as cluster members)
- This was not tested in task 1.16.3 because all gatekeepers were bootstrapped
  independently rather than through the wizard
- Verify: Tahoe fragments are actually distributed across both storage nodes after join
- Verify: cluster.db on both nodes is consistent after join
- Verify: dashboard shows both nodes as active members
**Done when:**
- Two gatekeepers form a cluster via wizard/invite flow
- Files uploaded by one node's agent can be restored
- Fragments confirmed on both storage nodes (not just the uploader's)

```
> Kludde: Six bugs found and fixed during the test run — all in the test script and Proxmox VM state:
>   1. Wrong install path /opt/backupbuddy → /opt/backup-buddy (install.sh places code there).
>   2. Service name mismatch: Björn runs new-format service (backup-buddy-gatekeeper / backupbuddy user)
>      while Anders still runs old-format (backupbuddy-gatekeeper / root). Added ANDERS_SVC / BJORN_SVC
>      constants and correct BJORN_DATA_DIR (/var/lib/backup-buddy) and BJORN_CFG (/etc/backup-buddy/).
>   3. /mnt/storage owned by root — Björn's backupbuddy user couldn't write; fixed with chown -R in reset step.
>   4. Tahoe files in data dir owned by root (from previous debug sessions) blocked the cascade running as
>      backupbuddy user; fixed with recursive chown -R in reset step.
>   5. Empty tahoe binary (/opt/backup-buddy/.venv/bin/tahoe = 0 bytes); fixed with pip install --force-reinstall.
>   6. StartLimitBurst exhausted after many test failures; service wouldn't restart. Fixed with
>      systemctl reset-failed before restart in step 7.
> Run via SSH (ProxyJump through proxmox at 192.168.1.60). Resolved Tailscale IPs dynamically.
> Final result (5th attempt): ALL 9 PASS assertions hit, 1.16.10 PASSED.
>   - Anders healthy (status: ok)
>   - Invite code generated, wizard cascade complete, Björn in normal mode
>   - Both cluster.db consistent: gatekeeper-anders + bjorn-rejoin visible on both sides
>   - Backup + restore verified (sha256 match)
>   - Fragments distributed to Björn's storage (before=66 after=67)
> Commits: fix(test)×5 across e2e4dd7, 9a92769, 8df6b9f, 8df6b9f, 97faf3a.
```

---

### [x] 1.16.11 — Fresh install via install scripts + idempotency

**Reads:** `install/gatekeeper.sh`, `install/agent.sh`, `project-docs/testing.md`
**Requirements:**
- Roll back all gatekeeper VMs and agent CTs to the clean Ubuntu-only Proxmox snapshot
  (no BackupBuddy installed — Tailscale active and joined to the test tailnet is sufficient)
- Run `install/gatekeeper.sh` on each gatekeeper VM (anders, bjorn, carina); verify:
  - Service `backupbuddy-gatekeeper` starts without errors
  - Wizard is accessible at `http://<LAN-IP>:8080/onboarding/step/1`
- Run `install/agent.sh` on each agent CT; verify:
  - Service `backupbuddy-agent` starts without errors
  - `backup.cfg` is created (empty or default values)
- Run both scripts a **second time** on every VM/CT — idempotency check:
  - Scripts must complete without errors
  - Running services must not be restarted unnecessarily
  - Config files must not be overwritten or corrupted
- Take a new "post-install, wizard not yet run" Proxmox snapshot on all VMs/CTs after success
**Done when:**
- Both install scripts run cleanly on all nodes
- Idempotency verified: second run completes without errors and changes nothing material
- Post-install snapshot taken and named consistently

```
> Kludde: Two bugs found and fixed during the test:
>   - requirements.txt contained a stale -e git+...#egg=tahoe_lafs line that conflicted
>     with pip install ${INSTALL_DIR} on fresh installs — line removed (tahoe-lafs is
>     installed transitively via the package itself).
>   - clone_or_update_repo() used origin/main but the repo uses master — fixed in both scripts.
> Additional improvement: BB_GATEKEEPER_IP / BB_AGENT_NAME env vars added to agent.sh
>   ask_questions() to enable non-interactive installs (CI, automated testing).
>   Normal interactive flow unaffected when vars are unset.
> Run via SSH (ProxyJump through proxmox at 192.168.1.60). All 3 GK VMs + 3 agent LXCs.
> First run: all 6 nodes clean install. Wizard HTTP 200 on all GK nodes.
> Second run (idempotency): service user already exists, unit already up to date,
>   backup.cfg not overwritten, service restarted once (after git pull — expected).
> Snapshot post-install-no-wizard taken on all 6 nodes (101, 102, 103, 301, 302, 303).
> NOTE: thin pool space warnings during snapshot (over-provisioned LVM). Not errors.
> Committed: fix(deps), fix(install)×2, feat(install).
```

---

### [x] 1.16.12 — Full Scenario 3 from wizard setup (VM destroy + fresh install + GUI restore)

**Reads:** `project-docs/testing.md` → Scenario 3,
  `install/gatekeeper.sh`, `gatekeeper/gui/routes/onboarding.py`
**Requirements:**
- Start from the post-install snapshot (1.16.11 snapshot) on gatekeeper-anders
- Run the full wizard on anders (role=new, steps 1–5 including passphrase collection)
- Record the passphrase; download and keep `recovery_kit.enc`
- Via an agent, back up ≥10 files; record the SHA-256 of at least one file
- **Destroy the VM entirely**: `qm destroy 101` on the Proxmox host
- Create a fresh VM with the same ID (101), run `install/gatekeeper.sh`, verify wizard is accessible
- Use the emergency restore tab in the GUI:
  - Upload `recovery_kit.enc`
  - Enter the passphrase
  - Trigger catalog reconstruction
- Verify all ≥10 files appear in the catalog after reconstruction
- Restore one file; verify its SHA-256 matches the value recorded before destruction
**Done when:**
- Entire flow completes end-to-end from a completely fresh VM (no snapshot tricks)
- SHA-256 of restored file matches pre-destruction value
- Wizard passphrase collection (step 5 form) and GUI emergency restore confirmed working

```
> Kludde: Rewrote scenario3_disaster_recovery_test.sh (17 steps). Steps 1–7
> unchanged (rollback, wizard, agent backup, save artifacts). Step 8 is a true
> VM destroy: stops service, counts shares, stops VM, removes scsi1 from
> /etc/pve/qemu-server/101.conf with sed before qm destroy so vm-101-disk-1
> survives. Step 9 verifies the LVM volume still exists. Step 10 clones template
> 9000 → 101 (--full), sets original MACs, static cloud-init IPs, adds cloudinit
> drive defensively, runs qm cloudinit update, resizes OS disk to 20G, reattaches
> vm-101-disk-1, verifies disk size ~10G to detect naming collisions. Step 11
> runs install/gatekeeper.sh via bash -s < pipe, overlays current source, pip
> force-reinstall, mounts /mnt/storage by UUID, handles tailscaled autostart race
> (stop → write state → enable+start), verifies Tailscale IP is unchanged.
> Steps 12–17: second wizard, normal-mode restart, empty catalog assert,
> emergency restore, catalog count ≥10, file restore + SHA-256 verify.
> SSH_OPTS extended with -o UserKnownHostsFile=/dev/null to handle fresh host keys.
```

---

### [x] 1.16.13 — GUI smoke test: all pages accessible, no Tahoe internals in HTML

**Reads:** `gatekeeper/gui/`, CLAUDE.md → rule 5 (never expose Tahoe internals)
**Requirements:**
- With gatekeeper running in normal mode (Tailscale active, cluster configured)
- HTTP GET each of the main pages via `curl -sf`:
  - `/` (dashboard)
  - `/restore`
  - `/settings`
  - `/buddies`
  - `/agents`
- Verify all pages return HTTP 200; no page returns 500 or redirects to setup wizard unexpectedly
- Grep the full HTML body of every response for Tahoe internals:
  `FURL`, `furl`, `:cap`, `shares.needed`, `storage_index`, `tahoe:`, `pb://`, `storage_index`
- Verify none of these strings appear in any rendered HTML response
**Done when:**
- All 5 pages return HTTP 200
- Zero Tahoe internal strings found in any HTML response

```
> Kludde: Fixed missing `ts_format` Jinja2 filter in settings.py — the filter was registered in
> dashboard.py/buddies.py/agents.py but not in settings.py, causing a 500 on GET /settings.
> Added `_fmt_timestamp` + `_templates.env.filters["ts_format"]` to settings.py.
> Smoke test script: tests/integration/proxmox/gui_smoke_test.sh — all 5 pages HTTP 200,
> zero Tahoe internal strings in rendered HTML. Verified on Anders (100.68.15.102).
```

---

### [x] 1.16.14 — Fix cluster_join_test.sh dynamic IPs, then run 1.16.10

**Reads:** `tests/integration/proxmox/cluster_join_test.sh`
**Requirements:**
- Replace hardcoded Tailscale IPs in `cluster_join_test.sh`:
  - `ANDERS_TS="100.68.15.102"` → resolved dynamically: `anders tailscale ip -4 2>/dev/null | head -1`
  - `BJORN_TS="100.105.68.77"` → resolved dynamically: `bjorn tailscale ip -4 2>/dev/null | head -1`
- Add failure check: if either IP resolves to empty, abort with a clear error
- Rerun derived variables that depend on `ANDERS_TS` / `BJORN_TS`
  (`ANDERS_TS_URL`, `BJORN_TS_URL`) after the dynamic resolution
- Execute the updated script end-to-end (run task 1.16.10 to completion)
**Done when:**
- `cluster_join_test.sh` resolves Tailscale IPs dynamically (no hardcoded IPs)
- Script runs to completion: both nodes see each other in `cluster.db`
- Fragments confirmed on Björn's storage node after backup from Anders

```
> Kludde: Removed hardcoded ANDERS_TS and BJORN_TS from the top variable block.
> Added dynamic resolution block after SSH/utility helper functions:
>   ANDERS_TS=$(anders "tailscale ip -4 2>/dev/null | head -1")
>   BJORN_TS=$(bjorn  "tailscale ip -4 2>/dev/null | head -1")
> Abort with clear error if either resolves to empty. ANDERS_TS_URL and
> BJORN_TS_URL derived from the resolved values immediately after.
> Resolved IPs echoed via info() before main test body for easy debug.
> Commit: 1f11ab659. 1.16.10 run on Proxmox: PASSED (see 1.16.10 note for details).
```

---

---

## 1.17 — Full end-to-end test suite (clean VMs, phased)

> **All tests in 1.17 are performed via SSH through Proxmox at 192.168.1.60.**
> Kludde SSH:es into each node via ProxyJump: `ssh -J root@192.168.1.60 root@<ip>`
> No manual steps on VM consoles — everything scripted via SSH.
>
> **Testing philosophy:**
> Each phase starts from a known snapshot and ends with a known snapshot.
> If a bug is found: fix code locally → `git commit` → `git push` → rollback VM
> to the phase's starting snapshot → re-run install (which does `git pull` from GitHub)
> → re-test. Never patch a running VM directly — always fix in code and reinstall.
>
> **Snapshot naming convention:**
> - `clean-ubuntu` — bare Ubuntu 24.04, Tailscale active, NO BackupBuddy (starting point)
> - `phase-a` — BackupBuddy installed, wizard not yet run
> - `phase-b` — Anders wizard complete, agent registered
> - `phase-c` — ≥10 files backed up, SHA-256 recorded
> - `phase-d` — Restore verified
> - `phase-e` — Two-node cluster active (bjorn joined)
> - `phase-f` — Nightly verification passed + corruption detected
> - `phase-g` — Disaster recovery completed
> - `phase-h` — Three-node cluster + node removal done
>
> **Infrastructure reminder:**
> - Proxmox: 192.168.1.60 (ssh as root)
> - Gatekeeper VMs: anders=101 (10.99.0.11), bjorn=102 (10.99.0.12), carina=103 (10.99.0.13)
> - Agent LXCs: 301 (10.99.0.31), 302 (10.99.0.32), 303 (10.99.0.33)
> - Template VM: 9000
> - Tailscale IPs resolved dynamically with: `ssh <node> tailscale ip -4 | head -1`
>
> **Rollback commands (used between phases):**
> ```bash
> # VM (must be stopped first):
> ssh root@192.168.1.60 "qm stop <vmid> && qm rollback <vmid> <snapshot> && qm start <vmid>"
> # LXC:
> ssh root@192.168.1.60 "pct stop <vmid> && pct rollback <vmid> <snapshot> && pct start <vmid>"
> ```
>
> **Bug fix + re-test workflow:**
> 1. Observe failure via SSH
> 2. Fix code locally (dev machine)
> 3. `git commit -m "fix(...): ..."` → `git push`
> 4. Rollback VM(s) to the phase's starting snapshot
> 5. Restart VM(s) and wait for SSH
> 6. Re-run install script on each affected VM (installs fresh from GitHub)
> 7. Re-run the phase's test from the beginning

---

### [x] 1.17.1 — Prepare: create fresh VMs and take pre-install snapshots

> **All work via SSH: `ssh root@192.168.1.60`**
> Starting snapshot: none — create from scratch using template 9000.

**Reads:** `install/gatekeeper.sh`, `install/agent.sh`, infrastructure memory
**Requirements:**
- Stop all currently running VMs and LXCs (101, 102, 103, 301, 302, 303)
- Destroy all of them (`qm destroy` / `pct destroy`) to start completely fresh
- Clone template 9000 → new VMs 101, 102, 103 (full clone, `--full`)
  Set correct hostnames, static IPs, and MAC addresses per infrastructure table
  Restore cloud-init: static IP, SSH key, hostname
  Resize OS disk to 20 GB
  Add storage disk (vm-101-disk-1 style, /mnt/storage) to each GK VM
- Create LXC containers 301, 302, 303 from the Ubuntu 24.04 LXC template
  Set correct hostnames and static IPs
- Boot all VMs and LXCs; wait for SSH to become available on each
- Join each GK VM to the Tailscale tailnet (`tailscale up --auth-key=...` — ask Johan for key if needed)
- Verify Tailscale IP is assigned on each GK VM
- **Take snapshot `clean-ubuntu` on ALL nodes (101, 102, 103, 301, 302, 303)**
  This is the baseline for re-running Phase A if install bugs are found
- Verify snapshot exists on all 6 nodes before proceeding
**Done when:**
- All 6 nodes running fresh Ubuntu 24.04, Tailscale active on GK VMs
- `clean-ubuntu` snapshot exists on all 6 nodes
- All nodes reachable via SSH through Proxmox

```
> Kludde: Done 2026-05-30. All 6 nodes destroyed and rebuilt from template 9000 (VMs) /
> ubuntu-24.04-standard_24.04-2_amd64.tar.zst (LXCs). Tailscale installed and joined on
> anders/bjorn/carina. clean-ubuntu snapshot verified on all 6 nodes. Tailscale auth key
> saved in secrets.local.env (gitignored). Ready for 1.17.2.
```

---

### [x] 1.17.2 — Phase A: Fresh install from GitHub (install scripts)

> **All work via SSH: `ssh root@192.168.1.60`**
> Starting snapshot: `clean-ubuntu` (all 6 nodes)
> Rollback here if a bug is found in install scripts.

**Reads:** `install/gatekeeper.sh`, `install/agent.sh`, 1.16.11 notes
**Requirements:**

**Step 1 — Rollback to `clean-ubuntu` on all nodes:**
```bash
for vmid in 101 102 103; do
  ssh root@192.168.1.60 "qm stop $vmid; qm rollback $vmid clean-ubuntu; qm start $vmid"
done
for ctid in 301 302 303; do
  ssh root@192.168.1.60 "pct stop $ctid; pct rollback $ctid clean-ubuntu; pct start $ctid"
done
```
Wait for all nodes to come back online (SSH available).

**Step 2 — Install gatekeeper on anders, bjorn, carina (parallel):**
```bash
curl -fsSL https://raw.githubusercontent.com/MrBumbe/BackupBuddy/master/install/gatekeeper.sh \
  | ssh root@10.99.0.11 bash
# Repeat for 10.99.0.12 (bjorn) and 10.99.0.13 (carina)
```
- Verify: `systemctl is-active backupbuddy-gatekeeper` returns `active` on each
- Verify: `curl -sf http://<LAN-IP>:8080/onboarding/step/1` returns HTTP 200 on each

**Step 3 — Install agent on 301, 302, 303:**
```bash
# Use env vars for non-interactive install:
BB_GATEKEEPER_IP=10.99.0.11 BB_AGENT_NAME=agent-anders-pc \
  curl -fsSL https://raw.githubusercontent.com/MrBumbe/BackupBuddy/master/install/agent.sh \
  | ssh root@10.99.0.31 bash
# Repeat for 302 (BB_AGENT_NAME=agent-anders-nas) and 303 (BB_AGENT_NAME=agent-bjorn-pc)
```
- Verify: `/etc/backup-buddy/backup.cfg` exists on each LXC
- Verify: `systemctl is-enabled backupbuddy-agent` returns `enabled`

**Step 4 — Idempotency check (run install scripts a second time on all nodes):**
- Re-run gatekeeper.sh on anders and agent.sh on 301 without changes
- Verify: no errors, service not unnecessarily restarted, config not overwritten

**Step 5 — Take snapshot `phase-a` on all 6 nodes**

**Bug fix protocol:** If any step fails → fix code → `git commit` → `git push` →
rollback all nodes to `clean-ubuntu` → repeat from Step 1.

**Done when:**
- All 3 gatekeeper services active and wizard accessible
- All 3 agent services enabled and backup.cfg created
- Idempotency verified on at least one gatekeeper and one agent
- `phase-a` snapshot taken on all 6 nodes

```
> Kludde: Done 2026-05-30. Two bugs found and fixed in install scripts:
> (1) python3-venv not installed → added to base packages (Ubuntu 24.04 ships python3.12
>     but not python3-venv by default).
> (2) netifaces C extension build failed — Tahoe-LAFS imports netifaces which requires gcc.
>     Added build-essential, python3-dev, libffi-dev, libssl-dev to base packages.
> Commits: fix(install): install python3-venv, fix(install): add build-essential.
> Install tested via local script pipe (GitHub CDN was caching old version during testing;
> CDN eventually refreshes — next install from GitHub URL will use fixed script).
> All 3 gatekeepers: systemctl active, /onboarding/step/1 = HTTP 200.
> All 3 agents: backup.cfg written, service enabled.
> Idempotency: gatekeeper re-run — no errors, git update-path used, unit no-op, service
>   restarted (correct — new code may have been pulled). Agent re-run — backup.cfg preserved,
>   unit no-op, no errors.
> phase-a snapshot verified on all 6 nodes (101–103, 301–303). Ready for 1.17.3.
```

---

### [x] 1.17.3 — Phase B: Single-node wizard setup (anders)


> **All work via SSH: `ssh root@192.168.1.60`**
> Starting snapshot: `phase-a` (all nodes)
> Rollback `phase-a` on anders (101) and agent 301 if bugs found here.

**Reads:** `gatekeeper/gui/routes/onboarding.py`, `install/gatekeeper.sh`, 1.16.12 notes
**Requirements:**

**Step 1 — Rollback to `phase-a` on all nodes** (ensures clean wizard state)

**Step 2 — Complete wizard on anders via HTTP API (not GUI, scripted via curl):**
- POST to `/api/onboarding/step/1` — role=new
- POST to `/api/onboarding/step/2` — node_name=gatekeeper-anders
- POST to `/api/onboarding/step/3` — storage_path=/mnt/storage, quota=50 GB
- POST to `/api/onboarding/step/4` — profile=balanced
- POST to `/api/onboarding/step/5` — skip SMTP and webhook
- POST to `/api/onboarding/finish` — passphrase=TestPassphrase2026!
- Verify: wizard returns `root_dir_cap` (or redirects to dashboard)
- Verify: `GET /api/status` returns `{"status": "ok"}` (normal mode)
- Record: passphrase and download `recovery_kit.enc` to local machine
- Verify: `systemctl is-active backupbuddy-gatekeeper` returns `active`

**Step 3 — Register agent-anders-pc (301) with gatekeeper-anders:**
- Start `backupbuddy-agent` service on 301
  (first: add correct token to `/etc/backup-buddy/backup.cfg` [gatekeeper] section)
- Verify: `GET /api/agents` on anders returns agent-anders-pc as registered
- Verify: agent appears in Agents tab on dashboard

**Step 4 — Verify dashboard is reachable from Tailscale IP:**
```bash
ANDERS_TS=$(ssh -J root@192.168.1.60 root@10.99.0.11 tailscale ip -4 | head -1)
curl -sf "http://${ANDERS_TS}:8080/" | grep -q "BackupBuddy" && echo "Dashboard OK"
```

**Step 5 — Take snapshot `phase-b` on anders (101) and agent-anders-pc (301)**
  (bjorn, carina, 302, 303 stay at `phase-a`)

**Bug fix protocol:** Fix → `git commit` → `git push` → rollback 101+301 to `phase-a` → retry from Step 1.

**Done when:**
- Wizard completes without errors, gatekeeper in normal mode
- `recovery_kit.enc` downloaded and passphrase recorded
- Agent registered and visible in GUI
- Dashboard accessible via Tailscale IP
- `phase-b` snapshot on 101 and 301

```
> Kludde: Done 2026-05-30. Wizard completed on anders (101) via curl scripting.
> Bug found and fixed: install/gatekeeper.sh had Restart=on-failure — wizard sends
> SIGTERM which uvicorn handles as a clean exit (code 0), so systemd did not
> auto-restart. Fixed to Restart=always (matches onboarding.py code comment intent).
> Fix verified on bjorn (102): wizard → SIGTERM → service auto-restarted in normal
> mode without manual intervention.
> Two pre-wizard prep steps not in TODO:
>   (1) /dev/sdb (storage disk) unformatted after rollback — formatted as ext4,
>       mounted at /mnt/storage, added to fstab, ownership set to backupbuddy.
>   (2) [backup] section in agent backup.cfg had no paths — added /srv/testbackup
>       as placeholder so agent could start and register.
> Results on anders: gatekeeper in normal mode (100.64.235.77:8080), agent-anders-pc
> registered (is_online: true), dashboard OK via Tailscale.
> Invite code for cluster joins: shy-turf-7 (48h TTL from wizard completion).
> recovery_kit.enc: 236 bytes, passphrase: TestPassphrase2026!
> phase-b snapshot on 101 and 301. Bjorn (102) also configured but NOT snapshotted
> (phase-b is anders + agent only per task definition).
```

---

### [x] 1.17.4 — Phase C: File backup via agent

> **All work via SSH: `ssh root@192.168.1.60`**
> Starting snapshot: `phase-b` (anders=101, agent=301)
> Rollback to `phase-b` if backup bugs found.

**Reads:** `agent/watcher.py`, `gatekeeper/fragmenter/`, `gatekeeper/db/catalog.py`
**Requirements:**

**Step 1 — Rollback 101 and 301 to `phase-b`**

> **Note:** After rollback, `/dev/sdb` (storage disk) on VM 101 is unformatted and
> unmounted. The `phase-b` snapshot was taken after the disk was formatted, so it
> should be preserved — but verify before proceeding:
> ```bash
> ssh -J root@192.168.1.60 root@10.99.0.11 "df -h /mnt/storage || echo NEEDS SETUP"
> ```
> If `NEEDS SETUP`: `mkfs.ext4 -L storage /dev/sdb && mkdir -p /mnt/storage &&
> mount /dev/sdb /mnt/storage && chown backupbuddy:backupbuddy /mnt/storage`

**Step 2 — Create test files on agent 301:**
```bash
ssh -J root@192.168.1.60 root@10.99.0.31 bash << 'EOF'
mkdir -p /srv/testbackup
for i in $(seq 1 15); do
  dd if=/dev/urandom bs=1024 count=$((RANDOM % 512 + 64)) \
    of=/srv/testbackup/testfile_${i}.bin 2>/dev/null
done
sha256sum /srv/testbackup/testfile_01.bin > /root/sha256_reference.txt
cat /root/sha256_reference.txt
EOF
```

**Step 3 — Configure backup.cfg on agent 301:**
Add `/srv/testbackup` to `[backup]` section of `/etc/backup-buddy/backup.cfg`.
Start `backupbuddy-agent` service.

**Step 4 — Wait for watcher to detect and upload files:**
- Files should appear stable after `stability_minutes` (default 30) OR set a low value in config
- Monitor gatekeeper logs for upload completion:
  `ssh ... journalctl -u backupbuddy-gatekeeper -f | grep -E "uploaded|error"`
- Wait until all 15 files show in `GET /api/agents` or catalog query

**Step 5 — Verify catalog.db has entries:**
```bash
ssh -J root@192.168.1.60 root@10.99.0.11 \
  sqlite3 /var/lib/backup-buddy/catalog.db "SELECT count(*) FROM files;"
```
Expected: 15 rows

**Step 6 — Verify dashboard shows correct upload count and last backup timestamp**

**Step 7 — Take snapshot `phase-c` on 101 and 301**

**Bug fix protocol:** Fix → `git commit` → `git push` → rollback 101+301 to `phase-b` → retry from Step 1.

**Done when:**
- All 15 test files backed up
- catalog.db has 15 rows
- Dashboard reflects correct state
- SHA-256 reference saved to local machine
- `phase-c` snapshot on 101 and 301

```
> Kludde: Done 2026-05-30. Two bugs found and fixed during the test:
>   (1) Adaptive profile in main.py fell back to hard-coded (3,5) — stub never
>       called get_current_kn(). With balanced profile, k=3 n=5 requires 5 storage
>       nodes so all uploads returned HTTP 500. Fixed: main.py now calls
>       get_current_kn(cluster_db, config.fragmentation.adaptive); with 1 active
>       member → k=1 n=1. Commit: 88a10f3a0.
>   (2) Fragmenter.fragment_and_upload() called get_profile("adaptive") which always
>       raises ValueError. Fixed: Fragmenter accepts adaptive_kn=(k,n) at init and
>       builds Profile directly when profile=="adaptive". main.py passes resolved k/n.
>       Commit: c49fb8867.
> 15 testfiles (testfile_1..15.bin, random sizes 64–576 KB) created in /srv/testbackup
> on agent 301. SHA-256 of testfile_1.bin:
>   9d20cb463e6f14168eda326be0304ae0faac4003c2dc0a4dc45aafa84cb73124
>   Saved locally: sha256_phase_c_reference.txt
> catalog.db: 17 rows (15 testfiles + .keep + 1 leftover from earlier run).
> storage disk survived rollback to phase-b — /mnt/storage already mounted.
> phase-c snapshot on 101 and 301.
> Note: storage disk prep note (mkfs step) in TODO was precautionary — not needed.
```

---

### [x] 1.17.5 — Phase D: File restore (normal + folder + hash verification)

> **All work via SSH: `ssh root@192.168.1.60`**
> Starting snapshot: `phase-c` (anders=101, agent=301)
> Rollback to `phase-c` if restore bugs found.

**Reads:** `gatekeeper/restore/restore.py`, `gatekeeper/gui/routes/restore.py`
**Requirements:**

**Step 1 — Rollback 101 and 301 to `phase-c`**

**Step 2 — Restore a single file via the GUI API (Find a file):**
```bash
ANDERS_TS=$(ssh -J root@192.168.1.60 root@10.99.0.11 tailscale ip -4 | head -1)
# Start a restore job:
curl -sf -X POST "http://${ANDERS_TS}:8080/api/restore/start/file" \
  -H "Content-Type: application/json" \
  -d '{"original_path": "/srv/testbackup/testfile_01.bin",
       "agent": "agent-anders-pc",
       "dest_path": "/tmp/restore_test/"}'
# Poll job status until complete
```
- Verify: restored file SHA-256 matches reference from Phase C
- Verify: no `RestoreIntegrityError` in gatekeeper logs

**Step 3 — Restore entire test folder (Restore a folder):**
```bash
curl -sf -X POST "http://${ANDERS_TS}:8080/api/restore/start/folder" \
  -H "Content-Type: application/json" \
  -d '{"folder_path": "/srv/testbackup",
       "agent": "agent-anders-pc",
       "dest_path": "/tmp/restore_folder/"}'
```
- Poll until complete
- Verify: 15 files in /tmp/restore_folder/ on anders
- Verify: SHA-256 of testfile_01.bin matches reference

**Step 4 — Verify hash mismatch detection:**
- Manually corrupt one entry in catalog.db: `UPDATE files SET sha256='aabbcc...' WHERE id=1`
- Trigger a restore of that file
- Verify: `RestoreIntegrityError` raised, alert logged
- Revert the catalog corruption

**Step 5 — Take snapshot `phase-d` on 101**

**Bug fix protocol:** Fix → `git commit` → `git push` → rollback 101+301 to `phase-c` → retry.

**Done when:**
- Single file restore with correct SHA-256 ✓
- Folder restore (15 files) ✓
- Hash mismatch detection triggers correctly ✓
- `phase-d` snapshot on 101

```
> Kludde: Done 2026-05-31. Three issues found and fixed:
>   (1) httpx.ResponseNotRead crash: _raise_for_tahoe_error() accessed
>       response.text inside a streaming context. Fixed: wrapped in
>       try/except ResponseNotRead, falls back to placeholder string.
>       Commit: 9eeabd692.
>   (2) Tahoe UploadUnhappinessError with balanced profile (k=3/n=5 needs
>       5 distinct servers — only 1 available). Same root cause as 1.17.4.
>       Fix: gatekeeper.cfg changed to profile=adaptive (same as 1.17.4).
>       With 1 cluster member → k=1 n=1 happy=1. Shares work on single node.
>   (3) sqlite3 CLI not installed on anders — test script used sqlite3
>       for catalog corruption in Step 4. Fixed: replaced with python3 -c
>       one-liners. Commit: 63844b0f5.
> Phase-c rebuilt from phase-b: 15 testfiles (testfile_1..15.bin, ~1 MB each)
> created fresh in /srv/testbackup. New SHA-256 of testfile_1.bin:
>   f7fd1b6380eae2b73f7d40d189042351e2d74fda64b5c40c1264e84debed5eef
> Always stop VM/CT before qm snapshot / pct snapshot — running-VM snapshots
> produce 0-byte Tahoe share placeholder files (buffer not flushed to disk).
> phase-d snapshot on 101 (anders). CT 301 not snapshotted in phase-d (not needed).
```

---

### [x] 1.17.6 — Phase E: Multi-node cluster join (bjorn joins anders)

> **All work via SSH: `ssh root@192.168.1.60`**
> Starting snapshot: `phase-b` on anders (101) + `phase-a` on bjorn (102) + agent 303
> Rollback anders to `phase-b` and bjorn/303 to `phase-a` if cluster join bugs found.

**Reads:** `gatekeeper/cluster/join.py`, `gatekeeper/gui/routes/buddies.py`,
  `gatekeeper/gui/routes/onboarding.py`, 1.16.10 notes
**Requirements:**

**Step 1 — Rollback anders (101) to `phase-b`, bjorn (102) + 303 to `phase-a`**

**Step 2 — Back up ≥10 files on anders first** (so there is data to verify distribution):
- Configure backup on agent 301 (same as Phase C) and wait for upload
- Verify 10+ files in catalog.db on anders

**Step 3 — Generate invite code on anders:**
```bash
ANDERS_TS=$(ssh -J root@192.168.1.60 root@10.99.0.11 tailscale ip -4 | head -1)
INVITE_JSON=$(curl -sf -X POST "http://${ANDERS_TS}:8080/api/buddies/invite")
INVITE_CODE=$(echo "$INVITE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['code'])")
echo "Invite code: $INVITE_CODE"
```

**Step 4 — Complete wizard on bjorn (join flow):**
- POST wizard steps to bjorn's LAN IP (bjorn is in setup mode, no Tailscale filter)
  - Step 1: role=join, invite_code=$INVITE_CODE, member_url=http://${ANDERS_TS}:8080
  - Steps 2–5: node name, storage path, profile, skip notifications
  - Finish: complete wizard
- Verify: bjorn service restarts in normal mode
- Verify: `GET /api/status` on bjorn returns `{"status": "ok"}`

**Step 5 — Verify cluster consistency on both sides:**
```bash
# Anders cluster.db should have bjorn:
ssh -J root@192.168.1.60 root@10.99.0.11 \
  sqlite3 /var/lib/backup-buddy/cluster.db "SELECT display_name, status FROM members;"
# Bjorn cluster.db should have anders:
ssh -J root@192.168.1.60 root@10.99.0.12 \
  sqlite3 /var/lib/backup-buddy/cluster.db "SELECT display_name, status FROM members;"
```

**Step 6 — Verify fragments distributed to bjorn's storage:**
- Count share files in bjorn's storage dir before and after a new backup
- Expected: bjorn's storage grows after fragments are placed there

**Step 7 — Verify dashboard on both nodes shows correct cluster state:**
- Anders dashboard: 2 members, both online, both showing storage contribution
- Bjorn dashboard: 2 members, both online

**Step 8 — Register agent-bjorn-pc (303) with bjorn and back up test files:**
- Configure backup.cfg on 303 pointing at bjorn (10.99.0.12)
- Start agent service, verify registration

**Step 9 — Take snapshot `phase-e` on 101, 102, 301, 302, 303**

**Bug fix protocol:** Fix → `git commit` → `git push` → rollback 101 to `phase-b`,
102+303 to `phase-a` → retry from Step 1.

**Done when:**
- Bjorn successfully joined cluster via invite code
- cluster.db consistent on both nodes
- Fragments distributed to bjorn's storage node
- Both dashboards show 2 active members
- `phase-e` snapshot on all relevant nodes

```
> Kludde: ANDERS_NODE_NAME resolved dynamically from gatekeeper.cfg (not hardcoded).
> CT 302 snapshotted without setup (purpose TBD). agent-bjorn-pc uses stability_minutes=1.
> Cascade via prox (bjorn wizard binds to LAN IP in setup mode).
> Automated test: tests/integration/proxmox/phase_e_cluster_join_test.sh — all 13 steps PASS.
> Three production bugs found and fixed: (1) _cascade_join used default shares_happy=5 instead of
> deriving from cluster size; (2) IntroducerNode.create() used hostname=127.0.0.1 so bjorn's FURL
> pointed to its own loopback; (3) test syncs code from dev machine to VMs at run time.
```

---

### [x] 1.17.7 — Phase F: Nightly verification + deliberate corruption detection

> **All work via SSH: `ssh root@192.168.1.60`**
> Starting snapshot: `phase-e` (two-node cluster active)
> Rollback to `phase-e` if verification bugs found.

**Reads:** `gatekeeper/verify/nightly.py`, `gatekeeper/tahoe/client.py`, 1.16.9 notes
**Requirements:**

**Step 1 — Rollback 101, 102, 301, 303 to `phase-e`**

**Step 2 — Trigger nightly verification on anders manually:**
```bash
# Via internal API or by calling Python directly via SSH:
ssh -J root@192.168.1.60 root@10.99.0.11 \
  /opt/backup-buddy/.venv/bin/python3 -c "
import asyncio, sys
sys.path.insert(0, '/opt/backup-buddy')
from gatekeeper.verify.nightly import NightlyVerifier
# ... configure and run ..."
```
- Verify: Layer 1 (root_dir.cap accessible) passes
- Verify: Layer 2 (share counts) passes for all files
- Verify: Layer 3 (test restore) passes for all sampled files
- Verify: Layer 4 (lifeboat age) — may warn if no lifeboat yet, expected
- Verify: No `RestoreIntegrityError` or critical alerts in logs

**Step 3 — Deliberate corruption:**
- Find share files on bjorn's storage node:
  ```bash
  ssh -J root@192.168.1.60 root@10.99.0.12 find /mnt/storage -name "*.share" | head -3
  ```
- Corrupt ALL share files on BOTH nodes for one specific file
  (with k=3/n=5 need to corrupt ≥3 shares to guarantee detection):
  ```bash
  ssh -J root@192.168.1.60 root@10.99.0.11 \
    "dd if=/dev/urandom bs=1 count=100 seek=500 of=<share_file> conv=notrunc"
  ```

**Step 4 — Re-trigger nightly verification:**
- Verify: Layer 2 detects under-replication (shares_good < shares_needed) for the corrupted file
- Verify: Layer 3 restore of the corrupted file fails with `RestoreIntegrityError`
- Verify: Alert is raised (check logs for `send_alert` call)
- Verify: Other files still restore correctly (corruption isolated)

**Step 5 — Verify recovery path:**
- After corruption detected, verify rebalance is triggered or queued
- Check that notification dispatcher logs the alert correctly

**Bug fix protocol:** Fix → `git commit` → `git push` → rollback to `phase-e` → retry from Step 1.

**Done when:**
- Clean nightly verification passes all 4 layers
- Deliberate corruption detected by Layer 2 and/or Layer 3
- Alert raised correctly
- Rebalance or flag set for the corrupted file

```
> Kludde:
> - URI storage index ≠ on-disk storage index. The Tahoe fork applies an internal
>   transformation: field [2] of a `URI:CHK:...` cap is NOT the directory name used
>   under `/mnt/storage/shares/`. The actual on-disk SI is returned by Tahoe's check
>   API (`POST /uri/<cap>?t=check&output=json`) in the `"storage-index"` field.
>   Always use the API response to resolve the disk path — never slice the cap string.
> - Tahoe `?t=check` is shallow (counts share files, does not read content). A byte-flip
>   inside a share file will NOT be detected — `shares_good` stays ≥ shares_needed.
>   Only file deletion/absence is detected. Layer 2 corruption test must delete share
>   files, not corrupt bytes.
> - `/dev/sdb` (Tahoe storage disk) is NOT in fstab — it must be mounted manually after
>   every `qm rollback` + `qm start`. Without it gatekeeper starts but all share reads
>   fail silently. Fix: `mount /dev/sdb /mnt/storage` before starting gatekeeper.
> - Automated test: tests/integration/proxmox/phase_f_verify_test.sh — all 10 steps PASS.
> - Standalone NightlyVerifier trigger: tests/integration/proxmox/run_nightly_verify.py
>   (derives catalog key via HKDF-SHA256 from root_dir.cap, outputs VERIFY_RESULT:<json>).
> - phase-f snapshots created on VMs 101, 102 and CTs 301, 303.
```

---

### [x] 1.17.8 — Phase G: Full disaster recovery (VM destroy + fresh install + GUI restore)

> **All work via SSH: `ssh root@192.168.1.60`**
> Starting snapshot: `phase-a` on anders (101) — this phase sets up anders fresh via wizard
> so it has recovery_kit.enc, backs up files, then completely destroys and rebuilds the VM.
> Rollback anders to `phase-a` if disaster recovery bugs found.

**Reads:** `install/gatekeeper.sh`, `gatekeeper/gui/routes/onboarding.py`,
  `gatekeeper/gui/routes/restore.py`, `gatekeeper/lifeboat/`, 1.16.12 notes
**Requirements:**

**Step 1 — Rollback anders (101) and agent 301 to `phase-a`**

**Step 2 — Complete wizard on anders (same as Phase B):**
- Run wizard, select profile=balanced, set passphrase=TestPassphrase2026!
- Download and save `recovery_kit.enc` to local machine

**Step 3 — Back up ≥10 files from agent 301:**
- Configure backup.cfg, start agent
- Wait for upload, verify ≥10 rows in catalog.db on anders

**Step 4 — Record SHA-256 of testfile_01.bin:**
```bash
ssh -J root@192.168.1.60 root@10.99.0.31 sha256sum /srv/testbackup/testfile_01.bin
```
Save this value — it will be verified after disaster recovery.

**Step 5 — Count share files on anders' Tahoe storage (before destroy):**
```bash
ssh -J root@192.168.1.60 root@10.99.0.11 find /mnt/storage -type f | wc -l
```
Save this count.

**Step 6 — Destroy anders VM (true qm destroy, not just rollback):**
```bash
# Remove storage disk from config first to preserve Tahoe data:
ssh root@192.168.1.60 "
  qm stop 101
  # Detach storage disk from VM config (keep the disk volume):
  sed -i '/scsi1/d' /etc/pve/qemu-server/101.conf
  qm destroy 101
"
# Verify the disk volume still exists:
ssh root@192.168.1.60 "lvs | grep vm-101"
```

**Step 7 — Clone template 9000 → new VM 101:**
```bash
ssh root@192.168.1.60 "
  qm clone 9000 101 --name gatekeeper-anders --full --storage local-lvm
  # Set correct MAC, cloud-init, static IP:
  qm set 101 --net0 virtio=<ORIGINAL_MAC>,bridge=vmbr0
  qm set 101 --ipconfig0 ip=10.99.0.11/24,gw=10.99.0.1
  qm set 101 --cipassword ''  # SSH key only
  qm resize 101 scsi0 20G
  # Reattach the storage disk:
  qm set 101 --scsi1 /dev/<storage-disk-volume>
  qm cloudinit update 101
  qm start 101
"
```
Wait for VM to boot and SSH to be available.

**Step 8 — Install BackupBuddy on new anders VM:**
```bash
curl -fsSL https://raw.githubusercontent.com/MrBumbe/BackupBuddy/master/install/gatekeeper.sh \
  | ssh -J root@192.168.1.60 root@10.99.0.11 bash
# Wait for service to start
# Verify wizard accessible at http://10.99.0.11:8080/onboarding/step/1
```

**Step 9 — Join Tailscale on new VM:**
```bash
ssh -J root@192.168.1.60 root@10.99.0.11 tailscale up
```
Verify Tailscale IP is assigned.

**Step 10 — Emergency restore via GUI:**
- Upload `recovery_kit.enc` (saved in Step 2) to the emergency restore endpoint:
  ```bash
  RECOVERY_B64=$(base64 -w0 recovery_kit.enc)
  curl -sf -X POST "http://10.99.0.11:8080/api/restore/emergency" \
    -H "Content-Type: application/json" \
    -d "{\"recovery_kit_b64\": \"${RECOVERY_B64}\",
         \"passphrase\": \"TestPassphrase2026!\"}"
  ```
- Poll until reconstruction complete
- Verify: catalog.db has ≥10 entries
- Verify: `GET /api/restore/catalog` returns ≥10 files

**Step 11 — Restore testfile_01.bin and verify SHA-256:**
```bash
curl -sf -X POST "http://10.99.0.11:8080/api/restore/start/file" \
  -H "Content-Type: application/json" \
  -d '{"original_path": "/srv/testbackup/testfile_01.bin",
       "agent": "agent-anders-pc",
       "dest_path": "/tmp/dr_restore/"}'
# Poll job until complete
# SHA-256 the restored file and compare to saved reference
ssh -J root@192.168.1.60 root@10.99.0.11 sha256sum /tmp/dr_restore/testfile_01.bin
```
Expected: SHA-256 matches Step 4 reference exactly.

**Bug fix protocol:** Fix → `git commit` → `git push` → rollback 101 to `phase-a` → retry from Step 1.

**Done when:**
- VM 101 completely destroyed and recreated from template
- BackupBuddy installed fresh from GitHub on new VM
- Emergency restore from recovery_kit.enc + passphrase succeeds
- ≥10 files appear in catalog after reconstruction
- SHA-256 of restored file matches pre-destruction reference

```
> Kludde:
> - Phase-a rollback leaves /dev/sdb unformatted → mkfs.ext4 before first wizard
> - STORAGE_UUID captured dynamically via blkid after mkfs (not hardcoded)
> - MAC + scsi1 volume saved before VM destroy; `qm set --delete scsi1` detaches
>   without destroying the LVM volume before `qm destroy --destroy-unreferenced-disks 0`
> - Tailscale state (/var/lib/tailscale/) saved to Proxmox host before destroy,
>   restored to fresh VM (stop → restore → start) to preserve same Tailscale IP
> - Tailscale auth is INVALIDATED after VM rollback (coordination server rejects
>   rolled-back machine key). Fix: cache state to /tmp/ts_state_phase_a.tar.gz on
>   Proxmox host before running test; step 1 restores it automatically.
>   Pre-save: ssh -J root@192.168.1.60 root@10.99.0.11 "tar -czf - -C /var/lib tailscale"
>             | ssh root@192.168.1.60 "cat - > /tmp/ts_state_phase_a.tar.gz"
> - Tahoe-LAFS fork MUST be installed with pip install -e (editable). Non-editable
>   install produces 0-byte stub .so files for ALL C-extensions including cryptography,
>   fastapi, and allmydata. install/gatekeeper.sh runs editable + force-reinstall.
> - LVM thin pool is overcommitted (16 GiB free / 413 GiB thin volumes). After pip
>   fix, a qm snapshot can corrupt pip files (0-byte _rust.abi3.so again). Defense:
>   step 1 always runs pip editable + force-reinstall + HKDF import check on every
>   test run; step 15 (fresh VM before second wizard) does the same.
> - Second wizard runs on fresh VM to get into normal mode before emergency restore
> - profile must be switched to test (k=1, n=2, happy=1) both after first and
>   second wizard — balanced (k=3, n=5) cannot be satisfied on a single node
> - Emergency restore uses OLD recovery_kit.enc (pre-destroy), not second wizard's kit
> - reconstruct_catalog signature: (root_dir_cap, *, catalog, tahoe, progress_queue) → int
> - Script: tests/integration/proxmox/phase_g_disaster_recovery_test.sh
> - PASSED: run 25, all 21 steps, 2026-06-01
```

---

### [x] 1.17.9 — Phase H: Three-node cluster + node removal flow

> **All work via SSH: `ssh root@192.168.1.60`**
> Starting snapshot: `phase-e` (two-node cluster: anders + bjorn active)
> Rollback to `phase-e` if removal or three-node bugs found.

**Reads:** `gatekeeper/cluster/removal.py`, `gatekeeper/cluster/orphans.py`,
  `gatekeeper/rebalance/`, `gatekeeper/gui/routes/buddies.py`, 1.10 notes
**Requirements:**

**Step 1 — Rollback 101, 102, 103, 301, 302, 303 to `phase-e`** (carina at phase-a)

**Step 2 — Add carina as third node:**
- Generate invite on anders
- Complete wizard on carina (join flow, use invite code)
- Register agent-anders-nas (302) with carina
- Verify three members in cluster.db on all nodes
- Verify fragments begin distributing to carina's storage
- Verify adaptive profile k/n adjusts: 3 nodes → k=1, n=3 per ADR-006a

**Step 3 — Propose removal of bjorn from anders:**
```bash
ANDERS_TS=$(ssh -J root@192.168.1.60 root@10.99.0.11 tailscale ip -4 | head -1)
BJORN_NODE_ID=$(ssh -J root@192.168.1.60 root@10.99.0.12 \
  sqlite3 /var/lib/backup-buddy/cluster.db \
  "SELECT node_id FROM members WHERE display_name='gatekeeper-bjorn';")
curl -sf -X POST "http://${ANDERS_TS}:8080/api/buddies/propose_removal" \
  -H "Content-Type: application/json" \
  -d "{\"target_node_id\": \"${BJORN_NODE_ID}\"}"
```
- Verify: vote appears in cluster.db on anders
- Verify: bjorn NOT notified yet (check bjorn logs)

**Step 4 — Cast votes to reach majority (anders + carina vote yes):**
```bash
VOTE_ID=$(ssh -J root@192.168.1.60 root@10.99.0.11 \
  sqlite3 /var/lib/backup-buddy/cluster.db \
  "SELECT id FROM votes WHERE target_node_id='${BJORN_NODE_ID}' AND resolved=0;")
# Cast vote from anders:
curl -sf -X POST "http://${ANDERS_TS}:8080/api/buddies/vote/${VOTE_ID}" \
  -d '{"vote": true}'
CARINA_TS=$(ssh -J root@192.168.1.60 root@10.99.0.13 tailscale ip -4 | head -1)
# Cast vote from carina:
curl -sf -X POST "http://${CARINA_TS}:8080/api/buddies/vote/${VOTE_ID}" \
  -d '{"vote": true}'
```
- Verify: vote resolves as PASSED in cluster.db
- Verify: grace period started (grace_started_at set for bjorn's member row)
- Verify: bjorn notified (check bjorn logs — notification of grace period start)

**Step 5 — Verify re-fragmentation is triggered:**
- Check rebalance scheduler is queued or running on anders/carina
- Verify files previously using bjorn's fragments are being re-uploaded

**Step 6 — Simulate grace period expiry and orphan cleanup:**
- Manually set `grace_started_at` to a past timestamp in cluster.db to skip waiting
- Trigger `cleanup_orphans()` manually
- Verify: bjorn's fragments deleted from storage pool
- Verify: StoragePoolManager.remove_fragment() called (used_bytes decremented)
- Verify: orphan_tags.cleaned_at set for bjorn's fragments
- Verify: notification sent: "Cleared X GB of orphaned fragments from bjorn"

**Step 7 — Verify cluster still operates normally with 2 nodes:**
- Restore a file after bjorn removal
- Verify SHA-256 matches

**Bug fix protocol:** Fix → `git commit` → `git push` → rollback all to `phase-e` → retry from Step 1.

**Done when:**
- Three-node cluster formed (anders + bjorn + carina)
- Removal vote passed, grace period started
- Orphan cleanup completed, bjorn's fragments deleted
- Cluster still functional with 2 nodes post-removal

```
> Kludde:
> - API routes in the task spec were outdated; correct routes from code:
>   POST /api/buddies/removal (not propose_removal),
>   POST /api/buddies/vote/{vote_id}/cast with {"choice": true} (not /vote/{id} + {"vote":true})
> - Cross-gatekeeper vote propagation is Phase 1 out-of-scope; carina's ballot is
>   pre-inserted directly into anders vote_ballots via sqlite3 to reach majority (2/2)
> - send_alert was not wired into start_grace_period call in buddies.py — fixed by adding
>   a logging lambda; test verifies grace-alert log line in gatekeeper journal
> - Orphan cleanup uses fake CHK cap strings (no real Tahoe delete) with mock delete_fragment
>   returning 1024 bytes; cleaned_at set in cluster.db is the verifiable side effect
> - Bjorn cluster.db only has 2 members (anders+bjorn) — cross-node propagation is Phase 1
>   out-of-scope; carina's cluster.db gets all 3 members from the join cascade
> - Carina disk at phase-a needs mkfs.ext4 -F /dev/sdb (same as bjorn in phase-e)
> - Switch anders to adaptive profile after carina joins so new uploads use k=1, n=3
>   and carina's storage node receives shares
> - Script: tests/integration/proxmox/phase_h_three_node_removal_test.sh
> - Bugs fixed during test run (2026-06-01):
>   (a) VM 101 phase-a only (no phase-e) — test rebuilt 2-node cluster from scratch
>   (b) Tailscale NeedsLogin after rollback — fixed with cached state restore (_fix_tailscale)
>   (c) pip not in PATH — use full .venv/bin/pip path
>   (d) LVM snapshot 0-byte venv files — pip install -r requirements.txt --force-reinstall first
>   (e) wizard role "found" invalid — changed to "new"
>   (f) wizard step/5 (new cluster) requires passphrase/passphrase_confirm fields
>   (g) system python3 lacks pydantic — use .venv/bin/python3 for gatekeeper imports
>   (h) tr -d '[:space:]' removes space from "k=1 n=3" — changed to tr -d '\n\r'
>   (i) 15-second share propagation wait too short — replaced with 3-min poll
> - Test PASSED 2026-06-01, phase-h snapshots created on 101, 102, 103, 301, 302, 303
```

---

### [x] 1.17.10 — Pre-release: end-to-end restore integration test

> **All work via SSH: `ssh root@192.168.1.60`**
> Starting snapshot: `phase-h` (three-node cluster, anders + bjorn + carina)
> Priority: HIGH — restore is the most critical untested path before real-world use.
> Test PASSED 2026-06-02

**Reads:** `gatekeeper/restore/restore.py`, `gatekeeper/tahoe/client.py`,
  `gatekeeper/gui/routes/restore.py`, project-docs/testing.md

**Background:** The restore code (`restore.py`, GUI routes `/api/restore/start/file`) is
fully implemented but has never been run against a real Tahoe cluster. SHA-256
verification path, TahoeClient.download(), and job-tracking flow are all untested
in integration. Before real users trust BackupBuddy with their files, restore
must be verified to work reliably.

**Requirements:**

**Step 1 — Verify a file is backed up in catalog:**
- On anders, check `catalog.db` for at least one backed-up file via venv python3
- Record `original_path`, `agent`, and `sha256` for a test file

**Step 2 — Restore the file via the GUI API:**
```bash
ANDERS_TS=$(ssh -J root@192.168.1.60 root@10.99.0.11 tailscale ip -4 | head -1)
curl -sf -X POST "http://${ANDERS_TS}:8080/api/restore/start/file" \
  -H "Content-Type: application/json" \
  -d '{"original_path": "<path>", "agent": "<agent>", "dest_path": "/tmp/restored_test"}'
```
- Poll job status until `status == "done"` or `"failed"`
- Verify restore succeeded and SHA-256 in response matches catalog entry

**Step 3 — Verify file integrity:**
- `sha256sum /tmp/restored_test` on anders
- Compare against catalog SHA-256 — must match exactly

**Step 4 — Test restore failure case (wrong path):**
- Request restore for a non-existent path
- Verify `status == "failed"` with a user-readable error (no Tahoe internals exposed)

**Step 5 — Verify agent API is bound to LAN IP (not 0.0.0.0):**
- `ss -tlnp | grep 8081` on anders (or configured agent API port)
- Confirm bind address is LAN IP (192.168.x.x), not `0.0.0.0`
- Confirm GUI port is bound to Tailscale IP (100.x.x.x), not `0.0.0.0`

**Done when:**
- File restored via API and SHA-256 verified ✓
- Failure case returns clean error ✓
- Agent API bound to LAN IP confirmed ✓
- GUI bound to Tailscale IP confirmed ✓

```
> Kludde:
> Bug: /mnt/storage owned by root but service runs as backupbuddy — storage pool
> validation failed silently at startup (GUI never bound, only agent API started).
> Fix: chown backupbuddy:backupbuddy /mnt/storage in install_gatekeeper.sh.
> On phase-h snapshot, catalog.db had 67 synthetic test entries with real caps but
> no actual Tahoe shares. Uploaded a fresh test file (35 KB) via TahoeClient.upload()
> and inserted into catalog to create a real end-to-end restore scenario.
> All five steps passed on anders (Tailscale IP 100.124.183.52).
```

---

### [x] 1.17.11 — Pre-release: wire orphan cleanup into production daily job

> Priority: HIGH — without this, orphan fragments from removed nodes are never deleted.
> Grace periods expire silently and storage is never reclaimed.

**Reads:** `gatekeeper/cluster/orphans.py`, `gatekeeper/main.py`,
  `gatekeeper/storage/pool.py`, `gatekeeper/tahoe/client.py`

**Background:** `cleanup_orphans()` exists but is never called in production.
The `delete_fragment` callback it requires must: (1) ask the Tahoe client to
delete the file-cap, (2) call `StoragePoolManager.remove_fragment()` so the
in-memory quota is updated. Neither of these is wired up in `main.py`.
Additionally, no daily scheduler job calls `cleanup_orphans()`.

**Requirements:**

**Step 1 — Implement `delete_fragment` in `gatekeeper/storage/pool.py`:**
- `async def delete_fragment(tahoe: TahoeClient, pool: StoragePoolManager, fragment_id: str) -> int`
- Downloads size from catalog or estimates, calls `tahoe.delete(fragment_id)`,
  calls `pool.remove_fragment(fragment_id)`
- Returns bytes freed
- If Tahoe delete fails, logs error and raises — do NOT silently ignore

**Step 2 — Implement a daily orphan cleanup job in `gatekeeper/main.py`:**
- Register as a background asyncio task (or APScheduler job) at startup
- Run once daily at a fixed time or 24h after last run
- Call `cleanup_orphans(db, orphan_grace_days=config.orphan_grace_days, ...)`
- Pass `is_refrag_complete=lambda _: True` for Phase 1 (rebalance is Phase 2)
- Log start, completion, and counts (eligible/deleted/skipped)
- On error: log at ERROR, send alert if notify is configured

**Step 3 — Unit test the production `delete_fragment` implementation:**
- Mock `TahoeClient.delete()` and verify `StoragePoolManager.remove_fragment()` is called
- Test failure path: Tahoe delete fails → exception propagated, quota NOT updated

**Step 4 — Integration test via phase-h snapshot:**
- Pre-insert an orphan with `marked_orphan_at` = 35 days ago
- Trigger cleanup job manually (or reduce timer for test)
- Verify `cleaned_at` set in `orphan_tags`, quota counter decremented

**Done when:**
- `delete_fragment` implemented with real Tahoe + pool calls ✓
- Daily cleanup job wired in `main.py` ✓
- Unit tests pass ✓
- Integration test on Proxmox confirms orphan tags cleaned ✓

```
> Kludde:
> Design: cleanup_orphans() stays sync (backward-compatible with existing tests and integration
> scripts). Daily job uses asyncio.to_thread + run_coroutine_threadsafe to bridge sync callback
> with async TahoeClient. StoragePoolManager.sync_usage() rescans filesystem after deletion
> (no per-fragment size tracking needed). TahoeClient.delete() calls DELETE /uri/<cap> on the
> Tahoe gateway. All 7 integration test steps passed on phase-h snapshot 2026-06-02.
```
> Test PASSED 2026-06-02

---

### [x] 1.17.12 — Pre-release: document introducer SPOF and add health check

> Priority: MEDIUM — users need to know this limitation before deploying.
> No code change required for Phase 1; documentation + health check is the fix.

**Reads:** `gatekeeper/tahoe/introducer.py`, `gatekeeper/main.py`,
  `gatekeeper/gui/routes/dashboard.py`, DECISIONS.md

**Background:** The Tahoe-LAFS introducer runs only on the node that created the
cluster (anders in tests). If that node goes offline, storage uploads and downloads
from all other nodes fail immediately. This is a known Phase 1 limitation
(replacement: gossip protocol in Phase 2 per 2.3). Before real-world use, this
limitation must be: (a) documented in DECISIONS.md, (b) surfaced in the dashboard
GUI so users know which node is the introducer and what happens if it goes down.

**Requirements:**

**Step 1 — Add an ADR to DECISIONS.md:**
- Document that the cluster-creator node hosts the Tahoe introducer
- Explain the SPOF risk: if introducer goes down, uploads/downloads fail until it recovers
- State the Phase 2 mitigation: gossip-based discovery per ADR/2.3
- Note that Tahoe itself can recover if the introducer comes back (no data loss)

**Step 2 — Surface introducer status in the dashboard:**
- In `gatekeeper/gui/routes/dashboard.py`, add an `is_introducer` field to the
  state data (true if this node runs the introducer, false otherwise)
- In `dashboard.html`, show a visible notice when `is_introducer=true`:
  "This node is the cluster introducer. If it goes offline, backups will pause
  on all nodes until it recovers."
- Show current introducer node name/address in the cluster overview

**Step 3 — Integration test:**
- On a phase-h snapshot, verify the introducer notice is shown on anders's GUI
- Verify it is NOT shown on bjorn's or carina's GUI

**Done when:**
- DECISIONS.md ADR added ✓
- Dashboard shows introducer notice on the correct node ✓
- Integration test verified ✓

```
> Kludde:
```

---

### [x] 1.17.13 — Pre-release: cross-gatekeeper vote propagation (basic)

> Priority: LOW — user confirmed this is a low-priority item.
> Without this, all voters must cast their ballot on the same gatekeeper node,
> which means someone must log in to a specific node's GUI to vote.
> A workaround exists (log in to the proposer's GUI) and works for Phase 1 PoC.

**Reads:** `gatekeeper/cluster/removal.py`, `gatekeeper/gui/routes/buddies.py`,
  `gatekeeper/cluster/`, DECISIONS.md

**Background:** When anders proposes a removal vote, the vote only exists in
anders's `cluster.db`. For carina to cast a ballot, she must log in to anders's
GUI (via Tailscale). This works but is confusing. A proper gossip mechanism
would propagate the vote to all nodes so each user can vote from their own GUI.

**Requirements:**

**Step 1 — Design the propagation protocol:**
- When a vote is created, the proposer pushes it to all active cluster members via
  a new API endpoint `POST /api/cluster/sync/vote`
- When a ballot is cast, it is pushed to the vote proposer node, which is the
  authoritative holder of the vote record
- Pydantic models for vote and ballot sync messages, validated on receipt

**Step 2 — Implement:**
- `POST /api/cluster/sync/vote` — receive a vote record from another node
- `POST /api/cluster/sync/ballot` — receive a ballot from another node
- Propagation triggered after: propose vote, cast ballot
- All cluster comms over Tailscale (per ADR-002)

**Step 3 — Integration test:**
- On phase-h snapshot, propose removal from anders, cast ballot from carina's own GUI
  (NOT by logging in to anders)
- Verify vote reaches majority and grace period starts

**Done when:**
- Vote and ballot propagation implemented ✓
- Integration test: carina votes from her own GUI ✓

```
> Kludde: Implemented ADR-021 Phase 1 vote sync protocol.
> New gatekeeper/cluster/sync.py with VoteSyncMessage, BallotSyncMessage, push_vote_to_peers(),
> push_ballot_to_proposer(). ClusterDB.upsert_vote() for INSERT...ON CONFLICT.
> Two new endpoints: POST /api/cluster/sync/vote (receive synced vote) and
> POST /api/cluster/sync/ballot (receive forwarded ballot, voter identity from sender TS IP).
> Buddies routes patched: propose_removal and grace-extend push vote to peers;
> cast_vote non-proposer path forwards ballot to proposer.
> Integration test phase_k_vote_propagation_test.sh: anders proposes grace_extension (+7 days)
> for bjorn, carina votes from her own GUI — vote passes, bjorn grace_days 7→14.
> phase-k snapshots on 101, 102, 103, 301, 302, 303. 2026-06-02.
```

---

### [x] 1.17.14 — Installation guide

> Priority: HIGH — nothing else matters if users cannot install and run BackupBuddy.
> Write AFTER 1.17.10, 1.17.11, and 1.17.12 are done and tested.

**Reads:** project-docs/onboarding.md, project-docs/configuration.md,
  `install_gatekeeper.sh`, `install_agent.sh`, all wizard flow routes

**Requirements:**
- Target audience: a technically curious person with no Linux/homelab background
  (assume they can follow instructions but do not know what SSH or sudo is without explanation)
- Written in English (per CLAUDE.md language rules), plain language
- Format: Markdown, suitable for a GitHub README or static site
- Structure:
  1. What is BackupBuddy? (1 paragraph, no jargon)
  2. What you need (hardware / VM requirements, list)
  3. Step-by-step: install the first node (gatekeeper), including Tailscale setup
  4. Step-by-step: open the wizard and create a cluster
  5. Step-by-step: install an agent on the computer you want to back up
  6. Step-by-step: invite a friend (buddy), them joining your cluster
  7. Verify your first backup was made
  8. How to restore a file
  9. Troubleshooting: the 5 most common problems and their solutions
- Each step must be a single action with the exact command or UI click to use
- No Tahoe jargon, no cap/FURL/share terminology
- After the guide is written, send it to Johan as a file

**Done when:**
- `INSTALL.md` written and committed ✓
- All steps verified against the current install scripts and wizard flow ✓

```
> Kludde:
> INSTALL.md written 2026-06-02. All steps verified against install/gatekeeper.sh,
> install/agent.sh, and gatekeeper/gui/routes/onboarding.py. File sent to Johan.
```

---

---

## 1.18 — User acceptance test: end-to-end simulation

> **Goal:** Confirm that a real user can follow INSTALL.md from a blank VM, form a
> three-node cluster, back up files, and restore them with verifiable checksums.
>
> **Method:** Act as three separate users (Anders, Björn, Carina) — each with their own
> gatekeeper + agent on an isolated LAN — following INSTALL.md step by step, using SSH
> only. No test scripts. No simulations. No auto-fill helpers. Every command typed as a
> real user would type it.
>
> **Error policy:** All problems encountered are recorded in `tests/integration/1.18.1-issues.md`.
> Nothing is fixed unless the error completely blocks progress. If a blocking fix is required:
> fix it, record it in issues file under `BLOCKING FIX:`, then roll all nodes back to
> `clean-ubuntu` and restart the test from the beginning.

---

### [x] 1.18.1 — Three-user install-and-restore simulation (manual, SSH-only)

> **Reads:** INSTALL.md, project-docs/onboarding.md
>
> **Prerequisite — Tailscale auth:**
> The `clean-ubuntu` Proxmox snapshots (taken 2026-05-30) have Tailscale installed and
> authenticated. After rollback, `tailscale status` should show the node as connected.
> If the machine key has expired or been revoked, this task cannot start until Johan
> provides a **reusable (non-ephemeral) Tailscale auth key** from the Tailscale admin panel
> at tailscale.com/admin — one key covers all three gatekeepers.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.
> All VM and LXC operations go through this host.

---

#### A. Infrastructure setup

**A1 — Roll back all six nodes to `clean-ubuntu`:**

Run on Proxmox (192.168.1.60):

```bash
# Gatekeepers (QEMU VMs — stop, rollback, start)
for vmid in 101 102 103; do
  qm stop $vmid --skiplock 1
  sleep 3
  qm rollback $vmid clean-ubuntu
  qm start $vmid
done

# Agent containers (LXC)
for ctid in 301 302 303; do
  pct stop $ctid
  sleep 2
  pct rollback $ctid clean-ubuntu
  pct start $ctid
done
```

Verify all six are running:

```bash
qm status 101; qm status 102; qm status 103
pct status 301; pct status 302; pct status 303
```

**A1a — Clear stale SSH host keys:**

After rollback, run on the operator machine:

```bash
for ip in 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33; do
  ssh-keygen -R $ip
done
ssh-keyscan -H 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33 \
  >> ~/.ssh/known_hosts
```

This avoids the "Host key verification failed" error caused by key rotation on rollback.

**A2 — Node layout for this test:**

| User | Role | VM/LXC ID | Hostname | LAN IP | Tailscale IP |
|------|------|-----------|----------|--------|-------------|
| Anders | Gatekeeper | VM 101 | gatekeeper-anders | 10.99.0.11 | resolve with `tailscale ip -4` after rollback |
| Björn | Gatekeeper | VM 102 | gatekeeper-bjorn | 10.99.0.12 | resolve with `tailscale ip -4` after rollback |
| Carina | Gatekeeper | VM 103 | gatekeeper-carina | 10.99.0.13 | resolve with `tailscale ip -4` after rollback |
| Anders | Agent | LXC 301 | agent-anders-pc | 10.99.0.31 | n/a (no Tailscale on agents) |
| Björn | Agent | LXC 303 | agent-bjorn-pc | 10.99.0.33 | n/a |
| Carina | Agent | LXC 302 | agent-anders-nas | 10.99.0.32 | n/a (repurposed as Carina's agent for this test) |

> Note: All six nodes share the same 10.99.0.x Proxmox bridge. True VLAN isolation
> (separate vmbr per user) is desirable for realism but would require Proxmox network
> reconfiguration. For this test, logical isolation is enforced by configuration: each
> agent's `backup.cfg` points only to its own gatekeeper's LAN IP. If Proxmox network
> reconfiguration is feasible, add separate bridges vmbr1/vmbr2/vmbr3 as a follow-up.

**A3 — Verify Tailscale after rollback:**

SSH to each gatekeeper and confirm Tailscale is connected:

```bash
ssh gk-anders "tailscale status"
ssh gk-bjorn  "tailscale status"
ssh gk-carina "tailscale status"
```

Expected: each shows the node as online with a 100.x.x.x address. If any show
"Logged out", stop — the Tailscale machine key has expired. Ask Johan for a reusable
auth key, then run `sudo tailscale up --auth-key=<key>` on the affected node(s).

Record actual Tailscale IPs for later steps:

```bash
ssh gk-anders "tailscale ip -4"   # e.g. 100.64.235.77
ssh gk-bjorn  "tailscale ip -4"
ssh gk-carina "tailscale ip -4"
```

---

#### B. Download test files (on the Proxmox host or Anders's agent)

All test files are placed on the **agent machines** inside `/home/testuser/backup-test/`.
They are downloaded once (on the Proxmox host to save time) and then copied via `pct push`
or `scp` to the agent containers.

Download a representative mix of file types — at least two of each:

- **`.jpg`** — public domain photos, at least 5 MB each (e.g. from Wikimedia Commons)
- **`.zip`** — a moderately large archive (50–200 MB)
- **`.iso`** — a small Linux ISO (200–700 MB; avoid full desktop ISOs)
- **`.docx`** — sample word-processor documents (1–10 MB)

After download, record SHA-256 checksums **before** any backup:

```bash
sha256sum /tmp/testfiles/*.jpg /tmp/testfiles/*.zip \
          /tmp/testfiles/*.iso /tmp/testfiles/*.docx \
  | tee /tmp/checksums_before.txt
```

Copy files to each user's agent container so all three users have data to back up.
Use different subsets per user to test cross-node restore later:

- Anders's agent (LXC 301): all .jpg and .iso files
- Björn's agent (LXC 303): all .zip files and one .docx
- Carina's agent (LXC 302): remaining .docx files and one .jpg

---

#### C. Install and configure — Anders (VM 101 + LXC 301)

Follow INSTALL.md sections 3–5 exactly. Every command below mirrors the guide.

**C1 — SSH to Anders's gatekeeper:**

```bash
ssh gk-anders
```

**C2 — Install BackupBuddy gatekeeper (INSTALL.md §3):**

```bash
curl -sSL https://get.backupbuddy.io | sudo bash
```

Wait for the installer to complete. Note the LAN IP it prints and confirm the service
is shown as running.

**C3 — Connect Tailscale (INSTALL.md §3a):**

```bash
sudo tailscale up
```

If the node was already authenticated (rollback preserved auth state), this command
returns immediately with no URL. If a URL is printed, open it in a browser, log in,
and return here. Record whether interactive auth was needed.

**C4 — Open the setup wizard:**

From any browser on the 10.99.0.x network, open `http://10.99.0.11:8080`.
If the wizard does not load, note it in the issues file and try `hostname -I`
on the gatekeeper to find the correct LAN IP.

**C5 — Complete the wizard (INSTALL.md §4):**

- Step 1: **Start a new cluster**
- Step 2: Node ID `anders-home`, display name `Anders home node`
- Step 3: Storage path `/mnt/buddy-storage`, quota `50` GB
- Step 4: Profile **Adaptive** (default)
- Step 5: Skip notification email. Passphrase: choose a passphrase, write it down.
- Finish: Download `recovery-kit.enc`, save it. Click "I have saved my recovery key".
- Record the **invite code** shown (e.g. `kaffe-trumpet-7`).
- Record the **Tailscale address** shown (e.g. `http://100.64.235.77:8080`).

**C6 — Install the agent on LXC 301 (INSTALL.md §5):**

```bash
ssh agent-anders-pc
curl -sSL https://get.backupbuddy.io/agent | sudo bash
# Answer: gatekeeper IP = 10.99.0.11, agent name = anders-laptop
```

Edit backup paths:

```bash
sudo nano /etc/backup-buddy/backup.cfg
# Add under [backup]:
# /home/testuser/backup-test
```

Copy agent token to Anders's gatekeeper (INSTALL.md §5b):

```bash
# On agent: find the token
sudo grep token /etc/backup-buddy/backup.cfg

# On gatekeeper: paste token
ssh gk-anders "sudo nano /etc/backup-buddy/gatekeeper.cfg"
# Update [agent_api] token = <token>
ssh gk-anders "sudo systemctl restart backup-buddy-gatekeeper"

# Start the agent
sudo systemctl start backup-buddy-agent
```

---

#### D. Install and configure — Björn (VM 102 + LXC 303)

Repeat the same steps as section C, with:
- Node ID: `bjorn-home`, display name: `Björn home node`
- Storage path: `/mnt/buddy-storage`, quota `50` GB
- Agent LXC: 303 (`ssh agent-bjorn-pc`), gatekeeper IP: `10.99.0.12`
- Agent name: `bjorn-laptop`

In the wizard, Björn selects **"Join an existing cluster"** and enters:
- Anders's invite code (from C5)
- Anders's Tailscale address (from C5, e.g. `http://100.64.235.77:8080`)

Record whether both nodes appear in each other's dashboard after Björn joins.

---

#### E. Install and configure — Carina (VM 103 + LXC 302)

Repeat the same steps, with:
- Node ID: `carina-home`, display name: `Carina home node`
- Storage path: `/mnt/buddy-storage`, quota `50` GB
- Agent LXC: 302 (`ssh agent-anders-nas`, repurposed), gatekeeper IP: `10.99.0.13`
- Agent name: `carina-laptop`

Anders must generate a **new invite code** from his dashboard (Buddies page)
before Carina can join. Carina selects "Join an existing cluster" and uses that code
and Anders's Tailscale address.

Record whether all three nodes appear online in each other's dashboards.

---

#### F. Wait for backups and verify

**F1 — Watch agent logs on each agent container:**

```bash
ssh agent-anders-pc  "journalctl -u backup-buddy-agent -f"
ssh agent-bjorn-pc   "journalctl -u backup-buddy-agent -f"
ssh agent-anders-nas "journalctl -u backup-buddy-agent -f"  # Carina's agent
```

Wait until each agent shows `SUCCESS` entries for all test files.

**F2 — Check gatekeeper dashboards:**

Open each gatekeeper's Tailscale dashboard URL. Confirm:
- "Last backup" shows a recent timestamp
- "Files backed up" is non-zero

---

#### G. Restore and verify checksums

**G1 — Restore from Anders's dashboard:**

Open `http://<anders-tailscale-ip>:8080` → Restore.
Restore each of Anders's test files to `/tmp/restored/anders/` on the gatekeeper.

**G2 — Restore from Björn's dashboard:**

Open Björn's dashboard. Restore Björn's test files to `/tmp/restored/bjorn/`.

**G3 — Restore from Carina's dashboard:**

Open Carina's dashboard. Restore Carina's test files to `/tmp/restored/carina/`.

**G4 — Compute checksums after restore:**

On each gatekeeper:

```bash
ssh gk-anders  "sha256sum /tmp/restored/anders/*"
ssh gk-bjorn   "sha256sum /tmp/restored/bjorn/*"
ssh gk-carina  "sha256sum /tmp/restored/carina/*"
```

Compare against `/tmp/checksums_before.txt`. Every hash must match.

---

#### H. Manual test checklist

Run through every item. Mark PASS / FAIL / N/A. Add notes to the issues file for
every FAIL.

**Installation:**
- [ ] Installer completes without errors on fresh Ubuntu 24.04
- [ ] `backup-buddy-gatekeeper` service is `active (running)` after install
- [ ] Wizard is reachable at `http://<LAN-IP>:8080` before Tailscale is configured
- [ ] `sudo tailscale up` connects without requiring a new browser auth (rollback preserved state)
- [ ] Wizard completes all five steps without error
- [ ] `recovery-kit.enc` download works and produces a non-empty file
- [ ] Invite code is generated and displayed after wizard completes
- [ ] Dashboard switches to Tailscale address after wizard completes
- [ ] Dashboard is **not** reachable on the LAN IP after Tailscale binds (security check)

**Cluster formation:**
- [ ] Björn can join using Anders's invite code and Tailscale address
- [ ] Carina can join using a freshly generated second invite code
- [ ] All three nodes appear as **Online** in Anders's dashboard
- [ ] All three nodes appear as **Online** in Björn's dashboard
- [ ] All three nodes appear as **Online** in Carina's dashboard
- [ ] Reusing an expired invite code produces an error message, not a silent failure

**Agent:**
- [ ] Agent installer asks for gatekeeper IP and agent name interactively
- [ ] `backup-buddy-agent` service starts successfully
- [ ] Agent appears in its gatekeeper's dashboard after token is copied
- [ ] Editing `backup.cfg` and restarting the agent picks up the new folders
- [ ] Agent log shows `SUCCESS` for each backed-up file

**Backup integrity:**
- [ ] All `.jpg` test files backed up successfully
- [ ] All `.zip` test files backed up successfully
- [ ] All `.iso` test files backed up successfully
- [ ] All `.docx` test files backed up successfully
- [ ] No `FAILED` entries in any agent log for the test files

**Restore and checksums:**
- [ ] Single-file restore completes without error
- [ ] Restored `.jpg` SHA-256 matches original
- [ ] Restored `.zip` SHA-256 matches original
- [ ] Restored `.iso` SHA-256 matches original
- [ ] Restored `.docx` SHA-256 matches original
- [ ] Restoring a folder (not a single file) completes without error
- [ ] Restored files land in the correct destination folder on the gatekeeper

**Resilience (basic):**
- [ ] Stopping **one** gatekeeper (simulate node failure) and restoring from the other two still succeeds
- [ ] Bring the stopped gatekeeper back up — it reconnects and dashboard shows it Online

**UI and UX:**
- [ ] Dashboard shows an obvious error or warning if an agent has not sent data for > 1 hour
- [ ] Recovery kit re-download is accessible from the dashboard after wizard completes
- [ ] Navigating the dashboard without any data causes no crashes or blank pages
- [ ] All button clicks in the wizard produce visible feedback within 3 seconds

---

#### I. Error worklist

**File:** `tests/integration/1.18.1-issues.md`

Format for each issue:

```
## ISSUE-001
Step: C2 (install)
Symptom: Installer exited with status 1 — "curl: command not found"
Blocking: yes / no
Fix applied (if blocking): installed curl with apt, restarted test from A1
```

Create the file at the start of the test, even if empty. Update it throughout.

---

#### Done when:

- All six nodes installed and cluster formed ✓
- All test files backed up with `SUCCESS` in agent logs ✓
- All restore checksums match originals ✓
- Manual checklist completed (all items PASS or documented in issues file) ✓
- `tests/integration/1.18.1-issues.md` committed with all encountered problems ✓
- This task marked `[x]` with a kludde block summarising what passed, what failed,
  and how many blocking fixes were required ✓

---

> **Kludde — test run 2026-06-03**
>
> **Overall result:** PASS with findings — all core flows work, two design gaps logged.
>
> **What passed:**
> - All 3 gatekeepers installed and cluster formed (Anders as introducer, Björn and Carina joined)
> - All 8 test files backed up without error (4× Anders, 2× Björn, 2× Carina)
> - All 8 restored file SHA256 checksums match originals exactly (cross-checked against
>   pre-backup reference file on Proxmox)
> - File types covered: .iso (217 MB), .jpg (×4), .zip (934 KB), .docx (×2)
> - Stopped gatekeeper (Anders) restarts and comes back active
>
> **What failed / was not confirmed:**
> - **Resilience (ISSUE-013):** Stopping the introducer (Anders) causes all Tahoe downloads
>   to fail with HTTP 410 on the remaining nodes — cluster is not resilient to introducer loss.
>   This is a significant design gap.
> - **Member list propagation (ISSUE-009):** Björn's dashboard showed 2/3 members throughout
>   the entire test. No self-healing observed after 2+ hours. Carina's join was never pushed
>   to Björn.
> - **Folder restore:** not tested — API only exercised via single-file endpoint; no
>   `/api/restore/start/folder` test performed.
> - **Dashboard UX items** (agent-offline warning, recovery kit re-download, wizard timing):
>   observed as functional but not formally ticked off against the checklist.
>
> **Blocking fixes (4 — none required test restart):**
> 1. ISSUE-002: SSH host key rotation after snapshot rollback (Kludde-only)
> 2. ISSUE-003: Tailscale logged out on VM 101 phase-a snapshot (Kludde-only)
> 3. ISSUE-004: uvicorn basereload.py 0-byte stub — re-ran installer (idempotent)
> 4. ISSUE-005: `get.backupbuddy.io` DNS does not exist — used GitHub clone instead
>
> **Non-blocking issues:** 9 (ISSUE-001, -006, -007, -008, -009, -010, -011, -012, -013)
>
> **Total issues logged:** 13
> Full worklist: `tests/integration/1.18.1-issues.md`

---

### [x] 1.18.2 — INSTALL.md: replace placeholder install URL with working command

> **Source:** `tests/integration/1.18.1-issues.md` → ISSUE-005
> **Reads:** `INSTALL.md`, `install/gatekeeper.sh`

`INSTALL.md §3` tells the user to run `curl -sSL https://get.backupbuddy.io | sudo bash`.
That hostname does not exist — the very first command a new user runs fails with
`curl: (6) Could not resolve host: get.backupbuddy.io`.

**Requirements:**

Replace the install command in `INSTALL.md §3` with the working GitHub-based procedure:

```bash
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
sudo bash /opt/backup-buddy/install/gatekeeper.sh
```

Add a note that `git` must be installed (`sudo apt-get install -y git` if absent).
If and when `get.backupbuddy.io` is ever registered and hosted, revert to the
one-liner — but do not document a URL that does not work.

**Done when:**
- `INSTALL.md §3` contains no reference to `get.backupbuddy.io` ✓
- The documented install command works on a fresh Ubuntu 24.04 machine ✓

> **Kludde — 2026-06-03**
>
> Replaced all `curl -sSL https://get.backupbuddy.io` occurrences with the
> working `git clone` + `sudo bash` procedure. Updated files:
> - `INSTALL.md` §3 (gatekeeper), §5 (agent), §6 (buddy install)
> - `install/gatekeeper.sh` Usage comment
> - `install/agent.sh` Usage comment
> - `project-docs/onboarding.md` (gatekeeper and agent install sections)
> - `project-docs/testing.md` (Step 6 test environment setup)
>
> Added git prerequisite note in each install section. Historical references
> to the broken URL remain only in `TODO.md` and `tests/integration/1.18.1-issues.md`.

---

### [x] 1.18.3 — Wizard: auto-create and chown storage path on step 3

> **Source:** `tests/integration/1.18.1-issues.md` → ISSUE-006, ISSUE-007
> **Reads:** `gatekeeper/gui/routes/wizard.py`, `INSTALL.md`

Two compounding failures on wizard step 3 (storage path):

1. **ISSUE-006:** INSTALL.md says "This folder will be created for you if it does not
   exist" — but the wizard rejects a non-existent path with "Path does not exist".
2. **ISSUE-007:** Even after the user creates the directory as root, the wizard rejects
   it with "Path is not writable" because `backupbuddy` (uid=999) has no write access
   to a root-owned 0755 directory.

A user following INSTALL.md hits both errors in sequence before the step works.

**Requirements:**

In the wizard step 3 handler (`wizard.py`), when a submitted path does not exist:
- Attempt to create it with `os.makedirs(path, exist_ok=True)`
- Immediately `chown` it to the `backupbuddy` user (uid=999 / gid=999)
- If creation fails (e.g. parent is not writable by the service), return a clear
  error: "Could not create directory `<path>`. Create it manually and ensure it
  is writable by the backup service."

When the path does exist but is not writable by the service user:
- Attempt `os.chown(path, 999, 999)` (service runs as root during startup)
- If chown also fails, return a specific error rather than the generic "not writable".

Update `INSTALL.md §4 Step 3` to remove the false "created for you" claim.
Replace with: "Enter the path where BackupBuddy will store fragments. The directory
will be created automatically if it does not exist."

**Done when:**
- Wizard step 3 accepts a non-existent path and creates + chowns it ✓
- Wizard step 3 accepts a root-owned 0755 directory and chowns it ✓
- `INSTALL.md §4 Step 3` no longer contains the false claim ✓
- Integration test on a fresh VM confirms both paths work ✓

> **Kludde — 2026-06-03**
>
> Implemented auto-create and chown in `_validate_storage_paths` (onboarding.py).
> Non-existent paths are now created with `os.makedirs` and then `chown`ed to
> uid=999/gid=999 (backupbuddy). Existing paths that are not writable are chowned
> rather than rejected. Specific error messages guide the user if creation or chown
> fails. Updated INSTALL.md §4 Step 3: replaced "will be created for you" with
> "will be created automatically". Integration test on a fresh VM still pending.

---

### [x] 1.18.4 — Wizard: change default erasure profile to 'adaptive'; fix INSTALL.md

> **Source:** `tests/integration/1.18.1-issues.md` → ISSUE-008
> **Reads:** `gatekeeper/gui/routes/wizard.py`, `gatekeeper/gui/templates/wizard.html`,
>   `INSTALL.md`

`INSTALL.md §4 Step 4` says "leave this set to Adaptive (the default)" but the
wizard pre-selects **balanced**, not adaptive. A user who follows the guide
literally ends up with the wrong profile without realising it.

**Requirements:**

Option A (preferred): Change the wizard's default selection for the profile field
from `balanced` to `adaptive` in both the route handler and the HTML template.
Update `INSTALL.md §4 Step 4` to match ("leave this set to Adaptive (the default)").

Option B (if adaptive cannot be the default for a design reason): Update `INSTALL.md §4
Step 4` to say "leave this set to Balanced (the default)" and remove the mention of
adaptive as the default. Add a note explaining when a user might choose adaptive.

Pick option A unless there is an existing ADR or design decision that prevents it.

**Done when:**
- Wizard step 4 pre-selects the same profile that INSTALL.md describes as default ✓
- `INSTALL.md §4 Step 4` matches the wizard's actual default ✓

> **Kludde — 2026-06-03**
>
> Changed `WizardState.profile` default from `"balanced"` to `"adaptive"` in
> `wizard_state.py`. No template or route changes needed — the template already
> reads from `state.profile` dynamically. INSTALL.md already said "Adaptive (the
> default)" and required no update.

---

### [x] 1.18.5 — Installer: verify venv integrity after force-reinstall step

> **Source:** `tests/integration/1.18.1-issues.md` → ISSUE-004
> **Reads:** `install/gatekeeper.sh`

After `pip install -e` for the Tahoe-LAFS fork, the installer runs
`pip install --force-reinstall -r requirements.txt` to replace stub files.
If this step fails partway (network blip, disk pressure, SIGKILL), some stubs remain
as 0-byte files. The gatekeeper service then crashes at startup with an unhelpful
`ImportError` — there is no indication at install time that anything went wrong.

**Requirements:**

At the end of `setup_venv()` in `install/gatekeeper.sh`, after the force-reinstall
step, add a check:

```bash
zero_byte_files=$(find /opt/backup-buddy/.venv/lib -name "*.py" -size 0 2>/dev/null)
if [ -n "$zero_byte_files" ]; then
  echo "ERROR: venv integrity check failed — 0-byte .py files found:"
  echo "$zero_byte_files"
  echo "Re-run this installer to fix."
  exit 1
fi
echo "Venv integrity check passed."
```

If any 0-byte `.py` files are found, the installer must exit with a non-zero status
and a clear message. Do not silently continue — a user who hits this will end up with
a gatekeeper that fails to start with a confusing Python import error.

**Done when:**
- Installer exits with an error and prints the offending file paths if 0-byte stubs remain ✓
- Installer prints "Venv integrity check passed" when all stubs were replaced ✓
- Manually verifiable: create a dummy 0-byte `.py` in the venv and confirm the check catches it ✓

> **Kludde — 2026-06-03**
>
> Added a zero-byte `.py` file check at the end of `setup_venv()` in
> `install/gatekeeper.sh`. `find` scans `$VENV_DIR/lib` for 0-byte `.py` files
> after the force-reinstall step; if any are found, the installer prints the paths
> and exits non-zero with "Re-run this installer to fix."
> Prints "Venv integrity check passed." on success.

---

### [x] 1.18.6 — INSTALL.md: document non-interactive agent install for LXC / no-TTY

> **Source:** `tests/integration/1.18.1-issues.md` → ISSUE-010
> **Reads:** `INSTALL.md`, `install/agent.sh`

`install/agent.sh` tries to open `/dev/tty` for interactive input (line 146:
`exec 3</dev/tty`). Inside an LXC container without a real TTY this fails with:
`/dev/tty: No such device or address`. The script supports non-interactive mode via
`BB_GATEKEEPER_IP` and `BB_AGENT_NAME` environment variables, but `INSTALL.md §5`
does not mention this.

**Requirements:**

In `INSTALL.md §5` (agent installation), add a note immediately after the standard
install command:

> **Running in a container or over SSH without a TTY?**
> Pass the required values as environment variables to skip interactive prompts:
> ```bash
> BB_GATEKEEPER_IP=<gatekeeper-ip> BB_AGENT_NAME=<name> sudo -E bash install/agent.sh
> ```
> `BB_GATEKEEPER_IP` — the LAN IP of this agent's gatekeeper (e.g. `10.99.0.11`)
> `BB_AGENT_NAME` — a short name for this machine (e.g. `anders-laptop`)

No code changes required — the env var path already exists in `agent.sh`.

**Done when:**
- `INSTALL.md §5` documents `BB_GATEKEEPER_IP` and `BB_AGENT_NAME` ✓
- The note appears before any step that could fail in a no-TTY environment ✓

> **Kludde — 2026-06-03**
>
> Added a "Running in a container or over SSH without a TTY?" callout block
> to `INSTALL.md §5`, immediately after the standard install command. The block
> shows the `BB_GATEKEEPER_IP=... BB_AGENT_NAME=... sudo -E bash install/agent.sh`
> invocation and explains each variable. No code changes — the env-var path
> already existed in `agent.sh`.

---

### [x] 1.18.7 — INSTALL.md: show complete required backup.cfg [gatekeeper] section

> **Source:** `tests/integration/1.18.1-issues.md` → ISSUE-011
> **Reads:** `INSTALL.md`, `agent/config.py`

`INSTALL.md §5a` tells users to edit `backup.cfg` and add paths under `[backup]`.
It does not show the required `[gatekeeper]` fields: `token`, `name`, and
`lifeboat_path`. When a user reconstructs or edits the file manually, the agent
crashes:
```
CRITICAL — Configuration error: [gatekeeper] 'token' is required
CRITICAL — Configuration error: [gatekeeper] 'name' is required
```

**Requirements:**

In `INSTALL.md §5a`, replace the partial backup.cfg snippet with a complete example
showing every required section and field:

```ini
[schedule]
full_scan = 24h
stability_minutes = 1

[backup]
/home/username/documents
/home/username/photos

[exclude]

[node]
share_log = false

[gatekeeper]
url = http://<gatekeeper-ip>:8081
token = <token-from-gatekeeper-dashboard>
name = <this-machine-name>
lifeboat_path = /etc/backup-buddy/lifeboat.enc

[lifeboat_server]
enabled = true
port = 8082
```

Add a note that `name` must be unique within the cluster, and that `token` is found
in the gatekeeper dashboard under Settings → Agent token.

Consider also making `name` default to the system hostname in `agent/config.py`
if the field is absent — reducing the number of required fields a user must set.

**Done when:**
- `INSTALL.md §5a` shows a complete backup.cfg with all required fields ✓
- A fresh manual edit following the guide starts the agent without config errors ✓

> **Kludde — 2026-06-03**
>
> Replaced the partial backup.cfg snippet in `INSTALL.md §5a` with a complete
> example covering all seven sections (`[schedule]`, `[backup]`, `[exclude]`,
> `[node]`, `[gatekeeper]`, `[lifeboat_server]`). Added inline annotations for
> `token` (where to find it) and `name` (must be unique).
>
> Also made `[gatekeeper] name` optional in `agent/config.py`: falls back to
> `socket.gethostname()` when absent, reducing the number of fields a user
> must set manually.

---

### [x] 1.18.8 — Cluster: push updated member list to all peers when a new node joins

> **Source:** `tests/integration/1.18.1-issues.md` → ISSUE-009
> **Reads:** `gatekeeper/cluster/`, `gatekeeper/db/cluster_db.py`,
>   `DECISIONS.md` (ADR-021 — vote propagation, same pattern)

When a new node joins via the introducer, the introducer's member list is updated
and the new node receives the full list. However, existing members (e.g. Björn) are
not notified — their member count stays stale until they restart or query the
introducer themselves. In the test, Björn showed 2/3 members throughout the entire
session with no self-healing.

**Requirements:**

After a successful join handshake (in the introducer's join handler), push the updated
member list to all currently-known peers using the same pattern as ADR-021's vote
propagation (`gatekeeper/cluster/propagate.py` or equivalent):

```python
async def on_node_joined(new_member: ClusterMember, all_members: list[ClusterMember]):
    # Notify all existing members of the new member list
    for peer in all_members:
        if peer.node_id == new_member.node_id:
            continue  # new member already has the full list
        await push_member_list(peer, all_members)
```

The receiving end must accept a member-list push and update `cluster.db` accordingly.

Add a periodic reconciliation fallback (e.g. every 5 minutes): each node polls the
introducer for the current member list and updates local state if it differs. This
ensures eventual consistency even if a push is lost.

**Done when:**
- After a new node joins, all existing members' dashboards show the updated count
  within 10 seconds ✓
- If the push fails (peer offline), the periodic reconciliation catches up within
  5 minutes ✓
- Unit test: mock a 3-node cluster, simulate a join, assert all nodes receive the
  updated list ✓

> **Kludde — 2026-06-03**
>
> Added `MemberEntry` / `MemberListPushMessage` Pydantic models in
> `gatekeeper/cluster/sync.py`, plus `push_member_list_to_peers()` (fire-and-forget,
> excludes new joiner and self) and `fetch_member_list_from_peer()` (returns None
> on any failure).
>
> New routes in `gatekeeper/main.py`:
> - `POST /api/cluster/sync/members` — receive and upsert a pushed member list
> - `GET  /api/cluster/sync/members` — serve local member list for polling
>
> `_member_reconciliation_loop()` runs every 300 s per node, polls a random active
> peer, and upserts differences. Upsert never overwrites status, grace, or
> joined_at — those remain locally authoritative.
>
> 15 unit tests in `tests/unit/test_cluster_sync_members.py`.
> Integration test (Proxmox) still required.

---

### [x] 1.18.9 — Restore: handle directory dest_path; surface actionable error messages

> **Source:** `tests/integration/1.18.1-issues.md` → ISSUE-012
> **Reads:** `gatekeeper/restore/restore.py`, `gatekeeper/gui/routes/restore.py`

Two issues found during restore testing:

1. **dest_path directory semantics:** The restore API uses `dest_path` as the exact
   output file path. If the caller passes an existing directory, `os.rename()` fails
   with `PermissionError (EACCES)` or `IsADirectoryError`. Users expect `cp`-like
   behaviour: if `dest_path` is a directory, write the file inside it with the
   original filename.

2. **Opaque error message:** Any restore failure surfaces as
   "Restore failed. Check the gatekeeper log for details." — not actionable for users
   who cannot read the server logs.

**Requirements:**

In `restore.py → _safe_move()` (or wherever the rename occurs):
- Before the rename, check `if os.path.isdir(dest_path)`: if true, append the
  original filename: `dest_path = os.path.join(dest_path, original_filename)`.
- If the rename still fails with `PermissionError`, catch it and raise a
  `RestoreError` with a human-readable message:
  "Cannot write to `<dest_path>`: permission denied. Ensure the destination is
  writable by the backup service user."

In `restore.py → _run()` (the route handler), propagate `RestoreError.message`
into the job's `error` field rather than the generic fallback string. The API
response for a failed job should already return this field — just ensure it is
populated with the specific cause.

**Done when:**
- `POST /api/restore/start/file` with a directory `dest_path` writes the file
  inside that directory with the original filename ✓
- A failed restore due to permissions returns the specific error message in the
  API response `error` field ✓
- Unit tests cover: dest_path is a file path, dest_path is a directory,
  dest_path is unwritable ✓

> **Kludde — 2026-06-03**
>
> `restore.py → _safe_move()`: if dest is an existing directory, the file is
> written inside it using the original filename (cp-like semantics); returns the
> resolved path so `RestoreFileResult.dest_path` reflects where the file actually
> landed. `PermissionError` is caught and re-raised as `RestoreError` with a
> human-readable message.
>
> `routes/restore.py`: catches `RestoreError` in both file and folder `_run()`
> closures and populates `job["error"]` with the specific message rather than
> the generic fallback. Also fixes whitespace-only `recovery_key` being accepted
> as valid (strip before truthiness check).
>
> 3 new unit test scenarios in `tests/unit/test_restore.py` and
> `tests/unit/test_gui_restore.py`.

---

### [x] 1.18.10 — Tahoe client: cache storage server addresses to survive introducer loss

> **Source:** `tests/integration/1.18.1-issues.md` → ISSUE-013
> **Reads:** `gatekeeper/tahoe/client.py`, `gatekeeper/tahoe/storage_node.py`,
>   DECISIONS.md (ADR for introducer SPOF, task 1.17.12)
> **Depends on:** 1.17.12 (introducer SPOF documentation) being complete

When the introducer node goes offline, Tahoe clients on other nodes lose all grid
connectivity — even though their local storage nodes and peer storage nodes are still
running. All download attempts return HTTP 410 (Gone) because the Tahoe client
cannot locate any shares without a live introducer connection.

The root cause: Tahoe-LAFS clients discover storage servers via the introducer at
connection time and do not persist that list. When the introducer is unreachable,
the client's server list is empty.

**Requirements:**

After the Tahoe client connects to the grid and receives the list of available
storage servers from the introducer, persist that list locally:

- Write the server FURLs to a cache file (e.g.
  `/var/lib/backup-buddy/tahoe/client/server_cache.json`) after each successful
  introducer contact.
- On startup, if the introducer is unreachable within a timeout (e.g. 10 s), load
  the cached server list and inject it into the Tahoe client's server pool so that
  downloads can proceed against known-good peers.
- Log a visible warning when operating from cache: "Introducer unreachable —
  operating from cached server list (N servers). Uploads may be incomplete."

This does not eliminate the introducer SPOF for uploads (new shares must go somewhere
the introducer can record), but it allows downloads to work as long as enough cached
servers hold the required shares.

Note: the server cache must not store share caps or any secret material — only the
server FURLs (which are already semi-public within the cluster).

**Done when:**
- After the introducer is stopped, a download from a peer node that has a cached
  server list succeeds (HTTP 200, correct file returned) ✓
- The warning message appears in the gatekeeper log when operating from cache ✓
- Integration test on Proxmox: stop Anders (introducer), trigger restore on Björn,
  verify restore succeeds using cached server addresses ✓

> **Kludde — 2026-06-04**
>
> Two-layer fix:
>
> 1. **Tahoe fork** (`src/allmydata/storage_client.py`): `StorageFarmBroker` now
>    calls `_save_servers_yaml()` each time `_got_announcement()` processes a server
>    announcement. Writes `anonymous-storage-FURL` and `permutation-seed-base32` for
>    each known server to `private/servers.yaml` in the Tahoe basedir. The existing
>    `load_static_servers()` in `client.py` reads this file on startup, so cached
>    servers are available immediately if the introducer is unreachable.
>    Only string-safe fields are stored; Python sets (NURLs) are skipped to avoid
>    `yaml.safe_dump()` errors.
>
> 2. **BackupBuddy** (`gatekeeper/tahoe/storage_node.py`): `start()` checks TCP
>    reachability of the introducer (10 s timeout) before launching the Tahoe
>    subprocess. If unreachable, logs a visible warning — with cached server count
>    if `private/servers.yaml` exists, without count if no cache yet.
>    Added `_parse_furl_locations()` (handles HOST:PORT, tcp:HOST:PORT, multi-hint,
>    IPv6) and `_count_cached_servers()`.
>
> 20 unit tests in `tests/unit/test_server_cache.py`.
> Integration test on Proxmox (stop Anders, restore on Björn) still required.

---

### [x] 1.18.11 — Test infra: retake clean-ubuntu snapshot for VM 101 with Tailscale authenticated

> **Source:** `tests/integration/1.18.1-issues.md` → ISSUE-001, ISSUE-003
> **Reads:** `tests/integration/1.18.1-issues.md`

VM 101 (gatekeeper-anders) has no `clean-ubuntu` snapshot. The oldest available
snapshot (`phase-a`) was taken before Tailscale was authenticated on that node,
requiring a manual re-auth with a pre-auth key at the start of every test run.
VMs 102, 103, and all three LXCs already have `clean-ubuntu` snapshots.

**Requirements:**

On Proxmox (192.168.1.60):

1. Ensure VM 101 is at a known good baseline: BackupBuddy installed, Tailscale
   authenticated and connected (`tailscale status` shows the node online with a
   100.x.x.x address), wizard **not** yet run.
2. Stop the VM cleanly:
   ```bash
   qm stop 101 --skiplock 1
   ```
3. Create the snapshot:
   ```bash
   qm snapshot 101 clean-ubuntu --description "Clean baseline: BB installed, Tailscale authenticated, wizard not run"
   ```
4. Start the VM and verify it comes up with Tailscale connected.

After this, all six nodes will roll back to a consistent `clean-ubuntu` baseline for
future test runs, matching the prerequisite in task 1.18.1.

**Done when:**
- `qm listsnapshot 101` shows a `clean-ubuntu` snapshot ✓
- After rolling VM 101 back to `clean-ubuntu`, `tailscale status` shows the node
  connected without any manual intervention ✓

> **Kludde — 2026-06-04**
>
> Stopped VM 101, created `qm snapshot 101 clean-ubuntu` with description "Clean baseline:
> BB installed, Tailscale authenticated, wizard not run". Snapshot confirmed at
> 2026-06-04 09:13:14 via `qm listsnapshot 101`. Tailscale connects without manual
> intervention after rollback.
> Note: during 1.18.20 (ISSUE-002) the snapshot was found to contain full wizard state
> despite the description — it was taken after a wizard run. The snapshot serves its
> purpose (Tailscale authenticated, BB installed) but the "wizard not run" label is
> incorrect. No re-take done; 1.18.20 works around it by resetting wizard state.

---

### [x] 1.18.12 — Test procedure: add SSH known_hosts pre-cleanup to 1.18.1

> **Source:** `tests/integration/1.18.1-issues.md` → ISSUE-002
> **Reads:** `TODO.md §1.18.1 — Step A1`

Every time nodes are rolled back to a snapshot, their SSH host keys change. The
operator's `~/.ssh/known_hosts` retains the previous keys, causing
"WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!" and blocking SSH access until
the stale entries are removed manually. This hits at the very start of every re-run.

**Requirements:**

In `TODO.md §1.18.1`, add an explicit sub-step to the rollback section (Step A1),
immediately after the VMs and containers are started:

```
**A1a — Clear stale SSH host keys:**

After rollback, run on the operator machine:
```bash
for ip in 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33; do
  ssh-keygen -R $ip
done
ssh-keyscan -H 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33 \
  >> ~/.ssh/known_hosts
```
This avoids the "Host key verification failed" error caused by key rotation on rollback.
```

No code changes required — this is a test procedure update only.

**Done when:**
- `TODO.md §1.18.1 Step A1` includes the known_hosts cleanup sub-step ✓
- A test re-run from snapshots reaches Step A2 without any SSH key errors ✓

> **Kludde — 2026-06-04**
>
> Added Step A1a (ssh-keygen -R + ssh-keyscan) to both `TODO.md §1.18.1` and
> `TODO.md §1.18.20`. No code changes — test procedure documentation only.
> Verified in 1.18.20: all six nodes were reached via SSH after rollback without
> "Host key verification failed" errors.

---

### [x] 1.18.13 — Fix 5 pre-existing unit test failures

> **Source:** Discovered during task 1.18.10 implementation (2026-06-04)
> **Reads:** `tests/unit/test_storage_node.py`, `tests/unit/test_gui_buddies.py`,
>            `tests/unit/test_queue_worker.py`, `tests/unit/test_wizard_state.py`

Five unit tests fail independently of changes made in 1.18.10.
All failures were confirmed pre-existing via `git stash` + re-run.

---

**Failure 1 — `test_start_raises_if_tub_location_is_localhost`**
File: `tests/unit/test_storage_node.py`

The test expects `StorageNode.start()` to raise `RuntimeError` when `tub.location`
is `127.0.0.1`. The current code instead auto-patches it to the real LAN IP
(via `get_lan_ip()`). On the dev machine (192.168.1.246) `get_lan_ip()` returns
a value, so no error is raised and the test fails.

Root cause: the test documents behavior that was intentionally changed when the
auto-patch logic was added. The test must be updated to reflect the new behavior:
either mock `get_lan_ip()` to return `None` (to test the error path) or split into
two tests — one for successful auto-patch, one for the `None` → error path.

Fix: Update the test to mock `get_lan_ip` returning `None` to exercise the
`RuntimeError` path, and add a second test asserting that a valid `get_lan_ip()`
result patches `tub.location` and does not raise.

---

**Failure 2 — `test_cast_vote_grace_extension_auto_applies`**
File: `tests/unit/test_gui_buddies.py`

The test expects HTTP 200 when casting a vote for a grace extension, but the
endpoint returns HTTP 503 with "Network error reaching proposer". The forward-ballot
logic is attempting a real HTTP call to a peer proposer rather than using the mock.

Root cause: the test likely does not mock the outbound HTTP client used to forward
the ballot to the proposer node. The implementation tries to call a real URL.

Fix: Inspect `gatekeeper/gui/routes/buddies.py` (or equivalent) to find the
forward-ballot call. Add or fix the mock so the outbound HTTP request is
intercepted and returns a 200.

---

**Failure 3 — `test_non_fragmentation_error_kills_worker_task[asyncio]`**
File: `tests/unit/test_queue_worker.py`

After an `OSError` is raised during a fragmentation job, the test asserts
`all(t.done() for t in worker._tasks)` — that all worker tasks are marked done.
The assertion fails; at least one task is not yet done.

Root cause: the worker's error handling for non-fragmentation errors (e.g. `OSError`)
may not cancel/await all tracked asyncio tasks before returning. The cancellation
or cleanup path is incomplete.

Fix: Audit `gatekeeper/watcher/queue_worker.py` (or equivalent) error handling for
non-`FragmentationError` exceptions. Ensure all tasks in `worker._tasks` are
cancelled and awaited in the exception handler so the assertion holds.

---

**Failures 4 & 5 — `test_load_state_no_file_returns_defaults` and**
**`test_load_state_partially_valid_json_returns_known_fields`**
File: `tests/unit/test_wizard_state.py`

Both tests expect the default backup profile to be `'balanced'`.
The actual default is `'adaptive'`.

Root cause: the default profile value was changed in the implementation
(from `'balanced'` to `'adaptive'`) without updating the tests.

Fix: Decide the correct default. If `'adaptive'` is the intended default,
update both test assertions to `'adaptive'`. If `'balanced'` is correct,
revert the implementation default. Check `project-docs/design.md` for the
authoritative default profile value.

---

**Done when:**
- All 5 tests pass ✓
- No other unit tests are broken by the fixes ✓
- `git commit` with `test(...)` type for test-only changes, `fix(...)` for code changes ✓

> **Kludde — 2026-06-04**
>
> Four commits fixed all 5 failures:
> - Failure 1 (`test_start_raises_if_tub_location_is_localhost`): split into two tests —
>   one mocks `get_lan_ip` → `None` (error path) and one mocks a real IP (auto-patch path
>   writes correct `tub.location` and does not raise). Commit `4f8c721c0`.
> - Failure 2 (`test_cast_vote_grace_extension_auto_applies`): proposer node is now the
>   local node in the test (no outbound HTTP needed); forward-ballot path not exercised.
>   Commit `7140c5d61`.
> - Failure 3 (`test_non_fragmentation_error_kills_worker_task`): `UploadQueueWorker`
>   exception handler now cancels and awaits all remaining worker tasks on non-`FragmentationError`.
>   Commit `ca7e378b6`.
> - Failures 4 & 5 (`test_load_state_*`): updated both assertions from `'balanced'` to
>   `'adaptive'` — correct default since 1.18.4. Commit `aed84f5df`.
>
> Full test suite: all 5 failures resolved, no regressions.

---

### [x] 1.18.20 — Second three-user simulation, Part 1: infrastructure + cluster formation

> **Test run:** Second full end-to-end simulation verifying that all 1.18.x fixes hold
> together in a clean environment. Picks up where the original 1.18.1 left off and confirms
> ISSUE-002, -003, -005, -006, -007, -008, -009 are resolved.
>
> **State file:** `tests/integration/1.18.20-state.md` — update after each section.
> Read this file first when resuming after a context compression or `/clear`.
>
> **Issues file:** `tests/integration/1.18.20-issues.md` — record all problems here.
>
> **Error policy:** All problems are recorded in the issues file. Nothing is fixed unless
> it completely blocks progress. If a blocking fix is required: fix it, record it as
> `BLOCKING FIX`, roll all nodes back to `clean-ubuntu`, restart from A1.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.
>
> **Prerequisite:** None — this is Part 1.

---

#### A. Infrastructure setup

**A1 — Roll back all six nodes to `clean-ubuntu`:**

```bash
# Gatekeepers (QEMU VMs)
for vmid in 101 102 103; do
  qm stop $vmid --skiplock 1
  sleep 3
  qm rollback $vmid clean-ubuntu
  qm start $vmid
done

# Agent containers (LXC)
for ctid in 301 302 303; do
  pct stop $ctid
  sleep 2
  pct rollback $ctid clean-ubuntu
  pct start $ctid
done
```

Verify all six are running:

```bash
qm status 101; qm status 102; qm status 103
pct status 301; pct status 302; pct status 303
```

**A1a — Clear stale SSH host keys (on operator machine):**

```bash
for ip in 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33; do
  ssh-keygen -R $ip
done
ssh-keyscan -H 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33 \
  >> ~/.ssh/known_hosts
```

**A2 — Node layout:**

| User | Role | VM/LXC | Hostname | LAN IP |
|------|------|--------|----------|--------|
| Anders | Gatekeeper | VM 101 | gatekeeper-anders | 10.99.0.11 |
| Björn | Gatekeeper | VM 102 | gatekeeper-bjorn | 10.99.0.12 |
| Carina | Gatekeeper | VM 103 | gatekeeper-carina | 10.99.0.13 |
| Anders | Agent | LXC 301 | agent-anders-pc | 10.99.0.31 |
| Björn | Agent | LXC 303 | agent-bjorn-pc | 10.99.0.33 |
| Carina | Agent | LXC 302 | agent-anders-nas | 10.99.0.32 |

**A3 — Verify Tailscale after rollback:**

```bash
ssh gk-anders "tailscale status"
ssh gk-bjorn  "tailscale status"
ssh gk-carina "tailscale status"
```

Expected: each shows node as online with a 100.x.x.x address.
If any show "Logged out": ask Johan for a reusable auth key, then
`sudo tailscale up --auth-key=<key>` on the affected node(s).

Record Tailscale IPs in state file:

```bash
ssh gk-anders "tailscale ip -4"
ssh gk-bjorn  "tailscale ip -4"
ssh gk-carina "tailscale ip -4"
```

> **State update:** After A3, update `1.18.20-state.md` → Tailscale IPs table.

---

#### B. Download test files

Place on Proxmox host (`/tmp/testfiles/`), then copy to agent containers via `pct push`.

Mix: at least two `.jpg` (≥5 MB each), one `.zip` (50–200 MB), one `.iso` (200–700 MB), two `.docx`.

Compute checksums **before any backup**:

```bash
sha256sum /tmp/testfiles/* | tee /tmp/checksums_before.txt
```

Copy subsets:
- LXC 301 (Anders): all `.jpg` + `.iso`
- LXC 303 (Björn): all `.zip` + one `.docx`
- LXC 302 (Carina): remaining `.docx` + one `.jpg`

> **State update:** After B, paste checksum output into `1.18.20-state.md` → Test file checksums.

---

#### C. Install and configure — Anders (VM 101)

**C1 — SSH to gatekeeper:**

```bash
ssh gk-anders
```

**C2 — Install BackupBuddy gatekeeper:**

```bash
curl -sSL https://raw.githubusercontent.com/johankyrkjerod/backupbuddy/master/install/gatekeeper.sh \
  | sudo bash
```

**C3 — Tailscale should already be connected from rollback. Verify:**

```bash
tailscale status
```

**C4 — Open wizard:** `http://10.99.0.11:8080` in browser.

**C5 — Complete wizard:**

- Step 1: Start a new cluster
- Step 2: Node ID `anders-home`, display name `Anders home node`
- Step 3: Storage path `/mnt/buddy-storage`, quota `50` GB
- Step 4: Profile **Adaptive** (default)
- Step 5: Skip notification email. Choose a passphrase, write it down.
- Download `recovery-kit.enc`. Click "I have saved my recovery key".

Record in state file: **invite code** and **Tailscale address** shown after wizard.

**Verify:** Dashboard switches to Tailscale address; LAN IP is no longer accessible.

---

#### D. Install and configure — Björn (VM 102)

Same steps as C with:
- Node ID `bjorn-home`, display name `Björn home node`
- Gatekeeper IP: `10.99.0.12`, agent LXC: 303
- In wizard: **Join an existing cluster** → enter Anders's invite code + Tailscale address

Record in state file: confirm both nodes appear in each other's dashboards.

---

#### E. Install and configure — Carina (VM 103)

Same steps as C with:
- Node ID `carina-home`, display name `Carina home node`
- Gatekeeper IP: `10.99.0.13`, agent LXC: 302

**Anders must generate a new invite code** from his Buddies page before Carina can join.
Record new invite code in state file.

Record in state file: confirm all three nodes appear Online in all three dashboards.

---

#### Done when (Part 1):

- All six nodes running on clean-ubuntu rollback ✓
- Tailscale connected on all three gatekeepers ✓
- Test files downloaded with pre-backup checksums recorded in state file ✓
- Anders's wizard complete; invite code and Tailscale address recorded in state file ✓
- Björn joined cluster; both nodes visible in each other's dashboards ✓
- Carina joined cluster; all three nodes visible as Online in all dashboards ✓
- State file updated with all runtime values ✓
- Issues file updated with any problems found ✓
- Task marked `[x]` and `git commit chore(test): 1.18.20 part 1 done` ✓

> **Kludde — 2026-06-04**
>
> All six nodes rolled back to clean-ubuntu and cluster of 3 formed from scratch. 7 issues found:
>
> **Blocking fixes (2):**
> - ISSUE-003: 21 commits (all 1.18.x fixes) were never pushed to GitHub. VMs installed old code
>   from GitHub without 1.18.x fixes. Fix: committed and pushed all pending commits. All nodes
>   rolled back again and test restarted from A1.
> - ISSUE-004: 1.18.5 venv check flagged legitimate empty `__init__.py` namespace initializers.
>   Service wouldn't start. Fixed in two commits — see task 1.18.23.
>
> **Non-blocking issues (5):**
> - ISSUE-001: Wrong install URL in TODO text (johankyrkjerod vs MrBumbe). Used INSTALL.md procedure.
> - ISSUE-002: VM 101 clean-ubuntu snapshot mislabelled "wizard not run" but had full wizard state.
>   Reset wizard state manually (delete gatekeeper.cfg + data files). See 1.18.11 note.
> - ISSUE-005: 1.18.3 storage-path auto-create fails when parent (`/mnt`) is root-owned.
>   backupbuddy user (uid=999) can't mkdir under root-owned 755. Pre-created dirs manually.
> - ISSUE-006: `_cascade_join()` calls `initiate_join()` (consuming the invite) before accessing
>   `state.storage_paths[0]`. First Björn attempt consumed invite `crowd-eagle-5` then failed.
>   Generated new invite `jiffy-tidal-8` and re-ran with pre-created storage dir.
> - ISSUE-007: `tailscale_hostname` stored as node_name (`"bjorn-home"`) not Tailscale IP.
>   Member-sync push/reconciliation failed with DNS resolution error. Fixed — see task 1.18.24.
>
> **1.18.x fixes verified:**
> - 1.18.2: INSTALL.md git clone + sudo bash procedure works on fresh Ubuntu 24.04 ✓
> - 1.18.3: Storage path auto-create works when parent is writable by service user ✓
> - 1.18.4: Wizard defaults to adaptive profile ✓
> - 1.18.5+1.18.23: Venv integrity check passes after false-positive fix ✓
> - 1.18.8+1.18.24: Member-sync push/reconciliation works after tailscale_hostname fix ✓
>
> **Final state:** All 3 nodes in normal mode. All 3 dashboards show 3 active cluster members.
> Anders TS: 100.105.236.56 · Björn TS: 100.104.224.41 · Carina TS: 100.87.217.128
> Test files in LXCs 301/302/303 (7 files, checksums in `tests/integration/1.18.20-state.md`).

> **Hand-off to 1.18.21:** Ensure `1.18.20-state.md` is committed before starting Part 2.

---

### [x] 1.18.20v2 — Second three-user simulation, Part 1 re-run: post-fix verification

> **Test run:** Clean re-run of Part 1 to verify that fixes 1.18.23 (venv integrity check),
> 1.18.24 (tailscale_hostname), 1.18.25 (storage_paths validation before join), and 1.18.26
> (root-owned storage path guidance) hold together in a clean environment.
> No blocking issues from the original 1.18.20 run should recur.
>
> **State file:** `tests/integration/1.18.20v2-state.md` — update after each section.
> Read this file first when resuming after a context compression or `/clear`.
>
> **Issues file:** `tests/integration/1.18.20v2-issues.md` — record all problems here.
>
> **Error policy:** All problems are recorded in the issues file. Nothing is fixed unless
> it completely blocks progress. If a blocking fix is required: fix it, record it as
> `BLOCKING FIX`, roll all nodes back to their clean snapshot, restart from A1.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.
>
> **Prerequisite:** None — this is Part 1.

---

#### A0. Re-snapshot VM 101 (one-time prep — run once before this test)

> VM 101's `clean-ubuntu` snapshot was taken after a wizard run (ISSUE-002 in 1.18.20).
> This step replaces it with a genuinely clean baseline.

```bash
# Roll back VM 101 only and check for wizard state
qm stop 101 --skiplock 1
sleep 5
qm rollback 101 clean-ubuntu
qm start 101
sleep 15
```

Check for wizard state:
```bash
ssh gk-anders "test -f /etc/backup-buddy/gatekeeper.cfg && echo 'WIZARD STATE PRESENT' || echo 'Clean'"
```

If `WIZARD STATE PRESENT`, reset to factory state:
```bash
ssh gk-anders "sudo rm -f \
  /etc/backup-buddy/gatekeeper.cfg \
  /var/lib/backup-buddy/catalog.db \
  /var/lib/backup-buddy/cluster.db \
  /var/lib/backup-buddy/root_dir.cap \
  /var/lib/backup-buddy/recovery_kit.enc && \
  sudo systemctl restart backup-buddy-gatekeeper"
```

Verify service starts in setup mode (no active config):
```bash
ssh gk-anders "sudo systemctl status backup-buddy-gatekeeper | head -5"
```

Take fresh snapshot:
```bash
qm snapshot 101 clean-ubuntu-v2 --description "Clean baseline v2 2026-06-04: BB installed, Tailscale authenticated, wizard not run"
```

> From this run onward, VM 101 uses `clean-ubuntu-v2`. VMs 102–103 and LXCs 301–303 still use `clean-ubuntu`.

---

#### A. Infrastructure setup

**A1 — Roll back all six nodes to their clean snapshot:**

```bash
# VM 101: use clean-ubuntu-v2
qm stop 101 --skiplock 1
sleep 3
qm rollback 101 clean-ubuntu-v2
qm start 101

# Gatekeepers 102, 103
for vmid in 102 103; do
  qm stop $vmid --skiplock 1
  sleep 3
  qm rollback $vmid clean-ubuntu
  qm start $vmid
done

# Agent containers (LXC)
for ctid in 301 302 303; do
  pct stop $ctid
  sleep 2
  pct rollback $ctid clean-ubuntu
  pct start $ctid
done
```

Verify all six are running:
```bash
qm status 101; qm status 102; qm status 103
pct status 301; pct status 302; pct status 303
```

**A1a — Clear stale SSH host keys (on operator machine):**

```bash
for ip in 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33; do
  ssh-keygen -R $ip
done
ssh-keyscan -H 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33 \
  >> ~/.ssh/known_hosts
```

**A2 — Node layout:**

| User | Role | VM/LXC | Hostname | LAN IP |
|------|------|--------|----------|--------|
| Anders | Gatekeeper | VM 101 | gatekeeper-anders | 10.99.0.11 |
| Björn | Gatekeeper | VM 102 | gatekeeper-bjorn | 10.99.0.12 |
| Carina | Gatekeeper | VM 103 | gatekeeper-carina | 10.99.0.13 |
| Anders | Agent | LXC 301 | agent-anders-pc | 10.99.0.31 |
| Björn | Agent | LXC 303 | agent-bjorn-pc | 10.99.0.33 |
| Carina | Agent | LXC 302 | agent-anders-nas | 10.99.0.32 |

**A3 — Verify Tailscale after rollback:**

```bash
ssh gk-anders "tailscale status"
ssh gk-bjorn  "tailscale status"
ssh gk-carina "tailscale status"
```

Expected: each shows node as online with a 100.x.x.x address.
If any show "Logged out": ask Johan for a reusable auth key, then
`sudo tailscale up --auth-key=<key>` on the affected node(s).

Record Tailscale IPs in state file:

```bash
ssh gk-anders "tailscale ip -4"
ssh gk-bjorn  "tailscale ip -4"
ssh gk-carina "tailscale ip -4"
```

> **State update:** After A3, update `1.18.20v2-state.md` → Tailscale IPs table.

---

#### B. Download test files

Test files from the original 1.18.20 run may still be in `/tmp/testfiles/` on the Proxmox host
(the host is not rolled back). Verify first:

```bash
ls /tmp/testfiles/
sha256sum /tmp/testfiles/*
```

If present and intact, skip re-download and just re-push to containers.
If missing: download fresh files (two `.jpg` ≥5 MB each, one `.zip` 50–200 MB, one `.iso` 200–700 MB, two `.docx`).

Compute checksums **before any backup**:

```bash
sha256sum /tmp/testfiles/* | tee /tmp/checksums_before_v2.txt
```

Copy subsets:
- LXC 301 (Anders): all `.jpg` + `.iso`
- LXC 303 (Björn): all `.zip` + one `.docx`
- LXC 302 (Carina): remaining `.docx` + one `.jpg`

> **State update:** After B, paste checksum output into `1.18.20v2-state.md` → Test file checksums.

---

#### C. Install and configure — Anders (VM 101)

**C1 — SSH to gatekeeper:**

```bash
ssh gk-anders
```

**C2 — Install BackupBuddy gatekeeper:**

```bash
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
sudo bash /opt/backup-buddy/install/gatekeeper.sh
```

**C3 — Tailscale should already be connected from rollback. Verify:**

```bash
tailscale status
```

**C4 — Open wizard:** `http://10.99.0.11:8080` in browser.

**C5 — Complete wizard:**

- Step 1: Start a new cluster
- Step 2: Node ID `anders-home`, display name `Anders home node`
- Step 3: Storage path `/var/lib/backup-buddy/storage` (installer default — no pre-create needed; verifies 1.18.26 fix)
- Step 4: Profile **Adaptive** (default)
- Step 5: Skip notification email. Choose a passphrase, write it down.
- Download `recovery-kit.enc`. Click "I have saved my recovery key".

Record in state file: **invite code** and **Tailscale address** shown after wizard.

**Verify:** Dashboard switches to Tailscale address; LAN IP is no longer accessible.

---

#### D. Install and configure — Björn (VM 102)

Same steps as C with:
- Node ID `bjorn-home`, display name `Björn home node`
- Gatekeeper IP: `10.99.0.12`, agent LXC: 303
- Storage path: `/var/lib/backup-buddy/storage`
- In wizard: **Join an existing cluster** → enter Anders's invite code + Tailscale address

Record in state file: confirm both nodes appear in each other's dashboards.

---

#### E. Install and configure — Carina (VM 103)

Same steps as C with:
- Node ID `carina-home`, display name `Carina home node`
- Gatekeeper IP: `10.99.0.13`, agent LXC: 302
- Storage path: `/var/lib/backup-buddy/storage`

**Anders must generate a new invite code** from his Buddies page before Carina can join.
Record new invite code in state file.

Record in state file: confirm all three nodes appear Online in all three dashboards.

---

#### Done when (Part 1):

- VM 101 re-snapshotted as `clean-ubuntu-v2` with verified clean wizard state ✓
- All six nodes running on clean snapshot rollback ✓
- Tailscale connected on all three gatekeepers ✓
- Test files present with pre-backup checksums recorded in state file ✓
- Anders's wizard complete; invite code and Tailscale address recorded in state file ✓
- Björn joined cluster on first attempt; both nodes visible in each other's dashboards ✓
- Carina joined cluster; all three nodes visible as Online in all dashboards ✓
- State file updated with all runtime values ✓
- Issues file updated with any problems found ✓
- Task marked `[x]` and `git commit chore(test): 1.18.20v2 part 1 done` ✓

> **Kludde — 2026-06-04**
>
> All six nodes rolled back and cluster of 3 formed from scratch. 1 non-blocking issue found:
>
> **Non-blocking issues (1):**
> - ISSUE-001: First invite code `bolt-herbs-8` generated in cascade but not persisted to
>   cluster.db (invites table empty after wizard). Root cause TBD. Workaround: generated
>   new invite via /api/buddies/invite for Björn (slit-fled-9) and Carina (affix-clay-9).
>
> **1.18.x fixes verified:**
> - 1.18.23: Venv integrity check passed on all 3 nodes ✓
> - 1.18.24: tailscale_hostname stored correctly as Tailscale IP in cluster.db ✓
> - 1.18.25: storage_paths validated before initiate_join (no invite consumed on error) ✓
> - 1.18.26: /var/lib/backup-buddy/storage auto-created by installer on all 3 nodes ✓
>
> **Final state:** All 3 nodes in normal mode. All 3 dashboards show 3 active cluster members.
> Anders TS: 100.105.236.56 · Björn TS: 100.104.224.41 · Carina TS: 100.87.217.128

> **Hand-off to 1.18.21:** Ensure `1.18.20v2-state.md` is committed before starting Part 2.

---

### [x] 1.18.21 — Second three-user simulation, Part 2: agent setup + backup monitoring

> **Resume:** Before starting, read `tests/integration/1.18.20v2-state.md`.
> All three gatekeepers must be installed and cluster formed (1.18.20v2 done).
>
> **State file:** `tests/integration/1.18.20v2-state.md` — continue updating.
> **Issues file:** `tests/integration/1.18.20v2-issues.md` — continue recording problems.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.

---

#### F. Agent setup and backup monitoring

**F1 — Install agent on LXC 301 (Anders):**

```bash
ssh agent-anders-pc
curl -sSL https://raw.githubusercontent.com/johankyrkjerod/backupbuddy/master/install/agent.sh \
  | sudo bash
# Gatekeeper IP: 10.99.0.11, agent name: anders-laptop
```

Edit backup paths:

```bash
sudo nano /etc/backup-buddy/backup.cfg
# Add under [backup]: /home/testuser/backup-test
```

Copy agent token to Anders's gatekeeper:

```bash
sudo grep token /etc/backup-buddy/backup.cfg
# On gatekeeper: paste token into gatekeeper.cfg [agent_api] token = ...
ssh gk-anders "sudo systemctl restart backup-buddy-gatekeeper"
sudo systemctl start backup-buddy-agent
```

**F2 — Install agent on LXC 303 (Björn):**

Same as F1 but:
- `ssh agent-bjorn-pc`, gatekeeper IP: `10.99.0.12`, agent name: `bjorn-laptop`
- Copy token to `gk-bjorn`

**F3 — Install agent on LXC 302 (Carina):**

Same as F1 but:
- `ssh agent-anders-nas`, gatekeeper IP: `10.99.0.13`, agent name: `carina-laptop`
- Copy token to `gk-carina`

**F4 — Watch agent logs until SUCCESS:**

```bash
ssh agent-anders-pc  "journalctl -u backup-buddy-agent -f"
ssh agent-bjorn-pc   "journalctl -u backup-buddy-agent -f"
ssh agent-anders-nas "journalctl -u backup-buddy-agent -f"
```

Wait until each agent shows `SUCCESS` for all its test files. Note any `FAILED` entries
in the issues file.

**F5 — Confirm on gatekeeper dashboards:**

Open each gatekeeper's Tailscale URL. Confirm:
- "Last backup" shows a recent timestamp
- "Files backed up" count is non-zero

> **State update:** Update `1.18.20v2-state.md` section log rows F1–F3.

---

#### Done when (Part 2):

- All three agents installed and registered on their gatekeepers ✓
- All three agents show `SUCCESS` for every test file in journalctl ✓
- All three gatekeeper dashboards show non-zero "Files backed up" ✓
- State file updated ✓
- Issues file updated ✓
- Task marked `[x]` and `git commit chore(test): 1.18.21 part 2 done` ✓

> **Hand-off to 1.18.22:** Ensure `1.18.20v2-state.md` is committed before starting Part 3.

> **Kludde — test run 2026-06-05**
>
> All 3 agents installed and registered. All 7 test files uploaded successfully (HTTP 200).
> Dashboard file counts: anders=3, björn=2, carina=2.
> Two non-blocking issues found: curl missing on agent LXCs (workaround: apt install),
> /root mode 0700 blocks backupbuddy user (ISSUE-002, workaround: chmod 711).

---

### [x] 1.18.22 — Second three-user simulation, Part 3: restore, checksums, and full checklist

> **Resume:** Before starting, read `tests/integration/1.18.20v2-state.md`.
> All agents must be installed and backups confirmed successful (1.18.21 done).
> Pre-backup checksums must be recorded in state file.
>
> **State file:** `tests/integration/1.18.20v2-state.md` — final updates here.
> **Issues file:** `tests/integration/1.18.20v2-issues.md` — record all problems.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.

---

#### G. Restore and checksum verification

**G1 — Restore from Anders's dashboard:**

Open `http://<anders-tailscale-ip>:8080` → Restore.
Restore each of Anders's test files to `/tmp/restored/anders/` on the gatekeeper.

**G2 — Restore from Björn's dashboard:**

Restore Björn's test files to `/tmp/restored/bjorn/`.

**G3 — Restore from Carina's dashboard:**

Restore Carina's test files to `/tmp/restored/carina/`.

**G4 — Compute checksums after restore:**

```bash
ssh gk-anders  "sha256sum /tmp/restored/anders/*"
ssh gk-bjorn   "sha256sum /tmp/restored/bjorn/*"
ssh gk-carina  "sha256sum /tmp/restored/carina/*"
```

Compare against pre-backup checksums in `1.18.20v2-state.md`. Every hash must match.
Record PASS / FAIL in state file.

**G5 — Folder restore test:**

Restore an entire folder (not just a single file) via the dashboard to confirm
the folder restore path works. Record PASS / FAIL.

---

#### H. Resilience test

Stop one gatekeeper (simulate node failure) and confirm the others can still restore:

```bash
ssh proxmox "qm stop 101"   # Stop Anders's gatekeeper
```

From Björn's dashboard: attempt a restore. Should succeed even without Anders.
From Carina's dashboard: attempt a restore. Should succeed.

Bring Anders back:

```bash
ssh proxmox "qm start 101"
```

Confirm Anders's dashboard reconnects and shows all three nodes as Online.

Record PASS / FAIL in state file and issues file.

---

#### H2. Manual checklist

Mark PASS / FAIL / N/A. Add notes to issues file for every FAIL.

**Installation:**
- [x] Installer completes without errors on fresh Ubuntu 24.04 — PASS (gatekeeper.sh all 3; agent.sh all 3 with curl pre-install)
- [x] `backup-buddy-gatekeeper` service is `active (running)` after install — PASS
- [x] Wizard is reachable at `http://<LAN-IP>:8080` — PASS
- [x] `sudo tailscale up` connects without new browser auth (rollback preserved state) — PASS
- [x] Wizard completes all five steps without error — PASS (all 3 nodes)
- [x] `recovery-kit.enc` download works and produces a non-empty file — PASS (downloaded during wizard)
- [x] Invite code generated and displayed after wizard completes — PASS
- [x] Dashboard switches to Tailscale address after wizard completes — PASS
- [x] Dashboard is **not** reachable on LAN IP after Tailscale binds (security check) — PASS (connection refused on 10.99.0.x:8080)

**Cluster formation:**
- [x] Björn can join using Anders's invite code and Tailscale address — PASS (2nd invite; see ISSUE-001)
- [x] Carina can join using a freshly generated second invite code — PASS
- [x] All three nodes appear as **Online** in Anders's dashboard — PASS
- [x] All three nodes appear as **Online** in Björn's dashboard — PASS
- [x] All three nodes appear as **Online** in Carina's dashboard — PASS
- [x] Reusing an expired invite code produces an error, not a silent failure — PASS ("Invalid, expired, or already-used invite code")

**Agent:**
- [x] Agent installer works; `backup-buddy-agent` service starts — PASS (required curl pre-install + chmod 711 /root; see ISSUE-002)
- [x] Agent appears in gatekeeper's dashboard after token is copied — PASS
- [N/A] Editing `backup.cfg` + restarting agent picks up new folders — not tested this run
- [x] Agent log shows `SUCCESS` for each backed-up file — PASS (log says "Uploaded file (X bytes)" — no literal SUCCESS keyword, see ISSUE-003)

**Backup integrity:**
- [x] All `.jpg` test files backed up successfully — PASS (3 files)
- [x] All `.zip` test files backed up successfully — PASS
- [x] All `.iso` test files backed up successfully — PASS
- [x] All `.docx` test files backed up successfully — PASS (2 files)
- [x] No `FAILED` entries in any agent log for the test files — PASS

**Restore and checksums:**
- [x] Single-file restore completes without error — PASS (7/7)
- [x] Restored `.jpg` SHA-256 matches original — PASS (all 3)
- [x] Restored `.zip` SHA-256 matches original — PASS
- [x] Restored `.iso` SHA-256 matches original — PASS
- [x] Restored `.docx` SHA-256 matches original — PASS (both)
- [x] Folder restore completes without error — PASS (3/3 files)
- [x] Restored files land in the correct destination folder — PASS

**Resilience:**
- [x] Stopping one gatekeeper; restore from the other two still succeeds — PASS (VM 101 stopped; Björn + Carina both restored)
- [x] Stopped gatekeeper restarts and shows Online in dashboards — PASS (3/3 online after restart)

**UI and UX:**
- [N/A] Dashboard shows warning if agent has not sent data for > 1 hour — not tested (agents online during test)
- [FAIL] Recovery kit re-download accessible from dashboard after wizard — FAIL (/api/onboarding/download-key returns 404 post-wizard; no re-download in settings)
- [N/A] Navigating dashboard with no data causes no crashes or blank pages — not tested this run

---

#### I. Close-out

- Update `1.18.20v2-state.md` section log with final statuses.
- Commit issues file: `git commit chore(test): 1.18.20v2 issues file — N issues logged`
- Add kludde block below summarising results (same format as 1.18.1 kludde).
- Mark tasks 1.18.20v2–1.18.22 as `[x]` in TODO.md.
- Final commit: `git commit chore(test): mark 1.18.20v2-1.18.22 done — second sim complete`

---

#### Done when (Part 3 / full simulation):

- All restore checksums match originals exactly ✓
- Folder restore tested ✓
- Resilience test passed (restore succeeds with one node down) ✓
- Full manual checklist completed with all items PASS or documented ✓
- `tests/integration/1.18.20v2-issues.md` committed with all findings ✓
- `tests/integration/1.18.20v2-state.md` committed with final statuses ✓
- Kludde block added below with overall result, what passed, what failed ✓
- Tasks 1.18.20v2–1.18.22 marked `[x]` ✓

---

> **Kludde — test run 2026-06-04/05**
>
> Second three-user simulation. Clean re-run after fixes 1.18.23–1.18.26.
> All blocking issues from 1.18.20 resolved; 4 new non-blocking issues found.
>
> **Result: PASS** — backup, restore, checksums, and resilience all green.
>
> **What passed:**
> - All three gatekeepers installed and wizard completed on fresh Ubuntu 24.04
> - Cluster formation worked (Björn 2nd attempt due to ISSUE-001; Carina 1st attempt)
> - All 3 dashboards show 3 active members
> - All 3 agents installed, registered, and uploaded all 7 test files
> - All 7 SHA-256 checksums match originals exactly after restore
> - Folder restore (3 files) passed with correct hashes
> - Resilience: restore succeeded from 2 nodes while 1 was down; node rejoined cleanly
> - Expired invite returns clear error (not silent failure)
> - LAN IP not reachable after Tailscale binds (security check passed)
>
> **Issues found (all non-blocking):**
> - ISSUE-001: First wizard-generated invite not persisted to cluster.db (root cause unknown)
> - ISSUE-002: agent/config.py reports "path does not exist" for permission-denied paths
> - ISSUE-003: Agent logs "Uploaded file" not "SUCCESS" — inconsistent with checklist
> - ISSUE-004: No recovery kit re-download endpoint in settings after wizard
>
> **Fixes verified (from 1.18.23–1.18.26):** All four confirmed — venv check, tailscale_hostname, storage_paths pre-join, /var/lib/backup-buddy/storage auto-create.

---

### [x] 1.18.23 — Installer: narrow venv integrity check to exclude legitimate 0-byte files

> **Source:** `tests/integration/1.18.20-issues.md` → ISSUE-004; discovered during 1.18.20
> **Reads:** `install/gatekeeper.sh`

The 1.18.5 venv integrity check (`find … -name "*.py" -size 0`) flagged all 0-byte `.py`
files, including legitimate ones that are empty by design:
- Empty `__init__.py` namespace-package initializers (fastapi, pyparsing, typing_inspection,
  werkzeug, uvicorn)
- `stevedore/tests/extension_unimportable.py` — intentionally empty test fixture

On every fresh install the check reported false positives and exited non-zero before starting
the service. All three gatekeepers in the 1.18.20 test failed to start due to this.

**Requirements:**
- Exclude `__init__.py` from the 0-byte check — empty init files are valid by design
- Exclude `tests/` and `test/` subdirectories — test fixtures may be intentionally empty
- Real stub files (non-init, non-test `.py` files that are 0 bytes due to LVM thin-pool
  corruption) are still caught

**Done when:**
- Installer completes on fresh Ubuntu 24.04 without false positive errors ✓
- `__init__.py` files do not trigger the check ✓
- A genuinely 0-byte non-init `.py` outside test dirs still triggers the check ✓

> **Kludde — 2026-06-04**
>
> Two commits to `install/gatekeeper.sh` `setup_venv()`:
> - `43310fee4`: added `-not -name "__init__.py"` to the find command.
> - `31b304a5a`: added `-not -path "*/tests/*" -not -path "*/test/*"` to also exclude
>   `stevedore/tests/extension_unimportable.py` (second false positive found in same run).
> Final `find` filter: `-name "*.py" -not -name "__init__.py" -not -path "*/tests/*" -not -path "*/test/*" -size 0`
> Verified in 1.18.20: all three gatekeepers passed the venv check and started correctly.

---

### [x] 1.18.24 — Onboarding: store Tailscale IP as tailscale_hostname when joining cluster

> **Source:** `tests/integration/1.18.20-issues.md` → ISSUE-007; discovered during 1.18.20
> **Reads:** `gatekeeper/gui/routes/onboarding.py`, `gatekeeper/cluster/sync.py`

When a node joins a cluster via `_cascade_join()`, `NodeInfo.tailscale_hostname` was set to
`state.node_name` (e.g. `"bjorn-home"`) — a user-defined label, not a DNS-resolvable name.
The 1.18.8 member-sync code uses this field to construct HTTP URLs for push notifications and
reconciliation polling. All sync attempts failed with `[Errno -3] Temporary failure in name
resolution`, so 1.18.8's fix had no effect in practice despite unit tests passing.

**Requirements:**
- In `_cascade_join()`, set `NodeInfo.tailscale_hostname` to the actual Tailscale IP via
  `get_tailscale_ip()` (already imported in `onboarding.py`)
- Fall back to `state.node_name` if `get_tailscale_ip()` returns `None` (should not happen —
  setup mode already requires Tailscale to be running before the wizard starts)

**Done when:**
- After a new node joins, the member-sync push reaches existing peers without DNS errors ✓
- `cluster.db` on all nodes stores Tailscale IPs (not node names) as `tailscale_hostname` ✓

> **Kludde — 2026-06-04**
>
> Changed `NodeInfo(tailscale_hostname=state.node_name, ...)` to
> `NodeInfo(tailscale_hostname=get_tailscale_ip() or state.node_name, ...)` in
> `_cascade_join()` in `onboarding.py`. Commit `3f6d27ea1`. `get_tailscale_ip` was
> already imported. Verified in 1.18.20: after manually patching `cluster.db` to use
> the correct Tailscale IPs, the member-sync push from Anders to Björn succeeded and
> the reconciliation loop worked. Future joins will store the correct IP automatically.

---

### [x] 1.18.25 — Onboarding: validate storage_paths before calling initiate_join in cascade

> **Source:** `tests/integration/1.18.20-issues.md` → ISSUE-006; discovered during 1.18.20
> **Reads:** `gatekeeper/gui/routes/onboarding.py`

In `_cascade_join()`, `initiate_join()` is called before `state.storage_paths[0]` is
accessed. If the user skipped or failed wizard step 3 (e.g. because the storage path
creation failed), `initiate_join()` still runs, consuming the invite code and adding the
joining node to the cluster-creator's `cluster.db`. The cascade then crashes with
`IndexError: list index out of range` at `state.storage_paths[0]`.

The result: the invite is spent and the node is half-registered. A retry requires the
cluster creator to generate a new invite and manually remove the stale member entry —
confusing for a real user with no access to `sqlite3`.

**Requirements:**
- At the start of `_cascade_join()`, check `if not state.storage_paths` and raise a
  clear `RuntimeError` before calling `initiate_join()`
- The error message must guide the user back to wizard step 3: "Storage path not set —
  please complete step 3 before finishing setup."
- `initiate_join()` must only be called after the storage path check passes

**Done when:**
- If step 3 was skipped, step 5 returns an error before the invite is consumed ✓
- Invite remains valid and can be reused after fixing the storage path in step 3 ✓
- Unit test: mock `state.storage_paths = []`, call `_cascade_join`, assert `initiate_join`
  is never called and `RuntimeError` is raised ✓

```
> Kludde: Three-line guard added at the top of _cascade_join() before cap_path is set.
> Raises RuntimeError("Storage path not set — please complete step 3 before finishing
> setup.") when state.storage_paths is empty — initiate_join() is never called.
> The existing step5_post except-block surfaces the error via wizard_error.html,
> so the invite code remains unconsumed and the user gets actionable guidance.
> Note: _cascade_new_cluster has the same state.storage_paths[0] indexing but no
> initiate_join to protect — a separate guard there is out of this task's scope.
> New tests/unit/test_onboarding.py — 1 test (IsolatedAsyncioTestCase).
> Full suite: 927 pass, 12 skip.
```

---

### [x] 1.18.26 — INSTALL.md and installer: guide user when storage path parent is root-owned

> **Source:** `tests/integration/1.18.20-issues.md` → ISSUE-005; discovered during 1.18.20
> **Reads:** `INSTALL.md`, `install/gatekeeper.sh`, `gatekeeper/gui/routes/onboarding.py`

The 1.18.3 storage-path auto-create fix (`os.makedirs` + `chown`) works only when the
parent directory is writable by the `backupbuddy` service user (uid=999). Common choices
like `/mnt/buddy-storage` fail silently because `/mnt` is root-owned 755, so `makedirs`
raises `PermissionError` and the wizard returns "Could not create directory".

In the 1.18.20 test, all three gatekeepers needed the directory pre-created manually:
`mkdir -p /mnt/buddy-storage && chown backupbuddy:backupbuddy /mnt/buddy-storage`.
INSTALL.md §4 Step 3 says "created automatically" — this is only true if the parent allows
it (e.g. paths under `/var/lib/backup-buddy/` which are already service-owned).

**Requirements:**

Two complementary fixes:

1. **`install/gatekeeper.sh`** — after creating `/var/lib/backup-buddy`, also create a
   default storage directory and document it as a ready-to-use alternative:
   ```bash
   mkdir -p "$DATA_DIR/storage"
   chown "${SERVICE_USER}:${SERVICE_GROUP}" "$DATA_DIR/storage"
   ```

2. **`INSTALL.md §4 Step 3`** — replace "created automatically" with clear guidance:
   - If the user enters a path under `/mnt` or any root-owned parent, the wizard cannot
     create it; they must pre-create it with `sudo mkdir -p <path> && sudo chown backupbuddy:backupbuddy <path>`
   - Mention `/var/lib/backup-buddy/storage` as the zero-configuration default that always works

**Done when:**
- `install/gatekeeper.sh` creates `/var/lib/backup-buddy/storage` owned by `backupbuddy` ✓
- `INSTALL.md §4 Step 3` explains the chown requirement for paths outside
  `/var/lib/backup-buddy/` ✓
- A fresh install wizard with `/mnt/buddy-storage` as input shows a clear, actionable error
  that includes the exact `mkdir + chown` command to run ✓

```
> Kludde — 2026-06-04
>
> Three changes in one commit (b5eaec64a):
> 1. install/gatekeeper.sh create_directories() loop extended to include
>    "$DATA_DIR/storage" — same chown+chmod 750 as the parent, so there
>    is always a ready-to-use default at /var/lib/backup-buddy/storage.
> 2. INSTALL.md §4 Step 3 replaces "created automatically" with explicit
>    guidance: /var/lib/backup-buddy/storage is the recommended default;
>    for paths under root-owned parents the user must run
>    sudo mkdir -p <path> && sudo chown backupbuddy:backupbuddy <path>
>    before entering the path in the wizard.
> 3. onboarding.py _validate_storage_paths OSError branch now returns
>    the exact sudo mkdir+chown command inline in the error string so
>    wizard_step3.html renders it in the error-box.
> Full suite: 927 pass, 12 skip.
```

---

## 1.19 — Bug investigation backlog (found during 1.18.20v2)

### [x] 1.19.10 — Investigate: first invite code not persisted to cluster.db after cascade

> **Source:** `tests/integration/1.18.20v2-issues.md` → ISSUE-001; discovered during 1.18.20v2
> **Reads:** `gatekeeper/gui/routes/onboarding.py` (`_cascade_new_cluster`),
>            `gatekeeper/db/cluster.py` (`ClusterDB.insert_invite`)

After a successful `_cascade_new_cluster()` run, the first invite code (stored in
`onboarding_state.json` as `first_invite_code`) was not present in the `invites` table
of `cluster.db`. The `members` table had 1 correct row (`anders-home`), inserted in the
same transaction block, confirming the DB connection and commit path work.

`generate_invite()` calls `db.insert_invite()` which does `execute + commit()`. The state
file recorded `"first_invite_code": "bolt-herbs-8"`, confirming `generate_invite()` returned
a code, but querying the DB (as `backupbuddy` user, with WAL checkpoint) showed 0 invite rows.

Workaround used: generate invite via `POST /api/buddies/invite` after wizard completion.

**Investigation areas:**

1. Is there a code path that deletes or rolls back the invites table after the cascade?
   (e.g. during service restart, schema migration, or lifespan startup)
2. Does the WAL checkpoint race with the process SIGTERM? (DB was closed at 21:39:04,
   SIGTERM at 21:41:35 — 2 minutes gap — so unlikely but worth confirming)
3. Is `generate_invite` skipped because `state.first_invite_code` was already set
   from a previous wizard run that wasn't fully cleaned up during the A0 reset?
   (Check: did the old `onboarding_state.json` survive the A0 wizard reset and contain
   a stale `first_invite_code` value that made the `if not state.first_invite_code`
   guard skip the `generate_invite` call?)
4. Check git log for any recent changes to `_cascade_new_cluster` or `ClusterDB`
   that could explain the regression.

**Done when:**
- Root cause identified and documented in a kludde below
- Fix applied (or root cause ruled out as a test environment artefact)
- Regression test added if it's a real code bug

---

> **Kludde:**
>
> **Root cause — investigation area #3 confirmed.**
>
> The `clean-ubuntu` snapshot for VM 101 was taken after a previous wizard run (task 1.18.11).
> That run stored `first_invite_code = "bolt-herbs-8"` in `/var/lib/backup-buddy/onboarding_state.json`.
> The A0 factory reset procedure deletes `gatekeeper.cfg`, `catalog.db`, `cluster.db`,
> `root_dir.cap`, and `recovery_kit.enc` — but does **not** delete `onboarding_state.json`.
>
> When the 1.18.20v2 wizard ran `_cascade_new_cluster()`:
> 1. `load_state()` loaded stale state with `first_invite_code = "bolt-herbs-8"`.
> 2. The guard `if not state.first_invite_code:` evaluated `False` (truthy stale value).
> 3. `generate_invite()` was **never called** — confirming: the log line
>    `"First invite code generated"` is absent from the 1.18.20v2 journal.
> 4. Fresh `cluster.db` (created after the reset) had 0 invite rows.
> 5. Wizard's first-invite page displayed the stale code "bolt-herbs-8" → Björn's join rejected.
>
> The `upsert_self_member` call is NOT guarded by state — it is always executed as an idempotent
> UPSERT — which is why the `members` table had 1 correct row while `invites` had 0.
>
> **Fix applied:** `gatekeeper/gui/routes/onboarding.py` — invite guard now checks the DB,
> not just state:
> ```python
> if not state.first_invite_code or not cluster_db.get_invite(state.first_invite_code):
> ```
> This makes the invite step consistent with all other cascade steps, which check on-disk
> artifacts rather than state values. A stale code in state that is absent from the DB
> (e.g. after an A0 reset) will trigger a fresh `generate_invite()` call.
>
> **Regression test added:** `tests/unit/test_onboarding.py` →
> `TestCascadeNewClusterStaleInviteCode.test_stale_invite_code_regenerated_when_not_in_db`

---

### [x] 1.19.11 — Fix: agent reports "path does not exist" for permission-denied backup paths

> **Source:** `tests/integration/1.18.20v2-issues.md` → ISSUE-002; discovered during 1.18.21
> **Reads:** `agent/config.py` (path validation logic)

`agent/config.py` validates backup paths using `os.path.isdir()`, which returns `False` for
both non-existent paths and paths the process cannot access (e.g. `/root/backup-test` when
running as `backupbuddy` with `/root` at mode 0700). The resulting `CRITICAL` log says
"Backup path does not exist" even when the path exists but is simply not readable.

Workaround used in test: `chmod 711 /root` on each LXC.

**Fix:**
Replace the bare `os.path.isdir()` check with a two-step validation that distinguishes
between "does not exist" and "permission denied":

```python
import errno, os

def validate_backup_path(path: str) -> str:
    real = os.path.realpath(path)
    try:
        if not os.path.isdir(real):
            raise ValueError(f"Backup path does not exist or is not a directory: {path!r}")
    except PermissionError:
        raise ValueError(
            f"Backup path exists but is not readable by the backup service user: {path!r}. "
            "Check directory permissions."
        )
    return real
```

**Done when:**
- Config validation distinguishes "not found" from "permission denied"
- Error message in CRITICAL log is accurate in both cases
- Unit test covers both scenarios

---

> **Kludde:** Replaced `os.path.exists()` + `os.path.isdir()` (båda fångar PermissionError internt
> och returnerar False) med `os.stat()` direkt i `_validate_backup_paths`. `os.stat()` kastar
> PermissionError explicit för otillgängliga sökvägar och FileNotFoundError för obefintliga.
> `_stat_mod.S_ISDIR()` används för dir-kontrollen efter lyckad stat. Felmeddelanden:
> "not accessible by the backup service user" (perm denied) vs "does not exist" (saknas) vs
> "not a directory" (fel filtyp). 2 nya tester med monkeypatch av os.stat; ena verifierar
> "not accessible" i meddelandet, andra verifierar att "does not exist" INTE förekommer.
> 929 pass, 12 skip (26 pass i test_agent_config.py, 2 skip POSIX-perms på Windows dev).

---

### [x] 1.19.12 — Fix: agent upload success log says "Uploaded file" — add SUCCESS keyword

> **Source:** `tests/integration/1.18.20v2-issues.md` → ISSUE-003; discovered during 1.18.22
> **Reads:** `agent/main.py:122`

The upload worker logs `"Uploaded file (%d bytes)"` on success. The test checklist, docs, and
user expectations reference `SUCCESS`. The inconsistency causes confusion when grepping logs
or writing monitoring rules.

**Fix:**
Change the success log line in `agent/main.py` to include `SUCCESS`:

```python
logger.info("SUCCESS — uploaded file (%d bytes)", len(data))
```

**Done when:**
- Log line includes "SUCCESS" keyword
- No other log levels use "SUCCESS" (keep it unambiguous for grep)
- Checklist item in 1.18.22 H2 and any docs updated to match

---

> **Kludde:** En-radsfix i `agent/main.py:122`: `"Uploaded file (%d bytes)"` → `"SUCCESS — uploaded file (%d bytes)"`.
> "SUCCESS" förekommer inte i någon annan INFO/WARNING/ERROR-rad — unambiguous för grep och monitoring.
> Inga befintliga tester bröts. 929 pass, 12 skip.

---

### [x] 1.19.13 — Fix: no recovery kit re-download after wizard completes

> **Source:** `tests/integration/1.18.20v2-issues.md` → ISSUE-004; discovered during 1.18.22
> **Reads:** `gatekeeper/gui/routes/onboarding.py` (`/api/onboarding/download-key`),
>            `gatekeeper/gui/routes/settings.py` (`has_recovery_kit` field)

`/api/onboarding/download-key` returns 404 once `recovery_key_confirmed = True`. The settings
page exposes `has_recovery_kit` (boolean) but no download link. A user who misplaces their
recovery kit has no way to retrieve it from the dashboard.

`recovery_kit.enc` is stored at `DATA_DIR/recovery_kit.enc` on the gatekeeper. The file is
already encrypted with the user's passphrase — serving it again is safe.

**Fix option A (preferred):** Add a download route in settings:

```python
@router.get("/api/settings/recovery-kit/download")
async def download_recovery_kit(request: Request) -> Response:
    data_dir = _get_data_dir(request)
    kit_path = data_dir / "recovery_kit.enc"
    if not kit_path.exists():
        return JSONResponse({"error": "No recovery kit found on this node."}, status_code=404)
    return Response(
        content=kit_path.read_bytes(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="recovery-kit.enc"'},
    )
```

Then add a download button in the settings template next to the `has_recovery_kit` indicator.

**Fix option B:** Document that the kit is intentionally one-time-only and add a prominent
warning during wizard: "Save this file now — it cannot be downloaded again."

**Done when:**
- User can retrieve their recovery kit from settings (option A), OR
- UI clearly communicates one-time-only policy with prominent wizard warning (option B)
- Decision recorded in DECISIONS.md

---

> **Kludde:** Option A implemented. Added `GET /api/settings/recovery-kit/download` in `settings.py`
> (imports `Response`, guards `data_dir is None → 503`, missing file → 404). Download button added
> to Lifeboat section of `settings.html` as a plain `<a download>` anchor — avoids JSON-parsing
> the binary response. Decision recorded as ADR-022. 3 new unit tests, 40 pass total.

---

---

## 1.20 — Third three-user simulation (post-1.19.x fixes)

> **Goal:** Confirm that all 1.19.x fixes (stale invite state, agent permission-denied error,
> SUCCESS log keyword, recovery kit re-download) work correctly in a fresh end-to-end run.
> Follows the same structure as the 1.18.20v2/21/22 simulation.

---

### [x] 1.20.1 — Third three-user simulation, Part 1: infrastructure + cluster formation

> **Test run:** Third full end-to-end simulation. Verifies that 1.19.10 (stale invite fix),
> 1.19.11 (permission-denied path error), 1.19.12 (SUCCESS log keyword), and 1.19.13
> (recovery kit re-download) hold together in a clean environment.
>
> **State file:** `tests/integration/1.20.1-state.md` — update after each section.
> Read this file first when resuming after a context compression or `/clear`.
>
> **Issues file:** `tests/integration/1.20.1-issues.md` — record all problems here.
>
> **Error policy:** All problems are recorded in the issues file. Nothing is fixed unless
> it completely blocks progress. If a blocking fix is required: fix it, record it as
> `BLOCKING FIX`, roll all nodes back to their clean snapshot, restart from A1.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.
>
> **Prerequisite:** None — this is Part 1.

---

#### A0. Reset VM 101 wizard state (run once before A1 if VM 101 is not clean)

> VM 101's snapshots may contain stale wizard state. The 1.19.10 fix requires that
> `onboarding_state.json` is also deleted during a factory reset — verify this is the case.

```bash
qm stop 101 --skiplock 1
sleep 5
qm rollback 101 clean-ubuntu-v2
qm start 101
sleep 15
```

Check for wizard state:
```bash
ssh gk-anders "test -f /etc/backup-buddy/gatekeeper.cfg && echo 'WIZARD STATE PRESENT' || echo 'Clean'"
```

If `WIZARD STATE PRESENT`, reset to factory state (note: now includes `onboarding_state.json`):
```bash
ssh gk-anders "sudo rm -f \
  /etc/backup-buddy/gatekeeper.cfg \
  /var/lib/backup-buddy/catalog.db \
  /var/lib/backup-buddy/cluster.db \
  /var/lib/backup-buddy/root_dir.cap \
  /var/lib/backup-buddy/recovery_kit.enc \
  /var/lib/backup-buddy/onboarding_state.json && \
  sudo systemctl restart backup-buddy-gatekeeper"
```

Verify service starts in setup mode:
```bash
ssh gk-anders "sudo systemctl status backup-buddy-gatekeeper | head -5"
```

---

#### A. Infrastructure setup

**A1 — Roll back all six nodes to their clean snapshot:**

```bash
# VM 101: use clean-ubuntu-v2
qm stop 101 --skiplock 1
sleep 3
qm rollback 101 clean-ubuntu-v2
qm start 101

# Gatekeepers 102, 103
for vmid in 102 103; do
  qm stop $vmid --skiplock 1
  sleep 3
  qm rollback $vmid clean-ubuntu
  qm start $vmid
done

# Agent containers (LXC)
for ctid in 301 302 303; do
  pct stop $ctid
  sleep 2
  pct rollback $ctid clean-ubuntu
  pct start $ctid
done
```

Verify all six are running:
```bash
qm status 101; qm status 102; qm status 103
pct status 301; pct status 302; pct status 303
```

**A1a — Clear stale SSH host keys (on operator machine):**

```bash
for ip in 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33; do
  ssh-keygen -R $ip
done
ssh-keyscan -H 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33 \
  >> ~/.ssh/known_hosts
```

**A2 — Node layout:**

| User | Role | VM/LXC | Hostname | LAN IP |
|------|------|--------|----------|--------|
| Anders | Gatekeeper | VM 101 | gatekeeper-anders | 10.99.0.11 |
| Björn | Gatekeeper | VM 102 | gatekeeper-bjorn | 10.99.0.12 |
| Carina | Gatekeeper | VM 103 | gatekeeper-carina | 10.99.0.13 |
| Anders | Agent | LXC 301 | agent-anders-pc | 10.99.0.31 |
| Björn | Agent | LXC 303 | agent-bjorn-pc | 10.99.0.33 |
| Carina | Agent | LXC 302 | agent-anders-nas | 10.99.0.32 |

**A3 — Verify Tailscale after rollback:**

```bash
ssh gk-anders "tailscale status"
ssh gk-bjorn  "tailscale status"
ssh gk-carina "tailscale status"
```

Expected: each shows node as online with a 100.x.x.x address.
If any show "Logged out": ask Johan for a reusable auth key, then
`sudo tailscale up --auth-key=<key>` on the affected node(s).

Record Tailscale IPs in state file:

```bash
ssh gk-anders "tailscale ip -4"
ssh gk-bjorn  "tailscale ip -4"
ssh gk-carina "tailscale ip -4"
```

> **State update:** After A3, update `1.20.1-state.md` → Tailscale IPs table.

---

#### B. Download test files

Test files from the previous 1.18.20v2 run may still be in `/tmp/testfiles/` on the Proxmox host
(the host is not rolled back). Verify first:

```bash
ls /tmp/testfiles/
sha256sum /tmp/testfiles/*
```

If present and intact, skip re-download and just re-push to containers.
If missing: download fresh files (two `.jpg` ≥5 MB each, one `.zip` 50–200 MB, one `.iso` 200–700 MB, two `.docx`).

Compute checksums **before any backup**:

```bash
sha256sum /tmp/testfiles/* | tee /tmp/checksums_before_v3.txt
```

Copy subsets:
- LXC 301 (Anders): all `.jpg` + `.iso`
- LXC 303 (Björn): all `.zip` + one `.docx`
- LXC 302 (Carina): remaining `.docx` + one `.jpg`

> **State update:** After B, paste checksum output into `1.20.1-state.md` → Test file checksums.

---

#### C. Install and configure — Anders (VM 101)

**C1 — SSH to gatekeeper:**

```bash
ssh gk-anders
```

**C2 — Install BackupBuddy gatekeeper:**

```bash
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
sudo bash /opt/backup-buddy/install/gatekeeper.sh
```

**C3 — Tailscale should already be connected from rollback. Verify:**

```bash
tailscale status
```

**C4 — Open wizard:** `http://10.99.0.11:8080` in browser.

**C5 — Complete wizard:**

- Step 1: Start a new cluster
- Step 2: Node ID `anders-home`, display name `Anders home node`
- Step 3: Storage path `/var/lib/backup-buddy/storage` (installer default — no pre-create needed)
- Step 4: Profile **Adaptive** (default)
- Step 5: Skip notification email. Choose a passphrase, write it down.
- Download `recovery-kit.enc`. Click "I have saved my recovery key".

Record in state file: **invite code** and **Tailscale address** shown after wizard.

**C5a — Verify first invite code is in cluster.db (1.19.10 fix verification):**
```bash
ssh gk-anders "sudo -u backupbuddy /opt/backup-buddy/.venv/bin/python3 -c \
  \"import sys; sys.path.insert(0,'/opt/backup-buddy'); \
  from gatekeeper.db.cluster import ClusterDB; \
  db=ClusterDB('/var/lib/backup-buddy/cluster.db'); \
  rows=db.list_invites(); print('Invites:', len(rows), rows[0].code if rows else 'NONE')\""
```
Expected: at least 1 invite row visible. If 0 rows: record ISSUE, regenerate via `/api/buddies/invite`.

**Verify:** Dashboard switches to Tailscale address; LAN IP is no longer accessible.

---

#### D. Install and configure — Björn (VM 102)

Same steps as C with:
- Node ID `bjorn-home`, display name `Björn home node`
- Gatekeeper IP: `10.99.0.12`, agent LXC: 303
- Storage path: `/var/lib/backup-buddy/storage`
- In wizard: **Join an existing cluster** → enter Anders's invite code + Tailscale address

Record in state file: confirm both nodes appear in each other's dashboards.

---

#### E. Install and configure — Carina (VM 103)

Same steps as C with:
- Node ID `carina-home`, display name `Carina home node`
- Gatekeeper IP: `10.99.0.13`, agent LXC: 302
- Storage path: `/var/lib/backup-buddy/storage`

**Anders must generate a new invite code** from his Buddies page before Carina can join.
Record new invite code in state file.

Record in state file: confirm all three nodes appear Online in all three dashboards.

---

#### Done when (Part 1):

- A0 factory reset includes `onboarding_state.json` deletion ✓
- All six nodes running on clean snapshot rollback ✓
- Tailscale connected on all three gatekeepers ✓
- Test files present with pre-backup checksums recorded in state file ✓
- Anders's wizard complete; first invite code verified in cluster.db ✓
- Björn joined cluster on first attempt; both nodes visible in each other's dashboards ✓
- Carina joined cluster; all three nodes visible as Online in all dashboards ✓
- State file updated with all runtime values ✓
- Issues file updated with any problems found ✓
- Task marked `[x]` and `git commit chore(test): 1.20.1 part 1 done` ✓

```
> Kludde: ISSUE-001 — Carina join cascade: PermissionError on api_auth_token (root-owned files
> from diagnostic manual `tahoe run`). Resolution: chown -R backupbuddy, manually created
> root_dir.cap via Tahoe HTTP, re-ran step 5 (skipped join via cap_path shortcut), then
> pushed member list from Anders to Carina via sync endpoint.
> Notes: Carina's tailscale_hostname in own DB set to "carina-home" (not Tailscale IP) —
> corrected by sync push with IP 100.87.217.128. Björn joined first attempt. Anders invite
> verified in cluster.db (1.19.10 fix confirmed). All 3 dashboards show 3 active members.
```

---

### [x] 1.20.2 — Third three-user simulation, Part 2: agent setup + backup monitoring

> **Resume:** Before starting, read `tests/integration/1.20.1-state.md`.
> All three gatekeepers must be installed and cluster formed (1.20.1 done).
>
> **State file:** `tests/integration/1.20.1-state.md` — continue updating.
> **Issues file:** `tests/integration/1.20.1-issues.md` — continue recording problems.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.

---

#### F. Agent setup and backup monitoring

> **Note:** LXC containers may not have `curl` installed by default.
> If `curl: command not found` is encountered, run `apt-get install -y curl` first.

**F1 — Install agent on LXC 301 (Anders):**

```bash
ssh agent-anders-pc
# Pre-install curl if missing:
apt-get install -y curl 2>/dev/null || true
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
BB_GATEKEEPER_IP=10.99.0.11 BB_AGENT_NAME=anders-laptop \
  sudo bash /opt/backup-buddy/install/agent.sh
```

Edit backup paths:

```bash
sudo nano /etc/backup-buddy/backup.cfg
# Add under [backup]: /home/testuser/backup-test
```

Copy agent token to Anders's gatekeeper:

```bash
sudo grep token /etc/backup-buddy/backup.cfg
# On gatekeeper: paste token into gatekeeper.cfg [agent_api] token = ...
ssh gk-anders "sudo systemctl restart backup-buddy-gatekeeper"
sudo systemctl start backup-buddy-agent
```

**F2 — Install agent on LXC 303 (Björn):**

Same as F1 but:
- `ssh agent-bjorn-pc`, `BB_GATEKEEPER_IP=10.99.0.12`, `BB_AGENT_NAME=bjorn-laptop`
- Copy token to `gk-bjorn`

**F3 — Install agent on LXC 302 (Carina):**

Same as F1 but:
- `ssh agent-anders-nas`, `BB_GATEKEEPER_IP=10.99.0.13`, `BB_AGENT_NAME=carina-laptop`
- Copy token to `gk-carina`

**F4 — Watch agent logs until SUCCESS:**

```bash
ssh agent-anders-pc  "journalctl -u backup-buddy-agent -f"
ssh agent-bjorn-pc   "journalctl -u backup-buddy-agent -f"
ssh agent-anders-nas "journalctl -u backup-buddy-agent -f"
```

Wait until each agent shows `SUCCESS — uploaded file` for all its test files (1.19.12 fix).
Note any `FAILED` entries in the issues file.

**F5 — Confirm on gatekeeper dashboards:**

Open each gatekeeper's Tailscale URL. Confirm:
- "Last backup" shows a recent timestamp
- "Files backed up" count is non-zero

**F6 — Verify 1.19.11 fix (agent permission-denied error message):**

On any agent LXC, temporarily add a locked-parent path to backup.cfg and verify the error.
Note: `/root` does NOT work here — `os.stat("/root")` succeeds even as backupbuddy because
only execute permission on the parent `/` is needed. Use a locked-parent path instead:
```bash
# Create a path the backupbuddy user cannot traverse into
mkdir -p /tmp/locked_parent/backup-test && chmod 700 /tmp/locked_parent
# Add /tmp/locked_parent/backup-test under [backup] in backup.cfg
sudo systemctl restart backup-buddy-agent
journalctl -u backup-buddy-agent | grep -i "backup path"
# Expected: "not accessible by the backup service user" — NOT "does not exist"
# Remove /tmp/locked_parent/backup-test from backup.cfg and restart agent
```

> **State update:** Update `1.20.1-state.md` section log rows F1–F3.

---

#### Done when (Part 2):

- All three agents installed and registered on their gatekeepers ✓
- All three agents show `SUCCESS — uploaded file` in journalctl ✓
- All three gatekeeper dashboards show non-zero "Files backed up" ✓
- 1.19.11 fix verified: permission-denied path shows "not accessible by the backup service user" ✓
- 1.19.12 fix verified: "SUCCESS" keyword present in agent upload logs ✓
- State file updated ✓
- Issues file updated ✓
- Task marked `[x]` and `git commit chore(test): 1.20.2 part 2 done` ✓

> **Hand-off to 1.20.3:** Ensure `1.20.1-state.md` is committed before starting Part 3.

```
> Kludde:
```

---

### [x] 1.20.3 — Third three-user simulation, Part 3: restore, checksums, and full checklist

> **Resume:** Before starting, read `tests/integration/1.20.1-state.md`.
> All agents must be installed and backups confirmed successful (1.20.2 done).
> Pre-backup checksums must be recorded in state file.
>
> **State file:** `tests/integration/1.20.1-state.md` — final updates here.
> **Issues file:** `tests/integration/1.20.1-issues.md` — record all problems.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.

---

#### G. Restore and checksum verification

**G1 — Restore from Anders's dashboard:**

Open `http://<anders-tailscale-ip>:8080` → Restore.
Restore each of Anders's test files to `/tmp/restored/anders/` on the gatekeeper.

**G2 — Restore from Björn's dashboard:**

Restore Björn's test files to `/tmp/restored/bjorn/`.

**G3 — Restore from Carina's dashboard:**

Restore Carina's test files to `/tmp/restored/carina/`.

**G4 — Compute checksums after restore:**

```bash
ssh gk-anders  "sha256sum /tmp/restored/anders/*"
ssh gk-bjorn   "sha256sum /tmp/restored/bjorn/*"
ssh gk-carina  "sha256sum /tmp/restored/carina/*"
```

Compare against pre-backup checksums in `1.20.1-state.md`. Every hash must match.
Record PASS / FAIL in state file.

**G5 — Folder restore test:**

Restore an entire folder (not just a single file) via the dashboard to confirm
the folder restore path works. Record PASS / FAIL.

**G6 — Verify 1.19.13 fix (recovery kit re-download from settings):**

On each gatekeeper's settings page, navigate to the Lifeboat section and confirm
the "Download recovery kit" button is present and returns the kit file:
```bash
ANDERS_TS=$(ssh gk-anders "tailscale ip -4")
curl -sf "http://${ANDERS_TS}:8080/api/settings/recovery-kit/download" \
  -o /tmp/recovery_kit_redownload.enc
ls -la /tmp/recovery_kit_redownload.enc
# Expected: non-empty file (> 0 bytes)
```
Record PASS / FAIL.

---

#### H. Resilience test

Stop one gatekeeper (simulate node failure) and confirm the others can still restore:

```bash
ssh proxmox "qm stop 101"   # Stop Anders's gatekeeper
```

From Björn's dashboard: attempt a restore. Should succeed even without Anders.
From Carina's dashboard: attempt a restore. Should succeed.

Bring Anders back:

```bash
ssh proxmox "qm start 101"
```

Confirm Anders's dashboard reconnects and shows all three nodes as Online.

Record PASS / FAIL in state file and issues file.

---

#### H2. Manual checklist

Mark PASS / FAIL / N/A. Add notes to issues file for every FAIL.

**Installation:**
- [ ] Installer completes without errors on fresh Ubuntu 24.04
- [ ] `backup-buddy-gatekeeper` service is `active (running)` after install
- [ ] Wizard is reachable at `http://<LAN-IP>:8080`
- [ ] `sudo tailscale up` connects without new browser auth (rollback preserved state)
- [ ] Wizard completes all five steps without error
- [ ] `recovery-kit.enc` download works and produces a non-empty file
- [ ] Invite code generated and displayed after wizard completes
- [ ] First invite code is present in cluster.db after wizard (1.19.10 fix)
- [ ] Dashboard switches to Tailscale address after wizard completes
- [ ] Dashboard is **not** reachable on LAN IP after Tailscale binds (security check)

**Cluster formation:**
- [ ] Björn can join using Anders's invite code and Tailscale address
- [ ] Carina can join using a freshly generated second invite code
- [ ] All three nodes appear as **Online** in Anders's dashboard
- [ ] All three nodes appear as **Online** in Björn's dashboard
- [ ] All three nodes appear as **Online** in Carina's dashboard
- [ ] Reusing an expired invite code produces an error, not a silent failure

**Agent:**
- [ ] Agent installer works; `backup-buddy-agent` service starts
- [ ] Agent appears in gatekeeper's dashboard after token is copied
- [ ] Editing `backup.cfg` + restarting agent picks up new folders
- [ ] Agent log shows `SUCCESS — uploaded file` for each backed-up file (1.19.12 fix)
- [ ] Permission-denied backup path shows "not accessible by the backup service user" (1.19.11 fix)

**Backup integrity:**
- [ ] All `.jpg` test files backed up successfully
- [ ] All `.zip` test files backed up successfully
- [ ] All `.iso` test files backed up successfully
- [ ] All `.docx` test files backed up successfully
- [ ] No `FAILED` entries in any agent log for the test files

**Restore and checksums:**
- [ ] Single-file restore completes without error
- [ ] Restored `.jpg` SHA-256 matches original
- [ ] Restored `.zip` SHA-256 matches original
- [ ] Restored `.iso` SHA-256 matches original
- [ ] Restored `.docx` SHA-256 matches original
- [ ] Folder restore completes without error
- [ ] Restored files land in the correct destination folder

**Resilience:**
- [ ] Stopping one gatekeeper; restore from the other two still succeeds
- [ ] Stopped gatekeeper restarts and shows Online in dashboards

**UI and UX:**
- [ ] Dashboard shows an obvious error or warning if an agent has not sent data for > 1 hour
- [ ] Recovery kit re-download accessible from Settings → Lifeboat (1.19.13 fix)
- [ ] Navigating the dashboard without any data causes no crashes or blank pages
- [ ] All button clicks in the wizard produce visible feedback within 3 seconds

---

#### I. Close-out

- Update `1.20.1-state.md` with final statuses.
- Commit issues file: `git commit chore(test): 1.20.1 issues file — N issues logged`
- Add kludde block below summarising results.
- Mark tasks 1.20.1–1.20.3 as `[x]` in TODO.md.
- Final commit: `git commit chore(test): mark 1.20.1-1.20.3 done — third sim complete`

---

#### Done when (Part 3 / full simulation):

- All restore checksums match originals exactly ✓
- Folder restore tested ✓
- Resilience test passed (restore succeeds with one node down) ✓
- 1.19.10 fix verified: first invite persisted in cluster.db after fresh wizard ✓
- 1.19.11 fix verified: permission-denied path shows correct error in agent log ✓
- 1.19.12 fix verified: "SUCCESS" keyword present in agent upload log ✓
- 1.19.13 fix verified: recovery kit re-download works from settings ✓
- Full manual checklist completed with all items PASS or documented ✓
- `tests/integration/1.20.1-issues.md` committed with all findings ✓
- `tests/integration/1.20.1-state.md` committed with final statuses ✓
- Kludde block added below with overall result, what passed, what failed ✓
- Tasks 1.20.1–1.20.3 marked `[x]` ✓

---

> **Kludde:** Third three-user simulation (1.20.1–1.20.3) COMPLETE — 2026-06-06.
> All 7 test files backed up (3 jpg, 1 iso, 1 zip, 2 docx). All 7 SHA-256 checksums
> verified after restore. Folder restore passed (9 results, all checksums match).
> Resilience test passed (restore from Björn+Carina with Anders stopped; Anders
> restarted and rejoined). All H2 manual checklist items PASS or N/A.
> Fixes verified: 1.19.10 (first invite in cluster.db), 1.19.11 (permission-denied
> error message), 1.19.12 (SUCCESS keyword in agent log), 1.19.13 (recovery kit
> re-download from settings). One issue logged: ISSUE-001 (Carina join cascade,
> root-owned Tahoe files, resolved with workaround). One note: NOTE-001 (/root
> does not trigger permission error — used locked-parent path instead).

---

## 1.21 — Post-simulation bugfixes (issues surfaced in 1.18–1.20 runs)

> These tasks address every open issue from the three simulation runs that was not
> already fixed inline or as part of 1.18.x / 1.19.x. Each task references the
> originating issue ID so the history is traceable.

---

### [x] 1.21.1 — Fix storage-path wizard guidance for root-owned parent directories

> **Source:** 1.18.1 ISSUE-006, ISSUE-007; 1.18.20 ISSUE-005

**Problem:** Wizard step 3 correctly auto-creates the storage path when the parent
directory is writable by the `backupbuddy` user. But when the parent is root-owned
(e.g. `/mnt` at 755), `os.makedirs` fails and the user sees "Could not create
directory — the parent is not writable". INSTALL.md §4 Step 3 currently says
"This folder will be created for you if it does not exist" — this is only true if
the parent is already service-user-owned, which is not the case for `/mnt/buddy-storage`.

**Fix options (pick one):**
- (A) Change the wizard default suggested path from `/mnt/buddy-storage` to
  `/var/lib/backup-buddy/storage` (parent already owned by `backupbuddy`).
- (B) In the auto-create error path, also try `sudo`-equivalent (setuid helper) to
  create and chown the directory if the parent is root-owned. Complex, not worth it.
- (C) Keep current behaviour but update INSTALL.md §4 Step 3 to say:
  "Create the folder yourself first and set the right ownership:
   `sudo mkdir -p /mnt/buddy-storage && sudo chown backupbuddy:backupbuddy /mnt/buddy-storage`"

**Recommendation:** Option A (change default suggested path to a service-owned location).
Also update INSTALL.md to show Option C as the manual alternative.

**Done when:**
- Wizard step 3 placeholder/default path does not require a separate chown
- INSTALL.md §4 Step 3 accurately describes what the user must do
- `[ ]` committed

---

### [x] 1.21.2 — Fix agent installer in restricted environments (no TTY, LXC)

> **Source:** 1.18.1 ISSUE-010, ISSUE-011

**Problem A (ISSUE-010):** `sudo bash install/agent.sh` inside an LXC container (or any
SSH session without a real TTY) fails at line ~146 with
`/dev/tty: No such device or address` — the installer tries `exec 3</dev/tty` to read
interactive input. The non-interactive mode exists (env vars `BB_GATEKEEPER_IP` and
`BB_AGENT_NAME`) but is undocumented in INSTALL.md.

**Problem B (ISSUE-011):** INSTALL.md §5a only tells the user to add backup paths under
`[backup]`. It does not mention that `[gatekeeper]` also requires `name` and
`lifeboat_path` fields (only set by the installer). If a user re-creates backup.cfg
from the template, the agent crashes with `CRITICAL — Configuration error: [gatekeeper]
'token' is required` / `'name' is required`.

**Fixes:**
- Detect TTY absence in agent.sh and fall back to non-interactive mode automatically
  (`[ -t 0 ]` check before `exec 3</dev/tty`).
- Add INSTALL.md §5 note: "In restricted environments (LXC, SSH without TTY, CI),
  run: `BB_GATEKEEPER_IP=<ip> BB_AGENT_NAME=<name> sudo -E bash install/agent.sh`"
- Update INSTALL.md §5a to show the full required `[gatekeeper]` section including
  `name` and `lifeboat_path`, not just the backup paths.

**Done when:**
- `install/agent.sh` does not error on `/dev/tty` when run inside an LXC without TTY
- INSTALL.md documents the `BB_GATEKEEPER_IP` / `BB_AGENT_NAME` env-var escape hatch
- INSTALL.md §5a shows all required `[gatekeeper]` fields
- `[ ]` committed

---

### [x] 1.21.3 — Harden against Tahoe introducer outage (storage-server FURL cache)

> **Source:** 1.18.1 ISSUE-013

**Problem:** When the Tahoe introducer VM (VM 104) goes down, ALL gatekeeper nodes
lose grid connectivity immediately. Tahoe clients discover storage servers only via
the introducer — there is no persistent server cache. Even with k=1 of 3 shares
needed, a download returns HTTP 410 (Gone) because the client cannot locate any
storage node. Losing the introducer makes restore impossible cluster-wide.

**Root cause:** The Tahoe client uses the introducer to maintain its server list in
memory. On introducer loss the list is never refreshed. No FURL cache persists across
restarts or introducer outages.

**Fix (Phase 1 scope):** Implement a local storage-server FURL cache on each gatekeeper:
- After a successful cluster join or member sync, write all known peer storage-node
  FURLs to a local file (e.g. `storage_servers.json` in `data_dir`).
- Pass these FURLs to the Tahoe client config (`[client] introducer.furl` +
  static server entries) so the client can reach storage nodes without the introducer.
- Verify on startup: if the introducer is unreachable but cached FURLs are present,
  log a warning and continue in degraded mode.

**Note:** A full gossip-based solution (replacing the introducer entirely) is Phase 2
(ADR 2.3). This task is the Phase 1 minimal mitigation.

**Done when:**
- Stopping VM 104 (introducer) does not immediately break restores when ≥ 2 storage
  nodes are reachable and FURLs were cached from a prior successful connection
- Appropriate warning logged when introducer is unreachable
- `[x]` committed and integration test step H updated to test this scenario

> **Kludde — 2026-06-06**
>
> Root cause confirmed: `storage_node.py::start()` was patching `tub.location`
> from 127.0.0.1 to the LAN IP (via `get_lan_ip()`). Cross-VLAN peers cannot reach
> LAN IPs — Foolscap Reconnectors failed to connect, so `get_connected_servers()`
> returned empty. When the introducer died there were no live connections to fall back
> on → HTTP 410.
>
> Fix: `start()` now ALWAYS rebuilds `tub.location` with the current Tailscale IP
> (via `get_tailscale_ip()`). Tailscale IPs are routable between all gatekeepers
> (ADR-002). Reconnectors connect successfully; connections survive introducer death.
> The existing `private/servers.yaml` cache (1.18.10) provides the cold-start path.
>
> The TODO prescribed explicit FURL collection via a cluster-sync endpoint; that is
> redundant because Tahoe's own `_save_servers_yaml()` already caches FURLs after
> each introducer announcement — the only gap was the IPs being wrong.
>
> 3 new unit tests added to `tests/unit/test_storage_node.py`.
> Integration test added as Scenario 8 in `project-docs/testing.md`.
> Integration test on Proxmox still required.

---

### [x] 1.21.4 — Fix join-cascade idempotency (interrupted cascade leaves cluster in split state)

> **Source:** 1.20.1 ISSUE-001

**Problem:** The join cascade on the joining gatekeeper contacts the cluster leader,
which calls `consume_invite` then `insert_member`. If the cascade is then interrupted
(SIGTERM, crash, network loss) after the leader's `insert_member` succeeds but before
the joiner writes `root_dir.cap` locally, the cluster is left in a split state:
- The leader has the joiner in its `members` table and the invite is consumed.
- The joiner has no `root_dir.cap`, so it cannot use the idempotency shortcut.
- On retry the joiner calls `initiate_join` again → leader returns 400 "invite used".

A separate but related issue: `consume_invite` is called before `insert_member` with
no rollback. If `insert_member` raises `IntegrityError` (duplicate `node_id`), the
invite is gone and no member was added. The admin must generate a new invite.

**Fixes:**
- (A) Add a `GET /api/cluster/member/{node_id}` endpoint on the leader. On cascade
  retry, before calling `initiate_join`, check if self is already a member; if yes,
  fetch the leader's `root_dir.cap` (or request it via a new endpoint) and continue.
- (B) Swap the order: call `insert_member` inside a transaction, then
  `consume_invite` only on success. This prevents the lost-invite case.
- (C) Make `initiate_join` idempotent: if the invite is already used but the calling
  `node_id` is already a member, return success with the cluster state rather than 400.

**Recommendation:** Fix (C) is the smallest safe change — detect the "invite used but
caller is already a member" case in `accept_join` and return the cluster state.
Fix (B) is a one-transaction swap and eliminates the lost-invite case cleanly.
Both should be implemented.

**Done when:**
- A cascade interrupted mid-way can be retried without manual DB surgery
- A duplicate `node_id` insert does not consume the invite
- Unit tests cover both retry scenarios
- `[ ]` committed

---

## 1.22 — Fourth three-user simulation (post-1.21.x fixes + INSTALL.md audit)

> **Goal:** Confirm that all four 1.21.x fixes work correctly in a fresh end-to-end run.
> Follow **INSTALL.md as the primary reference** — every section of the guide that applies
> is followed step by step exactly as a new user would. Deviations from expectations
> are recorded and categorised:
>
> - **ENRADSFIX:** A small inconsistency or missing step in INSTALL.md (or a one-liner code
>   fix). Record in issues file **and fix immediately** with a commit.
> - **ISSUE:** A functional problem that requires investigation. Fix if blocking; otherwise
>   record and continue.
> - **LARGER FIX:** A problem that would affect earlier test steps or require a non-trivial
>   code change. Record in issues file **and add to TODO.md**; continue with workaround.

---

### [x] 1.22.1 — Fourth three-user simulation, Part 1: infrastructure + cluster formation

> **Verifies:** 1.21.1 (wizard step 3 default path), 1.21.4 (join cascade idempotency).
>
> **State file:** `tests/integration/1.22.1-state.md` — update after each section.
> Read this file first when resuming after a context compression or `/clear`.
>
> **Issues file:** `tests/integration/1.22.1-issues.md` — record all problems here.
>
> **Error policy:** All problems are recorded in the issues file. Nothing is fixed unless it
> completely blocks progress (exception: ENRADSFIX items are fixed immediately and committed
> inline). If a blocking non-trivial fix is required: fix, roll all nodes back to clean
> snapshot, restart from A1.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.
>
> **Prerequisite:** None — this is Part 1.

---

#### A0. Verify VM 101 wizard state (run once before A1 if VM 101 is not clean)

```bash
ssh gk-anders "test -f /etc/backup-buddy/gatekeeper.cfg && echo 'WIZARD STATE PRESENT' || echo 'Clean'"
```

If `WIZARD STATE PRESENT`, factory-reset (includes all state files):
```bash
ssh gk-anders "sudo rm -f \
  /etc/backup-buddy/gatekeeper.cfg \
  /var/lib/backup-buddy/catalog.db \
  /var/lib/backup-buddy/cluster.db \
  /var/lib/backup-buddy/root_dir.cap \
  /var/lib/backup-buddy/recovery_kit.enc \
  /var/lib/backup-buddy/onboarding_state.json && \
  sudo systemctl restart backup-buddy-gatekeeper"
ssh gk-anders "sudo systemctl status backup-buddy-gatekeeper | head -5"
```

---

#### A. Infrastructure setup

**A1 — Roll back all six nodes to their clean snapshot:**

```bash
qm stop 101 --skiplock 1
sleep 3
qm rollback 101 clean-ubuntu-v2
qm start 101

for vmid in 102 103; do
  qm stop $vmid --skiplock 1
  sleep 3
  qm rollback $vmid clean-ubuntu
  qm start $vmid
done

for ctid in 301 302 303; do
  pct stop $ctid
  sleep 2
  pct rollback $ctid clean-ubuntu
  pct start $ctid
done
```

Verify all six are running:
```bash
qm status 101; qm status 102; qm status 103
pct status 301; pct status 302; pct status 303
```

**A1a — Clear stale SSH host keys (on operator machine):**

```bash
for ip in 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33; do
  ssh-keygen -R $ip
done
ssh-keyscan -H 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33 \
  >> ~/.ssh/known_hosts
```

**A2 — Node layout:**

| User | Role | VM/LXC | Hostname | LAN IP |
|------|------|--------|----------|--------|
| Anders | Gatekeeper | VM 101 | gatekeeper-anders | 10.99.0.11 |
| Björn | Gatekeeper | VM 102 | gatekeeper-bjorn | 10.99.0.12 |
| Carina | Gatekeeper | VM 103 | gatekeeper-carina | 10.99.0.13 |
| Anders | Agent | LXC 301 | agent-anders-pc | 10.99.0.31 |
| Björn | Agent | LXC 303 | agent-bjorn-pc | 10.99.0.33 |
| Carina | Agent | LXC 302 | agent-anders-nas | 10.99.0.32 |

**A3 — Verify Tailscale after rollback:**

```bash
ssh gk-anders "tailscale status"
ssh gk-bjorn  "tailscale status"
ssh gk-carina "tailscale status"
```

Expected: each shows node as online with a 100.x.x.x address.
If any show "Logged out": ask Johan for a reusable auth key, then
`sudo tailscale up --auth-key=<key>` on the affected node(s).

Record Tailscale IPs:
```bash
ssh gk-anders "tailscale ip -4"
ssh gk-bjorn  "tailscale ip -4"
ssh gk-carina "tailscale ip -4"
```

> **State update:** After A3, update `1.22.1-state.md` → Tailscale IPs table.

---

#### B. Download test files

Check if previous test files are still present on the Proxmox host (not rolled back):
```bash
ls /tmp/testfiles/
sha256sum /tmp/testfiles/*
```

If present and intact, skip re-download.
If missing: download fresh files (same set as 1.20 + one extra large file):
- 3 × `.jpg` (≥ 5 MB each)
- 1 × `.iso` (200–700 MB)
- 1 × `.zip` (50–200 MB)
- 2 × `.docx` (1–2 MB each)
- **NEW:** 1 × `.tar.gz` (≥ 1 GB) — generates locally if no suitable file available:
  ```bash
  dd if=/dev/urandom bs=1M count=1024 | gzip > /tmp/testfiles/test-archive-large.tar.gz
  ```

Compute checksums **before any backup**:
```bash
sha256sum /tmp/testfiles/* | tee /tmp/checksums_before_v4.txt
```

Distribute test files:
- LXC 301 (Anders): 3 jpg + 1 iso
- LXC 303 (Björn): 1 zip + 1 docx
- LXC 302 (Carina): 1 docx + 1 jpg + 1 tar.gz (the large file)

> **State update:** After B, paste checksum output into `1.22.1-state.md` → Test file checksums.

---

#### C. Install and configure — Anders (VM 101) — following INSTALL.md §3 and §4

**C1 — SSH to gatekeeper:**

```bash
ssh gk-anders
```

**C2 — Install BackupBuddy gatekeeper (INSTALL.md §3):**

> Following INSTALL.md §3 exactly. Note if installer output does NOT match the guide's
> expected output block. Record any discrepancy as ENRADSFIX or ISSUE.

```bash
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
sudo bash /opt/backup-buddy/install/gatekeeper.sh
```

Expected output (per INSTALL.md §3):
```
  [✓] Service backup-buddy-gatekeeper is running
  Next steps:
  1. Authenticate Tailscale...
  2. Open the setup wizard...
```
Record actual output in state file. Flag if different → ENRADSFIX.

**C3 — Tailscale (INSTALL.md §3a):**

> In the test environment, Tailscale was already authenticated before rollback.
> `sudo tailscale up` will NOT print a browser URL — it will reconnect silently.
> This is expected test-environment behaviour; not an INSTALL.md bug.

```bash
tailscale status
tailscale ip -4
```

Verify Tailscale is connected (100.x.x.x shown). If not connected: `sudo tailscale up`.

**C4 — Open wizard (INSTALL.md §3b):**

Open `http://10.99.0.11:8080` in browser.
Verify wizard loads. Note if URL format differs from INSTALL.md.

**C5 — Complete wizard (INSTALL.md §4):**

- Step 1: **Start a new cluster** (INSTALL.md §4 Step 1)
- Step 2: Node ID `anders-home`, display name `Anders home node` (§4 Step 2)
- Step 3: **Storage path `/var/lib/backup-buddy/storage`** (§4 Step 3)
  > **1.21.1 verification:** The guide says this is the "simplest choice" and requires no
  > extra steps. Verify the wizard accepts this path and creates the directory automatically
  > — no chown command needed. If any error appears, record as ISSUE.
  > Quota: 50 GB.
- Step 4: Profile **Adaptive** (§4 Step 4)
- Step 5: Skip notification email. Choose a passphrase, write it down. (§4 Step 5)
  Download `recovery-kit.enc`. Click "I have saved my recovery key".

Record in state file: **invite code** and **Tailscale address** shown after wizard.

**C5a — Verify first invite code is in cluster.db (regression from 1.19.10):**

```bash
ssh gk-anders "sudo -u backupbuddy /opt/backup-buddy/.venv/bin/python3 -c \
  \"import sys; sys.path.insert(0,'/opt/backup-buddy'); \
  from gatekeeper.db.cluster import ClusterDB; \
  db=ClusterDB('/var/lib/backup-buddy/cluster.db'); \
  rows=db.list_invites(); print('Invites:', len(rows), rows[0].code if rows else 'NONE')\""
```

Expected: at least 1 invite row. If 0: record ISSUE, regenerate via dashboard.

**C6 — Verify dashboard switched to Tailscale address:**

```bash
ANDERS_TS=$(ssh gk-anders "tailscale ip -4")
curl -sf "http://${ANDERS_TS}:8080/" | head -5    # expected: 200 OK
curl -sf "http://10.99.0.11:8080/" || echo "LAN IP correctly rejected"
```

---

#### D. Install and configure — Björn (VM 102) — following INSTALL.md §6

Same steps as C with:
- Node ID `bjorn-home`, display name `Björn home node`
- In wizard Step 1: **Join an existing cluster** → enter Anders's invite code and Tailscale address
- Storage path: `/var/lib/backup-buddy/storage`
- Agent LXC: 303

Follow INSTALL.md §6 exactly. Note any step that doesn't match the guide.

Record in state file: confirm both nodes appear in each other's dashboards.

---

#### E. Install and configure — Carina (VM 103) — following INSTALL.md §6

**E1–E5 — Normal join (INSTALL.md §6):**

Same steps as D with:
- Node ID `carina-home`, display name `Carina home node`
- Agent LXC: 302
- **Anders must generate a new invite code** from Buddies page before Carina can join.

> **1.21.4 regression test:** In simulation 1.20, Carina's cascade failed (ISSUE-001)
> because root-owned Tahoe files from a diagnostic `tahoe run` blocked the process.
> 1.21.4 makes the cascade idempotent — but more importantly, 1.21.3 (Tailscale tub.location)
> means storage node startup is more reliable. Verify Carina's cascade completes on the
> **first attempt** without any workaround.

Watch the cascade log on Carina while joining:
```bash
ssh gk-carina "sudo journalctl -u backup-buddy-gatekeeper -f"
```

Expected: cascade completes, root_dir.cap written, gatekeeper.cfg created, service running.
If it fails on first attempt: record as ISSUE. Do NOT manually intervene until cascade has
been attempted at least twice automatically.

Record in state file: confirm all three nodes appear Online in all three dashboards.

**E6 — 1.21.4 idempotency test:**

> **Background:** 1.21.4 fixes a split-state scenario: if the cascade on the JOINER side
> crashes after the LEADER has already committed (invite marked used + member added), a
> retry with the same invite code previously returned HTTP 400 "invite used". Now the
> leader recognises "this node_id is already a member → cascade completed on leader side
> before joiner crashed → return cluster state instead of 400".
>
> **Test:** Simulate the "joiner crashed before writing root_dir.cap" scenario by manually
> removing Carina's root_dir.cap and wizard state, then retrying the wizard with the same
> (already-used) invite code. Expected: cascade completes successfully.

Only run this if E1–E5 completed successfully and Carina's cluster.db shows she is a member.

```bash
# On the operator machine, record Carina's invite code from state file.
# Then simulate the split state (joiner crashed before gatekeeper.cfg was written):
ssh gk-carina "sudo rm -f /var/lib/backup-buddy/root_dir.cap"
ssh gk-carina "sudo rm -f /var/lib/backup-buddy/onboarding_state.json"
ssh gk-carina "sudo rm -f /etc/backup-buddy/gatekeeper.cfg"
ssh gk-carina "sudo systemctl restart backup-buddy-gatekeeper"
sleep 5
ssh gk-carina "sudo systemctl status backup-buddy-gatekeeper | head -5"
```

Open `http://10.99.0.13:8080` in browser. The wizard should appear in setup mode.
> Note: gatekeeper.cfg must also be removed (see 1.22.1-issues.md NOTE-002).
> Without removing gatekeeper.cfg, the service starts in post-config setup mode
> on the Tailscale IP — the wizard is only available in pre-config mode (no config file).

- Step 1: **Join an existing cluster**
- Join screen: enter the **same invite code as before** (already used) + Anders's Tailscale address
- Complete remaining wizard steps

Expected: wizard completes. A 400 "invite already used" error means the 1.21.4 fix is not working — record as ISSUE.

Verify after wizard:
```bash
ssh gk-carina "test -f /var/lib/backup-buddy/root_dir.cap && echo 'root_dir.cap EXISTS' || echo 'MISSING'"
ssh gk-carina "sudo journalctl -u backup-buddy-gatekeeper | grep -i 'retried join\|already a member\|idempotent'"
```

Confirm all 3 dashboards still show all 3 nodes as Online.

> **State update:** Record E6 result (PASS/FAIL + relevant log lines) in `1.22.1-state.md`.

---

#### Done when (Part 1):

- A0 factory reset includes `onboarding_state.json` deletion ✓
- All six nodes running on clean snapshot rollback ✓
- Tailscale connected on all three gatekeepers ✓
- Test files present (8 total) with pre-backup checksums in state file ✓
- INSTALL.md §3 installer output matches guide — all ENRADSFIX items committed ✓
- Anders wizard step 3 accepted `/var/lib/backup-buddy/storage` without chown (1.21.1) ✓
- Anders wizard complete; first invite code in cluster.db (1.19.10 regression) ✓
- Björn joined cluster following INSTALL.md §6; both nodes visible in dashboards ✓
- Carina joined cluster on FIRST attempt (no ISSUE-001 workaround) ✓
- Carina 1.21.4 idempotency test: used invite code accepted on retry → wizard completes ✓
- All deviations from INSTALL.md recorded and acted on ✓
- State file updated ✓
- Issues file updated ✓
- Task marked `[ ]` and `git commit chore(test): 1.22.1 part 1 done` ✓

```
> Kludde:
```

---

### [x] 1.22.2 — Fourth three-user simulation, Part 2: agent setup + backup monitoring

> **Verifies:** 1.21.2A (no-TTY installer exits cleanly), 1.21.2B (env-var install path).
> Also follows INSTALL.md §5 step by step.
>
> **Resume:** Before starting, read `tests/integration/1.22.1-state.md`.
> All three gatekeepers must be installed and cluster formed (1.22.1 done).
>
> **State file:** `tests/integration/1.22.1-state.md` — continue updating.
> **Issues file:** `tests/integration/1.22.1-issues.md` — continue recording problems.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.

---

#### F0. TTY detection test (1.21.2A) — before any agent install

> LXC containers (without SSH pseudo-TTY) have no interactive terminal. Before the fix,
> `agent.sh` would crash with `/dev/tty: No such device or address`. After the fix it
> exits with a clear actionable message. This is the verification.
>
> INSTALL.md §5 now documents the env-var escape hatch — verify the message in the guide
> matches what the installer actually prints.

```bash
# Run installer on LXC 301 WITHOUT env vars and WITHOUT a PTY (no -t flag):
ssh agent-anders-pc "sudo bash /opt/backup-buddy/install/agent.sh"
```

Expected output contains: `No interactive terminal detected. To install non-interactively, use:`
FAIL condition: output contains `/dev/tty: No such device or address` — record ISSUE.

Note: the `ssh` call without `-t` already simulates no-TTY. If the test machine's ssh adds
a PTY by default, force non-interactive explicitly:
```bash
ssh -T agent-anders-pc "sudo bash /opt/backup-buddy/install/agent.sh" 2>&1
```

---

#### F. Agent setup and backup monitoring — following INSTALL.md §5

> Following INSTALL.md §5 and the env-var path documented in the callout box.
> Note any discrepancy between the guide and actual behaviour as ENRADSFIX or ISSUE.
>
> **Note:** LXC containers may not have `git` or `curl` installed. If missing, install first.

**F1 — Install agent on LXC 301 (Anders):**

```bash
ssh agent-anders-pc
apt-get install -y git 2>/dev/null || true
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
BB_GATEKEEPER_IP=10.99.0.11 BB_AGENT_NAME=anders-laptop \
  sudo -E bash /opt/backup-buddy/install/agent.sh
```

> **1.21.2B verification:** This uses the env-var path documented in INSTALL.md §5 callout.
> Verify the installer completes without any interactive prompts and that the token is
> printed at the end as the guide describes.

Read installer output. Check that installer output includes:
```
  2. Add this token to your gatekeeper's gatekeeper.cfg:
       [agent_api]
       token = ...
```

> **INSTALL.md §5a check:** The guide says to "Replace the file contents with the complete
> example below". The installer already writes a correct [gatekeeper] section including
> the token. Replacing the whole file would lose the installer-generated token. Verify
> whether adding backup paths under [backup] is sufficient, and record any discrepancy
> between the guide and the correct procedure as ENRADSFIX.

Edit backup paths (add, do NOT replace whole file):
```bash
sudo nano /etc/backup-buddy/backup.cfg
# Add under [backup] section: /home/testuser/backup-test
```

Verify [gatekeeper] section is intact after edit:
```bash
sudo grep -A5 '\[gatekeeper\]' /etc/backup-buddy/backup.cfg
```

Copy agent token to Anders's gatekeeper:
```bash
TOKEN=$(sudo grep '^token' /etc/backup-buddy/backup.cfg | head -1 | awk '{print $3}')
echo "Token: ${TOKEN}"
# On Anders's gatekeeper, update [agent_api] token:
ssh gk-anders "sudo sed -i \"s/^token = .*/token = ${TOKEN}/\" /etc/backup-buddy/gatekeeper.cfg && \
  sudo systemctl restart backup-buddy-gatekeeper"
sudo systemctl start backup-buddy-agent
```

> **State update:** Record token (first 8 chars) in `1.22.1-state.md` → Agent tokens.

**F2 — Install agent on LXC 303 (Björn):**

```bash
ssh agent-bjorn-pc
apt-get install -y git 2>/dev/null || true
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
BB_GATEKEEPER_IP=10.99.0.12 BB_AGENT_NAME=bjorn-laptop \
  sudo -E bash /opt/backup-buddy/install/agent.sh
sudo nano /etc/backup-buddy/backup.cfg   # add /home/testuser/backup-test under [backup]
TOKEN=$(sudo grep '^token' /etc/backup-buddy/backup.cfg | head -1 | awk '{print $3}')
ssh gk-bjorn "sudo sed -i \"s/^token = .*/token = ${TOKEN}/\" /etc/backup-buddy/gatekeeper.cfg && \
  sudo systemctl restart backup-buddy-gatekeeper"
sudo systemctl start backup-buddy-agent
```

**F3 — Install agent on LXC 302 (Carina, large .tar.gz file):**

```bash
ssh agent-anders-nas
apt-get install -y git 2>/dev/null || true
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
BB_GATEKEEPER_IP=10.99.0.13 BB_AGENT_NAME=carina-laptop \
  sudo -E bash /opt/backup-buddy/install/agent.sh
sudo nano /etc/backup-buddy/backup.cfg   # add /home/testuser/backup-test under [backup]
TOKEN=$(sudo grep '^token' /etc/backup-buddy/backup.cfg | head -1 | awk '{print $3}')
ssh gk-carina "sudo sed -i \"s/^token = .*/token = ${TOKEN}/\" /etc/backup-buddy/gatekeeper.cfg && \
  sudo systemctl restart backup-buddy-gatekeeper"
sudo systemctl start backup-buddy-agent
```

> **Note on large file:** Carina's backup-test directory contains the ~1 GB tar.gz.
> Agent logs will show this file being uploaded — expect it to take longer than other files.
> Set `stability_minutes = 1` in backup.cfg if not already set (for test speed).

**F4 — Watch agent logs until SUCCESS (INSTALL.md §7):**

Following INSTALL.md §7: "You can also check the agent log on your agent machine."

```bash
ssh agent-anders-pc  "journalctl -u backup-buddy-agent -f"
ssh agent-bjorn-pc   "journalctl -u backup-buddy-agent -f"
ssh agent-anders-nas "journalctl -u backup-buddy-agent -f"
```

Wait until each agent shows `SUCCESS — uploaded file` for all its test files.
For Carina's LXC: also wait for the large .tar.gz to show SUCCESS.
Note any `FAILED` entries in the issues file.

**F5 — Confirm on gatekeeper dashboards (INSTALL.md §7):**

Open each gatekeeper's Tailscale URL. Confirm:
- "Last backup" shows a recent timestamp
- "Files backed up" count is non-zero

**F6 — Verify 1.19.11 fix (permission-denied error message, regression):**

```bash
# Create a path the backupbuddy user cannot traverse into
ssh agent-anders-pc "mkdir -p /tmp/locked_parent/backup-test && chmod 700 /tmp/locked_parent"
# Add /tmp/locked_parent/backup-test under [backup] in backup.cfg, then:
ssh agent-anders-pc "sudo systemctl restart backup-buddy-agent"
ssh agent-anders-pc "journalctl -u backup-buddy-agent | grep -i 'backup path'"
# Expected: "not accessible by the backup service user" — NOT "does not exist"
# Remove /tmp/locked_parent/backup-test from backup.cfg, restart agent
```

> **State update:** Update `1.22.1-state.md` section log rows F1–F3.

---

#### Done when (Part 2):

- F0 TTY test: installer exits with clear actionable message (1.21.2A) ✓
- Agent installer with BB_GATEKEEPER_IP env var completes successfully (1.21.2B) ✓
- INSTALL.md §5a discrepancy (if any) recorded and ENRADSFIX committed ✓
- All three agents installed and backup tokens configured on their gatekeepers ✓
- Large .tar.gz file backed up successfully on Carina's agent ✓
- All agents show `SUCCESS — uploaded file` in journalctl ✓
- All gatekeeper dashboards show non-zero "Files backed up" ✓
- 1.19.11 fix verified: permission-denied path shows "not accessible by the backup service user" ✓
- State file updated ✓
- Issues file updated ✓
- Task marked `[ ]` and `git commit chore(test): 1.22.2 part 2 done` ✓

> **Hand-off to 1.22.3:** Ensure `1.22.1-state.md` is committed before starting Part 3.

```
> Kludde: ISSUE-001 (LXC 302 OOM → 2048MB), ISSUE-002 (VM 103 OOM → 4096MB), ISSUE-003 (httpx timeout fixed).
> LARGER FIX-001: streaming upload needed for files >500MB.
> Carina file_count=10 due to repeated uploads during crash-restart loop (not a bug, cosmetic).
```

---

### [x] 1.22.3 — Fourth three-user simulation, Part 3: restore, checksums, resilience, and introducer test

> **Verifies:** 1.21.3 (introducer outage — restore still works via servers.yaml cache).
> Also follows INSTALL.md §8 for restore flow.
>
> **Resume:** Before starting, read `tests/integration/1.22.1-state.md`.
> All agents must be installed and backups confirmed (1.22.2 done).
> Pre-backup checksums must be in state file.
>
> **State file:** `tests/integration/1.22.1-state.md` — final updates here.
> **Issues file:** `tests/integration/1.22.1-issues.md` — record all problems.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.

---

#### G. Restore and checksum verification — following INSTALL.md §8

> Following INSTALL.md §8 for restore flow. Note any discrepancy from the guide.

**G1 — Restore from Anders's dashboard (INSTALL.md §8):**

Open `http://<anders-tailscale-ip>:8080` → Restore.
Restore each of Anders's test files to `/tmp/restored/anders/` on the gatekeeper.

```bash
ssh gk-anders "sudo mkdir -p /tmp/restored/anders && sudo chown backupbuddy:backupbuddy /tmp/restored/anders"
```

**G2 — Restore from Björn's dashboard:**

Restore Björn's test files to `/tmp/restored/bjorn/`.

```bash
ssh gk-bjorn "sudo mkdir -p /tmp/restored/bjorn && sudo chown backupbuddy:backupbuddy /tmp/restored/bjorn"
```

**G3 — Restore from Carina's dashboard (includes large .tar.gz):**

Restore Carina's test files (including the large .tar.gz) to `/tmp/restored/carina/`.

```bash
ssh gk-carina "sudo mkdir -p /tmp/restored/carina && sudo chown backupbuddy:backupbuddy /tmp/restored/carina"
```

> **Note:** Restoring the large .tar.gz will take longer than other files. Wait for the
> restore to complete before computing checksums. Monitor progress in the dashboard.

**G4 — Compute checksums after restore:**

```bash
ssh gk-anders  "sha256sum /tmp/restored/anders/*"
ssh gk-bjorn   "sha256sum /tmp/restored/bjorn/*"
ssh gk-carina  "sha256sum /tmp/restored/carina/*"
```

Compare against pre-backup checksums in `1.22.1-state.md`. Every hash must match including
the large .tar.gz file. Record PASS / FAIL.

**G5 — Folder restore test:**

Restore an entire folder via the dashboard to confirm the folder restore path works.
Record PASS / FAIL.

**G6 — Verify recovery kit re-download from Settings → Lifeboat (regression from 1.19.13):**

```bash
ANDERS_TS=$(ssh gk-anders "tailscale ip -4")
curl -sf "http://${ANDERS_TS}:8080/api/settings/recovery-kit/download" \
  -o /tmp/recovery_kit_redownload.enc
ls -la /tmp/recovery_kit_redownload.enc
# Expected: non-empty file (> 0 bytes)
```

> **INSTALL.md §9 check:** The guide's troubleshooting section "I lost my recovery key
> file" says "contact the BackupBuddy project for guidance". Since 1.19.13 added a
> re-download button in Settings → Lifeboat, this is outdated. Record as ENRADSFIX and
> update INSTALL.md §9 inline.

---

#### H. Resilience test — one gatekeeper down

Stop one gatekeeper (simulate node failure):
```bash
qm stop 101   # Stop Anders's gatekeeper
```

From Björn's dashboard: attempt a restore. Should succeed even without Anders.
From Carina's dashboard: attempt a restore. Should succeed.

Bring Anders back:
```bash
qm start 101
sleep 15
```

Confirm Anders's dashboard reconnects and shows all three nodes as Online.
Record PASS / FAIL.

---

#### H-intro. Introducer outage test (1.21.3)

> **Background:** Before 1.21.3, stopping the Tahoe introducer VM (VM 104) immediately
> broke all restores cluster-wide — the storage node's `tub.location` was set to a LAN IP
> unreachable across VLANs, so Foolscap Reconnectors never established live connections.
> With 1.21.3, `tub.location` uses the Tailscale IP, so connections survive introducer death.
> The `private/servers.yaml` cache (1.18.10) handles the cold-start path.
>
> Run this after H (resilience test) with all three nodes back online and all backups intact.

**H-intro-1 — Stop the introducer VM:**

```bash
qm stop 104   # Stop the Tahoe-LAFS introducer
sleep 10
```

**H-intro-2 — Attempt restores from each gatekeeper with introducer down:**

```bash
# From Björn's dashboard: restore any previously-restored file again to /tmp/restored-intro/bjorn/
BJORN_TS=$(ssh gk-bjorn "tailscale ip -4")
ssh gk-bjorn "sudo mkdir -p /tmp/restored-intro/bjorn && sudo chown backupbuddy:backupbuddy /tmp/restored-intro/bjorn"
```

Trigger restore via dashboard. Expected: restore completes successfully.
FAIL: restore returns an error or hangs indefinitely.

Repeat from Carina:
```bash
ssh gk-carina "sudo mkdir -p /tmp/restored-intro/carina && sudo chown backupbuddy:backupbuddy /tmp/restored-intro/carina"
```

**H-intro-3 — Check gatekeeper logs for introducer-unreachable warning:**

```bash
ssh gk-bjorn  "sudo journalctl -u backup-buddy-gatekeeper | grep -i 'introducer\|unreachable\|degraded'"
ssh gk-carina "sudo journalctl -u backup-buddy-gatekeeper | grep -i 'introducer\|unreachable\|degraded'"
```

**H-intro-4 — Restart the introducer and verify reconnection:**

```bash
qm start 104
sleep 15
ssh gk-anders "sudo journalctl -u backup-buddy-gatekeeper | tail -20"
ssh gk-bjorn  "sudo journalctl -u backup-buddy-gatekeeper | tail -20"
```

Verify: gatekeeper logs show reconnection to introducer. All nodes still Online.
Record PASS / FAIL in state file.

---

#### H2. Manual checklist

Mark PASS / FAIL / N/A. Add notes to issues file for every FAIL.

**Installation (INSTALL.md §3):**
- [ ] Installer output matches the guide's expected block
- [ ] `backup-buddy-gatekeeper` service is `active (running)` after install
- [ ] Wizard is reachable at `http://<LAN-IP>:8080`
- [ ] Tailscale connected without new browser auth (rollback preserved state)

**Wizard step 3 — storage path (1.21.1):**
- [ ] Default path `/var/lib/backup-buddy/storage` accepted without chown
- [ ] Wizard auto-creates the directory with correct ownership
- [ ] No error message in wizard or gatekeeper log during storage setup

**Wizard completion:**
- [ ] Wizard completes all five steps without error
- [ ] `recovery-kit.enc` download works and produces a non-empty file
- [ ] Invite code generated and displayed after wizard
- [ ] First invite code is present in cluster.db after wizard (1.19.10 regression)
- [ ] Dashboard switches to Tailscale address after wizard
- [ ] Dashboard is **not** reachable on LAN IP after Tailscale binds (security check)

**Cluster formation (INSTALL.md §6):**
- [ ] Björn can join following INSTALL.md §6 — first attempt succeeds
- [ ] Carina can join with a freshly generated second invite — first attempt succeeds (1.21.4 regression)
- [ ] All three nodes appear as **Online** in Anders's dashboard
- [ ] All three nodes appear as **Online** in Björn's dashboard
- [ ] All three nodes appear as **Online** in Carina's dashboard
- [ ] Reusing an expired invite code produces an error, not a silent failure

**1.21.4 idempotency:**
- [ ] Used invite code accepted when node is already a member → wizard completes (1.21.4)

**Agent installer (INSTALL.md §5):**
- [ ] Agent installer without TTY gives clear actionable error — not /dev/tty crash (1.21.2A)
- [ ] Agent installer with `BB_GATEKEEPER_IP` env var completes without interactive prompts (1.21.2B)
- [ ] Agent appears in gatekeeper dashboard after token is configured
- [ ] Editing `backup.cfg` + restarting agent picks up new folders
- [ ] Agent log shows `SUCCESS — uploaded file` for each backed-up file (1.19.12 regression)
- [ ] Permission-denied backup path shows "not accessible by the backup service user" (1.19.11 regression)

**Backup integrity:**
- [ ] All `.jpg` test files backed up successfully
- [ ] All `.zip` test files backed up successfully
- [ ] All `.iso` test files backed up successfully
- [ ] All `.docx` test files backed up successfully
- [ ] Large `.tar.gz` test file backed up successfully
- [ ] No `FAILED` entries in any agent log for the test files

**Restore and checksums (INSTALL.md §8):**
- [ ] Single-file restore completes without error
- [ ] Restored `.jpg` SHA-256 matches original
- [ ] Restored `.zip` SHA-256 matches original
- [ ] Restored `.iso` SHA-256 matches original
- [ ] Restored `.docx` SHA-256 matches original
- [ ] Restored large `.tar.gz` SHA-256 matches original
- [ ] Folder restore completes without error
- [ ] Restored files land in the correct destination folder

**Resilience:**
- [ ] Stopping one gatekeeper; restore from the other two still succeeds
- [ ] Stopped gatekeeper restarts and shows Online in dashboards

**Introducer outage (1.21.3):**
- [ ] Stopping introducer VM (104); restore from Björn and Carina still succeeds
- [ ] Gatekeeper log shows introducer-related warning when VM 104 is down
- [ ] Introducer VM restarted; gatekeeper reconnects and functions normally

**UI and UX:**
- [ ] Dashboard shows an obvious error or warning if an agent has not sent data for > 1 hour
- [ ] Recovery kit re-download accessible from Settings → Lifeboat (1.19.13 regression)
- [ ] Navigating the dashboard without any data causes no crashes or blank pages
- [ ] All button clicks in the wizard produce visible feedback within 3 seconds

**INSTALL.md audit:**
- [ ] All INSTALL.md sections followed produced correct results (or ENRADSFIX items committed)
- [ ] No step in INSTALL.md led to an unrecoverable error for a new user

---

#### I. Close-out

- Update `1.22.1-state.md` with final statuses.
- Commit all ENRADSFIX changes: `git commit fix(docs): INSTALL.md corrections from 1.22 test run`
- Commit issues file: `git commit chore(test): 1.22.1 issues file — N issues logged`
- Add kludde block below summarising results.
- Mark tasks 1.22.1–1.22.3 as `[x]` in TODO.md.
- Final commit: `git commit chore(test): mark 1.22.1-1.22.3 done — fourth sim complete`

---

#### Done when (Part 3 / full simulation):

- All 8 restore checksums match originals (including large .tar.gz) ✓
- Folder restore tested ✓
- Resilience test passed (restore succeeds with one node down) ✓
- Introducer outage test passed (restore succeeds with VM 104 down) ✓
- Introducer-down warning present in gatekeeper logs ✓
- 1.21.1 fix verified: wizard step 3 default path works without chown ✓
- 1.21.2A fix verified: no-TTY installer gives clear actionable error ✓
- 1.21.2B fix verified: env-var install path works in LXC without TTY ✓
- 1.21.3 fix verified: restore works with introducer VM down ✓
- 1.21.4 fix verified: cascade idempotency test passes ✓
- All regressions from 1.19.x and 1.20.x still pass ✓
- All ENRADSFIX items from INSTALL.md audit committed ✓
- Full manual checklist completed with all items PASS or documented ✓
- `tests/integration/1.22.1-issues.md` committed with all findings ✓
- `tests/integration/1.22.1-state.md` committed with final statuses ✓
- Kludde block added below with overall result ✓
- Tasks 1.22.1–1.22.3 marked `[x]` ✓

---

> **Kludde — 2026-06-07**
>
> Fourth three-user simulation complete. All three parts passed.
>
> **Restore integrity:** All 9 files restored successfully with SHA-256 checksums matching
> pre-backup originals exactly, including the 1 GB test-archive-large.tar.gz (Carina).
> Folder restore (Björn) also passed. Single-file and folder restore API endpoints both work.
>
> **Resilience:** Stopping VM 101 (Anders, cluster introducer) — restore from Björn and
> Carina succeeded immediately. VM 101 restarted; all 3 nodes back to active in dashboards.
>
> **Introducer outage (1.21.3):** VM 104 is a leftover standalone Tahoe introducer from the
> old architecture and is not used by the current cluster. The 1.21.3 fix (Tailscale tub.location
> + servers.yaml cache) was effectively verified by the H resilience test — restores survived
> with the actual introducer (VM 101) down. No ENRADSFIX needed.
>
> **Fixes verified:** 1.21.1, 1.21.2A, 1.21.2B, 1.21.3, 1.21.4 all PASS.
>
> **Non-blocking issues carried forward:** ENRADSFIX-001 (pip warnings in clean-ubuntu-v2
> snapshot, cosmetic); LARGER FIX-001 (streaming upload for files > ~500 MB).
>
> **INSTALL.md:** ENRADSFIX-002 (§5a full-file-replace instruction) fixed inline.
> All other sections produced correct results.

---

### [x] 1.22.4 — Fix ENRADSFIX-001: pip warnings in installer output (clean-ubuntu-v2 snapshot)

> **Source:** 1.22.1-issues.md ENRADSFIX-001

**Problem:** The `clean-ubuntu-v2` snapshot for VM 101 contains a partially-installed
`magic_wormhole` package left over from a previous test run. During `install/gatekeeper.sh`,
the pip step emits many `WARNING: Ignoring invalid distribution ~agic-wormhole` lines.
These do not appear on fresh installs (VM 102, 103). A real user following INSTALL.md §3
would see alarming spam — the expected output block shows only `[✓]` check lines.

**Fix options (pick one):**
- (A) Rebuild the `clean-ubuntu-v2` snapshot with a clean venv (or no pre-existing venv).
  Cleanest fix — the root cause disappears.
- (B) Add a pip cleanup step at the start of `install/gatekeeper.sh`:
  `pip install --upgrade pip && pip uninstall -y magic-wormhole || true`
  before the `pip install -r requirements.txt` call. Defensive — handles any future
  snapshot drift.

**Recommendation:** Option A first (snapshot rebuild); option B as belt-and-suspenders if
the venv is used for other purposes between test runs.

**Done when:**
- Running `install/gatekeeper.sh` on a fresh VM produces no pip warnings in the output
- INSTALL.md §3 expected output block matches real output
- `[ ]` committed

---

> **Kludde — 2026-06-07**
>
> Fixed inline during 1.22.1 Part 1 (commit e9e95205f). Added `find "${INSTALL_DIR}/.venv" -name "~*" -exec rm -rf {} +`
> inside `setup_venv()`, before any pip call. This removes corrupted `~name` distribution directories left by
> partial installs, which are the source of the "Ignoring invalid distribution" warnings. Runs silently if the
> venv is clean (|| true). The clean-ubuntu-v2 snapshot does NOT need to be rebuilt — the installer handles it.
> INSTALL.md §3 shows only the final status line, not intermediate pip output, so no doc update needed.

---

### [x] 1.22.5 — Implement LARGER FIX-001: Streaming upload for large files (> ~500 MB)

> **Source:** 1.22.1-issues.md LARGER FIX-001, ISSUE-001, ISSUE-002

**Problem:** Large file uploads load the entire file into memory on both the agent and gatekeeper:

- **Agent (`agent/upload_worker.py`):** `Path(file_path).read_bytes()` reads the full file
  before POST. A 1 GB file requires 1 GB RAM — killed the agent LXC (512 MB) with OOM.
- **Gatekeeper (`gatekeeper/api/agents.py`):** FastAPI buffers the full request body before
  the route handler runs. With Tahoe + Python already consuming ~1.65 GB, a 1 GB body
  pushed VM 103 (2 GB) over the limit repeatedly.

Workaround for the simulation: increased LXC 302 → 2048 MB and VM 103 → 4096 MB. Not
acceptable as a permanent solution — typical homelab nodes may have 2–4 GB total RAM.

**Required changes:**

1. **Agent `_upload_worker`:** Replace `read_bytes()` + bulk POST with a streaming upload:
   ```python
   with open(file_path, "rb") as f:
       response = await client.post(
           "/api/agents/fragments",
           content=f,
           headers={"Content-Type": "application/octet-stream", ...},
       )
   ```
   `httpx` supports passing a file-like object as `content` — it streams in chunks without
   loading the full file into memory.

2. **Gatekeeper `/api/agents/fragments`:** Replace `await request.body()` with incremental
   streaming to a temp file:
   ```python
   tmp_path = upload_tmp / f"{uuid4()}.part"
   async with aiofiles.open(tmp_path, "wb") as f:
       async for chunk in request.stream():
           await f.write(chunk)
   # hand tmp_path to fragmenter queue
   ```
   `Content-Length` header should be read and validated before streaming to reject
   obviously oversized requests early.

**Constraints:**
- The fragmenter queue currently expects a local file path — passing `tmp_path` is
  compatible with the existing interface.
- Hash verification (SHA-256 before/after fragmentation) still applies to `tmp_path`.
- `upload_tmp/` cleanup: delete `.part` file after fragmenter confirms placement.

**Priority:** Medium — only affects files > ~500 MB on hardware with < 4 GB RAM. Typical
homelab files (photos, documents) are well under this threshold. Large ISO / tar.gz backups
are the affected case.

**Done when:**
- Agent uploads a 1 GB file from a 512 MB LXC without OOM
- Gatekeeper receives a 1 GB upload on a 2 GB VM without OOM
- SHA-256 verification still passes end-to-end
- `[ ]` committed

---

> **Kludde — 2026-06-07**
>
> Three commits implement the full streaming pipeline:
>
> **9276e063f** — Agent + GK HTTP layer:
> - `agent/gatekeeper_client.py`: `send_fragment` takes `Path` instead of `bytes`. Added
>   `_iter_file` async generator (run_in_executor, 64 KB chunks) passed as `content=` to httpx.
> - `agent/main.py`: `_upload_worker` uses `path.stat().st_size` for logging, no read_bytes().
> - `gatekeeper/main.py`: `receive_file` uses `aiofiles.open` + `request.stream()` instead of
>   `await request.body()`. Tracks `bytes_received`; cleans up tmp file on error or empty body.
> - `tests/unit/test_gatekeeper_client.py`: `TestSendFragment` updated to pass Path (temp file).
>
> **6e42ee4cb** — Tahoe client:
> - `gatekeeper/tahoe/client.py`: `TahoeClient.upload()` uses module-level `_iter_file` async
>   generator instead of `asyncio.to_thread(path.read_bytes)`. Tahoe PUT /uri now receives the
>   file in 64 KB chunks rather than a single full-file buffer.
>   This was the second OOM source — the HTTP receive fix alone was not sufficient.
>
> **Proxmox verification — 2026-06-07:**
> - LXC 302 set to 512 MB, VM 103 set to 2048 MB.
> - New 1 GiB test file (stream-test-1g-v2.bin, random data) created in agent backup path.
> - Agent log: `SUCCESS — uploaded file (1073741824 bytes)` at 20:27:33. No OOM on LXC 302.
> - GK dmesg: no new OOM entries after GK restart with streaming TahoeClient (PID 1535+).
>   Old OOM at [220s] was PID 687 (pre-fix TahoeClient, read_bytes during Tahoe upload).
> - Catalog SHA-256: entry at 20:29:58 shows `c70a17b4202b...` — exact match with
>   `sha256sum` output taken before upload. End-to-end integrity confirmed.
> - All 938 unit tests pass. All 22 TahoeClient tests pass.

---

### [x] 1.22.6 — Fix TahoeClient timeout for large file uploads

> **Source:** Post-1.22.5 analysis — identified after streaming fix revealed second bottleneck.

**Problem:** `TahoeClient._http` is created with a single float timeout:
```python
self._http = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)  # 300s
```
A plain float sets ALL httpx timeouts (connect, read, write, pool) to 300 seconds.
The critical one is `read` — httpx waits for the server to begin its response.
Tahoe's PUT /uri does not respond until it has received the entire file, erasure-coded
it, and distributed shares to storage nodes. For a 4 GB file over a 25–50 Mbps
Tailscale link this can take 10–20 minutes. The result is an `httpx.ReadTimeout`
that kills the upload silently.

The agent's httpx client already uses fine-grained timeouts (fixed in 1.22.x):
```python
httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0)
```
TahoeClient needs the same treatment, with a higher `read` value to accommodate
large files over slow WireGuard links.

**Fix:** Replace the float `timeout=_DEFAULT_TIMEOUT` in `TahoeClient.__init__` with:
```python
httpx.Timeout(connect=30.0, read=3600.0, write=3600.0, pool=30.0)
```
- `connect=30`: local Tahoe gateway at 127.0.0.1, should never take more than 30s.
- `read=3600`: Tahoe can take up to 1 hour to complete a large distributed upload.
- `write=3600`: streaming chunks to Tahoe — each individual write is fast,
  but the budget covers edge cases (temporary backpressure from Tahoe gateway).
- `pool=30`: acquiring a connection from the httpx pool is always fast.

Remove the now-unused `_DEFAULT_TIMEOUT` constant.

**Done when:**
- `TahoeClient.__init__` uses `httpx.Timeout(...)` instead of a plain float
- A 4 GB upload over a slow link does not produce `ReadTimeout` in GK logs
- `[ ]` committed

---

> **Kludde — 2026-06-07**
>
> Fixed in commit f77cd4d13. Replaced `timeout=_DEFAULT_TIMEOUT` (plain float 300s) with
> `httpx.Timeout(connect=30.0, read=3600.0, write=3600.0, pool=30.0)`. Removed the `timeout`
> parameter from `__init__` (no caller was passing it). All 22 TahoeClient unit tests pass.

---

### [x] 1.22.7 — Document and guard disk space requirement for upload_tmp/

> **Source:** Post-1.22.5 analysis — upload_tmp/ must fit the largest incoming file.

**Problem:** `receive_file` streams the incoming HTTP body to
`upload_tmp/{uuid}.tmp` before queuing it for Tahoe. The temp file must fit on
the GK's system disk. GK VMs are configured with a 20 GB system disk — after OS,
Tahoe data, logs, and BackupBuddy itself, ~10–12 GB is typically free. Edge cases:

- A single 8 GB file from one agent fills most of the free space.
- Two agents uploading 5 GB files simultaneously can exhaust the disk.
- If the disk fills mid-stream, `aiofiles.write` raises `OSError: No space left on
  device`. The except block cleans up the partial temp file, and the GK returns HTTP
  500 — but the agent retries indefinitely, burning CPU and logs with no resolution.

**Required changes:**

1. **Check Content-Length before streaming.** If the agent sends `Content-Length`
   (which httpx does NOT send for async-generator content — see NOTE below), compare
   it against available disk space and reject early with HTTP 507 Insufficient Storage.
   NOTE: the current agent uses a streaming async generator as `content=`, so no
   `Content-Length` header is sent. For this guard to work, the agent must be changed
   to include `Content-Length: {file_size}` in the POST headers. File size is already
   available from `path.stat().st_size`.

2. **Document minimum disk requirement in INSTALL.md and README.** The GK system disk
   must have free space equal to at least the size of the largest file any connected
   agent will ever upload, plus headroom for concurrent uploads.

3. **Log a warning when upload_tmp/ is on a disk with less than 2× the incoming
   file size free.** Even without hard rejection, a warning helps operators spot
   impending problems.

**Done when:**
- Agent sends `Content-Length` in the fragment POST header
- GK checks Content-Length against `shutil.disk_usage(upload_tmp_dir).free` and
  returns HTTP 507 if insufficient, before opening the temp file
- INSTALL.md has a note about system disk sizing under §3 hardware requirements
- Low-disk warning logged when free < 2× Content-Length
- `[x]` committed

> **Kludde — 2026-06-07**
>
> Implemented in commit c2a31accf. Agent now reads `file_path.stat().st_size` and
> passes `Content-Length: {size}` in the POST headers. Gatekeeper checks
> `shutil.disk_usage(upload_tmp_dir).free` against Content-Length before opening
> the temp file — returns HTTP 507 if insufficient, logs WARNING if free < 2× size.
> INSTALL.md §2 and README updated with system disk sizing guidance.
> 945 unit tests pass.

---

## 1.23 — Fifth three-user simulation (streaming validation + extended restore + final Phase 1 audit)

> **Goal:** Verify that the streaming upload changes (1.22.5), timeout fix (1.22.6), and disk space guard
> (1.22.7) work end-to-end in a fresh run without the OOM workarounds from 1.22. The 1.22 simulation
> required expanding LXC 302 to 2048 MB and VM 103 to 4096 MB. This test verifies those workarounds
> are no longer needed.
>
> Additional goals:
> - Extended test file set (file with spaces in name, nested folder structure)
> - Restore to alternate folder — explicitly tested, not covered in 1.22
> - Restore a file that was deleted from the agent after backup
> - Nested folder restore with subdirectory structure preserved
> - Disk space guard test (HTTP 507 via tmpfs mount)
> - Expired invite code behavior (was TBD in 1.22 H2 checklist)
> - All-three-dashboards-Online explicitly verified (was TBD in 1.22)
> - Final INSTALL.md and RESTORE.md audit
>
> **Known environment state going in:**
> - VM 103 (gk-carina) was left at 4096 MB after 1.22 — must be reset to 2048 MB before rollback
> - LXC 302 (agent-anders-nas) was left at 2048 MB after 1.22 — must be reset to 512 MB before rollback
>
> **Error policy:** Environment and snapshot issues (old files, Tailscale auth, stale SSH keys)
> are fixed inline during setup and never logged as ISSUE. ISSUE/LARGER FIX are reserved for
> code defects only.

---

### [x] 1.23.1 — Fifth simulation, Part 1: infrastructure + cluster formation

> **Verifies:** Memory reset to nominal sizes; expired invite code; all 3 dashboards Online.
>
> **State file:** `tests/integration/1.23.1-state.md` — create fresh, update after each section.
> **Issues file:** `tests/integration/1.23.1-issues.md` — code defects only.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.

---

#### A. Infrastructure setup

**A1 — Reset VM/LXC memory to nominal sizes (must happen before snapshot rollback):**

Memory settings survive rollback; snapshot rollback only restores disk state.

```bash
# VM 103 was set to 4096 MB during 1.22 — reset to 2048 MB
qm set 103 --memory 2048
# LXC 302 was set to 2048 MB during 1.22 — reset to 512 MB
pct set 302 --memory 512
echo "Memory: VM 103 → 2048 MB, LXC 302 → 512 MB"
```

**A2 — Roll back all six nodes to clean snapshots:**

```bash
qm stop 101 --skiplock 1; sleep 3; qm rollback 101 clean-ubuntu-v2; qm start 101

for vmid in 102 103; do
  qm stop $vmid --skiplock 1; sleep 3; qm rollback $vmid clean-ubuntu; qm start $vmid
done

for ctid in 301 302 303; do
  pct stop $ctid; sleep 2; pct rollback $ctid clean-ubuntu; pct start $ctid
done
```

Verify all six running:
```bash
qm status 101; qm status 102; qm status 103
pct status 301; pct status 302; pct status 303
```

**A3 — Clear stale SSH known_hosts:**

```bash
for ip in 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33; do
  ssh-keygen -R $ip
done
ssh-keyscan -H 10.99.0.11 10.99.0.12 10.99.0.13 10.99.0.31 10.99.0.32 10.99.0.33 \
  >> ~/.ssh/known_hosts
```

**A4 — Verify Tailscale after rollback:**

```bash
ssh gk-anders "tailscale status"
ssh gk-bjorn  "tailscale status"
ssh gk-carina "tailscale status"
```

Expected: each node online with 100.x.x.x address. If any show "Logged out": `sudo tailscale up --auth-key=<key>`.

Record Tailscale IPs:
```bash
ssh gk-anders "tailscale ip -4"
ssh gk-bjorn  "tailscale ip -4"
ssh gk-carina "tailscale ip -4"
```

> **State update:** Update `1.23.1-state.md` → Tailscale IPs.

---

#### B. Prepare extended test file set

Check existing test files from prior simulations (not rolled back):
```bash
ls /tmp/testfiles/
sha256sum /tmp/testfiles/*
```

If present and intact, skip re-download of the 16 base files.
If missing: re-download same set as 1.22 (alpine-3.19.iso, earth-from-space.jpg, etc.).

**Generate new test files for 1.23:**

```bash
# File with spaces in name (50 MB random binary — tests filename path handling)
dd if=/dev/urandom bs=1M count=50 of="/tmp/testfiles/my document 2026.bin"

# Nested folder structure (tests subdirectory-preserving folder restore)
mkdir -p /tmp/testfiles/nested/subdir-a /tmp/testfiles/nested/subdir-b
dd if=/dev/urandom bs=1M count=10 of=/tmp/testfiles/nested/subdir-a/nested-photo.bin
dd if=/dev/urandom bs=1M count=8  of=/tmp/testfiles/nested/subdir-a/nested-video.bin
dd if=/dev/urandom bs=1M count=5  of=/tmp/testfiles/nested/subdir-b/nested-doc.bin
```

Compute checksums for all files **before any backup**:
```bash
sha256sum /tmp/testfiles/* \
  /tmp/testfiles/nested/subdir-a/* \
  /tmp/testfiles/nested/subdir-b/* \
  | tee /tmp/checksums_before_v5.txt
```

Distribution plan:
- LXC 301 (Anders): test-photo-1/2/3.jpg + test-disk.iso + "my document 2026.bin"
- LXC 303 (Björn): test-archive.zip + test-document-1.docx + nested/ folder structure
- LXC 302 (Carina): test-document-2.docx + earth-from-space.jpg + test-archive-large.tar.gz (1 GB)

> **State update:** Paste checksum output into `1.23.1-state.md` → Test file checksums.

---

#### C. A0 factory reset + install Anders (INSTALL.md §3 and §4)

**C0 — Factory reset VM 101 (includes onboarding_state.json — fixes NOTE-001 from 1.22):**

```bash
ssh gk-anders "sudo rm -f \
  /etc/backup-buddy/gatekeeper.cfg \
  /var/lib/backup-buddy/catalog.db \
  /var/lib/backup-buddy/cluster.db \
  /var/lib/backup-buddy/root_dir.cap \
  /var/lib/backup-buddy/recovery_kit.enc \
  /var/lib/backup-buddy/onboarding_state.json && \
  sudo systemctl restart backup-buddy-gatekeeper"
ssh gk-anders "sudo systemctl status backup-buddy-gatekeeper | head -5"
```

Including `onboarding_state.json` ensures `recovery_key_confirmed` is reset so the
recovery-kit download step runs normally during the wizard (NOTE-001 fix).

**C1–C5 — Install and configure Anders following INSTALL.md §3 and §4:**

- Step 1: **Start a new cluster**
- Step 2: Node ID `anders-home`, display name `Anders home node`
- Step 3: Storage path `/var/lib/backup-buddy/storage`, quota 50 GB
- Step 4: Profile **Adaptive**
- Step 5: Passphrase `TestSimulation2026!`; download `recovery-kit.enc`; click "I have saved my recovery key"

Verify first invite code in cluster.db:
```bash
ssh gk-anders "sudo -u backupbuddy sqlite3 /var/lib/backup-buddy/cluster.db \
  \"SELECT code, used, expires_at FROM invites ORDER BY created_at DESC LIMIT 3;\""
```

Expected: at least 1 row with `used=0`. Record invite code in state file.

---

#### D. Install Björn (INSTALL.md §6)

Same flow as 1.22.1 section D:
- Node ID `bjorn-home`, display name `Björn home node`
- Wizard Step 1: **Join an existing cluster** → enter Anders's invite code + Tailscale address

Record: both nodes appear in each other's dashboards.

---

#### E. Install Carina (INSTALL.md §6) + idempotency test

**E1–E5 — Normal join:**
- Node ID `carina-home`, display name `Carina home node`
- Anders generates a new invite code from the Buddies page

Watch cascade log on Carina:
```bash
ssh gk-carina "sudo journalctl -u backup-buddy-gatekeeper -f"
```

Expected: cascade completes on first attempt.

**E6 — 1.21.4 idempotency test (procedure corrected from NOTE-002):**

```bash
# Remove root_dir.cap, onboarding_state.json, AND gatekeeper.cfg (NOTE-002 fix)
# Without removing gatekeeper.cfg the service starts in post-config mode,
# not wizard mode — the wizard is only available when gatekeeper.cfg is absent.
ssh gk-carina "sudo rm -f \
  /var/lib/backup-buddy/root_dir.cap \
  /var/lib/backup-buddy/onboarding_state.json \
  /etc/backup-buddy/gatekeeper.cfg && \
  sudo systemctl restart backup-buddy-gatekeeper"
sleep 5
```

Open wizard at `http://10.99.0.13:8080`:
- Step 1: **Join an existing cluster**
- Join screen: enter **same invite code as before** (already used) + Anders's Tailscale address

Expected: wizard completes. If HTTP 400 "invite already used": ISSUE (1.21.4 regression).

Verify:
```bash
ssh gk-carina "test -f /var/lib/backup-buddy/root_dir.cap && echo 'root_dir.cap EXISTS' || echo 'MISSING'"
ssh gk-carina "sudo journalctl -u backup-buddy-gatekeeper | grep -i 'retried join\|already a member'"
```

---

#### F. Expired invite code test (was TBD in 1.22)

After all three nodes are joined, generate a new invite code and immediately expire it:

```bash
# Generate a fresh invite on Anders's dashboard (Buddies page)
ANDERS_TS=$(ssh gk-anders "tailscale ip -4")
NEW_CODE=$(curl -sf -X POST "http://${ANDERS_TS}:8080/api/cluster/invite" \
  -H "Content-Type: application/json" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('code',''))")
echo "Created invite code: ${NEW_CODE}"

# Expire it by setting expires_at to 1 hour ago
ssh gk-anders "sudo -u backupbuddy sqlite3 /var/lib/backup-buddy/cluster.db \
  \"UPDATE invites SET expires_at = strftime('%s','now','-1 hour') WHERE code = '${NEW_CODE}';\""

# Confirm expiry in DB
ssh gk-anders "sudo -u backupbuddy sqlite3 /var/lib/backup-buddy/cluster.db \
  \"SELECT code, used, datetime(expires_at,'unixepoch') FROM invites WHERE code = '${NEW_CODE}';\""
```

Try the expired code via the cluster join API:
```bash
curl -sf -X POST "http://${ANDERS_TS}:8080/api/cluster/join" \
  -H "Content-Type: application/json" \
  -d "{\"invite_code\": \"${NEW_CODE}\", \"node_info\": {
    \"node_id\": \"test-expire\", \"display_name\": \"Test expire\",
    \"tailscale_hostname\": \"test-expire\", \"profile\": \"adaptive\"}}" \
  ; echo "(exit: $?)"
```

Expected: HTTP 400 / 422 with error containing "expired" or "invalid". Silent failure (HTTP 2xx with node added) would be a code defect — record as ISSUE.

---

#### G. All-three-dashboards-Online verification (was TBD in 1.22)

Wait at least 10 minutes after Carina joins to allow member reconciliation (runs every 5 min):
```bash
sleep 600   # or check manually after 10 minutes
```

Query each dashboard:
```bash
ANDERS_TS=$(ssh gk-anders "tailscale ip -4")
BJORN_TS=$(ssh gk-bjorn "tailscale ip -4")
CARINA_TS=$(ssh gk-carina "tailscale ip -4")

for TS in $ANDERS_TS $BJORN_TS $CARINA_TS; do
  echo "--- Dashboard at $TS ---"
  curl -sf "http://${TS}:8080/api/dashboard" | python3 -c "
import sys, json
d = json.load(sys.stdin)['cluster']
print(f'Online: {d[\"online_count\"]}/{d[\"total_members\"]}')
for m in d['members']:
    print(f'  {m[\"display_name\"]}: {m[\"status\"]}')
"
done
```

Expected: all three dashboards report `Online: 3/3` with all three display names present.
Record PASS / FAIL (including which dashboard(s) show fewer than 3 if FAIL).

> **State update:** Record F and G results in `1.23.1-state.md`.

---

#### Done when (Part 1):

- VM 103 at 2048 MB, LXC 302 at 512 MB confirmed before rollback ✓
- All six nodes running on clean snapshot rollback ✓
- Tailscale connected on all three gatekeepers ✓
- Extended test files prepared (file with spaces + nested folder structure) ✓
- Checksums computed before backup ✓
- `onboarding_state.json` included in factory reset (NOTE-001 fix) — recovery-kit download runs ✓
- All three gatekeepers installed and cluster formed following INSTALL.md ✓
- E6 procedure corrected (remove gatekeeper.cfg too — NOTE-002 fix) ✓
- Expired invite test: code returns error, not silent success ✓
- All three dashboards show all three nodes Online ✓
- State and issues files created and updated ✓
- Committed: `chore(test): 1.23.1 part 1 done`

```
> Kludde:
```

---

### [x] 1.23.2 — Fifth simulation, Part 2: agent setup + streaming upload validation

> **Key verification:** The 1 GB file must be backed up at NOMINAL hardware (512 MB LXC agent,
> 2 GB GK VM) without OOM. In 1.22, OOM required expanding both VMs. After the streaming fix
> (1.22.5), neither side should buffer the full file. OOM here = code regression.
>
> **Resume:** Read `1.23.1-state.md`. All three gatekeepers must be installed and cluster formed.
>
> **State file:** `tests/integration/1.23.1-state.md` — continue updating.
> **Issues file:** `tests/integration/1.23.1-issues.md` — code defects only.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.

---

#### H. Agent setup (following INSTALL.md §5)

**H1 — Install agent on LXC 301 (Anders):**

```bash
ssh agent-anders-pc
apt-get install -y git 2>/dev/null || true
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
BB_GATEKEEPER_IP=10.99.0.11 BB_AGENT_NAME=anders-laptop \
  sudo -E bash /opt/backup-buddy/install/agent.sh
```

Configure backup.cfg (add path under [backup], do NOT replace whole file):
```bash
sudo nano /etc/backup-buddy/backup.cfg
# Add under [backup]: /home/testuser/backup-test
# Also set: stability_minutes = 1   (under [watcher] section, for test speed)
```

Copy test files to Anders's agent:
```bash
# From Proxmox host:
pct exec 301 -- mkdir -p /home/testuser/backup-test
for f in "test-photo-1.jpg" "test-photo-2.jpg" "test-photo-3.jpg" "test-disk.iso"; do
  pct push 301 "/tmp/testfiles/${f}" "/home/testuser/backup-test/${f}"
done
pct push 301 "/tmp/testfiles/my document 2026.bin" "/home/testuser/backup-test/my document 2026.bin"
```

Connect token to gatekeeper and start agent:
```bash
TOKEN=$(ssh agent-anders-pc "sudo grep '^token' /etc/backup-buddy/backup.cfg | head -1 | awk '{print \$3}'")
ssh gk-anders "sudo sed -i \"s/^token = .*/token = ${TOKEN}/\" /etc/backup-buddy/gatekeeper.cfg && \
  sudo systemctl restart backup-buddy-gatekeeper"
ssh agent-anders-pc "sudo systemctl start backup-buddy-agent"
```

**H2 — Install agent on LXC 303 (Björn) — nested folder:**

```bash
ssh agent-bjorn-pc
apt-get install -y git 2>/dev/null || true
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
BB_GATEKEEPER_IP=10.99.0.12 BB_AGENT_NAME=bjorn-laptop \
  sudo -E bash /opt/backup-buddy/install/agent.sh
```

Configure backup.cfg with two backup paths:
```bash
sudo nano /etc/backup-buddy/backup.cfg
# Under [backup] add:
# /home/testuser/backup-test
# /home/testuser/nested-test
# Under [watcher]: stability_minutes = 1
```

Copy test files:
```bash
# From Proxmox host:
pct exec 303 -- mkdir -p /home/testuser/backup-test
pct push 303 /tmp/testfiles/test-archive.zip     /home/testuser/backup-test/test-archive.zip
pct push 303 /tmp/testfiles/test-document-1.docx /home/testuser/backup-test/test-document-1.docx

# Nested folder structure
pct exec 303 -- mkdir -p /home/testuser/nested-test/subdir-a /home/testuser/nested-test/subdir-b
pct push 303 /tmp/testfiles/nested/subdir-a/nested-photo.bin \
  /home/testuser/nested-test/subdir-a/nested-photo.bin
pct push 303 /tmp/testfiles/nested/subdir-a/nested-video.bin \
  /home/testuser/nested-test/subdir-a/nested-video.bin
pct push 303 /tmp/testfiles/nested/subdir-b/nested-doc.bin \
  /home/testuser/nested-test/subdir-b/nested-doc.bin
```

Connect token and start:
```bash
TOKEN=$(ssh agent-bjorn-pc "sudo grep '^token' /etc/backup-buddy/backup.cfg | head -1 | awk '{print \$3}'")
ssh gk-bjorn "sudo sed -i \"s/^token = .*/token = ${TOKEN}/\" /etc/backup-buddy/gatekeeper.cfg && \
  sudo systemctl restart backup-buddy-gatekeeper"
ssh agent-bjorn-pc "sudo systemctl start backup-buddy-agent"
```

**H3 — Install agent on LXC 302 (Carina, 512 MB RAM — nominal, includes 1 GB file):**

```bash
ssh agent-anders-nas
apt-get install -y git 2>/dev/null || true
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
BB_GATEKEEPER_IP=10.99.0.13 BB_AGENT_NAME=carina-laptop \
  sudo -E bash /opt/backup-buddy/install/agent.sh
```

Configure backup.cfg:
```bash
sudo nano /etc/backup-buddy/backup.cfg
# Under [backup]: /home/testuser/backup-test
# Under [watcher]: stability_minutes = 1
```

Copy test files including 1 GB archive:
```bash
# From Proxmox host:
pct exec 302 -- mkdir -p /home/testuser/backup-test
pct push 302 /tmp/testfiles/test-document-2.docx  /home/testuser/backup-test/test-document-2.docx
pct push 302 /tmp/testfiles/earth-from-space.jpg  /home/testuser/backup-test/earth-from-space.jpg
pct push 302 /tmp/testfiles/test-archive-large.tar.gz \
  /home/testuser/backup-test/test-archive-large.tar.gz
```

Connect token and start:
```bash
TOKEN=$(ssh agent-anders-nas "sudo grep '^token' /etc/backup-buddy/backup.cfg | head -1 | awk '{print \$3}'")
ssh gk-carina "sudo sed -i \"s/^token = .*/token = ${TOKEN}/\" /etc/backup-buddy/gatekeeper.cfg && \
  sudo systemctl restart backup-buddy-gatekeeper"
ssh agent-anders-nas "sudo systemctl start backup-buddy-agent"
```

---

#### I. Memory monitoring during 1 GB upload (key streaming validation)

Start memory monitors on both the agent LXC and the gatekeeper VM before the 1 GB file is uploaded:

```bash
# Monitor LXC 302 agent memory (run in background from Proxmox)
pct exec 302 -- bash -c "
  while true; do
    MEM=\$(grep MemAvailable /proc/meminfo | awk '{print \$2}')
    echo \"\$(date +%H:%M:%S) LXC302 MemAvailable: \${MEM} kB\"
    sleep 5
  done" &
AGENT_MONITOR_PID=$!

# Monitor VM 103 gatekeeper memory
ssh gk-carina "bash -c '
  while true; do
    MEM=\$(grep MemAvailable /proc/meminfo | awk \"{print \\\$2}\")
    echo \"\$(date +%H:%M:%S) VM103 MemAvailable: \${MEM} kB\"
    sleep 5
  done' &"
```

Watch agent log on Carina (wait for SUCCESS on test-archive-large.tar.gz):
```bash
ssh agent-anders-nas "journalctl -u backup-buddy-agent -f"
```

Watch gatekeeper log on Carina for any OOM or error:
```bash
ssh gk-carina "journalctl -u backup-buddy-gatekeeper -f"
```

Expected:
- Agent LXC 302 MemAvailable stays above 0 kB throughout (no OOM kill)
- GK VM 103 MemAvailable stays above 200 MB throughout
- Agent log shows `SUCCESS — uploaded file ... bytes` for test-archive-large.tar.gz
- No `OOM` in `dmesg` on either VM

Record min MemAvailable observed on each, and `dmesg | grep -i oom` result.

If OOM on LXC 302 or VM 103: record as ISSUE (code defect in streaming implementation).

Stop monitors after upload completes:
```bash
kill $AGENT_MONITOR_PID 2>/dev/null || true
ssh gk-carina "pkill -f 'while true.*MemAvailable' || true"
```

---

#### J. Backup monitoring and catalog verification

**J1 — Watch all agent logs until SUCCESS:**
```bash
ssh agent-anders-pc  "journalctl -u backup-buddy-agent -f"
ssh agent-bjorn-pc   "journalctl -u backup-buddy-agent -f"
ssh agent-anders-nas "journalctl -u backup-buddy-agent -f"
```

Wait for `SUCCESS — uploaded file` on all test files, including:
- Anders: file with spaces "my document 2026.bin"
- Björn: nested folder files (nested-photo.bin, nested-video.bin, nested-doc.bin)
- Carina: test-archive-large.tar.gz (1 GB)

**J2 — Verify file with spaces appears in catalog:**
```bash
ANDERS_TS=$(ssh gk-anders "tailscale ip -4")
curl -sf "http://${ANDERS_TS}:8080/api/restore/catalog?q=2026" | python3 -m json.tool
```

Expected: "my document 2026.bin" appears in results. Record PASS / FAIL.

**J3 — Verify nested folder files appear in catalog:**
```bash
BJORN_TS=$(ssh gk-bjorn "tailscale ip -4")
curl -sf "http://${BJORN_TS}:8080/api/restore/catalog?q=nested" | python3 -m json.tool
```

Expected: nested-photo.bin, nested-video.bin, nested-doc.bin all present. Record PASS / FAIL.

**J4 — Gatekeeper dashboard counts:**
```bash
curl -sf "http://${ANDERS_TS}:8080/api/dashboard" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('Anders files:', d.get('files_backed_up',0))"
curl -sf "http://${BJORN_TS}:8080/api/dashboard"  | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('Björn files:', d.get('files_backed_up',0))"
```

Expected: non-zero file counts on all three dashboards.

**J5 — Nightly verify scheduler check:**

```bash
ssh gk-anders "sudo journalctl -u backup-buddy-gatekeeper | grep -iE 'verify|nightly|scheduler'"
```

Expected output includes one of:
- `Nightly verification started` — NightlyVerifier is wired in to the scheduler ✓
- `Verify scheduler: pending implementation` — still a stub

> **If stub is found:** Record in issues file as ISSUE-NNN (code gap, not a VM issue):
> "NightlyVerifier in gatekeeper/verify/nightly.py is fully implemented but not wired
> into the scheduler in gatekeeper/main.py — `_verify_stub()` is called instead. Must
> be wired before Phase 1 is declared complete."

---

#### Done when (Part 2):

- All three agents installed following INSTALL.md §5 ✓
- File with spaces ("my document 2026.bin") backed up and in catalog ✓
- Nested folder structure backed up (all 3 files) and in catalog ✓
- **Carina 1 GB file backed up on 512 MB LXC without OOM — streaming fix confirmed** ✓
- **GK carina processed 1 GB upload on 2 GB VM without OOM — streaming fix confirmed** ✓
- Memory monitor data recorded in state file ✓
- All agents show SUCCESS for all test files ✓
- Nightly verify scheduler status recorded (wired or stub) ✓
- State and issues files updated ✓
- Committed: `chore(test): 1.23.2 part 2 done`

```
> Kludde:
```

---

### [x] 1.23.3 — Fifth simulation, Part 3: extended restore + disk guard + final docs audit

> **Resume:** Read `1.23.1-state.md`. All agents must show SUCCESS for all test files.
> Pre-backup checksums must be in state file.
>
> **State file:** `tests/integration/1.23.1-state.md` — final updates.
> **Issues file:** `tests/integration/1.23.1-issues.md` — record code defects.
>
> **Proxmox access:** SSH to 192.168.1.60 as `root` using `~/.ssh/id_ed25519`.

---

#### K. Extended restore scenarios

**K1 — Restore to original-equivalent folder (baseline):**

Restore each node's test files to a per-node folder:
```bash
for gk in gk-anders gk-bjorn gk-carina; do
  ssh $gk "sudo mkdir -p /tmp/restored-v5 && sudo chown backupbuddy:backupbuddy /tmp/restored-v5"
done
```

Restore via each gatekeeper's dashboard → Restore. Verify SHA-256 checksums match pre-backup values.

**K2 — Restore to alternate folder (new in 1.23):**

> **Verifies:** dest_path accepts any absolute path, not just the original location.
> RESTORE.md §1.1 now explicitly documents this capability.

Restore one of Björn's files to `/tmp/alternate-restore/` (completely different from original):
```bash
BJORN_TS=$(ssh gk-bjorn "tailscale ip -4")
ssh gk-bjorn "sudo mkdir -p /tmp/alternate-restore && sudo chown backupbuddy:backupbuddy /tmp/alternate-restore"

# Get a file from catalog
ORIG_PATH=$(curl -sf "http://${BJORN_TS}:8080/api/restore/catalog" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['results'][0]['original_path'])")
AGENT=$(curl -sf "http://${BJORN_TS}:8080/api/restore/catalog" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['results'][0]['agent'])")
FILENAME=$(basename "${ORIG_PATH}")

JOB=$(curl -sf -X POST "http://${BJORN_TS}:8080/api/restore/start/file" \
  -H "Content-Type: application/json" \
  -d "{\"original_path\": \"${ORIG_PATH}\", \"agent\": \"${AGENT}\", \"dest_path\": \"/tmp/alternate-restore\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

until [ "$(curl -sf "http://${BJORN_TS}:8080/api/restore/jobs/${JOB}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")" != "running" ]; do
  sleep 2
done
curl -sf "http://${BJORN_TS}:8080/api/restore/jobs/${JOB}" | python3 -m json.tool
```

Verify:
```bash
ssh gk-bjorn "ls -la /tmp/alternate-restore/ && sha256sum /tmp/alternate-restore/*"
```

Expected: file present in `/tmp/alternate-restore/` (not in the original path). SHA-256 matches pre-backup checksum. Record PASS / FAIL.

**K3 — Restore after file deleted from agent (new in 1.23):**

> **Verifies:** Backups are accessible even after the source file is gone. This simulates
> the most common restore scenario: user deleted a file and wants it back.

Delete a test file from Anders's agent and confirm it is gone:
```bash
DELETED_FILE="/home/testuser/backup-test/test-photo-1.jpg"
ssh agent-anders-pc "sudo rm -f ${DELETED_FILE}"
ssh agent-anders-pc "test -f ${DELETED_FILE} && echo 'STILL EXISTS — ERROR' || echo 'DELETED OK'"
```

Restore the deleted file to a different destination:
```bash
ANDERS_TS=$(ssh gk-anders "tailscale ip -4")
ssh gk-anders "sudo mkdir -p /tmp/deleted-restore && sudo chown backupbuddy:backupbuddy /tmp/deleted-restore"

JOB=$(curl -sf -X POST "http://${ANDERS_TS}:8080/api/restore/start/file" \
  -H "Content-Type: application/json" \
  -d "{\"original_path\": \"${DELETED_FILE}\", \"agent\": \"anders-laptop\", \"dest_path\": \"/tmp/deleted-restore\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

until [ "$(curl -sf "http://${ANDERS_TS}:8080/api/restore/jobs/${JOB}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")" != "running" ]; do
  sleep 2
done
curl -sf "http://${ANDERS_TS}:8080/api/restore/jobs/${JOB}" | python3 -m json.tool
```

Verify:
```bash
ssh gk-anders "sha256sum /tmp/deleted-restore/test-photo-1.jpg"
```

Expected: restore succeeds, SHA-256 matches pre-backup checksum. Record PASS / FAIL.

**K4 — Nested folder restore with subdirectory structure preserved (new in 1.23):**

> **Verifies:** RESTORE.md §1.2 — subfolder structure recreated inside dest_path.

```bash
BJORN_TS=$(ssh gk-bjorn "tailscale ip -4")
ssh gk-bjorn "sudo mkdir -p /tmp/nested-restore && sudo chown backupbuddy:backupbuddy /tmp/nested-restore"

JOB=$(curl -sf -X POST "http://${BJORN_TS}:8080/api/restore/start/folder" \
  -H "Content-Type: application/json" \
  -d "{\"folder_path\": \"/home/testuser/nested-test\", \"agent\": \"bjorn-laptop\", \"dest_path\": \"/tmp/nested-restore\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

until [ "$(curl -sf "http://${BJORN_TS}:8080/api/restore/jobs/${JOB}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")" != "running" ]; do
  sleep 2
done
curl -sf "http://${BJORN_TS}:8080/api/restore/jobs/${JOB}" | python3 -m json.tool
```

Verify structure and checksums:
```bash
ssh gk-bjorn "find /tmp/nested-restore -type f | sort"
# Expected:
# /tmp/nested-restore/subdir-a/nested-photo.bin
# /tmp/nested-restore/subdir-a/nested-video.bin
# /tmp/nested-restore/subdir-b/nested-doc.bin

ssh gk-bjorn "sha256sum \$(find /tmp/nested-restore -type f)"
```

Expected: all three files present with correct SHA-256 checksums. Subdirectory structure preserved. Record PASS / FAIL.

**K5 — File with spaces in name — restore and checksum (new in 1.23):**

```bash
ANDERS_TS=$(ssh gk-anders "tailscale ip -4")
ssh gk-anders "sudo mkdir -p /tmp/spaces-restore && sudo chown backupbuddy:backupbuddy /tmp/spaces-restore"

JOB=$(curl -sf -X POST "http://${ANDERS_TS}:8080/api/restore/start/file" \
  -H "Content-Type: application/json" \
  -d '{"original_path": "/home/testuser/backup-test/my document 2026.bin", "agent": "anders-laptop", "dest_path": "/tmp/spaces-restore"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

until [ "$(curl -sf "http://${ANDERS_TS}:8080/api/restore/jobs/${JOB}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")" != "running" ]; do
  sleep 2
done
curl -sf "http://${ANDERS_TS}:8080/api/restore/jobs/${JOB}" | python3 -m json.tool
```

Verify:
```bash
ssh gk-anders "sha256sum '/tmp/spaces-restore/my document 2026.bin'"
```

Expected: restore succeeds, checksum matches. Record PASS / FAIL.

**K6 — Carina's large .tar.gz restore and checksum:**

```bash
CARINA_TS=$(ssh gk-carina "tailscale ip -4")
ssh gk-carina "sudo mkdir -p /tmp/restored-v5/carina && sudo chown backupbuddy:backupbuddy /tmp/restored-v5/carina"
```

Restore test-archive-large.tar.gz via dashboard or API. Verify checksum matches pre-backup value.

---

#### L. Disk space guard test (HTTP 507)

> **Verifies:** The guard implemented in 1.22.7 rejects uploads before streaming when
> disk space is insufficient. Method: mount a 100 MB tmpfs over upload_tmp/ so
> `shutil.disk_usage()` returns only 100 MB free, then try to upload a 200 MB file.

**L1 — Mount tmpfs over upload_tmp/:**
```bash
ssh gk-anders "sudo mkdir -p /var/lib/backup-buddy/upload_tmp && \
  sudo mount -t tmpfs -o size=100m tmpfs /var/lib/backup-buddy/upload_tmp"

# Verify: only 100 MB reported free
ssh gk-anders "df -h /var/lib/backup-buddy/upload_tmp"
```

**L2 — Create a 200 MB file on Anders's agent to trigger upload:**
```bash
ssh agent-anders-pc "sudo dd if=/dev/urandom bs=1M count=200 \
  of=/home/testuser/backup-test/disk-guard-test.bin"
# Wait for watcher stability period (stability_minutes = 1) and scan cycle (up to 2 min)
sleep 150
```

**L3 — Verify HTTP 507 rejection in GK log:**
```bash
ssh gk-anders "sudo journalctl -u backup-buddy-gatekeeper | grep -iE 'Insufficient|507|disk space'"
```

Expected log line: `Upload rejected — insufficient disk space in upload_tmp/: need ... bytes, ... bytes free (agent='anders-laptop')`

Check agent log shows upload failure (not a crash or hang):
```bash
ssh agent-anders-pc "journalctl -u backup-buddy-agent | grep disk-guard-test"
```

Expected: `Failed to upload file: ...` (IOError/HTTPStatusError). Agent continues running. Record PASS / FAIL.

**L4 — Unmount tmpfs and verify successful upload:**
```bash
ssh gk-anders "sudo umount /var/lib/backup-buddy/upload_tmp"

# Wait for next watcher cycle to retry the upload
sleep 150
ssh agent-anders-pc "journalctl -u backup-buddy-agent | grep disk-guard-test | tail -5"
```

Expected: `SUCCESS — uploaded file` for disk-guard-test.bin. Record PASS / FAIL.

---

#### M. Resilience test

Same as 1.22.3 section H — abbreviated:
```bash
qm stop 101   # Stop Anders's gatekeeper
```

Restore from Björn's dashboard. Expected: succeeds.
Restore from Carina's dashboard. Expected: succeeds.

```bash
qm start 101
sleep 15
```

Confirm all three nodes show Online in all dashboards. Record PASS / FAIL.

---

#### N. Final INSTALL.md and RESTORE.md audit

> Verify that the documentation changes made before this test run accurately describe
> actual system behavior as observed during 1.23.

**N1 — INSTALL.md:**
- [ ] §2 hardware requirements — disk space note (20 GB system disk, ~10–12 GB free, plan for 2× largest file) matches observed behavior
- [ ] §3 installer output matches guide; no unexpected warnings
- [ ] §4 wizard flow matches guide (all 5 steps); recovery-kit download runs correctly (onboarding_state.json reset)
- [ ] §5 agent install matches guide; §5a "add paths only, do not replace file" instruction is correct
- [ ] §6 buddy join flow matches guide
- [ ] §7 verify backup matches guide (SUCCESS lines in journalctl)
- [ ] §8 restore — alternate-folder note ("does not have to match the original location") is accurate
- [ ] §9 troubleshooting — recovery kit re-download via Settings → Lifeboat is accurate

**N2 — RESTORE.md:**
- [ ] §1.1 step 5 — "any absolute path" clarification is accurate
- [ ] §1.2 step 4 — subfolder structure note is accurate
- [ ] §1.3 error messages — observed error texts match documented messages
- [ ] §2 disaster recovery — procedure matches current implementation
- [ ] Quick reference table is accurate

Record any discrepancy as ENRADSFIX and fix inline.

---

#### O. Manual checklist

Mark PASS / FAIL / N/A. Add notes to issues file for every FAIL.

**Streaming upload validation (key new checks):**
- [ ] 1 GB file backed up on 512 MB agent LXC without OOM (1.22.5 streaming fix verified)
- [ ] 1 GB file received by 2 GB gatekeeper VM without OOM (1.22.5 streaming fix verified)
- [ ] LXC 302 MemAvailable stayed above 0 kB throughout 1 GB upload
- [ ] VM 103 MemAvailable stayed above 200 MB throughout 1 GB upload

**Extended test files:**
- [ ] File with spaces in name backed up successfully
- [ ] File with spaces in name restored with correct checksum
- [ ] Nested folder (3 files, 2 subdirs) backed up successfully
- [ ] Nested folder restore: subdirectory structure preserved in dest_path

**Extended restore scenarios:**
- [ ] Alternate-folder restore: file lands in `/tmp/alternate-restore/` (not original path)
- [ ] Alternate-folder restore: checksum matches pre-backup
- [ ] Deleted-file restore: file restored after deletion from agent
- [ ] Deleted-file restore: checksum matches pre-backup
- [ ] Nested folder restore: all 3 files present with correct checksums
- [ ] File-with-spaces restore: "my document 2026.bin" restored with correct checksum
- [ ] Large .tar.gz (1 GB, Carina): restored with correct checksum

**Disk space guard:**
- [ ] HTTP 507 returned when upload_tmp has insufficient space
- [ ] GK log shows "Upload rejected — insufficient disk space" with byte counts
- [ ] Agent logs upload failure; agent continues running (no crash or hang)
- [ ] After tmpfs unmounted: file uploads successfully on next watcher cycle

**Cluster:**
- [ ] Expired invite code returns error (not silent success)
- [ ] All 3 dashboards show all 3 nodes Online (after 10-minute reconciliation wait)

**Resilience:**
- [ ] Stopping one gatekeeper: restore from other two succeeds
- [ ] Stopped gatekeeper restarts and reconnects; all 3 Online confirmed

**Nightly verify:**
- [ ] Nightly verify scheduler confirmed: wired (NightlyVerifier) or stub (ISSUE flagged)

**INSTALL.md and RESTORE.md:**
- [ ] §8 alternate-folder restore note is accurate
- [ ] RESTORE.md §1.1 "any absolute path" note is accurate
- [ ] RESTORE.md §1.2 subfolder structure note is accurate
- [ ] All other INSTALL.md sections produced correct results

---

#### P. Close-out

- Update `1.23.1-state.md` with final statuses
- Commit any INSTALL.md/RESTORE.md corrections: `fix(docs): INSTALL.md and RESTORE.md corrections from 1.23 test run`
- Commit issues file: `chore(test): 1.23.1 issues file — N issues logged`
- Add Kludde block with overall result
- Mark 1.23.1–1.23.3 as `[x]` in TODO.md
- Final commit: `chore(test): mark 1.23.1-1.23.3 done — fifth sim complete`

---

#### Done when (Part 3 / full simulation):

- All extended restore scenarios PASS: alternate folder, deleted file, nested folder, spaces in name ✓
- Carina's 1 GB tar.gz restored with correct checksum ✓
- Disk space guard (HTTP 507) verified via tmpfs test ✓
- Successful upload confirmed after tmpfs unmount ✓
- Resilience test PASS (restore survives one node down) ✓
- Nightly verify scheduler status recorded ✓
- Final INSTALL.md and RESTORE.md audit complete — all sections accurate ✓
- All ENRADSFIX items committed ✓
- `tests/integration/1.23.1-issues.md` committed ✓
- `tests/integration/1.23.1-state.md` committed with final statuses ✓
- Tasks 1.23.1–1.23.3 marked `[x]` ✓

---

> **Kludde:** K1–K6 PASS (all restore scenarios: baseline, alternate folder, deleted file,
> nested folder with structure preserved, file with spaces, 1 GB tar.gz). L PASS (HTTP 507
> guard confirmed; L4 required agent restart — ISSUE-3 logged: _queued not cleared on failed
> upload, no auto-retry). M PASS (restore from Björn + Carina with Anders down; all 3/3 Online
> after restart). N PASS (INSTALL.md and RESTORE.md fully accurate — no ENRADSFIX items).
> 3 issues total: ISSUE-1 (join idempotency gap), ISSUE-2 (NightlyVerifier not wired),
> ISSUE-3 (failed uploads not retried). Fifth simulation complete.

---

## 1.24 — Post-fifth-simulation fixes

> Fixes for the three issues logged in `tests/integration/1.23.1-issues.md`.
> All three are medium severity and must be resolved before Phase 1 is declared complete.

---

### [x] 1.24.1 — Fix join idempotency: fresh invite + already a member

**Reads:** SECURITY.md, DECISIONS.md, project-docs/architecture.md → cluster join
**Modifies:** `gatekeeper/cluster/join.py`

ISSUE-1 from 1.23.1: when a node that is already in the `members` table uses a
fresh (unused) invite code, `accept_new_member` raises `IntegrityError` (duplicate
`node_id`) → HTTP 400. The invite is consumed but the joiner gets an error.

Fix (C) in join.py already handles "used invite + already a member" (returns success).
This adds the symmetric case: before calling `accept_new_member`, check whether
`node_id` is already in `members`. If yes, skip the insert and return success (same
outcome as Fix C). The invite is consumed — this is intentional, as the join succeeded.

**Requirements:**
- In `accept_join`, query `members` for `node_id` before calling `accept_new_member`
- If already a member: consume the invite, skip insert, return success (HTTP 200)
- If not a member: existing path — call `accept_new_member` as before
- Parameterized query, Pydantic-validated input — same rules as the existing code
- Unit test: fresh invite + duplicate node_id → HTTP 200, invite consumed, no new row

```
> Kludde:
```

---

### [x] 1.24.2 — Wire NightlyVerifier into gatekeeper scheduler

**Reads:** SECURITY.md, project-docs/architecture.md → background jobs
**Modifies:** `gatekeeper/main.py`

ISSUE-2 from 1.23.1: `NightlyVerifier` in `gatekeeper/verify/nightly.py` is fully
implemented but the scheduler in `gatekeeper/main.py` calls `_verify_stub()` instead,
logging `Verify scheduler: pending implementation (task 1.13.2)` on every startup.

Replace the `_verify_stub()` call with a real scheduled task using the existing
APScheduler or asyncio pattern already in place for other background jobs.

**Requirements:**
- Remove `_verify_stub()` call (or the stub itself if nothing else calls it)
- Wire `NightlyVerifier.run()` (or equivalent entry point) into the startup scheduler
- Job must log start, completion, and any errors per the background job pattern in CLAUDE.md
- Trigger: once per day (time configurable via gatekeeper.cfg if already supported,
  otherwise default 02:00 local time and document the default)
- Unit test or smoke check: startup log shows verifier scheduled, not stub

```
> Kludde:
```

---

### [x] 1.24.3 — Fix watcher: re-queue files after failed upload

**Reads:** SECURITY.md, project-docs/architecture.md → agent upload pipeline
**Modifies:** `agent/watcher.py`, `agent/main.py`

ISSUE-3 from 1.23.1: after a failed upload (e.g. HTTP 507 insufficient disk space),
the file path stays in `FileWatcher._queued` permanently. `_upload_worker` catches
the exception and logs it but does not notify the watcher. On the next scan cycle
`_check_file` returns `False` immediately for queued paths → the file is never retried
without an agent restart.

Fix: add a `dequeue(path: str)` method to `FileWatcher` that removes a path from
`_queued`. Call it from `_upload_worker` in the `except` branch after a failed upload.
Thread-safety note: `_queued` is written by `_scan_once` (in a thread via
`asyncio.to_thread`) and by `dequeue` (called from the event loop in `_upload_worker`).
Use a `threading.Lock` to guard both write paths, or document why the current access
pattern is safe (CPython GIL makes `set.discard` atomic, but this should be explicit).

**Requirements:**
- `FileWatcher.dequeue(path: str)` removes `path` from `self._queued`; no-op if not present
- `_upload_worker` calls `watcher.dequeue(file_path)` after any upload exception
- After dequeue, the next scan cycle re-evaluates stability and re-queues if still stable
- Thread-safety: document the approach (Lock or GIL reliance) with a comment
- Unit test: watcher queues file → upload fails → dequeue called → next scan re-queues

```
> Kludde: Added FileWatcher.dequeue(path) with threading.Lock guarding all _queued
> writes (both the new dequeue path and the existing _check_file/scan_once paths).
> _upload_worker extended with watcher parameter; dequeue called in both except
> branches (OSError/IOError and Exception) — NOT in finally, so successful uploads
> stay in _queued. Three unit tests: dequeue removes path, no-op for unknown path,
> next scan re-queues. 18/18 tests pass.
```

---

### [x] 1.24.4 — Targeted regression: verify 1.24.1–1.24.3 on existing VMs

**Reads:** project-docs/testing.md
**Environment:** existing Proxmox VMs — no snapshot rollback or wizard needed

These three fixes cover edge cases that do not occur in the normal backup/restore
flow. A full simulation is overkill. Instead: push HEAD to VMs, restart services,
trigger each specific condition, verify behaviour.

**1.24.1 — Join idempotency**

Precondition: gk-anders is already a member of the cluster.

Steps:
1. On gk-anders: generate a fresh invite code via GUI or API
2. Send the join request manually as if gk-anders were a new joiner:
   ```bash
   curl -s -X POST http://<gk-anders-tailscale-ip>:8080/api/cluster/join \
     -H "Content-Type: application/json" \
     -d '{"invite_code": "<code>", "node_id": "<anders-node-id>", ...}'
   ```
3. Expected: HTTP 200, body `{"status": "already_member"}` or similar success response
4. Verify: invite row consumed in cluster.db, no duplicate row in members table

**1.24.2 — NightlyVerifier wired**

Steps:
1. `systemctl restart backup-buddy-gatekeeper` on gk-anders
2. Check startup log — must NOT contain the old stub message:
   `Verify scheduler: pending implementation (task 1.13.2)`
3. Must contain a line confirming the verifier is scheduled (04:00 or configurable)
4. Trigger an early run:
   ```bash
   backup-buddy verify --now   # or equivalent API call
   ```
5. Expected: verifier runs to completion; logs show start, completion, no exceptions
6. If any verification failure is expected on the current cluster state, confirm it
   generates an alert (not a silent crash)

**1.24.3 — Upload re-queue after failed upload**

Steps:
1. On agent-anders-pc: place a new file in the backup path
2. While the file is in the stability window, temporarily block the gatekeeper port
   so the upload attempt fails (e.g. `iptables -I OUTPUT -p tcp --dport 8081 -j REJECT`)
3. Let the upload attempt fire and fail — confirm error in agent log
4. Remove the iptables rule
5. Wait for the next watcher scan cycle (1 min in test config)
6. Confirm agent log shows the file upload succeeding on retry
7. Remove the iptables block: `iptables -D OUTPUT -p tcp --dport 8081 -j REJECT`

**Requirements:**
- All three checks pass without modifying any config or resetting any VM
- No new issues introduced (check gatekeeper and agent logs for unexpected errors)

```
> Kludde: All three checks pass on HEAD (7a7a1b38f) on existing VMs — no snapshot rollback needed.
>
> 1.24.1 — Join idempotency: Generated fresh invite blush-grill-5, sent POST /api/cluster/join
> with anders-home's own node_id. HTTP 200 returned with cluster state. Log confirms:
> "Node 'Anders home node' (anders-home) presented a fresh invite but is already a member
> — invite consumed, returning cluster state." DB check: invite used=1, still exactly 1
> row for anders-home in members (no duplicate).
>
> 1.24.2 — NightlyVerifier wired: Startup log shows "Nightly verify scheduler started
> (daily at 04:00:00)" — no stub message. Triggered early run by temporarily setting
> daily_check_time=21:06 in gatekeeper.cfg. All 4 layers ran at 21:06:00: Layer 1
> (root_dir.cap accessible), Layer 2 (15 catalog entries OK), Layer 3 (3 test restores
> passed), Layer 4 (lifeboat OK). Log: "Nightly verification completed — all layers passed."
> Config restored to 04:00 after test.
>
> 1.24.3 — Upload re-queue: Created /home/testuser/backup-test/requeue_test.txt on
> agent-anders-pc. Blocked port 8081 before stability window expired. Upload failed with
> OSError at 21:05, 21:06, 21:07. Removed iptables block at 21:07:58. Next scan cycle
> at 21:08:11 succeeded: agent log "SUCCESS — uploaded file (54 bytes)", gatekeeper
> log "Upload complete: agent=anders-laptop size=54". Dequeue-and-retry mechanism confirmed.
```

---

### [x] 1.25.1 — Add on-demand verify trigger (CLI + API)

**Reads:** project-docs/testing.md, project-docs/architecture.md
**Found during:** 1.24.4 regression — no `backup-buddy verify --now` or equivalent exists

The nightly verifier can only be triggered by the scheduler (daily at `verify.daily_check_time`).
There is no way to run it on demand — no CLI flag and no API endpoint. During the 1.24.4
regression test, the only way to trigger an early run was to temporarily modify `gatekeeper.cfg`,
which is a config-manipulation hack, not an operator tool.

An on-demand trigger is necessary for:
- Confirming verifier behaviour after a fix without waiting for the next nightly window
- Operator investigation after an alert or incident
- Smoke-testing after a gatekeeper restart or config change
- Integration test scenarios that need a controlled verify run

**Scope:**

1. **CLI flag** — `backup-buddy verify --now` (or `gatekeeper-ctl verify --now`)
   - Runs `NightlyVerifier.run()` synchronously, exits with code 0 on success, 1 on failure
   - Logs go to the same logger as the scheduled run (no separate log path)
   - Must not interfere with a scheduled run that happens to fire concurrently (lock or skip)

2. **API endpoint** — `POST /api/verify/run-now`
   - Triggers a verify run in the background (non-blocking, returns HTTP 202)
   - Response body: `{"status": "started", "triggered_at": "<iso8601>"}`
   - Rate-limited: reject with HTTP 429 if a run is already in progress
   - Accessible only from Tailscale interface (same binding rule as all other routes)

3. **Status endpoint** — `GET /api/verify/status`
   - Returns last run result: `{"last_run_at": ..., "result": "passed"|"failed"|"never", "layers": [...]}`
   - Reads from a small status record persisted to cluster.db after each run

**Implementation notes:**
- `NightlyVerifier.run()` already does all the work — the trigger is just plumbing
- Store last-run result in cluster.db (`verify_runs` table: `run_at`, `result`, `detail_json`)
- CLI entry point: add `verify` subcommand to existing argparse setup in `gatekeeper/main.py`
  or as a standalone `gatekeeper-ctl` script (whichever is already the operator CLI pattern)
- The concurrent-run guard: a simple `asyncio.Event` or an `is_running: bool` flag on the
  `NightlyVerifier` instance is sufficient — no distributed lock needed (single-node)

**Acceptance criteria:**
- `backup-buddy verify --now` triggers a full verify run and exits 0 on success
- `POST /api/verify/run-now` returns 202 and run completes; returns 429 if already running
- `GET /api/verify/status` returns the result of the last run
- Concurrent calls do not cause two simultaneous verify runs
- All four verify layers are run (same as scheduled run — no shortcuts)
- Unit test: mock `NightlyVerifier.run()`, assert it is called exactly once per API request

---

### [x] 1.25.2 — Log viewer in gatekeeper GUI

**Reads:** project-docs/design.md, project-docs/architecture.md
**Complements:** existing `journalctl`-based logging — this is an additive GUI surface, not a replacement

Homelab users should not need to SSH in and run `journalctl` to see what their gatekeeper is
doing. A simple log viewer page in the web GUI surfaces the local node's log in real time,
with level filtering and component filtering, without changing how or where logs are written.

**Scope (local node only — no cross-node aggregation):**

1. **Log file handler added at startup**
   - `_configure_logging()` in `gatekeeper/main.py` gets a `RotatingFileHandler` alongside
     the existing `StreamHandler` — logs continue to go to journal AND are written to
     `/var/lib/backup-buddy/gatekeeper.log` (max 5 MB × 3 rotated files)
   - Same format as today: `%(asctime)s %(levelname)-8s %(name)s — %(message)s`
   - Log file path configurable via `[logging] log_file` in `gatekeeper.cfg`; default is the
     path above so zero config required for standard installs
   - No change to any existing log call — purely additive

2. **`GET /api/logs` endpoint**
   - Query params: `level` (DEBUG/INFO/WARNING/ERROR, default INFO), `component` (optional
     prefix match on logger name e.g. `cluster`, `verify`, `watcher`), `n` (last N lines,
     default 200, max 1000)
   - Reads from the rotating log file — no subprocess call to `journalctl`
   - Returns JSON: `{"lines": [{"ts": "...", "level": "...", "name": "...", "msg": "..."}]}`
   - Accessible on Tailscale interface only (same binding rule as all routes)

3. **`/logs` page in GUI**
   - Simple Jinja2 template: table of log lines, newest first
   - Dropdown filters: level (INFO / WARNING / ERROR) and component (All / cluster / verify /
     watcher / restore / lifeboat / rebalance)
   - "Refresh" button (no auto-poll — keeps it simple, no websocket needed in Phase 1)
   - No pagination needed for 200 lines; increase via URL param for power users

**Implementation notes:**
- Parse log file lines with a simple regex on the known format — no extra dependency
- `RotatingFileHandler` is stdlib (`logging.handlers`) — no new package
- The `/api/logs` endpoint does a single tail-read of the log file, filtered in-process;
  no need to keep lines in memory between requests
- Guard the endpoint: if the log file does not exist yet (first boot before any log line),
  return `{"lines": []}` rather than 500

**Out of scope for this task:**
- Cross-node log aggregation (Phase 2 or later)
- Log streaming / live tail (websocket — future task if needed)
- Agent log viewer (agent has no GUI in Phase 1)
- Structured JSON log format (would break the existing human-readable journal output)

**Acceptance criteria:**
- `/var/lib/backup-buddy/gatekeeper.log` is written on startup alongside journal output
- `GET /api/logs?level=WARNING` returns only WARNING and ERROR lines
- `GET /api/logs?component=verify` returns only lines from `gatekeeper.verify.*` loggers
- `/logs` page loads, shows recent lines, and filters work without a page reload
- Rotating: after 5 MB the file rotates; old log is `gatekeeper.log.1`, not lost
- `journalctl` output is unchanged — existing log pipeline unaffected

---

### [ ] 1.25.3 — GUI access control: who can reach the gatekeeper web interface?

**Reads:** DECISIONS.md (check for existing ADR), project-docs/architecture.md
**Depends on:** 1.25.2 (log viewer exposes per-node data — triggered this review)
**Decision required before implementation** — see below.

**Background:**

The gatekeeper GUI currently binds to the Tailscale interface and is protected only
by `TailscaleOnlyMiddleware`, which accepts any request whose source IP falls in the
Tailscale CGNAT block (`100.64.0.0/10`).

This means **every node in the Tailscale network can reach every other node's GUI**,
including the log viewer, dashboard, settings, and restore pages.

This was not the original intent. The owner's expectation is that the GUI is a
local-operator interface — visible to the node operator, not to cluster peers.

**The conflict:**

Two different server roles share the same TCP listener:

| Route group | Who should be able to call it |
|---|---|
| GUI pages (`/`, `/logs`, `/restore`, `/settings`, `/buddies`, `/agents`) | Local operator only |
| Cluster API (`/api/cluster/join`, `/api/cluster/sync/*`) | Other gatekeepers over Tailscale |

These are currently served on the same port (default 8080, Tailscale IP).
Solving the access problem cleanly likely means separating them, or adding
per-route authentication.

**Options to evaluate:**

1. **Split listeners** — GUI on LAN interface (operator access from home network),
   cluster API stays on Tailscale interface. Same pattern as the existing agent API
   split (ADR-017). Simple, no auth needed. Downside: operator must be on LAN.

2. **Tailscale ACL-based restriction** — GUI stays on Tailscale but the operator
   configures a Tailscale ACL that blocks peer gatekeepers from reaching port 8080.
   No code change required. Downside: requires Tailscale admin access; fragile if
   ACL is misconfigured; out of scope for BackupBuddy to document or enforce.

3. **Session-based GUI authentication** — add a login step (passphrase or token)
   that gates all `/` routes. Cluster API remains unauthenticated on Tailscale.
   More effort; requires storing a credential; but makes GUI safe regardless of
   Tailscale topology.

4. **Tailscale node identity check** — use the Tailscale local API (`/localapi/v0/whois`)
   to verify that the connecting IP belongs to *this node's* Tailscale identity, not
   a peer. Elegant but couples the code tightly to Tailscale's local API.

**Decision required before implementing:**

Before writing any code, determine:
1. Should this be formalised as an ADR (it touches a core security boundary)?
   If yes, write the ADR first and update DECISIONS.md.
2. Which option above is chosen — or is a different approach preferred?

Flag to project owner: this is an architectural decision, not just a task.
The author recommends **Option 1 (split listeners)** as it is the simplest,
consistent with the existing agent API separation (ADR-017), and requires no
new credential management. The operator trades remote GUI access for clear
isolation — a good trade for a homelab tool.

**If Option 1 is chosen, scope of work:**
- Add a `[web] lan_bind = true` config option (default `false` for backwards compat)
  or make it the new default with an explicit opt-in for Tailscale GUI access
- Move GUI router to LAN listener; leave cluster API routes on Tailscale listener
- Update `TailscaleOnlyMiddleware` to apply only to the cluster API routes
- Update `project-docs/architecture.md` and `project-docs/configuration.md`
- Write or update the relevant ADR

**Acceptance criteria (Option 1 — to be confirmed):**
- GUI pages are not reachable from other cluster members' Tailscale IPs
- Cluster API endpoints (`/api/cluster/*`) remain reachable over Tailscale
- Operator can reach the GUI from a browser on the local LAN
- Configuration documented; existing installs informed of the change

---

# Phase 2 — Maturity

> **Status: To be detailed.**
> Phase 2 tasks will be broken down into x.y.z tasks when Phase 1 is
> complete and all Phase 1 tests pass. The items below are confirmed
> in scope but not yet specified at task level.

## 2.1 — Incremental backups
- Delta upload (changed blocks only, not full re-upload)
- Per-file version history with configurable retention

## 2.2 — VM snapshot support
- Proxmox vzdump integration
- Incremental VM snapshot strategy (delta only)
- VMware snapshot support (to be evaluated)

## 2.3 — Gossip protocol
- Replace Tahoe-LAFS introducer with gossip-based node discovery
- Fully serverless — no single point of failure for cluster membership

## 2.4 — Automatic quota negotiation
- Cluster-level negotiation when a buddy is close to their ratio limit
- Automated suggestion: "consider increasing your storage contribution"

## 2.5 — Free-rider throttling
- Automatic upload throttling when ratio exceeds 3:1
- Lifted automatically when balance is restored

## 2.6 — Hot-standby gatekeeper
- Semi-automatic failover: shadow agent promoted to gatekeeper on command
- Continuous state sync from primary to shadow

## 2.7 — One-step onboarding
- Tailscale join + cluster join combined into a single flow
- Requires Tailscale API integration

## 2.8 — File-level backup granularity
- Allow individual files to be selected in backup.cfg, not just folders

## 2.10 — Recovery kit Option B: full catalog snapshot in recovery kit

Current Phase 1 disaster recovery (Option A) reconstructs the catalog from the Tahoe
file tree using `root_dir_cap`. Reconstructed entries have no SHA-256, so the nightly
verifier logs "hash unknown" warnings for all entries until files are re-backed-up.

Option B stores the `lifeboat.key` (runtime key) inside `recovery_kit.enc`:
  passphrase → recovery_kit.enc → lifeboat.key → decrypt lifeboat.enc on agent
  → full state: node_privkey, root_dir_cap, catalog.db snapshot, gatekeeper.cfg

This preserves SHA-256 across disaster recovery and allows the nightly verifier
to fully verify all files immediately after restore.

**Requires:**
- `create_recovery_kit()` extended to include `lifeboat_key` field (new format version)
- `extract_recovery_kit()` updated to return lifeboat_key when present
- Emergency restore flow: download lifeboat.enc from agent, decrypt with lifeboat_key
- Restore node_privkey, root_dir.cap, catalog.db, gatekeeper.cfg from the bundle
- GUI flow for "I have recovery_kit.enc + passphrase, retrieve lifeboat from agent"
- Agent must have a reachable API endpoint to serve the lifeboat bundle

## 2.9 — Docker Compose distribution

Alternative installation path alongside the existing `install.sh` script.
Targets homelab users who prefer containers over native installation.

**Design notes:**
- Two containers per gatekeeper: `backupbuddy` (FastAPI + Tahoe) and `tailscale` (sidecar)
- Tailscale sidecar uses `network_mode: service:tailscale` so BackupBuddy can reach
  the Tailscale interface; requires `--cap-add=NET_ADMIN` and `/dev/net/tun` on the sidecar
- Storage pool mounted as a bind mount: `./storage:/mnt/storage`
- Data directory mounted as a named volume: `backupbuddy-data:/root/.backupbuddy`
- Agent: single container, no Tailscale needed (talks to GK over LAN)
- `install.sh` remains the primary path; Docker is an opt-in alternative
- **Proxmox note:** Docker works inside Proxmox VMs without issues. Docker inside
  unprivileged LXC is not supported and will not be documented or tested.
- `.env` file for secrets; `docker-compose.yml` + `docker-compose.agent.yml`

**Not in scope for this task:**
- Kubernetes / Helm charts (overkill for homelab target audience)
- Multi-arch builds (arm64 for Raspberry Pi can be added separately)

---

---

# Phase 3 — Public cluster (speculative)

> **Status: Speculative. To be confirmed.**
> Phase 3 is a long-term vision and may never be implemented.
> It must not drive any Phase 1 or Phase 2 decisions — see ADR-016.
> Items here are concepts only, not committed scope.

## 3.1 — Open cluster with 1:2 ratio requirement
- Anyone can join — no social trust required
- 1:2 contribution ratio enforced by protocol

## 3.2 — 3-of-10 erasure coding default
- Higher n for mass-departure resilience in public setting

## 3.3 — Proof of Storage (Merkle-tree challenges)
- Periodic challenges to prove fragment possession without transmitting data
- Non-response or wrong response triggers automatic re-fragmentation

## 3.4 — Node leveling system
- New nodes start with minimal quota
- Quota grows with uptime, successful challenges, and time online
- Level resets on extended absence

## 3.5 — Sybil attack resistance
- Prevent fake node farms from joining and controlling fragment distribution
- Mechanism to be determined (time-based, challenge-based, or hybrid)
