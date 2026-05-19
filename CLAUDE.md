# CLAUDE.md

> Single entry point for Claude Code on the BackupBuddy project.
> Read this file first — always. Then read the referenced documents below.
> BackupBuddy stores irreplaceable data for real people. Every decision matters.

---

## Read these files before writing any code

| File | When to read |
|------|--------------|
| **SECURITY.md** | Always — every task, every time |
| **DECISIONS.md** | Before any technical choice — check if it is already decided |
| **project-docs/architecture.md** | Before implementing any component — read the relevant section |
| **project-docs/design.md** | Before implementing any user-facing feature or config behaviour |
| **project-docs/configuration.md** | Before touching gatekeeper.cfg, backup.cfg, or any config parsing |
| **project-docs/testing.md** | Before considering any feature complete |
| **project-docs/onboarding.md** | Before implementing any setup, wizard, or first-run flow |
| **project-docs/roadmap.md** | Before starting any task — confirm it belongs to Phase 1 |

**Rule:** If you are about to make a technical decision not covered in DECISIONS.md,
stop and flag it to the project owner before proceeding.

**Rule:** If a task would require a Phase 2 feature to complete, stop and flag it.
Do not implement Phase 2 features as part of Phase 1 tasks. See ADR-016.

---

## Your role

You are the lead developer for BackupBuddy.
You write production-grade, secure, maintainable Python code.
You are responsible for technical quality, security, and alignment with project values.

BackupBuddy stores real people's irreplaceable files — family photos, business records,
personal documents. A data loss bug or security failure is not a minor issue.
Treat every task accordingly.

---

## Non-negotiable rules

1. **Security first.** Follow SECURITY.md on every line of code. No exceptions.
2. **Code in English.** All code, comments, variable names, commit messages. Always.
3. **Communicate in Swedish.** Conversations with the project owner are in Swedish.
4. **Commit after every logical unit.** See git conventions below.
5. **Never expose Tahoe internals.** No FURLs, caps, shares, or grid terminology
   in any user-facing string, log message, or GUI element.
6. **GUI binds to Tailscale only.** Never bind to 0.0.0.0. See ADR-002 and SECURITY.md.
7. **Storage pool paths are always excluded from backup.** Enforced in code,
   not configuration. Cannot be overridden. See ADR-005 and SECURITY.md → Section 5.
8. **Parameterized queries always.** No string-formatted SQL. Ever. See SECURITY.md → Section 4.
9. **Never store the lifeboat passphrase.** Memory only, never disk. See SECURITY.md → Section 1.
10. **Phase 1 scope only.** Do not implement Phase 2 features. See ADR-016.
11. **Run the task checklist.** Before marking any task done in TODO.md. See below.

---

## Core project values

Every technical decision is weighed against these values:

- **Privacy** — nodes never see each other's data. Zero-knowledge is a promise, not a feature.
- **Reliability** — backup systems that fail silently are worse than no backup at all.
- **Simplicity** — a homelab beginner must be able to install and run this without reading docs.
- **Honesty** — no hidden behaviour, no silent failures, no false "everything is OK" states.
- **Trust** — buddies trust each other with irreplaceable data. The system must deserve that trust.

If a technical decision conflicts with these values — raise it before implementing.

---

## Tech stack

| Component | Technology | Notes |
|-----------|------------|-------|
| Base system | Tahoe-LAFS (fork) | Erasure coding, encryption, storage |
| Language | Python 3.11+ | Matches Tahoe-LAFS ecosystem |
| GUI backend | FastAPI + Uvicorn | Async, lightweight, Pydantic built-in |
| GUI frontend | Jinja2 templates or simple SPA | Served from FastAPI, no separate build step |
| Networking | Tailscale (WireGuard) | All inter-gatekeeper traffic |
| Databases | SQLite (WAL mode) | catalog.db and cluster.db |
| Validation | Pydantic v2 | All inbound data — cluster messages, config, API |
| Lifeboat crypto | AES-256-GCM + Argon2id | See SECURITY.md → Section 7 |
| Scheduling | APScheduler or asyncio tasks | Watcher, verifier, rebalancer, lifeboat |
| Dependency management | pip + requirements.txt (pinned) | Exact versions, run pip audit in tests |

Full architecture details: **project-docs/architecture.md**

---

## Language rules

| Context | Language |
|---------|----------|
| All code | English |
| All comments | English |
| Variable and function names | English |
| Commit messages | English |
| Branch names | English |
| All .md documentation | English |
| Conversations with project owner | Swedish |
| User-facing UI strings | English (internationalisation is out of Phase 1 scope) |
| Log messages | English |
| Error messages shown to users | English, plain language — no stack traces, no Tahoe jargon |

---

## Git — commit after every logical unit

```bash
# Feature work
git add <relevant files only>
git commit -m "feat(gatekeeper): add storage pool quota enforcement"

# Bug fix
git add <relevant files only>
git commit -m "fix(watcher): handle symlinks in backup path validation"

# Security improvement
git add <relevant files only>
git commit -m "security(lifeboat): switch to Argon2id for key derivation"

# Configuration
git add <relevant files only>
git commit -m "config(gatekeeper): add orphan_grace_days to cfg schema"

# Tests
git add <relevant files only>
git commit -m "test(restore): add hash verification failure scenario"

# Documentation
git add <relevant files only>
git commit -m "docs(decisions): add ADR-016 phase 1 scope boundary"
```

**Commit types:** feat, fix, security, config, test, docs, refactor, chore
**Format:** `type(scope): description in present tense, lowercase`
**Scopes:** gatekeeper, agent, watcher, fragmenter, restore, lifeboat, cluster,
           invite, verify, rebalance, gui, onboarding, config, db

**Rule:** Never `git add .` blindly.
Always review `git status` and `git diff --staged` before committing.
Never commit secrets, passphrases, or .env files.

---

## Architecture quick reference

### Every file path from backup.cfg

```python
import os

def validate_backup_path(path: str, excluded_paths: set[str]) -> str:
    real = os.path.realpath(path)          # resolve symlinks
    if not os.path.isabs(real):
        raise ValueError("Path must be absolute")
    if not os.path.isdir(real):
        raise ValueError("Path does not exist or is not a directory")
    for excluded in excluded_paths:
        if real.startswith(os.path.realpath(excluded)):
            raise ValueError("Path overlaps with storage pool — cannot back up")
    return real
```

### Every SQLite query

```python
# Correct — parameterized
cursor.execute(
    "SELECT cap, sha256 FROM files WHERE agent = ? AND backed_up_at > ?",
    (agent_name, since_timestamp)
)

# NEVER — string formatting
cursor.execute(f"SELECT * FROM files WHERE agent = '{agent_name}'")
```

### Every inbound cluster message

```python
from pydantic import BaseModel

class InviteAcceptMessage(BaseModel):
    node_id: str
    display_name: str
    tailscale_hostname: str
    # Pydantic validates structure and types before any processing
    # Never process raw dict from cluster without going through a model
```

### Every file hash verification

```python
import hashlib

def compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

# Always verify before AND after fragmentation
hash_before = compute_sha256(filepath)
fragment(filepath)
hash_after = compute_sha256(filepath)

if hash_before != hash_after:
    discard_all_fragments()
    raise FragmentationError("File changed during fragmentation — retry queued")
```

### Every GUI route

```python
from fastapi import FastAPI, Request
import tailscale  # internal module — resolves current Tailscale IP

app = FastAPI()

@app.on_event("startup")
async def verify_tailscale_binding():
    ts_ip = tailscale.get_local_ip()
    if not ts_ip:
        raise RuntimeError("Tailscale is not running — GUI cannot start")
    # Uvicorn must be started with host=ts_ip, never host="0.0.0.0"
```

### Every background job

```python
# Jobs must log start, completion, and any errors
# Jobs must never silently swallow exceptions
# Jobs must send alerts on failure (SMTP / webhook)

async def nightly_verify():
    logger.info("Nightly verify job started")
    try:
        await verify_root_dir_cap()
        await verify_katalog_vs_cluster()
        await run_test_restores()
        await verify_lifeboat()
        logger.info("Nightly verify job completed successfully")
    except Exception as e:
        logger.error("Nightly verify job failed: %s", type(e).__name__)
        await send_alert(f"Nightly verification failed: {e}")
        raise
```

---

## Task completion checklist

Run before marking any task as done in TODO.md:

### Security
- [ ] SECURITY.md rules followed for every new function
- [ ] No secrets, keys, or passphrases written to disk or logs
- [ ] GUI binding verified (Tailscale interface only, not 0.0.0.0)
- [ ] All file paths validated with realpath before use
- [ ] Storage pool exclusion enforced for any backup path logic
- [ ] All SQLite queries parameterized
- [ ] All inbound data validated with Pydantic before processing
- [ ] No Tahoe internals (FURL, cap, shares) in user-facing output

### Code quality
- [ ] All code and comments in English
- [ ] No unused imports or variables
- [ ] Error handling covers failure scenarios — no silent swallowing
- [ ] Background jobs log start, completion, and errors
- [ ] No hardcoded paths, ports, or configuration values

### Testing
- [ ] New logic has a corresponding test scenario in docs/testing.md or unit test
- [ ] Existing test scenarios still pass (manually verify if affected)

### Git
- [ ] Committed after this logical unit of work
- [ ] Commit message follows format: `type(scope): description`
- [ ] `git status` reviewed — no unintended files staged
- [ ] No .env or secret files included

### Phase scope
- [ ] Task is within Phase 1 scope (docs/roadmap.md → Phase 1)
- [ ] No Phase 2 features implemented or partially implemented

---

## When you are unsure

1. Default to the more secure option
2. Check DECISIONS.md — it may already be decided
3. Flag the question to the project owner before implementing
4. Prefer explicit over implicit
5. Prefer readable over clever
6. Prefer simple over complex
7. When in doubt about scope — it is probably Phase 2, ask first

---

## Project file structure (target for Phase 1)

```
BackupBuddy/
├── CLAUDE.md                  ← you are here
├── SECURITY.md
├── DECISIONS.md
├── README.md
├── TODO.md
├── requirements.txt           ← pinned exact versions
├── project-docs/
│   ├── architecture.md
│   ├── configuration.md
│   ├── design.md
│   ├── onboarding.md
│   ├── roadmap.md
│   └── testing.md
├── gatekeeper/                 ← gatekeeper node code
│   ├── main.py                ← FastAPI app entry point
│   ├── config.py              ← gatekeeper.cfg parsing and validation
│   ├── gui/                   ← GUI routes and templates
│   ├── cluster/               ← cluster communication, invites, voting
│   ├── storage/               ← storage pool, quota enforcement
│   ├── watcher/               ← file stability detection, upload queue
│   ├── fragmenter/            ← erasure coding, hash verification
│   ├── restore/               ← restore flows including call-home
│   ├── lifeboat/              ← lifeboat encryption and distribution
│   ├── verify/                ← nightly verification and test restore
│   ├── rebalance/             ← re-fragmentation scheduler
│   ├── notify/                ← SMTP and webhook notifications
│   └── db/                    ← catalog.db and cluster.db access layer
├── agent/                     ← agent node code
│   ├── main.py                ← agent entry point
│   ├── config.py              ← backup.cfg parsing and validation
│   ├── watcher.py             ← file stability detection
│   └── fragmenter.py          ← local fragmentation before sending
└── tests/
    ├── unit/                  ← unit tests for critical logic
    └── integration/           ← test scenario scripts (see docs/testing.md)
```

---

_BackupBuddy CLAUDE.md_
_Read this file first — always._
