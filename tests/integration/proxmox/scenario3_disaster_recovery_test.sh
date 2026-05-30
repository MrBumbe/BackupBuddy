#!/usr/bin/env bash
# Integration test 1.16.12: Scenario 3 — Disaster recovery (recovery kit)
#
# Prerequisites:
#   - Proxmox host reachable at 192.168.1.60
#   - gatekeeper-anders (vmid 101): snapshot "post-install-no-wizard" exists
#   - agent-anders-pc  (vmid 301): snapshot "post-install-no-wizard" exists
#   - Ubuntu cloud-init template exists at vmid 9000
#
# What it tests:
#   Full disaster-recovery flow — wizard setup, agent backup of 10 files,
#   TRUE VM destruction (qm destroy) while the storage disk (scsi1 /
#   vm-101-disk-1) survives, fresh VM from template + install/gatekeeper.sh,
#   Tailscale identity restored from saved state, second wizard run, emergency
#   catalog reconstruction from recovery kit, SHA-256 verified on restored file.
#
#   This is a genuine "alternative B" disaster: the OS disk is destroyed and
#   the VM is re-provisioned from scratch.  No snapshot tricks.
#
# Run from the BackupBuddy project root:
#   bash tests/integration/proxmox/scenario3_disaster_recovery_test.sh

set -euo pipefail

PROXMOX="root@192.168.1.60"
ANDERS_VMID=101
AGENT_VMID=301
TEMPLATE_VMID=9000
ANDERS_LAN="10.99.0.11"
AGENT_LAN="10.99.0.31"
SNAPSHOT="post-install-no-wizard"
ANDERS_SVC="backup-buddy-gatekeeper"
ANDERS_DATA_DIR="/var/lib/backup-buddy"
ANDERS_CFG="/etc/backup-buddy/gatekeeper.cfg"
AGENT_SVC="backup-buddy-agent"
AGENT_CFG="/etc/backup-buddy/backup.cfg"
PASSPHRASE="scenario3-test-passphrase-2026"
NODE_NAME="gatekeeper-anders"
NODE_DISPLAY="Anders+Scenario+3"
BACKUP_PATH="/home/testuser/documents"
TEST_FILE_COUNT=10
RESTORE_DIR="/tmp/bb-s3-restore"
ANDERS_WIZARD_URL="http://$ANDERS_LAN:8080"
# UUID of the ext4 filesystem on scsi1 (/dev/sdb, 10G storage disk).
# This disk is preserved across qm destroy / qm clone by removing scsi1 from
# the Proxmox config before destroying the old VM.
STORAGE_UUID="2f2ca349-9cf1-450f-82f8-2f2145d9bdc3"
# Original MAC addresses — reused on the fresh VM so cloud-init generates
# a netplan that matches what the gatekeeper code expects.
ANDERS_NET0_MAC="BC:24:11:A3:F0:07"
ANDERS_NET1_MAC="BC:24:11:38:AE:C3"

# -o UserKnownHostsFile=/dev/null: never fails on host-key changes when the
# fresh VM presents a different key at the same IP.
SSH_OPTS="-q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 -o ServerAliveInterval=15"

# ── SSH helpers ────────────────────────────────────────────────────────────────
anders() { ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "$@"; }
agent()   { ssh $SSH_OPTS -J "$PROXMOX" "root@$AGENT_LAN"  "$@"; }
prox()    { ssh $SSH_OPTS               "$PROXMOX"          "$@"; }

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }
info() { echo "  → $*"; }

wait_http_prox() {
    local url="$1" label="$2" timeout="${3:-90}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "  Waiting for $label..."
    while (( $(date +%s) < deadline )); do
        if prox "curl -sf --max-time 5 '$url' -o /dev/null" 2>/dev/null; then
            echo " OK"; return 0
        fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

wait_http_anders() {
    local url="$1" label="$2" timeout="${3:-120}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "  Waiting for $label..."
    while (( $(date +%s) < deadline )); do
        if anders "curl -sf --max-time 5 '$url' -o /dev/null" 2>/dev/null; then
            echo " OK"; return 0
        fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

wait_ssh_anders() {
    local timeout="${1:-90}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "  Waiting for SSH on Anders..."
    while (( $(date +%s) < deadline )); do
        if anders "true" 2>/dev/null; then
            echo " OK"; return 0
        fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

wait_ssh_agent() {
    local timeout="${1:-90}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "  Waiting for SSH on agent..."
    while (( $(date +%s) < deadline )); do
        if agent "true" 2>/dev/null; then
            echo " OK"; return 0
        fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

# POST a wizard step and expect 303 redirect.
step_post() {
    local step_url="$1"; shift
    local code
    code=$(prox curl -sw '%{http_code}' -o /dev/null -X POST "$step_url" "$@" | tail -c 3)
    info "$step_url → HTTP $code"
    [[ "$code" == "303" ]] || fail "Expected 303 from $step_url, got $code"
}

# Run wizard steps 1-4 (no cascade).
drive_wizard_steps_1_4() {
    local base_url="$1"
    step_post "$base_url/onboarding/step/1" -d "role=new"
    step_post "$base_url/onboarding/step/2" \
        -d "node_name=$NODE_NAME" \
        -d "node_display_name=$NODE_DISPLAY"
    step_post "$base_url/onboarding/step/3" \
        --data-urlencode "storage_paths=/mnt/storage" \
        -d "storage_quota_gb=50"
    step_post "$base_url/onboarding/step/4" -d "profile=test"
}

# Read catalog file count via the venv Python (bypasses importing gatekeeper.main).
catalog_count() {
    local root_cap
    root_cap=$(anders "cat $ANDERS_DATA_DIR/root_dir.cap 2>/dev/null || echo ''")
    anders /opt/backup-buddy/.venv/bin/python3 << PYTHON 2>/dev/null || echo "0"
import sys
sys.path.insert(0, '/opt/backup-buddy')
from gatekeeper.db.catalog import CatalogDB
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
root_cap = '''$root_cap'''.strip()
if not root_cap:
    print(0)
else:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"backupbuddy:catalog:v1")
    key = hkdf.derive(root_cap.encode("utf-8"))
    db = CatalogDB('$ANDERS_DATA_DIR/catalog.db', key)
    print(len(db.get_all_files()))
PYTHON
}

# ══════════════════════════════════════════════════════════════════════════════
echo "=============================================="
echo "  1.16.12 — Scenario 3: Disaster recovery"
echo "  (full VM destroy + fresh install)"
echo "=============================================="
echo ""

[[ -f "install/gatekeeper.sh" ]] \
    || fail "install/gatekeeper.sh not found — run from project root"

# ── Step 1: Roll back VMs to clean snapshot ────────────────────────────────────
echo "=== Step 1: Roll back VMs to $SNAPSHOT ==="
prox "qm stop $ANDERS_VMID 2>/dev/null; sleep 3; qm rollback $ANDERS_VMID $SNAPSHOT && qm start $ANDERS_VMID"
prox "pct stop $AGENT_VMID 2>/dev/null; sleep 3; pct rollback $AGENT_VMID $SNAPSHOT && pct start $AGENT_VMID"
info "VMs rolled back, waiting for SSH..."
wait_ssh_anders 120
wait_ssh_agent 120
# Stop any old-format gatekeeper service left in the snapshot (pre-1.16.11).
anders "systemctl stop backupbuddy-gatekeeper 2>/dev/null || true"
anders "fuser -k 8081/tcp 2>/dev/null || true"
# Rebuild the tahoe entrypoint binary — a known snapshot quirk from 1.16.10 debugging.
info "Regenerating tahoe entrypoint on Anders (force-reinstall)..."
anders "cd /opt/backup-buddy && /opt/backup-buddy/.venv/bin/pip install --force-reinstall -q . 2>&1 | tail -3"
# Deploy current dev-machine source so we test what is in the repo, not the snapshot.
info "Deploying current gatekeeper source to Anders..."
tar -czf - gatekeeper/ | \
    ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" \
    "cd /opt/backup-buddy && tar -xzf - --no-same-owner && find gatekeeper -name '*.pyc' -delete"
info "Deploying current agent source to agent-anders-pc..."
tar -czf - agent/ | \
    ssh $SSH_OPTS -J "$PROXMOX" "root@$AGENT_LAN" \
    "cd /opt/backup-buddy && tar -xzf - --no-same-owner && find agent -name '*.pyc' -delete"
anders "systemctl restart $ANDERS_SVC 2>/dev/null || true"
anders "chown -R backupbuddy:backupbuddy /mnt/storage 2>/dev/null || true"
pass "VMs rolled back and accessible"

# ── Step 2: Wait for wizard ────────────────────────────────────────────────────
echo ""
echo "=== Step 2: Wait for Anders wizard ==="
wait_http_prox "$ANDERS_WIZARD_URL/onboarding/step/1" "Anders wizard" 120
pass "Wizard accessible"

# ── Step 3: Drive wizard (role=new, known passphrase) ─────────────────────────
echo ""
echo "=== Step 3: Drive wizard on Anders (role=new) ==="
drive_wizard_steps_1_4 "$ANDERS_WIZARD_URL"
info "Triggering finish cascade (up to 180s)..."
prox curl -s -o /tmp/s3_cascade.txt --max-time 180 -X POST \
    --data-urlencode "passphrase=$PASSPHRASE" \
    --data-urlencode "passphrase_confirm=$PASSPHRASE" \
    "$ANDERS_WIZARD_URL/onboarding/step/5" || true
info "Cascade HTTP call done"
anders "test -f $ANDERS_CFG" \
    || { BODY=$(prox cat /tmp/s3_cascade.txt 2>/dev/null || echo "(no body)"); fail "Cascade failed — no gatekeeper.cfg: $BODY"; }
pass "Wizard cascade complete — gatekeeper.cfg created"

# ── Step 4: Read agent token, restart into normal mode ────────────────────────
echo ""
echo "=== Step 4: Read token and restart into normal mode ==="
AGENT_TOKEN=$(anders python3 << PYTHON
import configparser
c = configparser.ConfigParser()
c.read('$ANDERS_CFG')
print(c['agent_api']['token'])
PYTHON
)
[[ -n "$AGENT_TOKEN" ]] || fail "Could not read agent_api.token from gatekeeper.cfg"
info "Agent token: ${AGENT_TOKEN:0:12}..."

anders "systemctl reset-failed $ANDERS_SVC 2>/dev/null || true"
anders "systemctl restart $ANDERS_SVC"
sleep 5

ANDERS_TS=$(anders "tailscale ip -4 2>/dev/null | head -1")
[[ -n "$ANDERS_TS" ]] || fail "Could not resolve Anders Tailscale IP"
ANDERS_TS_URL="http://$ANDERS_TS:8080"
info "Tailscale IP: $ANDERS_TS"

wait_http_anders "$ANDERS_TS_URL/api/status" "Anders normal mode" 120
STATUS=$(anders "curl -sf '$ANDERS_TS_URL/api/status'")
echo "$STATUS" | grep -q '"status":"ok"' || fail "Anders not healthy after wizard"
pass "Anders running in normal mode"

# ── Step 5: Configure agent and create test files ─────────────────────────────
echo ""
echo "=== Step 5: Configure agent-anders-pc and create $TEST_FILE_COUNT test files ==="
agent "mkdir -p $BACKUP_PATH"
for i in $(seq -w 01 $TEST_FILE_COUNT); do
    agent "dd if=/dev/urandom bs=256 count=1 2>/dev/null | base64 > $BACKUP_PATH/scenario3_file_${i}.bin && chmod 644 $BACKUP_PATH/scenario3_file_${i}.bin"
done
info "Test files created in $BACKUP_PATH"

ORIGINAL_SHA256=$(agent "sha256sum $BACKUP_PATH/scenario3_file_01.bin | awk '{print \$1}'")
[[ -n "$ORIGINAL_SHA256" ]] || fail "Could not compute SHA-256 of scenario3_file_01.bin"
info "SHA-256 of file_01: $ORIGINAL_SHA256"

agent "printf '[gatekeeper]\nurl = http://$ANDERS_LAN:8081\ntoken = $AGENT_TOKEN\nname = anders-pc\nlifeboat_path = /etc/backup-buddy/lifeboat.enc\n\n[lifeboat_server]\nenabled = true\nport = 8082\n\n[schedule]\nfull_scan = 24h\nstability_minutes = 1\n\n[backup]\n$BACKUP_PATH\n\n[exclude]\n*.tmp\n' > $AGENT_CFG"
agent "chown backupbuddy:backupbuddy $AGENT_CFG && chmod 600 $AGENT_CFG"
agent "systemctl restart $AGENT_SVC"
info "Agent reconfigured (stability_minutes=1) and restarted"
pass "Agent configured with $TEST_FILE_COUNT test files"

# ── Step 6: Wait for all files to appear in catalog ───────────────────────────
echo ""
echo "=== Step 6: Wait for $TEST_FILE_COUNT files in catalog (up to 5 min) ==="
deadline=$(( $(date +%s) + 300 ))
COUNT=0
while (( $(date +%s) < deadline )); do
    COUNT=$(catalog_count)
    info "Catalog: $COUNT / $TEST_FILE_COUNT"
    [[ "$COUNT" -ge "$TEST_FILE_COUNT" ]] && break
    sleep 10
done
[[ "$COUNT" -ge "$TEST_FILE_COUNT" ]] || fail "Only $COUNT/$TEST_FILE_COUNT files in catalog after timeout"
pass "All $TEST_FILE_COUNT files backed up (catalog=$COUNT)"

# ── Step 7: Save recovery kit and Tailscale state before disaster ──────────────
echo ""
echo "=== Step 7: Save recovery kit and Tailscale state ==="
anders "test -f $ANDERS_DATA_DIR/recovery_kit.enc" \
    || fail "recovery_kit.enc missing — wizard did not create it"
KIT_BYTES=$(anders "wc -c < $ANDERS_DATA_DIR/recovery_kit.enc")
info "Recovery kit: $KIT_BYTES bytes"

# Copy recovery kit: Anders → dev machine → Proxmox /tmp
anders "cat $ANDERS_DATA_DIR/recovery_kit.enc" | \
    ssh $SSH_OPTS "$PROXMOX" "cat > /tmp/recovery_kit_anders.enc"
# Copy Tailscale state: Anders → dev machine → Proxmox /tmp
anders "cat /var/lib/tailscale/tailscaled.state" | \
    ssh $SSH_OPTS "$PROXMOX" "cat > /tmp/tailscaled_anders.state"
TS_STATE_BYTES=$(prox "wc -c < /tmp/tailscaled_anders.state")
info "Tailscale state saved: $TS_STATE_BYTES bytes"
pass "Recovery kit and Tailscale state saved to Proxmox /tmp"

# ── Step 8: TRUE disaster — destroy VM 101 while preserving scsi1 ─────────────
echo ""
echo "=== Step 8: TRUE disaster — VM destroy ==="
# Stop gatekeeper cleanly so Tahoe flushes any in-flight writes before we count shares.
anders "systemctl stop $ANDERS_SVC 2>/dev/null || true"
sleep 3

SHARES_BEFORE=$(anders "find /mnt/storage/shares -type f 2>/dev/null | wc -l || echo 0")
info "/mnt/storage shares before disaster: $SHARES_BEFORE"
[[ "$SHARES_BEFORE" -gt 0 ]] \
    || fail "No shares on /mnt/storage — backup did not produce any fragments"

# Graceful VM shutdown (qm stop blocks until stopped).
info "Stopping VM $ANDERS_VMID..."
prox "qm stop $ANDERS_VMID 2>/dev/null || true"
VM_STATUS=""
deadline=$(( $(date +%s) + 90 ))
while (( $(date +%s) < deadline )); do
    VM_STATUS=$(prox "qm status $ANDERS_VMID 2>/dev/null" | awk '{print $2}')
    [[ "$VM_STATUS" == "stopped" ]] && break
    echo -n "."; sleep 3
done
echo ""
[[ "$VM_STATUS" == "stopped" ]] \
    || fail "VM $ANDERS_VMID did not stop within 90s (status=$VM_STATUS)"

# Remove scsi1 from the Proxmox VM config.
# qm destroy only destroys disks listed in the config file, so removing scsi1
# here guarantees vm-$ANDERS_VMID-disk-1 (the storage disk) is NOT destroyed.
prox "sed -i '/^scsi1:/d' /etc/pve/qemu-server/${ANDERS_VMID}.conf"
info "scsi1 removed from Proxmox config — storage disk will survive qm destroy"

# Destroy the VM (destroys scsi0 OS disk only).
prox "qm destroy $ANDERS_VMID --destroy-unreferenced-disks 0"
info "VM $ANDERS_VMID destroyed"
pass "TRUE disaster complete: VM $ANDERS_VMID destroyed, storage disk preserved"

# ── Step 9: Verify storage disk survived VM destruction ───────────────────────
echo ""
echo "=== Step 9: Verify storage disk survived ==="
prox "test -e /dev/pve/vm-${ANDERS_VMID}-disk-1" \
    || fail "LVM volume vm-${ANDERS_VMID}-disk-1 missing — check sed + destroy logic"
pass "Storage disk /dev/pve/vm-${ANDERS_VMID}-disk-1 intact after VM destruction"

# ── Step 10: Create fresh VM from Ubuntu template ─────────────────────────────
echo ""
echo "=== Step 10: Create fresh VM $ANDERS_VMID from template $TEMPLATE_VMID ==="

info "Cloning template $TEMPLATE_VMID → VM $ANDERS_VMID (full copy)..."
prox "qm clone $TEMPLATE_VMID $ANDERS_VMID --full --storage local-lvm --name gatekeeper-anders"
info "Clone complete"

# Reuse original MAC addresses so cloud-init regenerates the same netplan config.
prox "qm set $ANDERS_VMID \
    --net0 virtio=$ANDERS_NET0_MAC,bridge=vmbr99 \
    --net1 virtio=$ANDERS_NET1_MAC,bridge=vmbr10"

# Cloud-init: replace template DHCP with static addresses.
prox "qm set $ANDERS_VMID \
    --ipconfig0 ip=$ANDERS_LAN/24,gw=10.99.0.1 \
    --ipconfig1 ip=10.10.1.10/24 \
    --nameserver 1.1.1.1 \
    --searchdomain local"

# Expand OS disk to 20G (template base is 3.5G).
prox "qm resize $ANDERS_VMID scsi0 20G"

# Ensure a cloud-init drive exists (required for ipconfig* settings to reach the
# guest).  The template normally carries one, but add it defensively in case the
# clone did not inherit it.  The command is a no-op when ide2 is already present.
prox "qm config $ANDERS_VMID | grep -q cloudinit \
    || qm set $ANDERS_VMID --ide2 local-lvm:cloudinit"
# Regenerate the cloud-init ISO after setting ipconfig/nameserver above.
prox "qm cloudinit update $ANDERS_VMID 2>/dev/null || true"
info "Cloud-init ISO regenerated"

# Reattach the preserved storage disk.
prox "qm set $ANDERS_VMID --scsi1 local-lvm:vm-${ANDERS_VMID}-disk-1"
info "Storage disk vm-${ANDERS_VMID}-disk-1 reattached as scsi1"

# Verify the preserved disk was not overwritten by the clone step.
DISK1_SIZE=$(prox "lvs --noheadings -o lv_size pve/vm-${ANDERS_VMID}-disk-1 2>/dev/null | tr -d ' '")
info "vm-${ANDERS_VMID}-disk-1 size: $DISK1_SIZE (expected ~10G)"
echo "$DISK1_SIZE" | grep -q "10" \
    || fail "vm-${ANDERS_VMID}-disk-1 has unexpected size '$DISK1_SIZE' — clone may have overwritten storage disk"

prox "qm start $ANDERS_VMID"
info "Fresh VM $ANDERS_VMID started"
pass "Fresh VM created and started"

# ── Step 11: Fresh install via install/gatekeeper.sh ─────────────────────────
echo ""
echo "=== Step 11: Fresh install via install/gatekeeper.sh ==="
wait_ssh_anders 300

# Run the official install script from the dev machine.
# This clones from GitHub, creates /opt/backup-buddy/.venv, registers the
# systemd service (starts in setup/wizard mode — no gatekeeper.cfg yet),
# and installs Tailscale without running tailscale up.
info "Running install/gatekeeper.sh on fresh VM..."
ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "bash -s" < install/gatekeeper.sh \
    || fail "install/gatekeeper.sh failed"
info "Install script complete"

# Overlay current source on top of the GitHub clone so we test the working tree.
info "Deploying current gatekeeper source..."
tar -czf - gatekeeper/ | \
    ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" \
    "cd /opt/backup-buddy && tar -xzf - --no-same-owner && find gatekeeper -name '*.pyc' -delete"
# Force-reinstall so site-packages picks up the overlaid source.
# install/gatekeeper.sh uses `pip install .` (non-editable), so the tar overlay
# alone is not enough — pip must copy the updated modules into site-packages.
anders "cd /opt/backup-buddy && /opt/backup-buddy/.venv/bin/pip install --force-reinstall -q . 2>&1 | tail -3"
info "Source deployed and pip reinstalled"

# Mount the preserved storage disk by UUID (survives across OS reinstalls).
anders "mkdir -p /mnt/storage"
anders "grep -q '$STORAGE_UUID' /etc/fstab || \
    echo 'UUID=$STORAGE_UUID /mnt/storage ext4 defaults 0 2' >> /etc/fstab"
anders "mount /mnt/storage 2>/dev/null || mount -a 2>/dev/null || true"
SHARES_AFTER_MOUNT=$(anders "find /mnt/storage/shares -type f 2>/dev/null | wc -l || echo 0")
info "/mnt/storage shares after mount: $SHARES_AFTER_MOUNT (expected $SHARES_BEFORE)"
[[ "$SHARES_AFTER_MOUNT" -eq "$SHARES_BEFORE" ]] \
    || fail "Share count mismatch after mount (before=$SHARES_BEFORE after=$SHARES_AFTER_MOUNT)"
anders "chown -R backupbuddy:backupbuddy /mnt/storage 2>/dev/null || true"
pass "Storage disk mounted: $SHARES_AFTER_MOUNT shares confirmed"

# Restore Tailscale identity from the saved state file.
# install/gatekeeper.sh installs and auto-starts tailscaled with a new (unauthenticated)
# identity.  We must stop it before overwriting the state to prevent a race where
# tailscaled writes a fresh state over ours while we are copying.
info "Restoring Tailscale identity..."
anders "systemctl stop tailscaled && systemctl disable tailscaled 2>/dev/null || true"
# Transfer: Proxmox /tmp → dev machine → Anders via ProxyJump
prox "cat /tmp/tailscaled_anders.state" | \
    ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" \
    "cat > /var/lib/tailscale/tailscaled.state && chmod 600 /var/lib/tailscale/tailscaled.state"
anders "systemctl enable tailscaled && systemctl start tailscaled"

# Wait for Tailscale to reconnect using the restored identity.
TS_STATUS=""
deadline=$(( $(date +%s) + 90 ))
while (( $(date +%s) < deadline )); do
    TS_STATUS=$(anders "tailscale status --json 2>/dev/null | \
        python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"BackendState\",\"\"))'" \
        2>/dev/null || echo "")
    [[ "$TS_STATUS" == "Running" ]] && break
    echo -n "."; sleep 5
done
echo ""
[[ "$TS_STATUS" == "Running" ]] \
    || fail "Tailscale did not reconnect with restored identity (status=$TS_STATUS)"

ANDERS_TS_NEW=$(anders "tailscale ip -4 2>/dev/null | head -1")
[[ "$ANDERS_TS_NEW" == "$ANDERS_TS" ]] \
    || fail "Tailscale IP changed after state restore: was $ANDERS_TS, now $ANDERS_TS_NEW — stored identity mismatch"
info "Tailscale IP confirmed: $ANDERS_TS_NEW"

# Restart the gatekeeper service so it loads the overlaid code, sees the
# connected Tailscale interface, and finds /mnt/storage already mounted.
anders "systemctl reset-failed $ANDERS_SVC 2>/dev/null || true"
anders "systemctl restart $ANDERS_SVC"
sleep 5
pass "Fresh install complete — code deployed, storage mounted ($SHARES_AFTER_MOUNT shares), Tailscale connected"

# ── Step 12: Wait for wizard on fresh install ─────────────────────────────────
echo ""
echo "=== Step 12: Wait for wizard on fresh install ==="
wait_http_prox "$ANDERS_WIZARD_URL/onboarding/step/1" "wizard on fresh install" 120
pass "Wizard accessible on fresh install"

# ── Step 13: Drive second wizard (same /mnt/storage, same passphrase) ─────────
echo ""
echo "=== Step 13: Drive second wizard (role=new, same /mnt/storage) ==="
drive_wizard_steps_1_4 "$ANDERS_WIZARD_URL"
info "Triggering second cascade (up to 180s)..."
prox curl -s -o /tmp/s3_cascade2.txt --max-time 180 -X POST \
    --data-urlencode "passphrase=$PASSPHRASE" \
    --data-urlencode "passphrase_confirm=$PASSPHRASE" \
    "$ANDERS_WIZARD_URL/onboarding/step/5" || true
info "Second cascade HTTP call done"
anders "test -f $ANDERS_CFG" \
    || { BODY=$(prox cat /tmp/s3_cascade2.txt 2>/dev/null || echo "(no body)"); fail "Second cascade failed — no gatekeeper.cfg: $BODY"; }
pass "Second wizard cascade complete"

# ── Step 14: Restart into normal mode, verify empty catalog ───────────────────
echo ""
echo "=== Step 14: Restart into normal mode ==="
anders "systemctl reset-failed $ANDERS_SVC 2>/dev/null || true"
anders "systemctl restart $ANDERS_SVC"
sleep 5
wait_http_anders "$ANDERS_TS_URL/api/status" "Anders normal mode (post-disaster)" 120
STATUS=$(anders "curl -sf '$ANDERS_TS_URL/api/status'")
echo "$STATUS" | grep -q '"status":"ok"' || fail "Anders not healthy after fresh install"
pass "Anders in normal mode after fresh install"

EMPTY_COUNT=$(catalog_count)
info "Catalog after fresh install (must be 0): $EMPTY_COUNT"
[[ "$EMPTY_COUNT" -eq 0 ]] \
    || fail "Catalog is not empty before emergency restore (count=$EMPTY_COUNT)"
pass "Catalog is empty — ready for emergency restore"

# ── Step 15: Emergency catalog reconstruction ──────────────────────────────────
echo ""
echo "=== Step 15: Emergency catalog reconstruction ==="

# Transfer recovery kit from Proxmox to the fresh Anders VM.
prox "cat /tmp/recovery_kit_anders.enc" | \
    ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" \
    "cat > /tmp/old_recovery_kit.enc"

KIT_B64=$(anders "base64 -w0 /tmp/old_recovery_kit.enc")
[[ -n "$KIT_B64" ]] || fail "Could not base64-encode recovery kit on Anders"

printf '{"recovery_kit_b64": "%s", "passphrase": "%s"}' "$KIT_B64" "$PASSPHRASE" | \
    ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "cat > /tmp/s3_emergency_req.json"

EMERGENCY_RESP=$(anders "curl -sf -X POST '$ANDERS_TS_URL/api/restore/emergency' \
    -H 'Content-Type: application/json' --data '@/tmp/s3_emergency_req.json'")
info "Emergency restore response: $EMERGENCY_RESP"
JOB_ID=$(echo "$EMERGENCY_RESP" | \
    python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null)
[[ -n "$JOB_ID" ]] || fail "No job_id in emergency restore response: $EMERGENCY_RESP"
info "Job ID: $JOB_ID"

JOB_STATUS=""
JOB_RESP=""
deadline=$(( $(date +%s) + 120 ))
while (( $(date +%s) < deadline )); do
    JOB_RESP=$(anders "curl -sf '$ANDERS_TS_URL/api/restore/jobs/$JOB_ID'")
    JOB_STATUS=$(echo "$JOB_RESP" | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    info "Reconstruction status: $JOB_STATUS"
    [[ "$JOB_STATUS" == "done" ]] && break
    if [[ "$JOB_STATUS" == "failed" ]]; then
        JOB_ERR=$(echo "$JOB_RESP" | \
            python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))" 2>/dev/null)
        fail "Emergency restore failed: $JOB_ERR"
    fi
    sleep 5
done
[[ "$JOB_STATUS" == "done" ]] || fail "Emergency restore timed out"

FILES_RECONSTRUCTED=$(echo "$JOB_RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
r = d.get('results', [{}])
print(r[0].get('files_reconstructed', d.get('progress', 0)) if r else d.get('progress', 0))
" 2>/dev/null || echo "?")
info "Files reconstructed: $FILES_RECONSTRUCTED"
pass "Emergency restore complete"

# ── Step 16: Verify catalog count ─────────────────────────────────────────────
echo ""
echo "=== Step 16: Verify catalog count >= $TEST_FILE_COUNT ==="
CATALOG_COUNT=$(catalog_count)
info "Catalog count after reconstruction: $CATALOG_COUNT"
[[ "$CATALOG_COUNT" -ge "$TEST_FILE_COUNT" ]] \
    || fail "Only $CATALOG_COUNT files in catalog (expected >= $TEST_FILE_COUNT)"
pass "Catalog has $CATALOG_COUNT files (>= $TEST_FILE_COUNT)"

# ── Step 17: Restore one file and verify SHA-256 ──────────────────────────────
echo ""
echo "=== Step 17: Restore scenario3_file_01.bin and verify SHA-256 ==="
TEST_FILE_PATH="$BACKUP_PATH/scenario3_file_01.bin"
RESTORE_PATH="$RESTORE_DIR/scenario3_file_01.bin"
anders "mkdir -p $RESTORE_DIR && chmod 777 $RESTORE_DIR"

printf '{"original_path": "%s", "agent": "anders-pc", "dest_path": "%s"}' \
    "$TEST_FILE_PATH" "$RESTORE_PATH" | \
    ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "cat > /tmp/s3_restore_req.json"
RESTORE_RESP=$(anders "curl -sf -X POST '$ANDERS_TS_URL/api/restore/start/file' \
    -H 'Content-Type: application/json' --data '@/tmp/s3_restore_req.json'")
info "Restore start response: $RESTORE_RESP"
RESTORE_JOB=$(echo "$RESTORE_RESP" | \
    python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null)
[[ -n "$RESTORE_JOB" ]] || fail "No job_id in restore response: $RESTORE_RESP"

RJOB_STATUS=""
RRESP=""
deadline=$(( $(date +%s) + 120 ))
while (( $(date +%s) < deadline )); do
    RRESP=$(anders "curl -sf '$ANDERS_TS_URL/api/restore/jobs/$RESTORE_JOB'")
    RJOB_STATUS=$(echo "$RRESP" | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    [[ "$RJOB_STATUS" == "done" || "$RJOB_STATUS" == "failed" ]] && break
    sleep 3
done
if [[ "$RJOB_STATUS" == "failed" ]]; then
    RERR=$(echo "$RRESP" | \
        python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))" 2>/dev/null)
    fail "File restore failed: $RERR"
fi
[[ "$RJOB_STATUS" == "done" ]] || fail "File restore timed out"

RESTORED_SHA256=$(echo "$RRESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
r = d.get('results', [{}])
print(r[0].get('sha256', '') if r else '')
" 2>/dev/null)
info "Restored SHA-256: $RESTORED_SHA256"
info "Original SHA-256: $ORIGINAL_SHA256"
[[ -n "$RESTORED_SHA256" ]] || fail "No sha256 in restore job result"
[[ "$RESTORED_SHA256" == "$ORIGINAL_SHA256" ]] \
    || fail "SHA-256 mismatch: got $RESTORED_SHA256, expected $ORIGINAL_SHA256"
pass "SHA-256 verified: $RESTORED_SHA256"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  1.16.12 PASSED"
echo "=============================================="
