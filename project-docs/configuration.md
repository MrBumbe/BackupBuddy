# Configuration reference

---

## gatekeeper.cfg

Full configuration file for the gateway node.

```ini
# ── Node identity ─────────────────────────────────────
[node]
name         = anders-gatekeeper
display_name = Anders home cluster

# ── Tahoe-LAFS ────────────────────────────────────────
[tahoe]
introducer   = pb://nodeid@introducer.tailnet.ts.net:3456/secret
# shares.needed / shares.happy / shares.total are set automatically
# based on the active fragmentation profile

# ── Fragmentation profile ─────────────────────────────
# Options: balanced | secure | paranoid | adaptive
[fragmentation]
profile              = adaptive

# Adaptive-specific settings (ignored for fixed profiles)
adaptive.ratio       = 0.33
adaptive.min_k       = 2
adaptive.max_n       = 20
adaptive.rebalance_time = 03:00

# ── Storage pool ──────────────────────────────────────
# Paths where buddy fragments are stored locally.
# Each path has a hard quota (GB).
# These paths are automatically excluded from backup — cannot be overridden.
[storage-pool]
/mnt/nas/buddy-storage  = 2000 GB
/mnt/data/buddy-storage =  400 GB
/mnt/usb/buddy-storage  =  800 GB

# ── Lifeboat ──────────────────────────────────────────
[lifeboat]
enabled       = true
interval      = 1h
# Passphrase is entered at first setup and never stored on disk.
# Required for restore. Store root_dir.cap externally as well (USB, password manager).
distribute_to = all_agents

# ── File watcher ──────────────────────────────────────
[watcher]
stability_minutes  = 30
check_open_handles = true
cpu_priority       = lowest    # nice +19
io_priority        = idle      # ionice -c 3
upload_concurrent  = 2

# ── Global excludes ───────────────────────────────────
# Storage pool paths are excluded automatically — do not add them here.
[exclude]
*.tmp
*.part
*.log
~$*
*.db-journal
Thumbs.db
.DS_Store

# ── Storage ratio ─────────────────────────────────────
[quota]
min_ratio  = 1.0    # warn if contribution < own backup size
warn_ratio = 1.2    # recommended minimum with buffer

# ── Re-fragmentation ──────────────────────────────────
[rebalance]
stability_days      = 7        # cluster must be stable before rebalancing
hysteresis_nodes    = 2        # ±N nodes does not trigger rebalance
daily_rebalance_pct = 3        # max % of files rebalanced per night
rebalance_time      = 03:30    # runs after nightly verify (04:00)
min_fragments_before_delete = true  # verify new fragments before removing old
notify_on_start     = true
notify_on_complete  = true

# ── Orphan cleanup ────────────────────────────────────
[maintenance]
orphan_check_interval = 24h
orphan_grace_days     = 30     # configurable, extendable by cluster vote
auto_clean            = true
notify_on_clean       = true

# ── Verification ──────────────────────────────────────
[verify]
daily_check_time       = 04:00
test_restore_enabled   = true
test_restore_files     = 3
test_restore_path      = /tmp/buddy-verify/
lifeboat_max_age_hours = 6
notify_on_success      = false
notify_on_warning      = true
notify_on_failure      = true
notify_on_corrupt      = true

# ── Alert thresholds ──────────────────────────────────
[alerts]
min_connected_nodes  = 3
storage_warning_pct  = 85
storage_critical_pct = 95
node_offline_after   = 15m

# ── Notifications ─────────────────────────────────────
[notify]
on_backup_success  = false
on_backup_failure  = true
on_storage_warning = true
on_node_offline    = true
on_rebalance       = true

[notify.smtp]
enabled  = true
host     = smtp.gmail.com
port     = 587
user     = anders@gmail.com
# password stored encrypted, entered via GUI
to       = anders@gmail.com

[notify.webhook]
enabled  = true
url      = https://discord.com/api/webhooks/...

# ── Web GUI ───────────────────────────────────────────
[web]
enabled = true
port    = 8080
bind    = tailscale    # listen on Tailscale interface only
```

---

## backup.cfg (agent)

Configuration file per agent device. Located at:
`/etc/backup-buddy/backup.cfg` (Linux)
`C:\ProgramData\BackupBuddy\backup.cfg` (Windows)

```ini
# ── Schedule ──────────────────────────────────────────
[schedule]
# Backup is event-driven (file watcher), not scheduled.
# This controls the fallback full-scan interval.
full_scan = 24h

# ── Backup paths ──────────────────────────────────────
# Directories to back up. Subdirectories included recursively.
# One path per line.
[backup]
/home/anders/documents
/home/anders/pictures
/mnt/nas/important

# ── Excludes ──────────────────────────────────────────
[exclude]
*.tmp
*.part
~$*

# ── Node sharing ──────────────────────────────────────
[node]
# Whether the gatekeeper can read this agent's backup.log.
# Default: false (privacy by default)
share_log = false
```

---

## backup.log (agent, optional)

Written by the agent. Read by gatekeeper only if `share_log = true`.

```
2026-05-15 03:14:22  SUCCESS  /home/anders/documents/tax_return.pdf  (2.3 MB)
2026-05-15 03:14:45  SUCCESS  /home/anders/pictures/vacation.jpg     (4.1 MB)
2026-05-15 03:15:01  FAILED   /home/anders/documents/locked.docx     (file in use)
2026-05-15 03:15:01  SKIPPED  /home/anders/documents/notes.tmp       (excluded by pattern)
```

---

## Internal databases (not user-edited)

### catalog.db (gatekeeper)

SQLite database. Rebuilt automatically via "call home" if lost (requires root_dir.cap).

Key fields per file entry:
- `cap` — Tahoe-LAFS capability URI
- `sha256` — hash of original file (used for restore verification)
- `original_path` — full path on source agent
- `agent` — which agent this came from
- `backed_up_at` — timestamp
- `size_bytes` — original file size
- `profile` — fragmentation profile used
- `k`, `n` — actual erasure coding parameters used

### cluster.db (gatekeeper)

Cluster membership and state.

Key fields:
- `founded_by` — node ID of cluster creator
- `founded_at` — timestamp
- `members[]` — list of node IDs, join dates, contribution/usage stats
- `open_votes[]` — active invite codes, pending removals, grace extensions
- `orphan_tags[]` — fragments marked for cleanup with timestamps
