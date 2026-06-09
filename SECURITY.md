# SECURITY.md

> **This document is mandatory reading for all code contributions.**
> Claude Code must follow these rules on every task, every file, every line.
> Security in BackupBuddy is not optional — people trust this system with
> irreplaceable data. A security failure is a failure of the entire project.

---

## ⚡ Quick reference — absolute rules

```
❌ NEVER hardcode secrets, passphrases, keys, or tokens anywhere in code
❌ NEVER log file paths, file names, or file contents from agent backup scope
❌ NEVER log or expose root_dir.cap, node.privkey, or lifeboat contents
❌ NEVER store the lifeboat passphrase on disk in any form
❌ NEVER bind any server to 0.0.0.0 (use specific interface addresses only)
❌ NEVER allow storage pool paths to be added to backup scope
❌ NEVER trust data received from cluster nodes without verification
❌ NEVER write SMTP passwords or webhook URLs to unencrypted config files
❌ NEVER include fragment content or file metadata in logs visible to other nodes
❌ NEVER allow agents to browse each other's or the gatekeeper's filesystem

✅ ALWAYS keep cluster API on Tailscale only; GUI binding is configurable (see ADR-023)
✅ ALWAYS encrypt the lifeboat bundle before writing or transmitting it
✅ ALWAYS verify SHA-256 before and after fragmentation
✅ ALWAYS verify SHA-256 after restore before delivering to user
✅ ALWAYS store SMTP passwords and webhook URLs encrypted at rest
✅ ALWAYS auto-exclude storage pool paths from backup scope (enforce, not suggest)
✅ ALWAYS treat all inbound cluster data as untrusted until verified
✅ ALWAYS use parameterized queries for all database operations (catalog.db, cluster.db)
✅ ALWAYS write code and comments in English
```

---

## 1. Cryptographic key handling

### root_dir.cap

The master key to all backed-up data. If lost without external backup,
data is permanently inaccessible. If leaked, all backed-up data is exposed.

Rules:
- Never write root_dir.cap to any log file
- Never transmit root_dir.cap over any channel other than the lifeboat mechanism
- Never display root_dir.cap in the GUI except in the designated "copy recovery key" flow
- After the initial setup display, root_dir.cap must never appear in plaintext in the UI again
- The GUI must require the user to confirm they have saved it before proceeding past setup

### node.privkey

The Tahoe-LAFS node identity key. Identifies the gatekeeper to the cluster.

Rules:
- Never log, transmit, or display node.privkey outside of the lifeboat mechanism
- File permissions must be 0600 (owner read/write only)
- Never include in error messages or diagnostic output

### Lifeboat passphrase

The AES encryption key for the lifeboat bundle. Never persisted anywhere.

Rules:
- Entered by the user at setup — stored only in memory for the duration of the session
- Never written to disk, config files, environment variables, or logs
- Never transmitted over the network
- If the process restarts, the passphrase must be re-entered
- The GUI must make clear that this passphrase cannot be recovered if lost

### SMTP passwords and webhook URLs

Sensitive credentials for notification delivery.

Rules:
- Never written in plaintext to gatekeeper.cfg on disk
- Stored encrypted using a key derived from the system (e.g. machine-specific key)
- Entered and managed exclusively through the GUI
- Never included in log output, error messages, or diagnostic dumps

---

## 2. Zero-knowledge design

BackupBuddy's core promise: nodes storing fragments for others cannot read
or identify that content. This must be enforced in code, not just in design.

Rules:
- Fragment files on disk must never be named after the original file
- Fragment metadata visible to the storing node must contain only:
  encrypted owner tag (node ID), creation timestamp, quota accounting size
- The storing node must never receive or log: original file name, original path,
  file size in plaintext, or any identifying information about the content
- Logs on gatekeeper must never include file paths or names from other nodes' data
- The GUI must never display another buddy's file names, paths, or content

### Encrypted metadata tags on file caps

At upload time, each file's Tahoe cap receives an encrypted metadata tag:

```python
metadata = {
    "original_path": encrypt(path, root_dir_cap_derived_key),
    "agent": encrypt(agent_name, root_dir_cap_derived_key),
    "backed_up_at": timestamp  # plaintext — not sensitive
}
```

Only the owner (holder of root_dir.cap) can decrypt these tags.
No other cluster node can read original_path or agent name.

---

## 3. Network security

### GUI binding (ADR-023)

The gatekeeper runs two separate listeners:

1. **Tailscale listener** — always active. Serves cluster API routes only:
   `/api/cluster/*`, `/api/verify/*`, `/api/status`.
   Never serves GUI routes to Tailscale peers (unless `gui_on_tailscale = true`).

2. **LAN listener** — active when `gui_on_lan = true` (default). Serves the
   operator's GUI from inside their home network.

Neither listener may bind to `0.0.0.0`. Use specific interface addresses only.

```python
# Correct — Tailscale listener (cluster API always, GUI if gui_on_tailscale=true)
uvicorn.run(app, host=tailscale_ip, port=8080)

# Correct — LAN listener (GUI only, started conditionally)
uvicorn.run(app, host=lan_ip, port=8080, lifespan="off")

# NEVER
uvicorn.run(app, host="0.0.0.0", port=8080)
```

The Tailscale IP must be resolved at startup, not hardcoded.
If Tailscale is not running, the gatekeeper must not start.

`AccessControlMiddleware` enforces the route classification at the application
layer regardless of which listener received the request:
- Service routes (`/api/cluster/*`, `/api/verify/*`, `/api/status`) require
  a Tailscale source IP — rejected with 404 from LAN.
- GUI routes are allowed based on `gui_on_lan` / `gui_on_tailscale` flags.

### Tailscale subnet routing

The gatekeeper acts as a subnet router. This must only be activated explicitly
by the user during setup. Never enable subnet routing automatically or silently.

### Agent-to-gatekeeper communication

Agents communicate with their gatekeeper over the local LAN only.
No agent should ever communicate directly with the cluster or with
another home network's gatekeeper.

Agent API on the gatekeeper must:
- Only accept connections from the local subnet (not from Tailscale)
- Authenticate agents by pre-shared token generated at agent setup time
- Never accept file listings or filesystem browsing requests from agents
  (the gatekeeper tells the agent what to do, not the other way around)

### Cluster communication

All cluster communication between gatekeepers passes through Tailscale (WireGuard).
No direct TCP/IP connections between gatekeepers outside of Tailscale.

---

## 4. Input validation and database safety

### SQLite queries (catalog.db, cluster.db)

Always use parameterized queries. Never construct SQL with string formatting.

```python
# Correct
cursor.execute(
    "SELECT * FROM files WHERE agent = ? AND backed_up_at > ?",
    (agent_name, since_timestamp)
)

# NEVER
cursor.execute(
    f"SELECT * FROM files WHERE agent = '{agent_name}'"
)
```

### File paths from backup.cfg

Paths specified in backup.cfg must be validated before use:
- Must be absolute paths
- Must exist on the local filesystem
- Must not resolve to storage pool paths (check after symlink resolution)
- Must not resolve to system-critical paths (/etc, /boot, /sys, /proc, /dev)

Path validation must use `os.path.realpath()` to resolve symlinks before
comparing against the exclusion list.

### Invite codes and cluster input

All data received from cluster nodes or via invite codes is untrusted:
- Validate structure and types before use
- Never eval or deserialize untrusted data with pickle or equivalent
- Use explicit schema validation (Pydantic) on all inbound cluster messages

---

## 5. Storage pool path exclusion

This is a hard security and data integrity requirement, not a UX feature.

The infinite backup loop (backing up the fragment store itself) must be
prevented at the code level, not through user configuration.

Implementation requirements:
- At startup, gatekeeper reads all configured storage pool paths
- These paths are added to a permanent exclusion set in memory
- Before any file is queued for backup, its real path (symlinks resolved)
  is checked against this exclusion set
- This check cannot be disabled by any configuration option
- If a storage pool path is added later via GUI, all agents are notified
  immediately and their exclusion sets updated before the path becomes active

---

## 6. Logging rules

Logs are a common source of accidental data leakage. Follow these rules:

```
✅ Log: operation names, timestamps, success/failure status, file sizes (bytes)
✅ Log: node IDs, cluster events, connection status
✅ Log: error types and codes (not full stack traces to external outputs)

❌ Never log: file names or paths from backup scope
❌ Never log: root_dir.cap, node.privkey, passphrase (not even partial)
❌ Never log: SMTP passwords, webhook URLs, auth tokens
❌ Never log: fragment contents or any reconstructed file data
❌ Never log: another node's file names, paths, or metadata
```

Log format must include: timestamp, node ID, log level, message.
Logs must never be shared externally without explicit user action.
backup.log (agent-side) is controlled by share_log in backup.cfg — this
opt-in must be strictly honored.

---

## 7. Lifeboat security

The lifeboat bundle contains the most sensitive data in the system.

Encryption requirements:
- AES-256-GCM (authenticated encryption — integrity verified on decrypt)
- Key derived from user passphrase using Argon2id (not SHA-256, not PBKDF2)
- Random salt generated per encryption, stored prepended to ciphertext
- The bundle file must be treated as opaque by the agent — it stores and
  returns it, but never reads or processes its contents

Transmission:
- Lifeboat bundle transmitted to agents over local LAN only
- Agent stores bundle at a fixed path with permissions 0600
- Grindvakt verifies the stored bundle can be decrypted after each update
  (using the in-memory passphrase) before considering the lifeboat current

---

## 8. Verification and test restore security

Test restores write data to a temporary directory. Rules:
- Temp directory must have permissions 0700
- Temp directory must be cleaned up immediately after hash verification,
  regardless of verification result
- Temp directory must never be inside any path listed in backup.cfg
  (would cause the restored file to be immediately re-backed up)
- Hash verification failures must generate an alert — never silently pass

---

## 9. Dependency and supply chain

- Pin all Python dependencies to exact versions in requirements.txt
- Run `pip audit` (or `safety check`) as part of the test suite
- No dependency should be added without a clear justification
- Prefer standard library over third-party where feasible
- Tahoe-LAFS and Tailscale are trusted base dependencies —
  do not patch or monkey-patch their internals

---

## 10. Error handling

Security-relevant errors must never be swallowed silently.

```python
# Correct — log and alert, never silently continue
try:
    result = decrypt_lifeboat(bundle, passphrase)
except DecryptionError as e:
    logger.error("Lifeboat decryption failed: %s", type(e).__name__)
    send_alert("Lifeboat integrity check failed — verify passphrase")
    raise

# NEVER — silent failure hides a critical problem
try:
    result = decrypt_lifeboat(bundle, passphrase)
except Exception:
    pass
```

Error messages shown to users must never include:
- Stack traces
- Internal file paths of the gatekeeper system
- Cryptographic key material (even partial)
- Other nodes' information

---

## 11. First-run and setup security

During the onboarding wizard:
- root_dir.cap must be displayed once, clearly marked as critical
- User must explicitly confirm they have saved it before wizard continues
- The confirmation must require a deliberate action (checkbox + button),
  not just a "next" click
- SMTP password test must not log the password on failure
- Tailscale auth key (if used during setup) must be cleared from memory
  and any temp files immediately after use

---

_BackupBuddy SECURITY.md_
_Mandatory reading before writing any code._
