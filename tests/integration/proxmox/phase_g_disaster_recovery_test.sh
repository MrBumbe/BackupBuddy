#!/usr/bin/env bash
# Integration test 1.17.8: Phase G — Full disaster recovery
#
# Prerequisites:
#   - Proxmox host reachable at 192.168.1.60
#   - phase-a snapshots exist on VM 101 (anders) and CT 301 (agent-anders-pc)
#   - Template VM 9000 exists on Proxmox (gatekeeper template with Tailscale)
#   - SSH key auth configured from dev machine via Proxmox jump host
#
# Run from repo root on the dev machine:
#   bash tests/integration/proxmox/phase_g_disaster_recovery_test.sh

set -euo pipefail

PROXMOX="root@192.168.1.60"
ANDERS_LAN="10.99.0.11"
ANDERS_VMID=101
AGENT_CTID=301
TEMPLATE_VMID=9000
PASSPHRASE="TestPassphrase2026!"
ANDERS_SVC="backup-buddy-gatekeeper"
AGENT_SVC="backup-buddy-agent"
GK_CFG="/etc/backup-buddy/gatekeeper.cfg"
ANDERS_DATA_DIR="/var/lib/backup-buddy"
CATALOG_DB="${ANDERS_DATA_DIR}/catalog.db"

SSH_OPTS="-q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 -o ServerAliveInterval=15"

anders()  { ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "$@"; }
prox()    { ssh $SSH_OPTS "$PROXMOX" "$@"; }

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }
info() { echo "  → $*"; }

# ── Wait helpers ───────────────────────────────────────────────────────────────

wait_ssh_anders() {
    local deadline=$(( $(date +%s) + 150 ))
    echo -n "  Waiting for SSH on anders..."
    while (( $(date +%s) < deadline )); do
        if anders "true" 2>/dev/null; then echo " OK"; return 0; fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

# Checks from Proxmox host (LAN-accessible, used during wizard mode).
wait_wizard_prox() {
    local base_url="$1" timeout="${2:-120}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "  Waiting for wizard at $base_url..."
    while (( $(date +%s) < deadline )); do
        if prox "curl -sf --max-time 5 '${base_url}/' -o /dev/null" 2>/dev/null; then
            echo " OK"; return 0
        fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

# Checks from anders itself (Tailscale-reachable, used in normal mode).
wait_gatekeeper_ts() {
    local base_url="$1" timeout="${2:-120}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "  Waiting for gatekeeper at $base_url..."
    while (( $(date +%s) < deadline )); do
        if anders "curl -sf --max-time 5 '${base_url}/api/status' -o /dev/null" 2>/dev/null; then
            echo " OK"; return 0
        fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

# POST to a wizard step URL via prox; expect HTTP 303.
step_post() {
    local step_url="$1"; shift
    local code
    code=$(prox "curl -sw '%{http_code}' -o /dev/null -X POST '$step_url' $*" | tail -c 3)
    info "$step_url → HTTP $code"
    [[ "$code" == "303" ]] || fail "Expected 303 from $step_url, got $code"
}

# Poll a restore/reconstruction job on anders until it is no longer running.
# Sets globals POLL_STATUS and POLL_RESP.
POLL_STATUS=""
POLL_RESP=""
poll_job() {
    local base_url="$1" job_id="$2" timeout="${3:-300}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "  Polling job $job_id..."
    while (( $(date +%s) < deadline )); do
        POLL_RESP=$(anders "curl -sf --max-time 10 '${base_url}/api/restore/jobs/${job_id}'" 2>/dev/null) \
            || { echo -n "?"; sleep 5; continue; }
        POLL_STATUS=$(echo "$POLL_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null) \
            || { echo -n "?"; sleep 5; continue; }
        case "$POLL_STATUS" in
            done)
                echo " done"
                return 0
                ;;
            failed)
                local err
                err=$(echo "$POLL_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error') or '')" 2>/dev/null || echo "?")
                echo " FAILED — $err"
                return 1
                ;;
        esac
        echo -n "."; sleep 5
    done
    POLL_STATUS="timeout"
    echo " TIMEOUT"
    return 1
}

# ── Main test ──────────────────────────────────────────────────────────────────

echo "=============================================="
echo "  1.17.8 — Phase G: Full disaster recovery"
echo "=============================================="
echo ""

# ── Step 1: Rollback anders (101) and agent CT (301) to phase-a ───────────────
echo "=== Step 1: Rollback to phase-a ==="

info "Stopping CT $AGENT_CTID..."
prox "pct stop $AGENT_CTID 2>/dev/null || true; sleep 2"

info "Stopping VM $ANDERS_VMID..."
prox "qm stop $ANDERS_VMID --skiplock 1 2>/dev/null || true; sleep 3"

info "Rolling back anders (VM $ANDERS_VMID) to phase-a..."
prox "qm rollback $ANDERS_VMID phase-a && qm start $ANDERS_VMID"

info "Rolling back agent-anders-pc (CT $AGENT_CTID) to phase-a..."
prox "pct rollback $AGENT_CTID phase-a && pct start $AGENT_CTID"

wait_ssh_anders || fail "Anders did not come up after rollback within 150 s"

# Sync current gatekeeper code so the test exercises the latest version.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
info "Syncing gatekeeper code to anders..."
tar -czf - -C "$REPO_ROOT" gatekeeper | ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" \
    "tar -xzf - -C /opt/backup-buddy/"

# Ensure Restart=always is in the unit file — phase-a snapshot may predate this setting.
info "Ensuring Restart=always in systemd unit..."
if ! anders "grep -q '^Restart=always' /etc/systemd/system/$ANDERS_SVC.service 2>/dev/null"; then
    info "Writing updated unit file with Restart=always..."
    {
        echo '[Unit]'
        echo 'Description=BackupBuddy Gatekeeper'
        echo 'After=network.target'
        echo ''
        echo '[Service]'
        echo 'Type=simple'
        echo 'User=backupbuddy'
        echo 'Group=backupbuddy'
        echo 'WorkingDirectory=/opt/backup-buddy'
        echo 'ExecStart=/opt/backup-buddy/.venv/bin/python -m gatekeeper.main --data-dir /var/lib/backup-buddy --config /etc/backup-buddy/gatekeeper.cfg'
        echo 'Restart=always'
        echo 'RestartSec=10'
        echo 'StandardOutput=journal'
        echo 'StandardError=journal'
        echo "SyslogIdentifier=$ANDERS_SVC"
        echo 'NoNewPrivileges=yes'
        echo ''
        echo '[Install]'
        echo 'WantedBy=multi-user.target'
    } | ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" \
        "tee /etc/systemd/system/$ANDERS_SVC.service > /dev/null && systemctl daemon-reload"
fi
anders "systemctl restart $ANDERS_SVC 2>/dev/null || true"

pass "Rollback complete"

# ── Step 2: Format anders storage disk, capture UUID ──────────────────────────
echo ""
echo "=== Step 2: Prepare anders storage disk ==="

info "Formatting /dev/sdb and mounting /mnt/storage on anders..."
anders "mkfs.ext4 -F /dev/sdb"
anders "mkdir -p /mnt/storage && mount /dev/sdb /mnt/storage"
anders "chown -R backupbuddy:backupbuddy /mnt/storage"

STORAGE_UUID=$(anders "blkid -s UUID -o value /dev/sdb 2>/dev/null | tr -d '[:space:]'")
[[ -n "$STORAGE_UUID" ]] || fail "Could not determine UUID of /dev/sdb"
info "Storage disk UUID: $STORAGE_UUID"

pass "Storage disk formatted and mounted at /mnt/storage (UUID=$STORAGE_UUID)"

# ── Step 3: Run first wizard (new cluster, balanced profile) ──────────────────
echo ""
echo "=== Step 3: Run first wizard ==="

BASE_LAN="http://$ANDERS_LAN:8080"
wait_wizard_prox "$BASE_LAN" 120 || fail "Anders wizard not reachable within 120 s"

step_post "$BASE_LAN/onboarding/step/1" \
    "-d 'role=new'"

step_post "$BASE_LAN/onboarding/step/2" \
    "-d 'node_name=anders'" \
    "-d 'node_display_name=Anders'"

step_post "$BASE_LAN/onboarding/step/3" \
    "--data-urlencode 'storage_paths=/mnt/storage'" \
    "-d 'storage_quota_gb=50'"

step_post "$BASE_LAN/onboarding/step/4" \
    "-d 'profile=balanced'"

# Step 5 triggers the finish cascade (Tahoe bootstrap, recovery_kit.enc, gatekeeper.cfg).
# Allow up to 180 s; the response is a redirect so we ignore curl exit code.
info "Triggering finish cascade via step/5 (up to 180 s)..."
prox "curl -s -o /tmp/cascade_body_g1.txt --max-time 180 \
    -X POST '$BASE_LAN/onboarding/step/5' \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode 'passphrase=$PASSPHRASE' \
    --data-urlencode 'passphrase_confirm=$PASSPHRASE'" || true
info "Cascade HTTP call done"

sleep 3
anders "test -f '$GK_CFG'" || {
    BODY=$(prox "cat /tmp/cascade_body_g1.txt 2>/dev/null || echo '(no body)'")
    fail "Cascade failed — gatekeeper.cfg not created on anders: $BODY"
}
pass "First wizard cascade complete"

# ── Step 4: Download recovery_kit.enc before confirming ───────────────────────
echo ""
echo "=== Step 4: Download recovery_kit.enc ==="

# Download to anders /tmp, then to the dev machine and the Proxmox host.
anders "curl -sf --max-time 15 '$BASE_LAN/api/onboarding/download-key' -o /tmp/recovery_kit.enc"
anders "test -s /tmp/recovery_kit.enc" || fail "recovery_kit.enc is empty or missing on anders"

KIT_SIZE=$(anders "wc -c < /tmp/recovery_kit.enc 2>/dev/null | tr -d '[:space:]'")
info "recovery_kit.enc: $KIT_SIZE bytes"

# Copy to dev machine (will be base64-encoded for emergency restore after rebuild).
ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "cat /tmp/recovery_kit.enc" \
    > /tmp/recovery_kit_g.enc
[[ -s /tmp/recovery_kit_g.enc ]] || fail "Failed to copy recovery_kit.enc to dev machine"

# Also save to Proxmox host as a backup.
anders "cat /tmp/recovery_kit.enc" | prox "cat - > /tmp/anders_recovery_kit_g.enc"
info "recovery_kit.enc saved to dev machine (/tmp/recovery_kit_g.enc)"

# Verify gatekeeper still responds before attempting confirm-key.
HEALTH_CODE=$(anders "curl -s -o /dev/null -w '%{http_code}' --max-time 10 '$BASE_LAN/'" \
    2>/dev/null) || HEALTH_CODE="000"
info "Gatekeeper health check (pre confirm-key): HTTP $HEALTH_CODE"

# Confirm receipt. Capture status code so failures are diagnosable.
CONFIRM_CODE=$(anders "curl -s -o /tmp/confirm_resp.txt -w '%{http_code}' \
    --max-time 20 -X POST '$BASE_LAN/onboarding/confirm-key'" 2>/dev/null) \
    || CONFIRM_CODE="000"
info "confirm-key HTTP status: $CONFIRM_CODE"
if [[ "$CONFIRM_CODE" != "303" && "$CONFIRM_CODE" != "200" && "$CONFIRM_CODE" != "302" ]]; then
    CONFIRM_BODY=$(anders "cat /tmp/confirm_resp.txt 2>/dev/null" || echo "(no body)")
    GK_LOG=$(anders "journalctl -u $ANDERS_SVC --since '-3min' --no-pager -n 30 2>/dev/null" \
        || echo "(no log)")
    info "confirm-key body: ${CONFIRM_BODY:0:300}"
    info "Gatekeeper journal (last 3 min):"
    while IFS= read -r line; do info "  $line"; done <<< "$GK_LOG"
    fail "confirm-key failed: HTTP $CONFIRM_CODE (expected 303)"
fi

RESTART_CODE=$(anders "curl -s -o /dev/null -w '%{http_code}' \
    --max-time 10 -X POST '$BASE_LAN/api/onboarding/restart'" 2>/dev/null) \
    || RESTART_CODE="000"
info "restart call HTTP status: $RESTART_CODE (expected 200)"
sleep 8

pass "recovery_kit.enc saved and wizard confirmed"

# ── Step 5: Wait for normal mode, switch to test profile ──────────────────────
echo ""
echo "=== Step 5: Restart in normal mode and switch to test profile ==="

ANDERS_TS=$(anders "tailscale ip -4 2>/dev/null | head -1")
[[ -n "$ANDERS_TS" ]] || fail "Could not resolve Anders Tailscale IP — is Tailscale running?"
BASE_TS="http://$ANDERS_TS:8080"
info "Anders Tailscale IP: $ANDERS_TS  →  $BASE_TS"

wait_gatekeeper_ts "$BASE_TS" 180 || {
    SVC_STATUS=$(anders "systemctl status $ANDERS_SVC --no-pager -n 40 2>/dev/null" || echo "(no status)")
    PORT_CHECK=$(anders "ss -tlnp 2>/dev/null | grep ':8080'" || echo "(port 8080 not listening)")
    GK_LOG=$(anders "journalctl -u $ANDERS_SVC --since '-4min' --no-pager -n 40 2>/dev/null" \
        || echo "(no log)")
    info "Service status:"
    while IFS= read -r line; do info "  $line"; done <<< "$SVC_STATUS"
    info "Port 8080 on anders: $PORT_CHECK"
    info "Gatekeeper journal (last 4 min):"
    while IFS= read -r line; do info "  $line"; done <<< "$GK_LOG"
    fail "Anders gatekeeper did not start in normal mode within 180 s"
}

# Profile=balanced (k=3, n=5, happy=5) cannot be satisfied on a single node.
# Switch to profile=test (k=1, n=2, happy=1) so uploads can succeed.
info "Switching fragmentation profile to test (k=1, n=2, happy=1)..."
anders "sed -i 's/^profile.*/profile = test/' '$GK_CFG'"
anders "grep 'profile' '$GK_CFG'"
# Remove introducer dir so it reinitialises with the correct LAN IP binding.
anders "rm -rf '${ANDERS_DATA_DIR}/tahoe/introducer/'"
anders "nohup bash -c 'systemctl restart $ANDERS_SVC' >/dev/null 2>&1 &"
sleep 8
wait_gatekeeper_ts "$BASE_TS" 90 || fail "Anders gatekeeper did not recover after profile switch"
pass "Profile switched to test — gatekeeper up"

# ── Step 6: Configure agent CT 301, start backup, wait for ≥10 files ──────────
echo ""
echo "=== Step 6: Configure agent-anders-pc (CT $AGENT_CTID) and back up ≥10 files ==="

# Read agent API token from gatekeeper.cfg.
ANDERS_AGENT_TOKEN=$(anders "python3 -c \"
import configparser
c = configparser.ConfigParser(allow_no_value=True, delimiters=('=',))
c.read('$GK_CFG')
print(c.get('agent_api', 'token', fallback=''))
\"" 2>/dev/null | tr -d '[:space:]')
[[ -n "$ANDERS_AGENT_TOKEN" ]] || fail "Could not read agent_api token from gatekeeper.cfg"
info "Agent API token: (read, not shown)"

# Ensure test files exist on CT 301.
info "Creating test files on CT $AGENT_CTID..."
prox "pct exec $AGENT_CTID -- bash -c 'mkdir -p /srv/testbackup && for i in \$(seq -w 1 15); do dd if=/dev/urandom of=/srv/testbackup/testfile_\$i.bin bs=512K count=1 2>/dev/null; done'"

# Write backup.cfg on CT 301.
info "Writing backup.cfg on CT $AGENT_CTID..."
AGENT_CFG_CONTENT="[schedule]
full_scan = 24h
stability_minutes = 1

[backup]
/srv/testbackup

[gatekeeper]
url = http://${ANDERS_LAN}:8081
token = ${ANDERS_AGENT_TOKEN}
name = agent-anders-pc
"
printf '%s' "$AGENT_CFG_CONTENT" \
    | prox "cat - | pct exec $AGENT_CTID -- tee /etc/backup-buddy/backup.cfg > /dev/null"
prox "pct exec $AGENT_CTID -- chown backupbuddy:backupbuddy /etc/backup-buddy/backup.cfg"
prox "pct exec $AGENT_CTID -- chmod 0600 /etc/backup-buddy/backup.cfg"

# Start agent service.
info "Starting agent service on CT $AGENT_CTID..."
prox "pct exec $AGENT_CTID -- systemctl restart $AGENT_SVC"
sleep 5
prox "pct exec $AGENT_CTID -- systemctl is-active $AGENT_SVC" \
    || fail "Agent service failed to start on CT $AGENT_CTID"

# Poll catalog until ≥10 backed-up files.
info "Polling anders catalog.db for ≥10 backed-up files (up to 10 min)..."
CATALOG_DEADLINE=$(( $(date +%s) + 600 ))
FILE_COUNT=0
while (( $(date +%s) < CATALOG_DEADLINE )); do
    FILE_COUNT=$(anders "python3 -c \"
import sqlite3
try:
    c = sqlite3.connect('${CATALOG_DB}')
    r = c.execute('SELECT COUNT(*) FROM files WHERE backed_up_at IS NOT NULL').fetchone()
    print(r[0] if r else 0)
    c.close()
except Exception:
    print(0)
\"" 2>/dev/null | tr -d '[:space:]') || FILE_COUNT=0
    info "Catalog file count: $FILE_COUNT"
    (( FILE_COUNT >= 10 )) && break
    sleep 20
done
(( FILE_COUNT >= 10 )) \
    || fail "Expected ≥10 backed-up files in anders catalog, found $FILE_COUNT after 10 min"
pass "Anders catalog has $FILE_COUNT backed-up files"

# ── Step 7: Record SHA-256 of testfile_01.bin and share count ─────────────────
echo ""
echo "=== Step 7: Record SHA-256 and share count ==="

SHA256_REF=$(prox "pct exec $AGENT_CTID -- sha256sum /srv/testbackup/testfile_01.bin 2>/dev/null | awk '{print \$1}'" \
    2>/dev/null | tr -d '[:space:]')
[[ -n "$SHA256_REF" ]] || fail "Could not compute SHA-256 of testfile_01.bin on CT $AGENT_CTID"
info "testfile_01.bin SHA-256 (pre-destroy): $SHA256_REF"

SHARE_COUNT=$(anders "find /mnt/storage/shares -type f 2>/dev/null | wc -l" 2>/dev/null \
    | tr -d '[:space:]')
info "Share files on anders storage: $SHARE_COUNT"
(( SHARE_COUNT >= 1 )) || fail "No share files found in /mnt/storage/shares — backup may not have completed"

# Verify catalog has enough entries before destroying the VM.
# original_path is AES-256-GCM encrypted, so plain LIKE queries don't work.
# FILE_COUNT >= 10 was already verified in step 6 — confirm it's still ≥ 1.
info "Confirming catalog still has backed-up entries on anders..."
CATALOG_TESTFILE=$(anders "python3 -c \"
import sqlite3
try:
    c = sqlite3.connect('${CATALOG_DB}')
    r = c.execute('SELECT COUNT(*) FROM files WHERE backed_up_at IS NOT NULL').fetchone()
    print(r[0] if r else 0)
    c.close()
except Exception:
    print(0)
\"" 2>/dev/null | tr -d '[:space:]') || CATALOG_TESTFILE=0
(( CATALOG_TESTFILE >= 10 )) \
    || fail "Anders catalog has only $CATALOG_TESTFILE backed-up entries — cannot proceed with VM destroy"
info "Catalog has $CATALOG_TESTFILE backed-up entries ✓"

pass "SHA-256 recorded: $SHA256_REF"
pass "Share count: $SHARE_COUNT"

# ── Step 8: Save artifacts before VM destroy ──────────────────────────────────
echo ""
echo "=== Step 8: Save MAC, scsi1 volume reference, and Tailscale state ==="

# Read scsi1 volume reference from VM config before removing it.
SCSI1_LINE=$(prox "grep '^scsi1:' /etc/pve/qemu-server/$ANDERS_VMID.conf 2>/dev/null" | tr -d '[:space:]')
[[ -n "$SCSI1_LINE" ]] || fail "Could not find scsi1 line in VM $ANDERS_VMID config"
# Format: "scsi1:local-lvm:vm-101-disk-1,size=50G"  →  "local-lvm:vm-101-disk-1"
SCSI1_VOL=$(echo "$SCSI1_LINE" | sed 's/^scsi1://; s/,.*//')
[[ -n "$SCSI1_VOL" ]] || fail "Could not extract scsi1 volume reference from: $SCSI1_LINE"
info "scsi1 volume reference: $SCSI1_VOL"

# Read original MAC address.
NET0_LINE=$(prox "grep '^net0:' /etc/pve/qemu-server/$ANDERS_VMID.conf 2>/dev/null")
ANDERS_MAC=$(echo "$NET0_LINE" | grep -oP '(?<=virtio=)[0-9A-Fa-f:]{17}' | head -1)
[[ -n "$ANDERS_MAC" ]] || fail "Could not read MAC address from VM $ANDERS_VMID net0 config"
info "Original MAC: $ANDERS_MAC"

# Save Tailscale state to Proxmox host so the new VM keeps the same identity.
info "Saving Tailscale state from anders to Proxmox host..."
anders "tar -czf - -C /var/lib tailscale 2>/dev/null" \
    | prox "cat - > /tmp/anders_tailscale_state.tar.gz"
prox "test -s /tmp/anders_tailscale_state.tar.gz" \
    || fail "Tailscale state archive is empty — Tailscale may not be installed"
info "Tailscale state saved (/tmp/anders_tailscale_state.tar.gz)"

pass "All pre-destroy artifacts saved"

# ── Step 9: Destroy VM 101 ────────────────────────────────────────────────────
echo ""
echo "=== Step 9: Destroy VM $ANDERS_VMID ==="

info "Stopping anders VM $ANDERS_VMID..."
prox "qm stop $ANDERS_VMID --skiplock 1 2>/dev/null || true"
sleep 5

# Detach storage disk from VM config so it is NOT destroyed with the VM.
info "Detaching scsi1 ($SCSI1_VOL) from VM config..."
prox "qm set $ANDERS_VMID --delete scsi1"
sleep 2

# Destroy VM; --destroy-unreferenced-disks 0 prevents any stray volumes from being wiped.
info "Destroying VM $ANDERS_VMID..."
prox "qm destroy $ANDERS_VMID --destroy-unreferenced-disks 0"

pass "VM $ANDERS_VMID destroyed"

# ── Step 10: Verify storage disk volume was preserved ─────────────────────────
echo ""
echo "=== Step 10: Verify storage disk preserved ==="

LVS_OUT=$(prox "lvs 2>/dev/null | grep vm-101" || true)
info "LVM volumes matching vm-101: ${LVS_OUT:-none}"
[[ -n "$LVS_OUT" ]] || fail "Storage disk volume not found after VM destroy — data may be lost"
pass "Storage disk LVM volume confirmed present"

# ── Step 11: Clone template 9000 → new VM 101 ─────────────────────────────────
echo ""
echo "=== Step 11: Clone template $TEMPLATE_VMID → VM $ANDERS_VMID ==="

info "Cloning template $TEMPLATE_VMID to VM $ANDERS_VMID..."
prox "qm clone $TEMPLATE_VMID $ANDERS_VMID --name gatekeeper-anders --full --storage local-lvm"

info "Setting original MAC address: $ANDERS_MAC..."
prox "qm set $ANDERS_VMID --net0 'virtio=${ANDERS_MAC},bridge=vmbr0'"

info "Setting static cloud-init IP: $ANDERS_LAN..."
prox "qm set $ANDERS_VMID --ipconfig0 'ip=${ANDERS_LAN}/24,gw=10.99.0.1'"

info "Resizing OS disk to 20G (no-op if already at target size)..."
prox "qm resize $ANDERS_VMID scsi0 20G 2>/dev/null || true"

info "Reattaching storage disk ($SCSI1_VOL)..."
prox "qm set $ANDERS_VMID --scsi1 '$SCSI1_VOL'"

info "Regenerating cloud-init ISO..."
prox "qm cloudinit update $ANDERS_VMID 2>/dev/null || true"

info "Starting new VM $ANDERS_VMID..."
prox "qm start $ANDERS_VMID"

wait_ssh_anders || fail "New anders VM did not accept SSH within 150 s"
pass "New VM $ANDERS_VMID running and reachable via SSH"

# ── Step 12: Install BackupBuddy on new VM ────────────────────────────────────
echo ""
echo "=== Step 12: Install BackupBuddy on fresh anders ==="

info "Running install script from GitHub..."
anders "curl -fsSL https://raw.githubusercontent.com/MrBumbe/BackupBuddy/master/install/gatekeeper.sh | bash" \
    || fail "Install script failed"

# Overlay the local dev code on top of the GitHub install.
info "Overlaying local gatekeeper code on top of install..."
tar -czf - -C "$REPO_ROOT" gatekeeper | ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" \
    "tar -xzf - -C /opt/backup-buddy/"

# Ensure Restart=always is in the unit file in case GitHub master lags behind local.
info "Ensuring Restart=always in systemd unit..."
if ! anders "grep -q '^Restart=always' /etc/systemd/system/$ANDERS_SVC.service 2>/dev/null"; then
    info "Writing updated unit file with Restart=always..."
    {
        echo '[Unit]'
        echo 'Description=BackupBuddy Gatekeeper'
        echo 'After=network.target'
        echo ''
        echo '[Service]'
        echo 'Type=simple'
        echo 'User=backupbuddy'
        echo 'Group=backupbuddy'
        echo 'WorkingDirectory=/opt/backup-buddy'
        echo 'ExecStart=/opt/backup-buddy/.venv/bin/python -m gatekeeper.main --data-dir /var/lib/backup-buddy --config /etc/backup-buddy/gatekeeper.cfg'
        echo 'Restart=always'
        echo 'RestartSec=10'
        echo 'StandardOutput=journal'
        echo 'StandardError=journal'
        echo "SyslogIdentifier=$ANDERS_SVC"
        echo 'NoNewPrivileges=yes'
        echo ''
        echo '[Install]'
        echo 'WantedBy=multi-user.target'
    } | ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" \
        "tee /etc/systemd/system/$ANDERS_SVC.service > /dev/null && systemctl daemon-reload"
fi
anders "systemctl restart $ANDERS_SVC 2>/dev/null || true"

pass "BackupBuddy installed on fresh anders"

# ── Step 13: Mount old storage disk by UUID ───────────────────────────────────
echo ""
echo "=== Step 13: Mount preserved storage disk (UUID=$STORAGE_UUID) ==="

anders "mkdir -p /mnt/storage"
anders "mount UUID='$STORAGE_UUID' /mnt/storage" \
    || fail "Could not mount storage disk by UUID — disk may not be attached or UUID changed"
anders "chown -R backupbuddy:backupbuddy /mnt/storage"

SHARE_COUNT_NEW=$(anders "find /mnt/storage/shares -type f 2>/dev/null | wc -l" 2>/dev/null \
    | tr -d '[:space:]')
info "Share files on preserved disk: $SHARE_COUNT_NEW"
[[ "$SHARE_COUNT_NEW" == "$SHARE_COUNT" ]] \
    || info "WARNING: share count changed ($SHARE_COUNT → $SHARE_COUNT_NEW) — expected no change"

pass "Storage disk mounted — $SHARE_COUNT_NEW share files present"

# ── Step 14: Restore Tailscale state ──────────────────────────────────────────
echo ""
echo "=== Step 14: Restore Tailscale state ==="

info "Restoring Tailscale state to new anders VM..."
anders "systemctl stop tailscaled 2>/dev/null || true"
sleep 2
prox "cat /tmp/anders_tailscale_state.tar.gz" \
    | ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "tar -xzf - -C /var/lib 2>/dev/null"
anders "systemctl start tailscaled 2>/dev/null || true"
sleep 5

ANDERS_TS_NEW=$(anders "tailscale ip -4 2>/dev/null | head -1")
if [[ -n "$ANDERS_TS_NEW" ]]; then
    info "Tailscale IP after state restore: $ANDERS_TS_NEW"
    [[ "$ANDERS_TS_NEW" == "$ANDERS_TS" ]] \
        || info "WARNING: Tailscale IP changed ($ANDERS_TS → $ANDERS_TS_NEW)"
    ANDERS_TS="$ANDERS_TS_NEW"
else
    info "Tailscale IP not yet available — attempting tailscale up..."
    anders "tailscale up --accept-routes 2>/dev/null || true"
    sleep 10
    ANDERS_TS=$(anders "tailscale ip -4 2>/dev/null | head -1")
    [[ -n "$ANDERS_TS" ]] || fail "Could not reconnect Tailscale on new anders VM"
    info "Tailscale IP: $ANDERS_TS"
fi
BASE_TS="http://$ANDERS_TS:8080"

pass "Tailscale active on new anders (IP=$ANDERS_TS)"

# ── Step 15: Run second wizard on fresh VM ────────────────────────────────────
echo ""
echo "=== Step 15: Run second wizard on fresh anders ==="

wait_wizard_prox "$BASE_LAN" 120 || fail "Wizard not reachable on fresh anders within 120 s"

step_post "$BASE_LAN/onboarding/step/1" \
    "-d 'role=new'"

step_post "$BASE_LAN/onboarding/step/2" \
    "-d 'node_name=anders'" \
    "-d 'node_display_name=Anders'"

step_post "$BASE_LAN/onboarding/step/3" \
    "--data-urlencode 'storage_paths=/mnt/storage'" \
    "-d 'storage_quota_gb=50'"

step_post "$BASE_LAN/onboarding/step/4" \
    "-d 'profile=balanced'"

info "Triggering second wizard finish cascade (up to 180 s)..."
prox "curl -s -o /tmp/cascade_body_g2.txt --max-time 180 \
    -X POST '$BASE_LAN/onboarding/step/5' \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode 'passphrase=$PASSPHRASE' \
    --data-urlencode 'passphrase_confirm=$PASSPHRASE'" || true
info "Second cascade HTTP call done"

sleep 3
anders "test -f '$GK_CFG'" || {
    BODY=$(prox "cat /tmp/cascade_body_g2.txt 2>/dev/null || echo '(no body)'")
    fail "Second wizard cascade failed — gatekeeper.cfg not created: $BODY"
}
pass "Second wizard cascade complete"

# Confirm the new recovery key and restart into normal mode.
anders "curl -sf --max-time 15 -X POST '$BASE_LAN/onboarding/confirm-key' -o /dev/null"
anders "curl -sf --max-time 5 -X POST '$BASE_LAN/api/onboarding/restart' -o /dev/null || true"
sleep 5

# ── Step 16: Switch to test profile and wait for normal mode ──────────────────
echo ""
echo "=== Step 16: Switch to test profile and verify normal mode ==="

wait_gatekeeper_ts "$BASE_TS" 120 || fail "Gatekeeper did not start in normal mode within 120 s"

info "Switching fragmentation profile to test (k=1, n=2, happy=1)..."
anders "sed -i 's/^profile.*/profile = test/' '$GK_CFG'"
anders "grep 'profile' '$GK_CFG'"
anders "rm -rf '${ANDERS_DATA_DIR}/tahoe/introducer/'"
anders "nohup bash -c 'systemctl restart $ANDERS_SVC' >/dev/null 2>&1 &"
sleep 8
wait_gatekeeper_ts "$BASE_TS" 90 \
    || fail "Gatekeeper did not recover after second profile switch"
pass "Normal mode confirmed, profile=test"

# ── Step 17: Verify catalog is empty before emergency restore ─────────────────
echo ""
echo "=== Step 17: Verify catalog is empty ==="

CATALOG_COUNT=$(anders "python3 -c \"
import sqlite3
try:
    c = sqlite3.connect('${CATALOG_DB}')
    r = c.execute('SELECT COUNT(*) FROM files').fetchone()
    print(r[0] if r else 0)
    c.close()
except Exception:
    print(0)
\"" 2>/dev/null | tr -d '[:space:]') || CATALOG_COUNT=0
info "Catalog entry count before reconstruction: $CATALOG_COUNT"
[[ "$CATALOG_COUNT" == "0" ]] \
    || fail "Catalog is not empty ($CATALOG_COUNT records) — emergency restore would return 409"
pass "Catalog is empty — emergency restore can proceed"

# ── Step 18: Emergency catalog reconstruction from recovery_kit.enc ───────────
echo ""
echo "=== Step 18: Emergency catalog reconstruction ==="

# Base64-encode the saved recovery_kit.enc.
RECOVERY_B64=$(base64 -w0 /tmp/recovery_kit_g.enc 2>/dev/null \
    || base64 /tmp/recovery_kit_g.enc 2>/dev/null)  # macOS base64 has no -w flag
[[ -n "$RECOVERY_B64" ]] || fail "Failed to base64-encode recovery_kit.enc"

info "POSTing recovery kit to /api/restore/emergency (kit=${#RECOVERY_B64} base64 chars)..."
EMERGENCY_RESP=$(anders "curl -sf --max-time 30 -X POST '${BASE_TS}/api/restore/emergency' \
    -H 'Content-Type: application/json' \
    -d '{\"recovery_kit_b64\":\"${RECOVERY_B64}\",\"passphrase\":\"${PASSPHRASE}\"}'" 2>/dev/null) \
    || fail "Emergency restore POST failed (gatekeeper returned an error or was unreachable)"
info "Emergency restore response: $EMERGENCY_RESP"

JOB_ID=$(echo "$EMERGENCY_RESP" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null) \
    || fail "Could not extract job_id from emergency restore response: $EMERGENCY_RESP"

# Catalog reconstruction traverses the Tahoe directory tree — allow up to 5 min.
poll_job "$BASE_TS" "$JOB_ID" 300 \
    || fail "Emergency catalog reconstruction job failed (status=$POLL_STATUS, resp=$POLL_RESP)"

RECONSTRUCTED=$(echo "$POLL_RESP" \
    | python3 -c "import sys,json; j=json.load(sys.stdin); print(j.get('results',[{}])[0].get('files_reconstructed','?'))" \
    2>/dev/null || echo "?")
info "Files reconstructed: $RECONSTRUCTED"
pass "Emergency catalog reconstruction complete"

# ── Step 19: Verify catalog has ≥10 entries ───────────────────────────────────
echo ""
echo "=== Step 19: Verify catalog after reconstruction ==="

POST_CATALOG_COUNT=$(anders "python3 -c \"
import sqlite3
try:
    c = sqlite3.connect('${CATALOG_DB}')
    r = c.execute('SELECT COUNT(*) FROM files WHERE backed_up_at IS NOT NULL').fetchone()
    print(r[0] if r else 0)
    c.close()
except Exception:
    print(0)
\"" 2>/dev/null | tr -d '[:space:]') || POST_CATALOG_COUNT=0
info "Catalog entry count after reconstruction: $POST_CATALOG_COUNT"
(( POST_CATALOG_COUNT >= 10 )) \
    || fail "Expected ≥10 catalog entries after reconstruction, found $POST_CATALOG_COUNT"
pass "Catalog has $POST_CATALOG_COUNT entries after emergency reconstruction ✓"

# ── Step 20: Restore testfile_01.bin and verify SHA-256 ───────────────────────
echo ""
echo "=== Step 20: Restore testfile_01.bin and verify SHA-256 ==="

RESTORE_DEST="/tmp/dr_restore/testfile_01.bin"
info "Restoring /srv/testbackup/testfile_01.bin → $RESTORE_DEST"

RESTORE_RESP=$(anders "curl -sf --max-time 30 -X POST '${BASE_TS}/api/restore/start/file' \
    -H 'Content-Type: application/json' \
    -d '{\"original_path\":\"/srv/testbackup/testfile_01.bin\",\"agent\":\"agent-anders-pc\",\"dest_path\":\"${RESTORE_DEST}\"}'" \
    2>/dev/null) \
    || fail "Restore start POST failed: check gatekeeper log"
info "Start response: $RESTORE_RESP"

JOB_ID=$(echo "$RESTORE_RESP" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null) \
    || fail "Could not extract job_id from restore response: $RESTORE_RESP"

poll_job "$BASE_TS" "$JOB_ID" 180 \
    || fail "File restore job failed (status=$POLL_STATUS, resp=$POLL_RESP)"

# Verify SHA-256 of restored file.
ACTUAL_SHA=$(anders "sha256sum '${RESTORE_DEST}' 2>/dev/null | awk '{print \$1}'" 2>/dev/null \
    | tr -d '[:space:]')
info "Restored SHA-256:  $ACTUAL_SHA"
info "Expected SHA-256:  $SHA256_REF"
[[ "$ACTUAL_SHA" == "$SHA256_REF" ]] \
    || fail "SHA-256 mismatch: expected $SHA256_REF got $ACTUAL_SHA"
pass "testfile_01.bin restored and SHA-256 verified ✓"

# ── Step 21: Take phase-g snapshot on VM 101 ──────────────────────────────────
echo ""
echo "=== Step 21: Take phase-g snapshot on VM $ANDERS_VMID ==="

info "Stopping anders for snapshot..."
prox "qm stop $ANDERS_VMID --skiplock 1"
sleep 5
prox "qm snapshot $ANDERS_VMID phase-g --description 'Phase G: disaster recovery verified 2026-05-31'"
prox "qm start $ANDERS_VMID"
pass "phase-g snapshot created on VM $ANDERS_VMID"

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  1.17.8 PASSED"
echo "  VM 101 destroyed and recreated from template ✓"
echo "  BackupBuddy installed fresh on new VM ✓"
echo "  Tailscale state restored ✓"
echo "  Emergency restore from recovery_kit.enc ✓"
echo "  Catalog reconstruction: $POST_CATALOG_COUNT entries ✓"
echo "  File restore SHA-256 verified ✓"
echo "  phase-g snapshot on VM 101 ✓"
echo "=============================================="
