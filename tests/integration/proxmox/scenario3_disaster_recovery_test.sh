#!/usr/bin/env bash
# Integration test 1.16.12: Scenario 3 — Disaster recovery (recovery kit)
#
# Prerequisites:
#   - Proxmox host reachable at 192.168.1.60
#   - gatekeeper-anders (vmid 101): snapshot post-install-no-wizard
#   - agent-anders-pc  (vmid 301): snapshot post-install-no-wizard
#
# What it tests:
#   Full disaster-recovery flow — wizard setup, agent backup of 10 files,
#   catalog wiped (simulating VM disaster while storage disk survives),
#   fresh wizard run, emergency catalog reconstruction from recovery kit,
#   SHA-256 verified on restored file.
#
# Key architectural note:
#   The Tahoe storage pool (/mnt/storage) is on a separate disk (scsi1) that
#   is not wiped during the simulated disaster.  The second wizard creates a
#   new Tahoe node pointing to the same /mnt/storage, so the old shares are
#   served by the new node.  The emergency restore then traverses the old
#   root_dir_cap and rebuilds the catalog from those shares.
#
# Run from the dev machine:
#   bash tests/integration/proxmox/scenario3_disaster_recovery_test.sh

set -euo pipefail

PROXMOX="root@192.168.1.60"
ANDERS_VMID=101
AGENT_VMID=301
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

SSH_OPTS="-q -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o ServerAliveInterval=15"

# ── SSH helpers using ProxyJump ────────────────────────────────────────────────
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

# Read catalog count from Anders (requires root_dir.cap for the decryption key).
# Uses the venv python3 and inlines the HKDF derivation to avoid importing
# gatekeeper.main (which pulls in uvicorn and cannot be used as a library).
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
echo "=============================================="
echo ""

# ── Step 1: Roll back VMs ─────────────────────────────────────────────────────
echo "=== Step 1: Roll back VMs to $SNAPSHOT ==="
prox "qm stop $ANDERS_VMID 2>/dev/null; sleep 3; qm rollback $ANDERS_VMID $SNAPSHOT && qm start $ANDERS_VMID"
prox "pct stop $AGENT_VMID 2>/dev/null; sleep 3; pct rollback $AGENT_VMID $SNAPSHOT && pct start $AGENT_VMID"
info "VMs rolled back, waiting for SSH..."
wait_ssh_anders 120
wait_ssh_agent 120
# Stop the old-format gatekeeper service (pre-1.16.11 — backupbuddy-gatekeeper) if
# running in the snapshot.  It holds ports 8080/8081 and blocks the new service.
anders "systemctl stop backupbuddy-gatekeeper 2>/dev/null || true"
# Kill any other lingering process holding port 8081 (belt-and-suspenders).
anders "fuser -k 8081/tcp 2>/dev/null || true"
# post-install-no-wizard snapshot has an empty tahoe entrypoint binary (known issue
# from 1.16.10 debugging — see cluster_join_test.sh).  Force-reinstall regenerates it.
info "Regenerating tahoe entrypoint on Anders (force-reinstall)..."
anders "cd /opt/backup-buddy && /opt/backup-buddy/.venv/bin/pip install --force-reinstall -q . 2>&1 | tail -3"
# Deploy current source from dev machine — snapshot may pre-date recent fixes.
# Use tar|ssh (more reliable than scp -r through ProxyJump on Windows).
info "Deploying current gatekeeper source to Anders..."
tar -czf - gatekeeper/ | \
    ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" \
    "cd /opt/backup-buddy && tar -xzf - --no-same-owner && find gatekeeper -name '*.pyc' -delete"
info "Deploying current agent source to agent-anders-pc..."
tar -czf - agent/ | \
    ssh $SSH_OPTS -J "$PROXMOX" "root@$AGENT_LAN" \
    "cd /opt/backup-buddy && tar -xzf - --no-same-owner && find agent -name '*.pyc' -delete"
# Restart service so the new Python modules are loaded.
anders "systemctl restart $ANDERS_SVC 2>/dev/null || true"
# Ensure storage pool is writable by the backupbuddy service user
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
# Note: no explicit -H Content-Type — curl sets it automatically for --data-urlencode.
# Passing -H 'Header: Value' through SSH splits it into three tokens breaking the header.
prox curl -s -o /tmp/s3_cascade.txt --max-time 180 -X POST \
    --data-urlencode "passphrase=$PASSPHRASE" \
    --data-urlencode "passphrase_confirm=$PASSPHRASE" \
    "$ANDERS_WIZARD_URL/onboarding/step/5" || true
info "Cascade HTTP call done"
anders "test -f $ANDERS_CFG" \
    || { BODY=$(prox cat /tmp/s3_cascade.txt 2>/dev/null || echo "(no body)"); fail "Cascade failed — no gatekeeper.cfg: $BODY"; }
pass "Wizard cascade complete — gatekeeper.cfg created"

# ── Step 4: Read agent API token, restart into normal mode ────────────────────
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

# Explicit restart (wizard remains in "awaiting restart" state; systemd does not
# auto-restart after the cascade because the process is still alive).
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

# Write backup.cfg — use printf to handle variable substitution over SSH cleanly
agent "printf '[gatekeeper]\nurl = http://$ANDERS_LAN:8081\ntoken = $AGENT_TOKEN\nname = anders-pc\nlifeboat_path = /etc/backup-buddy/lifeboat.enc\n\n[lifeboat_server]\nenabled = true\nport = 8082\n\n[schedule]\nfull_scan = 24h\nstability_minutes = 1\n\n[backup]\n$BACKUP_PATH\n\n[exclude]\n*.tmp\n' > $AGENT_CFG"
# Set ownership so the backupbuddy service user can read the config.
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

# ── Step 7: Save recovery kit before disaster ──────────────────────────────────
echo ""
echo "=== Step 7: Save recovery_kit.enc before simulated disaster ==="
anders "cp $ANDERS_DATA_DIR/recovery_kit.enc /tmp/old_recovery_kit.enc"
KIT_BYTES=$(anders "wc -c < /tmp/old_recovery_kit.enc")
info "Recovery kit saved: $KIT_BYTES bytes"
pass "Recovery kit preserved at /tmp/old_recovery_kit.enc"

# ── Step 8: Simulate disaster ─────────────────────────────────────────────────
echo ""
echo "=== Step 8: Simulate disaster (wipe config+catalog, keep /mnt/storage) ==="
# Stop gatekeeper FIRST so Tahoe flushes any in-flight writes before we count shares.
anders "systemctl stop $ANDERS_SVC 2>/dev/null || true"
sleep 3

SHARES_BEFORE=$(anders "find /mnt/storage/shares -type f 2>/dev/null | wc -l || echo 0")
info "/mnt/storage shares before wipe: $SHARES_BEFORE"

anders "rm -f $ANDERS_CFG"
anders "rm -rf $ANDERS_DATA_DIR/*"
# /mnt/storage is a separate mount point — untouched by the above rm

SHARES_AFTER_WIPE=$(anders "find /mnt/storage/shares -type f 2>/dev/null | wc -l || echo 0")
info "/mnt/storage shares after wipe: $SHARES_AFTER_WIPE (should equal $SHARES_BEFORE)"
[[ "$SHARES_AFTER_WIPE" -eq "$SHARES_BEFORE" ]] || fail "Storage data was unexpectedly wiped (before=$SHARES_BEFORE after=$SHARES_AFTER_WIPE)"

# Restore recovery kit to /tmp (rm -rf above does not touch /tmp)
anders "test -f /tmp/old_recovery_kit.enc" || fail "Recovery kit missing from /tmp after disaster simulation"

anders "chown -R backupbuddy:backupbuddy /mnt/storage 2>/dev/null || true"
anders "systemctl start $ANDERS_SVC"
info "Gatekeeper restarted — expecting wizard mode (no gatekeeper.cfg)"
sleep 5
pass "Disaster simulated — storage ($SHARES_BEFORE shares) intact, config wiped"

# ── Step 9: Wait for wizard (fresh) ───────────────────────────────────────────
echo ""
echo "=== Step 9: Wait for wizard after disaster ==="
wait_http_prox "$ANDERS_WIZARD_URL/onboarding/step/1" "wizard after disaster" 120
pass "Wizard accessible on fresh install"

# ── Step 10: Drive wizard again (role=new, same storage, same passphrase) ──────
echo ""
echo "=== Step 10: Drive second wizard (role=new, same /mnt/storage) ==="
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

# ── Step 11: Restart into normal mode ─────────────────────────────────────────
echo ""
echo "=== Step 11: Restart into normal mode ==="
anders "systemctl reset-failed $ANDERS_SVC 2>/dev/null || true"
anders "systemctl restart $ANDERS_SVC"
sleep 5
wait_http_anders "$ANDERS_TS_URL/api/status" "Anders normal mode (post-disaster)" 120
STATUS=$(anders "curl -sf '$ANDERS_TS_URL/api/status'")
echo "$STATUS" | grep -q '"status":"ok"' || fail "Anders not healthy after reinstall"
pass "Anders in normal mode after reinstall"

# Catalog must be empty before emergency restore
EMPTY_COUNT=$(catalog_count)
info "Catalog after fresh install (must be 0): $EMPTY_COUNT"
[[ "$EMPTY_COUNT" -eq 0 ]] || fail "Catalog is not empty before emergency restore (count=$EMPTY_COUNT)"
pass "Catalog is empty — ready for emergency restore"

# ── Step 12: Emergency catalog restore ────────────────────────────────────────
echo ""
echo "=== Step 12: Emergency catalog reconstruction ==="
KIT_B64=$(anders "base64 -w0 /tmp/old_recovery_kit.enc")
[[ -n "$KIT_B64" ]] || fail "Could not base64-encode recovery kit"

# Write the JSON body to a file on Anders to avoid SSH quoting issues with
# -H 'Content-Type: ...' passed as separate arguments through the anders() function.
printf '{"recovery_kit_b64": "%s", "passphrase": "%s"}' "$KIT_B64" "$PASSPHRASE" | \
    ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "cat > /tmp/s3_emergency_req.json"
EMERGENCY_RESP=$(anders "curl -sf -X POST '$ANDERS_TS_URL/api/restore/emergency' \
    -H 'Content-Type: application/json' --data '@/tmp/s3_emergency_req.json'")
info "Emergency restore response: $EMERGENCY_RESP"
JOB_ID=$(echo "$EMERGENCY_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null)
[[ -n "$JOB_ID" ]] || fail "No job_id in emergency restore response: $EMERGENCY_RESP"
info "Job ID: $JOB_ID"

# Poll until done
JOB_STATUS=""
deadline=$(( $(date +%s) + 120 ))
while (( $(date +%s) < deadline )); do
    JOB_RESP=$(anders "curl -sf '$ANDERS_TS_URL/api/restore/jobs/$JOB_ID'")
    JOB_STATUS=$(echo "$JOB_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    info "Reconstruction status: $JOB_STATUS"
    [[ "$JOB_STATUS" == "done" ]] && break
    if [[ "$JOB_STATUS" == "failed" ]]; then
        JOB_ERR=$(echo "$JOB_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))" 2>/dev/null)
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

# ── Step 13: Verify catalog count ─────────────────────────────────────────────
echo ""
echo "=== Step 13: Verify catalog count >= $TEST_FILE_COUNT ==="
CATALOG_COUNT=$(catalog_count)
info "Catalog count after reconstruction: $CATALOG_COUNT"
[[ "$CATALOG_COUNT" -ge "$TEST_FILE_COUNT" ]] \
    || fail "Only $CATALOG_COUNT files in catalog (expected >= $TEST_FILE_COUNT)"
pass "Catalog has $CATALOG_COUNT files (>= $TEST_FILE_COUNT)"

# ── Step 14: Restore one file and verify SHA-256 ──────────────────────────────
echo ""
echo "=== Step 14: Restore scenario3_file_01.bin and verify SHA-256 ==="
TEST_FILE_PATH="$BACKUP_PATH/scenario3_file_01.bin"
RESTORE_PATH="$RESTORE_DIR/scenario3_file_01.bin"
anders "mkdir -p $RESTORE_DIR && chmod 777 $RESTORE_DIR"

printf '{"original_path": "%s", "agent": "anders-pc", "dest_path": "%s"}' \
    "$TEST_FILE_PATH" "$RESTORE_PATH" | \
    ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "cat > /tmp/s3_restore_req.json"
RESTORE_RESP=$(anders "curl -sf -X POST '$ANDERS_TS_URL/api/restore/start/file' \
    -H 'Content-Type: application/json' --data '@/tmp/s3_restore_req.json'")
info "Restore start response: $RESTORE_RESP"
RESTORE_JOB=$(echo "$RESTORE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null)
[[ -n "$RESTORE_JOB" ]] || fail "No job_id in restore response: $RESTORE_RESP"

# Poll restore job
RJOB_STATUS=""
deadline=$(( $(date +%s) + 120 ))
while (( $(date +%s) < deadline )); do
    RRESP=$(anders "curl -sf '$ANDERS_TS_URL/api/restore/jobs/$RESTORE_JOB'")
    RJOB_STATUS=$(echo "$RRESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
    [[ "$RJOB_STATUS" == "done" ]] || [[ "$RJOB_STATUS" == "failed" ]] && break
    sleep 3
done
if [[ "$RJOB_STATUS" == "failed" ]]; then
    RERR=$(echo "$RRESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))" 2>/dev/null)
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
[[ "$RESTORED_SHA256" == "$ORIGINAL_SHA256" ]] || fail "SHA-256 mismatch: got $RESTORED_SHA256, expected $ORIGINAL_SHA256"
pass "SHA-256 verified: $RESTORED_SHA256"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  1.16.12 PASSED"
echo "=============================================="
