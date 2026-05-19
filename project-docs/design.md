# Design decisions

All decisions made during initial design phase. Rationale included.

---

## Fragmentation profiles

User-facing abstraction over Tahoe-LAFS k-of-n erasure coding parameters.
Set per node/buddy — not cluster-wide. Each buddy chooses independently.

| Profile  | k | n  | Min nodes needed | Use case |
|----------|---|----|------------------|----------|
| Balanced | 3 | 5  | 5                | Balanced default |
| Secure   | 3 | 7  | 7                | Extra margin |
| Paranoid | 3 | 10 | 10               | Critical data (e.g. business records) |
| Adaptive | — | —  | —                | See below |

### Adaptive profile

Scales automatically with cluster size. Ratio is always 1/3 (one fragment
per buddy, any 1/3 of fragments sufficient for restore).

```
3 buddies  → k=1, n=3
6 buddies  → k=2, n=6
9 buddies  → k=3, n=9
12 buddies → k=4, n=12
```

Parameters:
- `ratio = 0.33` — always 1/3
- `min_k = 2` — never below 2-of-6 regardless of cluster size
- `max_n = 20` — cap to avoid excessive fragment count in large clusters
- `rebalance_time = 03:00` — nightly rebalance job

Tahoe-LAFS maps: `shares.needed = k`, `shares.happy = n`, `shares.total = n`

---

## Storage ratio

Minimum requirement: you must contribute at least as much storage as you
want backed up. Recommended buffer is 1.2x.

```
You back up 100 GB → you must contribute at least 100 GB
Recommended:        → contribute 120 GB (1.2x buffer)
```

GUI shows per-buddy ratio. Warning triggers if contribution falls below
the user's own backup size. System will not guarantee backup completion
if ratio is insufficient.

---

## Storage pool (local fragment storage)

Fragments received from the cluster are distributed transparently across
multiple local paths. Implementation: thin scheduler in gatekeeper code,
not mergerfs or OS-level union mount (simpler, no kernel dependencies).

Each path has a hard quota configured in gatekeeper.cfg.
Paths are excluded from backup automatically and permanently —
this exclusion cannot be removed by the user (infinite loop prevention).

---

## Lifeboat mechanism

Critical gatekeeper state is encrypted and distributed to all local agents.
Restores the gatekeeper from scratch if hardware fails.

Lifeboat contents:
- `node.privkey` — Tahoe-LAFS node identity
- `root_dir.cap` — master key to the backup file tree
- `catalog.db` — backup catalog (file caps, hashes, original paths)
- `gatekeeper.cfg` — full gatekeeper configuration

Encryption: AES with user passphrase. Passphrase entered at first setup,
never stored on disk. Required for restore.

Distribution: encrypted copy pushed to every local agent after each update.
Update interval: every 1 hour (catalog.db changes frequently).
root_dir.cap is static after creation but always included in lifeboat.

Additionally, user is strongly encouraged to store root_dir.cap externally
(USB drive, password manager) during initial setup. GUI prompts for this
once and reminds until confirmed.

---

## root_dir.cap and catalog.db — criticality

`root_dir.cap` is the master key to all backed-up data. Without it,
fragments exist in the cluster but cannot be located or decrypted.
It never changes after initial node creation.

`catalog.db` maps file caps to original paths, hashes, agents, timestamps.
Without it, files can still be found by traversing the Tahoe file tree
via root_dir.cap, but original paths and hash verification are unavailable.

"Call home" reconstruction: if catalog.db is lost but root_dir.cap is
available, the gatekeeper can reconstruct catalog.db by traversing the
Tahoe file tree and decrypting the encrypted metadata tag stored on each
file's cap at upload time. This requires no manual work from the user.

---

## File watcher and backup triggering

Files are backed up as soon as they are considered stable — not on a fixed
schedule. This distributes load naturally across the day.

Stability detection:
1. mtime unchanged for 30 minutes
2. File size unchanged
3. No open file handles (lsof / inotify check)

All three conditions must be met before fragmenting begins.

CPU priority: `nice +19` (lowest)
IO priority: `ionice -c 3` (idle class)
Concurrent uploads: max 2 (configurable)

Hash verification during fragmentation:
- SHA-256 computed before fragmenting starts
- SHA-256 recomputed after all fragments are ready
- If hashes differ: discard all fragments, restart
- If hashes match: proceed to upload queue

Fragments can be uploaded as they complete (streaming upload).
catalog.db is updated only after all fragments are confirmed placed.
A file does not "exist" in the system until catalog.db is updated.

---

## Invite system

Model: anyone in the cluster can generate an invite. No designated admin.

Invite properties:
- Single-use (invalidated immediately after use)
- 48-hour expiry
- Short human-readable code: e.g. `coffee-trumpet-7`
- Visible to all cluster members in GUI
- Revocable by any member before use

Join flow (Phase 1 — two separate steps):
1. New buddy joins the Tailscale network manually
2. New buddy enters invite code in gatekeeper GUI
3. Gatekeeper fetches introducer FURL + cluster config
4. New buddy appears in all members' GUI within minutes

Under the hood: wraps Tahoe-LAFS `tahoe invite` / `tahoe create-client --join`.

---

## Node removal

Any member can propose removal. Simple majority vote required.
Proposed node does not see the vote while it is open.

Removal flow:
1. Member proposes removal in GUI
2. Vote open for 48 hours, visible to all except target
3. Majority yes → 7-day grace period begins
4. During grace period: re-fragmentation of target's data to other nodes
5. After grace period: target's fragments purged from cluster
6. Target's stored fragments (belonging to others) purged from target node

Grace period can be extended by cluster majority vote.
Extension use case: known hardware failure, temporary absence.

---

## Ratio abuse / free-rider detection

GUI shows a per-buddy table:

| Buddy  | Contributes | Uses    | Status |
|--------|-------------|---------|--------|
| Anders | 500 GB      | 200 GB  | ✓      |
| Carina | 12 GB       | 380 GB  | ⚠      |

Phase 1 response: warning visible to all cluster members.
Notification sent to the offending buddy.

Phase 2 response: automatic upload throttling if ratio exceeds 3:1
(taking three times more than contributing).

---

## Re-fragmentation policy

Re-fragmentation is triggered when cluster size changes enough to warrant
a new k/n value (Adaptive profile) or when fragments fall below minimum.

### Hysteresis zone

Small cluster size changes do not trigger re-fragmentation.
Default: ±2 nodes from current baseline.

```
Baseline: 9 nodes (3-of-9)
8 nodes  → no re-fragmentation
10 nodes → no re-fragmentation
11 nodes → no re-fragmentation
12 nodes → re-fragmentation queued  (outside ±2 zone)
```

### Stability threshold

Re-fragmentation does not start until cluster size has been stable
for a configurable number of days (default: 7).
Prevents churn from hit-and-run nodes.

### Gradual execution

Re-fragmentation runs nightly at low priority, limited to a percentage
of total files per night (default: 3%).

```
Night 1: 3% of files re-fragmented
Night 2: another 3%
...
Night ~34: complete
```

Priority order:
1. Files below minimum fragment count (critical, always first)
2. Oldest backed-up files
3. Largest files
4. Remainder

New fragments are verified before old fragments are deleted.

---

## Orphan fragment cleanup

Fragments become orphaned when their owner leaves the cluster or loses
root_dir.cap without recovery.

Each fragment carries an encrypted owner tag (node ID + timestamps).
Not readable by the storing node — only interpretable with cluster metadata.

Cleanup flow:
1. Daily job checks all stored fragments against cluster node list
2. Fragments from nodes absent > `orphan_grace_days` (default: 30) → marked orphan
3. Re-fragmentation confirmed complete → orphan fragments deleted
4. Notification sent: "Cleared X GB of orphaned fragments from [node]"

Grace period is configurable and extendable by cluster majority vote.

---

## Verification and test restore

Three-layer daily verification job (runs at 04:00 by default):

**Layer 1 — root_dir.cap**
Can the Tahoe file tree be opened? If not: critical alert, lifeboat restore required.

**Layer 2 — catalog.db vs cluster**
Does every entry in catalog.db exist in the Tahoe tree?
Does every file have the expected number of fragments (≥ k)?
Files below k: flagged for immediate re-fragmentation.

**Layer 3 — Test restore**
N random files (default: 3) are restored to a temporary path.
SHA-256 of restored file compared against catalog.db entry.
Temporary files deleted after verification.
Failure: alert sent, file flagged for investigation.

**Layer 4 — Lifeboat check**
Can the lifeboat be decrypted?
Is root_dir.cap inside identical to the active one?
Is catalog.db inside no older than `lifeboat_max_age_hours` (default: 6)?

All failures generate notifications via SMTP and/or webhook.
Success is silent by default.

---

## Monitoring and notifications

### Alert thresholds

| Alert | Default |
|-------|---------|
| Min connected nodes | 3 |
| Storage warning | 85% |
| Storage critical | 95% |
| Node offline after | 15 min |
| Lifeboat max age | 6 hours |

### Notification channels

**SMTP**: standard email. Password stored encrypted, entered via GUI.
Test button available in GUI before saving.

**Webhook**: generic HTTP POST. Compatible with Slack, Discord, Ntfy,
Gotify, and similar. Same JSON payload format for all targets.

Both channels can be active simultaneously.
Per-event granularity: success (silent by default), warning, failure, critical.

---

## Web GUI

Framework: FastAPI (Python — same ecosystem as Tahoe-LAFS).
Port: 8080 (configurable).
Binding: Tailscale interface only. Not exposed on LAN or public internet
unless Tailscale subnet routing is enabled on the gatekeeper.

### GUI sections

**Dashboard**
- Cluster node status (online/offline count)
- My storage pool usage (per path)
- How much I store for others (per buddy)
- Last backup per agent (timestamp + status)
- Active jobs (fragmenting, uploading, verifying)

**Restore**
- Find a specific file (search by name or date)
- Restore a full folder (select agent + snapshot date)
- Emergency restore (gatekeeper gone — load root_dir.cap)

**Settings**
- Fragmentation profile (four buttons: Balanced / Secure / Paranoid / Adaptive)
- Storage pool paths and quotas
- Notifications (SMTP + webhook, test buttons)
- Lifeboat (last saved timestamp, test decryption)

**Buddies**
- Per-buddy: name, online status, contribution vs usage, profile
- Cluster storage overview (total capacity, total used)
- Invite new buddy (generate code)
- Propose removal (initiates vote)
- Active votes (open invites, pending removals, grace period extensions)

**Agents**
- Per agent: name, last backup, backup.cfg status, log (if shared)
