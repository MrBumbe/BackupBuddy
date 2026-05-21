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

### [ ] 1.11.1 — Adaptiv profile k/n calculation

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
> Kludde: <!-- -->
```

---

### [ ] 1.11.2 — Rebalance scheduler

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
> Kludde: <!-- -->
```

---

## 1.12 — Restore

### [ ] 1.12.1 — Normal restore flow

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
> Kludde: <!-- -->
```

---

### [ ] 1.12.2 — "Call home" catalog reconstruction

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
> Kludde: <!-- -->
```

---

## 1.13 — Verification and notifications

### [ ] 1.13.1 — Notification dispatcher

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
> Kludde: <!-- -->
```

---

### [ ] 1.13.2 — Nightly verification job

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
> Kludde: <!-- -->
```

---

## 1.14 — Web GUI

### [ ] 1.14.1 — FastAPI application setup

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
> Kludde: <!-- -->
```

---

### [ ] 1.14.2 — Dashboard

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
> Kludde: <!-- -->
```

---

### [ ] 1.14.3 — Restore UI

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
> Kludde: <!-- -->
```

---

### [ ] 1.14.4 — Settings UI

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
> Kludde: <!-- -->
```

---

### [ ] 1.14.5 — Buddies and cluster management UI

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
> Kludde: <!-- -->
```

---

### [ ] 1.14.6 — Agents UI

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
> Kludde: <!-- -->
```

---

## 1.15 — Onboarding wizard

### [ ] 1.15.1 — Install script (gatekeeper)

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
> Kludde: <!-- -->
```

---

### [ ] 1.15.2 — Onboarding wizard (web)

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
> Kludde: <!-- -->
```

---

### [ ] 1.15.3 — Agent install script

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
> Kludde: <!-- -->
```

---

## 1.16 — Unit tests and small-scale integration tests

> **Hardware note:** Full Proxmox test environment (docs/testing.md) requires
> dedicated hardware not yet available. The tasks below use the development
> machine and/or existing servers for smaller-scope validation.
> Full integration testing (docs/testing.md scenarios 1–7) is a separate
> milestone — tracked here when hardware is ready.

### [ ] 1.16.1 — Unit test suite

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
> Kludde: <!-- -->
```

---

### [ ] 1.16.2 — Two-node smoke test (local machine)

**Reads:** docs/testing.md → Scenario 1, docs/testing.md → Scenario 3
**Creates:** `tests/integration/smoke_test.sh`
**Requirements:**
- Script spins up two gatekeeper processes and one agent on the local machine
  using separate config directories and ports
- Runs docs/testing.md Scenario 1 (basic backup and restore)
- Runs docs/testing.md Scenario 3 (lifeboat restore) in simplified form
  (simulate gatekeeper loss by deleting its DB and restoring from agent)
- Script cleans up all processes and temp files on exit
- Intended to run on the development machine or Ubuntu server without VMs
**Done when:**
- Scenario 1: file backed up, restored, hash verified
- Scenario 3 (simplified): DB deleted, catalog reconstructed from lifeboat,
  file restored successfully
- All processes cleaned up after test

```
> Kludde: <!-- -->
```

---

### [ ] 1.16.3 — Full Proxmox integration tests

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
> Kludde: <!-- -->
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
