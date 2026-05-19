# Onboarding

Goal: a person with general technical familiarity (homelab beginner, not a developer)
should be able to get a working node up and running without reading documentation.

No Tahoe-LAFS terminology exposed to the user. No FURLs, no caps, no shares.
Errors shown in plain language, never as stack traces.
Every step has sane defaults — clicking through without changing anything
produces a working system.

---

## Gatekeeper installation

### One command to start

```bash
curl -sSL https://get.backupbuddy.io | bash
```

The install script handles:
- Installing Tailscale (if not already present)
- Installing Tahoe-LAFS and BackupBuddy
- Generating a self-signed cert for local HTTPS (optional)
- Starting the gatekeeper service
- Starting the onboarding web server on port 8080
- Opening the browser automatically if a desktop environment is detected

After the script completes, the user is either looking at the onboarding
wizard in their browser, or sees:

```
BackupBuddy is running. Open http://192.168.1.50:8080 to continue setup.
```

---

## Onboarding wizard (web GUI)

Five steps. Progress is saved — the wizard can be resumed if interrupted.
All Tahoe-LAFS internals handled silently in the background.

---

### Step 1 of 5 — Role

```
Welcome to BackupBuddy                                    1 / 5

What do you want to do?

  ○  Start a new cluster
     You are the first buddy. You will invite others.

  ●  Join an existing cluster
     You have received an invite code from a buddy.

                                              [Continue →]
```

**Start new cluster** → steps 2–5, then generates first invite code.
**Join existing cluster** → asks for invite code first, then steps 2–5.

---

### Step 2 of 5 — Node name

```
What should this node be called?                          2 / 5

This name is visible to your buddies in the cluster.

  Node name:  [Anders home node          ]

  Tip: use something that describes the location or owner,
  e.g. "Erik office", "Carina basement server".

                                [← Back]  [Continue →]
```

---

### Step 3 of 5 — Storage

```
Where should your buddies' fragments be stored?           3 / 5

Your buddies' encrypted data will be stored here.
You cannot see or read the contents — only encrypted fragments.

  [Browse]  /mnt/nas/buddy-storage          Quota: [2000] GB

  [+ Add another path]

  Total allocated: 2000 GB

  ────────────────────────────────────────────────────────
  ℹ  These paths will be automatically excluded from your
     own backup. This cannot be changed.

                                [← Back]  [Continue →]
```

Minimum one path required. Multiple paths are pooled transparently.
Quota input validated against actual available disk space.

---

### Step 4 of 5 — Backup profile

```
How many copies of your data do you want?                 4 / 5

  ○  Lagom      Balanced. Requires 5 buddies online to restore.
                Good for photos, documents, general files.

  ○  Trygg      More margin. Requires 7 buddies online.
                Good for anything important.

  ○  Paranoid   Maximum redundancy. Requires 10 buddies online.
                Good for business records, irreplaceable files.

  ●  Adaptive   Recommended. Scales automatically as your group
                grows. Always keeps 1/3 of buddies as minimum
                for restore. Rebalances itself nightly.

  [What does this mean? ▾]

                                [← Back]  [Continue →]
```

Expandable explanation written in plain language — no erasure coding
terminology. Example: "If you have 9 buddies and Adaptive is on, your
files are split into 9 pieces. Any 3 of those pieces are enough to
get your files back."

---

### Step 5 of 5 — Notifications (optional)

```
How should BackupBuddy reach you?                         5 / 5

You will be notified if a backup fails, a buddy goes offline,
or your storage is running low.

  Email (SMTP)
  ┌──────────────────────────────────────────────────────┐
  │ SMTP server:  [smtp.gmail.com          ]  Port: [587]│
  │ Username:     [anders@gmail.com        ]             │
  │ Password:     [●●●●●●●●●●●●●●●●        ]             │
  │ Send to:      [anders@gmail.com        ]             │
  │                                    [Test email]      │
  └──────────────────────────────────────────────────────┘

  Webhook (Discord, Slack, Ntfy, etc.)
  ┌──────────────────────────────────────────────────────┐
  │ URL:  [https://discord.com/api/webhooks/...         ]│
  │                                  [Test webhook]      │
  └──────────────────────────────────────────────────────┘

  [Skip for now — I'll set this up later]

                                [← Back]  [Finish setup →]
```

Both channels optional. Skip link always visible.
Passwords stored encrypted. Never written to config files in plaintext.

---

### Setup complete

```
You're all set!                                           ✓

Your node "Anders home node" is running.

────────────────────────────────────────────────────────

  ⚠  Important: save your recovery key

  Your recovery key is the only way to access your
  backups if this node needs to be rebuilt from scratch.

  Recovery key:  [Show]  [Copy]  [Download as file]

  Save it in a password manager or on a USB drive
  somewhere safe. Do not store it only on this machine.

  [I have saved my recovery key]   ← must click to continue

────────────────────────────────────────────────────────

  Invite your first buddy

  Share this code with a friend. It expires in 48 hours
  and can only be used once.

  Code:  kaffe-trumpet-7          [Copy]  [New code]

────────────────────────────────────────────────────────

                                    [Go to dashboard →]
```

"I have saved my recovery key" must be clicked before the invite code
is shown. This makes it impossible to skip the key backup step by accident.

Recovery key = root_dir.cap. Never shown with that name in the UI.

---

## Agent installation

The agent is intentionally simpler than the gatekeeper.
It only needs to know what to back up and where its gatekeeper is.

### Install command

```bash
curl -sSL https://get.backupbuddy.io/agent | bash
```

The script asks two questions and exits:

```
What is your gatekeeper's IP address? [192.168.1.50]
What should this agent be called? [this-pc]

Done. Edit /etc/backup-buddy/backup.cfg to choose which
folders to back up, then restart the agent:

  systemctl restart backup-buddy-agent
```

### backup.cfg comes pre-commented

```ini
# BackupBuddy agent configuration
# Add the folders you want to back up under [backup].
# One folder per line. Subfolders are included automatically.

[backup]
# /home/yourname/documents
# /home/yourname/pictures
# /mnt/nas/important

# Share the backup log with your gatekeeper? (true/false)
# If true, your gatekeeper can see which files succeeded or failed.
# Default: false
[node]
share_log = false
```

---

## Design rules for onboarding

These apply to all onboarding UI and copy:

- No Tahoe-LAFS terminology (no FURL, cap, share, grid, introducer, tub)
- No jargon without inline explanation
- Every error message ends with a suggested action, not just a description
- Defaults are chosen so that clicking through without reading produces
  a working and reasonably secure system
- Progress is always saved — interrupting and resuming is safe
- The recovery key step cannot be skipped
- Notification setup is always optional and skippable
- Install script must be idempotent — safe to run twice
