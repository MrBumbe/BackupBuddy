# Testing

## Overview

All testing is done in an isolated Proxmox environment before any
live deployment. The Proxmox host is managed remotely via SSH and
its REST API from a dedicated Ubuntu build server (bare metal).

Claude Code runs on the Ubuntu build server and has full access to
orchestrate the Proxmox environment: create VMs and containers,
configure networking, run test scenarios, and report results.

The test cluster mirrors the real-world architecture exactly:
separate virtual home networks, Tailscale between gatekeepers,
agents talking only to their local gatekeeper via LAN.

---

## Infrastructure layout

```
[Ubuntu build server — bare metal]
    Claude Code runs here
    SSH access to Proxmox host
    Proxmox API access (REST, port 8006)

[Proxmox host — separate machine]
    All test VMs and containers run here
    Isolated from production network
```

---

## Proxmox network topology

Four virtual bridges are created on the Proxmox host.
They are completely isolated from each other and from the
Proxmox host's own network unless explicitly bridged.

```
vmbr10 → "Anders home network"   10.10.1.0/24
vmbr20 → "Björn home network"    10.10.2.0/24
vmbr30 → "Carina home network"   10.10.3.0/24
vmbr99 → "Management"            10.99.0.0/24  (SSH access for Claude Code)
```

The management bridge (vmbr99) is the only one with a route to
the outside world. Every VM gets a NIC on vmbr99 so Claude Code
can always reach them via SSH for configuration and inspection.

Gatekeepers do NOT have a direct connection between their home
network bridges. The only path between gatekeepers is Tailscale —
exactly as in a real deployment.

### Bridge setup on Proxmox host

Add to `/etc/network/interfaces` on the Proxmox host:

```
auto vmbr10
iface vmbr10 inet static
    address 10.10.1.1/24
    bridge-ports none
    bridge-stp off
    bridge-fd 0

auto vmbr20
iface vmbr20 inet static
    address 10.20.1.1/24
    bridge-ports none
    bridge-stp off
    bridge-fd 0

auto vmbr30
iface vmbr30 inet static
    address 10.30.1.1/24
    bridge-ports none
    bridge-stp off
    bridge-fd 0

auto vmbr99
iface vmbr99 inet static
    address 10.99.0.1/24
    bridge-ports none
    bridge-stp off
    bridge-fd 0
```

Apply with: `systemctl restart networking`

---

## Virtual machines and containers

### VM list

| VM/CT | Type | Role | Home bridge | Mgmt IP | Home IP |
|-------|------|------|-------------|---------|---------|
| gatekeeper-anders | VM | Gatekeeper + Tailscale | vmbr10 | 10.99.0.11 | 10.10.1.10 |
| gatekeeper-bjorn | VM | Gatekeeper + Tailscale | vmbr20 | 10.99.0.12 | 10.20.1.10 |
| gatekeeper-carina | VM | Gatekeeper + Tailscale | vmbr30 | 10.99.0.13 | 10.30.1.10 |
| introducer | VM | Tahoe-LAFS introducer | vmbr99 only | 10.99.0.20 | — |
| agent-anders-pc | CT | Agent (simulates a PC) | vmbr10 | 10.99.0.31 | 10.10.1.31 |
| agent-anders-nas | CT | Agent (simulates NAS) | vmbr10 | 10.99.0.32 | 10.10.1.32 |
| agent-bjorn-pc | CT | Agent (simulates a PC) | vmbr20 | 10.99.0.33 | 10.20.1.33 |

### VM specifications

Gatekeeper VMs:
- OS: Ubuntu Server 24.04 LTS
- CPU: 2 cores
- RAM: 2 GB
- Disk: 20 GB (OS) + 10 GB (storage pool simulation)
- NICs: eth0 → vmbr99 (mgmt), eth1 → home bridge

Agent containers (LXC):
- OS: Ubuntu 24.04
- CPU: 1 core
- RAM: 512 MB
- Disk: 5 GB
- NIC: eth0 → vmbr99 (mgmt), eth1 → home bridge
- Privileged: false

Introducer VM:
- OS: Ubuntu Server 24.04 LTS
- CPU: 1 core
- RAM: 1 GB
- Disk: 10 GB
- NIC: eth0 → vmbr99 (mgmt) only
  The introducer is reachable by all gatekeepers via Tailscale.

---

## Setting up the test environment

All steps below are run from the Ubuntu build server by Claude Code
via SSH to the Proxmox host or directly into each VM/CT.

### Step 1 — Create a base Ubuntu template

Create a cloud-init enabled Ubuntu 24.04 template on Proxmox.
This is used to clone all gatekeeper VMs quickly.

```bash
# On Proxmox host
wget https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img

qm create 9000 --name ubuntu-2404-template --memory 2048 --cores 2 \
  --net0 virtio,bridge=vmbr99 --serial0 socket --vga serial0

qm importdisk 9000 noble-server-cloudimg-amd64.img local-lvm
qm set 9000 --scsihw virtio-scsi-pci --scsi0 local-lvm:vm-9000-disk-0
qm set 9000 --boot c --bootdisk scsi0
qm set 9000 --ide2 local-lvm:cloudinit
qm set 9000 --ipconfig0 ip=dhcp
qm set 9000 --agent enabled=1
qm template 9000
```

### Step 2 — Clone and configure gatekeeper VMs

```bash
# Clone gatekeeper-anders from template (repeat for bjorn, carina)
qm clone 9000 101 --name gatekeeper-anders --full

# Add second NIC for home network
qm set 101 --net1 virtio,bridge=vmbr10

# Add storage disk for buddy fragment pool
qm set 101 --scsi1 local-lvm:10

# Set static IPs via cloud-init
qm set 101 --ipconfig0 ip=10.99.0.11/24,gw=10.99.0.1
qm set 101 --ipconfig1 ip=10.10.1.10/24

qm start 101
```

### Step 3 — Create agent containers

```bash
# Download Ubuntu LXC template if not present
pveam update
pveam download local ubuntu-24.04-standard_24.04-2_amd64.tar.zst

# Create agent container
pct create 301 local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
  --hostname agent-anders-pc \
  --memory 512 --cores 1 \
  --rootfs local-lvm:5 \
  --net0 name=eth0,bridge=vmbr99,ip=10.99.0.31/24,gw=10.99.0.1 \
  --net1 name=eth1,bridge=vmbr10,ip=10.10.1.31/24 \
  --unprivileged 1 \
  --start 1
```

### Step 4 — Install Tailscale on all gatekeepers

Run on each gatekeeper VM:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --authkey=<YOUR_TAILSCALE_AUTH_KEY>
```

Use a dedicated Tailscale tailnet for testing, separate from any
personal or production tailnet. Auth keys can be generated in the
Tailscale admin panel under Settings → Keys.

### Step 5 — Install and configure Tahoe-LAFS introducer

```bash
# On introducer VM (10.99.0.20)
pip install tahoe-lafs

# Create and start introducer
tahoe create-introducer --hostname=10.99.0.20 ~/introducer
tahoe run ~/introducer &

# Retrieve the FURL (needed for gatekeeper config)
cat ~/introducer/private/introducer.furl
```

The introducer FURL looks like:
`pb://nodeid@10.99.0.20:3458/secret-string`

This goes into gatekeeper.cfg on all gatekeeper VMs.

### Step 6 — Install BackupBuddy on gatekeepers and agents

```bash
# On each gatekeeper
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
sudo bash /opt/backup-buddy/install/gatekeeper.sh
# Complete the onboarding wizard or use a pre-seeded config for testing

# On each agent container
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
sudo bash /opt/backup-buddy/install/agent.sh
```

For automated testing, gatekeeper.cfg and backup.cfg can be
pre-written by Claude Code and copied via SCP instead of using
the wizard. This speeds up test environment rebuilds.

### Step 7 — Seed test data on agents

Create realistic test data on agent containers:

```bash
# On agent-anders-pc (10.10.1.31)
mkdir -p /home/testuser/documents /home/testuser/pictures

# Small files (documents)
for i in $(seq 1 50); do
  dd if=/dev/urandom bs=1K count=$((RANDOM % 500 + 10)) \
    of=/home/testuser/documents/doc_$i.pdf 2>/dev/null
done

# Medium files (photos)
for i in $(seq 1 20); do
  dd if=/dev/urandom bs=1M count=$((RANDOM % 8 + 1)) \
    of=/home/testuser/pictures/photo_$i.jpg 2>/dev/null
done

# One large file (simulates a video)
dd if=/dev/urandom bs=1M count=512 \
  of=/home/testuser/pictures/vacation_video.mp4 2>/dev/null
```

---

## Test scenarios

Each scenario has: goal, setup, steps, expected result, and
what to check if it fails.

Adjust these settings in gatekeeper.cfg for test environments
to avoid waiting hours for timeouts:

```ini
[watcher]
stability_minutes = 1       # 1 min instead of 30

[maintenance]
orphan_grace_days = 1       # 1 day instead of 30

[rebalance]
stability_days = 0          # no wait before rebalancing
```

---

### Scenario 1 — Basic backup and restore

**Goal**: confirm that a file backed up by an agent can be fully
restored with hash verification passing.

**Steps**:
1. Ensure agent-anders-pc is running and backup.cfg points to
   `/home/testuser/documents`
2. Copy a known file to the backup path and record its SHA-256:
   ```bash
   cp /etc/os-release /home/testuser/documents/testfile.txt
   sha256sum /home/testuser/documents/testfile.txt
   ```
3. Wait for the file watcher to detect stability (1 min in test config)
4. Watch gatekeeper-anders logs to confirm upload completes
5. Confirm the file appears in the GUI under agent-anders-pc
6. Delete the original file:
   ```bash
   rm /home/testuser/documents/testfile.txt
   ```
7. Restore via GUI: Restore → Find a specific file → testfile.txt
8. Verify the restored file:
   ```bash
   sha256sum /restored/testfile.txt
   ```

**Expected result**: SHA-256 of restored file matches original.
GUI shows restore as successful. catalog.db entry matches.

**If it fails**: check gatekeeper-anders logs for upload errors.
Check that at least k fragment nodes were reachable during restore.
Check that the introducer was running during upload.

---

### Scenario 2 — Node offline during backup

**Goal**: confirm that a backup completes even when one storage
node goes offline mid-transfer.

**Steps**:
1. Start backing up the large file (vacation_video.mp4, ~512 MB)
2. While upload is in progress, shut down gatekeeper-bjorn:
   ```bash
   # Via Proxmox API or SSH to Proxmox host
   qm stop 102
   ```
3. Wait for backup to complete
4. Confirm the backup completed successfully in gatekeeper-anders GUI
5. Check fragment distribution — all fragments should be on
   gatekeeper-anders and gatekeeper-carina only
6. Start gatekeeper-bjorn again:
   ```bash
   qm start 102
   ```
7. Verify re-fragmentation is queued or completed (if cluster size
   changed enough to trigger hysteresis)

**Expected result**: backup completes. File is restorable.
A warning may appear in GUI that fewer fragments than expected
were placed. Re-fragmentation queued once bjorn is back online.

**If it fails**: check that shares.happy threshold allows completion
with one node missing. May need to adjust fragmentation profile
for small test clusters (e.g. use Lagom 3-of-5 instead of Adaptiv).

---

### Scenario 3 — Disaster recovery (recovery kit)

**Goal**: confirm that a gatekeeper can be replaced using the
`recovery-kit.enc` file and passphrase from the onboarding wizard,
with no catalog or file data loss.

**Recovery path (Option A — Phase 1)**: passphrase decrypts
`recovery-kit.enc` → extracts `root_dir_cap` → catalog is
reconstructed by traversing the Tahoe file tree (same as Scenario 4).
Restored catalog entries have no SHA-256 stored (by design —
see Scenario 4 notes). Files are fully restorable via Tahoe caps.

**Pre-conditions**:
- Gatekeeper was set up via the onboarding wizard (so `recovery-kit.enc` exists)
- `recovery-kit.enc` was downloaded and stored safely
- The passphrase entered during setup is known

**Steps**:
1. Set up gatekeeper-anders via the onboarding wizard:
   - Record the passphrase
   - Download `recovery-kit.enc` and confirm the download
2. Back up at least 10 files on agent-anders-pc; record SHA-256 of one:
   ```bash
   sha256sum /home/testuser/documents/scenario3_testfile.txt
   ```
3. Snapshot gatekeeper-anders for reference:
   ```bash
   # On Proxmox host
   qm snapshot 101 before-scenario3
   ```
4. Destroy gatekeeper-anders completely:
   ```bash
   qm stop 101 && qm destroy 101
   ```
5. Create a fresh VM with the same Tailscale name; install and start BackupBuddy
6. Run the onboarding wizard on the fresh install to get a working gatekeeper
   (can use the same node name; a new Tahoe identity is fine for Phase 1)
7. In the GUI: Restore → Emergency restore tab
8. Upload `recovery-kit.enc` and enter the passphrase
9. Click "Reconstruct catalog" and poll until `status: done`
10. Confirm all ≥10 previously backed-up files appear in the catalog
11. Restore `scenario3_testfile.txt` and verify SHA-256 matches step 2

**Expected result**: catalog reconstructed with all files. Restore works.
SHA-256 of restored file matches the original.

**Notes**:
- Reconstructed entries have `sha256=""` (same as Scenario 4) — this is
  expected and logged as a warning by the nightly verifier, not an error.
- Phase 2 will add full catalog snapshot recovery (Option B) so SHA-256
  is preserved across disaster recovery.

**If it fails**: check that `recovery-kit.enc` decrypts correctly (wrong
passphrase returns a clear error in the GUI). Check Tahoe connectivity on
the new gatekeeper — the cluster must be reachable to traverse the file tree.

---

### Scenario 4 — Katalog.db reconstruction ("call home")

**Goal**: confirm that catalog.db can be fully rebuilt from the
Tahoe file tree using only root_dir.cap, with original paths restored.

**Steps**:
1. Back up at least 20 files with varied paths
2. Note exact original paths from catalog.db:
   ```bash
   sqlite3 /var/backup-buddy/catalog.db \
     "SELECT original_path, sha256 FROM files LIMIT 20;"
   ```
3. Delete catalog.db on gatekeeper-anders:
   ```bash
   rm /var/backup-buddy/catalog.db
   ```
4. Restart the gatekeeper service
5. In GUI: Restore → Emergency restore → "I have my recovery key,
   but the catalog is missing"
6. Enter root_dir.cap when prompted
7. System traverses the Tahoe file tree and rebuilds catalog.db
8. Compare rebuilt catalog.db entries against the saved output
   from step 2

**Expected result**: rebuilt catalog.db contains all files.
original_path matches for all entries. SHA-256 values match.
Files are restorable from the rebuilt catalog.

**If it fails**: check that encrypted metadata tags were written
correctly at upload time. Check that root_dir.cap can open the
Tahoe file tree (try: `tahoe ls --node-directory=~/.tahoe tahoe:`)

---

### Scenario 5 — Hit and run node (hysteresis test)

**Goal**: confirm that a node joining and leaving within the
stability window does not trigger re-fragmentation.

**Steps**:
1. Note current cluster size and k/n values in GUI
2. Note total fragment count across all nodes
3. Add gatekeeper-david (create and start a new VM, join cluster)
4. Wait 2 days (or set stability_days=3 and wait 2 days in test)
5. Remove gatekeeper-david from the cluster (propose removal,
   vote yes, grace period starts)
6. Monitor re-fragmentation logs on all gatekeepers

**Expected result**: no re-fragmentation occurs. Cluster returns
to previous k/n values. Logs show: "Cluster size change within
hysteresis zone — no rebalance triggered."

**If it fails**: check hysteresis_nodes setting in gatekeeper.cfg.
Check that stability_days timer was not reached before david left.

---

### Scenario 6 — Orphan fragment cleanup

**Goal**: confirm that fragments from a removed node are cleaned
up after the grace period, and disk space is reclaimed.

**Steps**:
1. Note how much storage gatekeeper-carina is using on other nodes:
   ```bash
   # On gatekeeper-anders
   du -sh /mnt/buddy-storage/
   sqlite3 /var/backup-buddy/cluster.db \
     "SELECT stored_bytes FROM orphan_tags WHERE owner='gatekeeper-carina';"
   ```
2. Remove gatekeeper-carina from the cluster (full removal flow)
3. Set orphan_grace_days=1 in test config, wait 24 hours
   (or manually trigger the orphan check job)
4. Check storage usage again on gatekeeper-anders:
   ```bash
   du -sh /mnt/buddy-storage/
   ```
5. Check notification log — should show cleanup notification

**Expected result**: storage used by carina's fragments is
reclaimed. Notification sent. cluster.db shows no remaining
orphan tags for gatekeeper-carina.

**If it fails**: check that re-fragmentation of carina's data
completed before orphan cleanup ran. Orphans should not be
deleted until the data they held has been re-fragmented elsewhere.

---

### Scenario 7 — Nightly verification and test restore

**Goal**: confirm the automated verification job runs correctly
and catches a deliberately corrupted fragment.

**Steps**:
1. Let the system run normally for at least one backup cycle
2. Manually corrupt one fragment on gatekeeper-bjorn:
   ```bash
   # Find a fragment file
   ls /mnt/buddy-storage/ | head -5
   # Corrupt it
   dd if=/dev/urandom bs=1 count=100 seek=500 \
     of=/mnt/buddy-storage/<fragment-file> conv=notrunc
   ```
3. Trigger the verification job manually (or wait for 04:00):
   ```bash
   backup-buddy verify --now
   ```
4. Check the GUI dashboard for the verification result
5. Check that a notification was sent (SMTP or webhook)
6. Check that the system attempted to re-fetch from other nodes

**Expected result**: verification detects the corrupted fragment.
Alert sent. System automatically attempts restore from other
fragment copies. If enough fragments remain (≥ k), the file
is re-verified as intact from the good copies.

**If it fails**: check that the test restore picked the corrupted
fragment's file. May need to guide the test restore to specifically
include the file whose fragment was corrupted.

---

### Scenario 8 — Introducer outage resilience

**Goal**: confirm that restores continue to work when the Tahoe introducer
VM goes down, as long as ≥ 2 storage nodes are reachable and peer FURLs
were cached from a prior successful connection.

This tests the fix from task 1.21.3: storage nodes now advertise their
Tailscale IP in `tub.location` so Foolscap Reconnectors on peer nodes can
maintain live connections independently of the introducer.

**Pre-conditions**:
- All three gatekeepers and the introducer are running
- At least one file has been backed up successfully (so shares exist
  on ≥ 2 storage nodes and peer FURLs are cached in `private/servers.yaml`)

**Steps**:
1. Back up a known test file and record its SHA-256:
   ```bash
   # On agent-anders-pc
   cp /etc/os-release /home/testuser/documents/scenario8.txt
   sha256sum /home/testuser/documents/scenario8.txt
   ```
2. Wait for backup to complete (1 min stability window in test config);
   confirm it appears in gatekeeper-anders GUI
3. Verify the storage node tub.location on each gatekeeper uses the
   Tailscale IP (100.x.x.x range), not a LAN IP:
   ```bash
   grep tub.location ~/.backupbuddy/tahoe/storage_node/tahoe.cfg
   # Expected: tub.location = tcp:100.x.x.x:PORT
   ```
4. Stop the introducer VM:
   ```bash
   ssh proxmox "qm stop 104"
   ```
5. Confirm the gatekeeper log shows the introducer warning:
   ```bash
   journalctl -u backup-buddy-gatekeeper -n 20 | grep -i introducer
   # On the NEXT restart, expect:
   # "Introducer unreachable — operating from cached server list (N servers)"
   ```
6. Without restarting any gatekeeper, attempt a restore via Björn's
   GUI: Restore → find `scenario8.txt` → restore
7. Verify the restored file:
   ```bash
   sha256sum /restored/scenario8.txt
   ```
8. Attempt a second restore via Carina's GUI to confirm both peers work
9. Restart one gatekeeper (e.g., Björn) while the introducer is still down:
   ```bash
   systemctl restart backup-buddy-gatekeeper
   ```
   Wait for it to come back online, then attempt a restore — confirms
   the `private/servers.yaml` cache is used correctly on cold start
10. Bring the introducer back:
    ```bash
    ssh proxmox "qm start 104"
    ```

**Expected result**:
- Step 6: restore succeeds (HTTP 200) even with the introducer down,
  because the Tahoe client's Foolscap Reconnectors maintain live
  connections to peer storage nodes via Tailscale IPs
- Step 7: SHA-256 of restored file matches original from step 1
- Step 8: Carina's restore also succeeds
- Step 9: After restart, Björn uses the `private/servers.yaml` cache
  and the warning "Introducer unreachable — operating from cached server
  list (N servers)" appears in its log
- No gatekeeper reports HTTP 410 as long as ≥ k shares are on reachable
  storage nodes

**What to check if it fails**:
1. Verify `tub.location` in tahoe.cfg contains Tailscale IP (step 3).
   If it shows a LAN IP, the `start()` patch did not fire — check that
   Tailscale is active (`tailscale status`) when gatekeeper starts.
2. Check that Tailscale connectivity between gatekeepers is up:
   ```bash
   tailscale ping <peer-tailscale-hostname>
   ```
3. Check gatekeeper log for Foolscap connection errors — if the
   Reconnector cannot reach the peer's Tailscale IP, there may be a
   Tailscale ACL blocking Tahoe's port.
4. Confirm the Tahoe TUB port is the same as in `tub.location` (check
   `tub.port` in `tahoe.cfg`) and that nothing is firewalled.

---

## Proxmox API access from Claude Code

Claude Code can control Proxmox programmatically using the REST API.
Install the Python client on the Ubuntu build server:

```bash
pip install proxmoxer requests
```

Basic usage pattern:

```python
from proxmoxer import ProxmoxAPI

proxmox = ProxmoxAPI(
    '192.168.x.x',      # Proxmox host IP
    user='root@pam',
    password='...',
    verify_ssl=False
)

# List VMs
proxmox.nodes('pve').qemu.get()

# Start a VM
proxmox.nodes('pve').qemu(101).status.start.post()

# Stop a VM
proxmox.nodes('pve').qemu(101).status.stop.post()

# Create a snapshot
proxmox.nodes('pve').qemu(101).snapshot.post(
    snapname='before-test',
    description='Pre-test snapshot'
)

# Rollback to snapshot
proxmox.nodes('pve').qemu(101).snapshot('before-test').rollback.post()
```

Claude Code should snapshot all VMs before each test scenario
and roll back after, so the environment is always clean for
the next test run.

---

## Recommended test order

Run in this order. Each scenario builds on confidence from the previous.

```
1. Scenario 1  — Basic backup and restore
                 (confirms the core loop works)

2. Scenario 4  — Katalog.db reconstruction
                 (confirms "call home" before testing lifeboat)

3. Scenario 3  — Lifeboat restore
                 (confirms disaster recovery works end to end)

4. Scenario 7  — Nightly verification
                 (confirms monitoring catches real problems)

5. Scenario 8  — Introducer outage resilience
                 (confirms restores survive introducer loss)

6. Scenario 2  — Node offline during backup
                 (confirms resilience during transfer)

7. Scenario 5  — Hit and run node
                 (confirms hysteresis works)

8. Scenario 6  — Orphan cleanup
                 (confirms storage is reclaimed correctly)
```

Scenarios 1–4 must all pass before any real-world deployment.
Scenarios 5–7 confirm advanced behaviour and can be iterated on
after an initial working deployment if needed.
