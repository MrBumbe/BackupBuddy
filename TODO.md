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

### [ ] 1.17.4 — Phase C: File backup via agent

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
> Kludde:
```

---

### [ ] 1.17.5 — Phase D: File restore (normal + folder + hash verification)

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
> Kludde:
```

---

### [ ] 1.17.6 — Phase E: Multi-node cluster join (bjorn joins anders)

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
> Kludde:
```

---

### [ ] 1.17.7 — Phase F: Nightly verification + deliberate corruption detection

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
```

---

### [ ] 1.17.8 — Phase G: Full disaster recovery (VM destroy + fresh install + GUI restore)

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
```

---

### [ ] 1.17.9 — Phase H: Three-node cluster + node removal flow

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
```

---

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
