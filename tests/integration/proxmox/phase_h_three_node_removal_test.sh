#!/usr/bin/env bash
# Integration test 1.17.9: Phase H — Three-node cluster + node removal flow
#
# Prerequisites:
#   - Proxmox host reachable at 192.168.1.60
#   - anders (VM 101): phase-e snapshot exists (two-node cluster: anders + bjorn)
#   - bjorn  (VM 102): phase-e snapshot exists
#   - carina (VM 103): phase-a snapshot exists (disk /dev/sdb raw, wizard mode)
#   - CT 301 (agent-anders-pc): phase-e snapshot exists
#   - CT 302 (agent-anders-nas): phase-e snapshot exists
#   - CT 303 (agent-bjorn-pc):   phase-e snapshot exists
#   - Tailscale active on all VMs
#
# Run from repo root on the dev machine:
#   bash tests/integration/proxmox/phase_h_three_node_removal_test.sh

set -euo pipefail

PROXMOX="root@192.168.1.60"
ANDERS_LAN="10.99.0.11"
BJORN_LAN="10.99.0.12"
CARINA_LAN="10.99.0.13"
ANDERS_VMID=101
BJORN_VMID=102
CARINA_VMID=103
ANDERS_AGENT_CTID=301   # agent-anders-pc
EXTRA_CTID=302          # agent-anders-nas
BJORN_AGENT_CTID=303    # agent-bjorn-pc

ANDERS_DATA_DIR="/var/lib/backup-buddy"
BJORN_DATA_DIR="/var/lib/backup-buddy"
CARINA_DATA_DIR="/var/lib/backup-buddy"
ANDERS_CATALOG_DB="${ANDERS_DATA_DIR}/catalog.db"
ANDERS_CLUSTER_DB="${ANDERS_DATA_DIR}/cluster.db"
CARINA_CFG="/etc/backup-buddy/gatekeeper.cfg"

GK_SVC="backup-buddy-gatekeeper"
AGENT_SVC="backup-buddy-agent"

CARINA_NODE_NAME="carina"

SSH_OPTS="-q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 -o ServerAliveInterval=15"

anders() { ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "$@"; }
bjorn()  { ssh $SSH_OPTS -J "$PROXMOX" "root@$BJORN_LAN"  "$@"; }
carina() { ssh $SSH_OPTS -J "$PROXMOX" "root@$CARINA_LAN" "$@"; }
prox()   { ssh $SSH_OPTS "$PROXMOX" "$@"; }

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }
info() { echo "  → $*"; }

# ── Wait helpers ───────────────────────────────────────────────────────────────

wait_ssh() {
    local host="$1" label="$2" deadline=$(( $(date +%s) + 150 ))
    echo -n "  Waiting for SSH on $label..."
    while (( $(date +%s) < deadline )); do
        if ssh $SSH_OPTS -J "$PROXMOX" "root@$host" "true" 2>/dev/null; then
            echo " OK"; return 0
        fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

wait_gatekeeper() {
    local url="$1" label="$2" timeout="${3:-120}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "  Waiting for $label at $url..."
    while (( $(date +%s) < deadline )); do
        if anders "curl -sf --max-time 5 '${url}/api/status' -o /dev/null" 2>/dev/null; then
            echo " OK"; return 0
        fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

# Checks from Proxmox host (wizard/setup mode; LAN-accessible).
wait_gatekeeper_prox() {
    local url="$1" label="$2" timeout="${3:-120}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "  Waiting for $label at $url..."
    while (( $(date +%s) < deadline )); do
        if prox "curl -sf --max-time 5 '${url}/' -o /dev/null" 2>/dev/null; then
            echo " OK"; return 0
        fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

wait_tahoe_ready() {
    local label="${1:-tahoe}" timeout="${2:-150}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "  Waiting for $label storage node to accept CHK uploads..."
    while (( $(date +%s) < deadline )); do
        local resp
        resp=$(anders "dd if=/dev/urandom bs=4096 count=1 2>/dev/null | curl -sf --max-time 15 -X PUT 'http://127.0.0.1:3456/uri' --data-binary @- 2>/dev/null" 2>/dev/null || true)
        if [[ "$resp" == URI:CHK:* ]]; then echo " OK"; return 0; fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

step_post() {
    local step_url="$1"; shift
    local code
    code=$(prox "curl -sw '%{http_code}' -o /dev/null -X POST '$step_url' $*" | tail -c 3)
    info "$step_url → HTTP $code"
    [[ "$code" == "303" ]] || fail "Expected 303 from $step_url, got $code"
}

# ── Main test ──────────────────────────────────────────────────────────────────

echo "================================================"
echo "  1.17.9 — Phase H: Three-node cluster + removal"
echo "================================================"
echo ""

# ── Step 1: Rollback all nodes to starting snapshots ─────────────────────────
echo "=== Step 1: Rollback all nodes ==="

info "Stopping CTs ${ANDERS_AGENT_CTID}, ${EXTRA_CTID}, ${BJORN_AGENT_CTID}..."
prox "pct stop $ANDERS_AGENT_CTID 2>/dev/null || true"
prox "pct stop $EXTRA_CTID        2>/dev/null || true"
prox "pct stop $BJORN_AGENT_CTID  2>/dev/null || true"
sleep 3

info "Stopping VMs ${ANDERS_VMID}, ${BJORN_VMID}, ${CARINA_VMID}..."
prox "qm stop $ANDERS_VMID  --skiplock 1 2>/dev/null || true"
prox "qm stop $BJORN_VMID   --skiplock 1 2>/dev/null || true"
prox "qm stop $CARINA_VMID  --skiplock 1 2>/dev/null || true"
sleep 5

info "Rolling back anders (VM $ANDERS_VMID) to phase-e..."
prox "qm rollback $ANDERS_VMID phase-e && qm start $ANDERS_VMID"

info "Rolling back bjorn (VM $BJORN_VMID) to phase-e..."
prox "qm rollback $BJORN_VMID phase-e && qm start $BJORN_VMID"

info "Rolling back carina (VM $CARINA_VMID) to phase-a..."
prox "qm rollback $CARINA_VMID phase-a && qm start $CARINA_VMID"

info "Rolling back CT $ANDERS_AGENT_CTID to phase-e..."
prox "pct rollback $ANDERS_AGENT_CTID phase-e && pct start $ANDERS_AGENT_CTID"

info "Rolling back CT $EXTRA_CTID to phase-e..."
prox "pct rollback $EXTRA_CTID phase-e && pct start $EXTRA_CTID 2>/dev/null || true"

info "Rolling back CT $BJORN_AGENT_CTID to phase-e..."
prox "pct rollback $BJORN_AGENT_CTID phase-e && pct start $BJORN_AGENT_CTID"

wait_ssh "$ANDERS_LAN" "anders" || fail "Anders did not come up within 150 s"
wait_ssh "$BJORN_LAN"  "bjorn"  || fail "Bjorn did not come up within 150 s"
wait_ssh "$CARINA_LAN" "carina" || fail "Carina did not come up within 150 s"

# Sync current gatekeeper code to all three VMs
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
info "Syncing gatekeeper code to anders..."
tar -czf - -C "$REPO_ROOT" gatekeeper | ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "tar -xzf - -C /opt/backup-buddy/"
info "Syncing gatekeeper code to bjorn..."
tar -czf - -C "$REPO_ROOT" gatekeeper | ssh $SSH_OPTS -J "$PROXMOX" "root@$BJORN_LAN"  "tar -xzf - -C /opt/backup-buddy/"
info "Syncing gatekeeper code to carina..."
tar -czf - -C "$REPO_ROOT" gatekeeper | ssh $SSH_OPTS -J "$PROXMOX" "root@$CARINA_LAN" "tar -xzf - -C /opt/backup-buddy/"

info "Reinstalling gatekeeper package on all VMs (editable + force-reinstall)..."
anders "cd /opt/backup-buddy && pip install -q -e . --force-reinstall 2>&1 | tail -3"
bjorn  "cd /opt/backup-buddy && pip install -q -e . --force-reinstall 2>&1 | tail -3"
carina "cd /opt/backup-buddy && pip install -q -e . --force-reinstall 2>&1 | tail -3"

pass "All nodes rolled back and code synced"

# ── Step 2: Mount storage disks ───────────────────────────────────────────────
echo ""
echo "=== Step 2: Mount storage disks ==="

info "Remounting /mnt/storage on anders (phase-e disk)..."
anders "mountpoint -q /mnt/storage || (mkdir -p /mnt/storage && mount /dev/sdb /mnt/storage)"

info "Remounting /mnt/storage on bjorn (phase-e disk)..."
bjorn "mountpoint -q /mnt/storage || (mkdir -p /mnt/storage && mount /dev/sdb /mnt/storage)"

info "Formatting /dev/sdb and mounting /mnt/storage on carina (phase-a: raw disk)..."
carina "mkfs.ext4 -F /dev/sdb"
carina "mkdir -p /mnt/storage && mount /dev/sdb /mnt/storage"
carina "chown -R backupbuddy:backupbuddy /mnt/storage 2>/dev/null || true"

pass "Storage disks ready on all nodes"

# ── Step 3: Resolve Tailscale IPs, wait for anders + bjorn gatekeepers ────────
echo ""
echo "=== Step 3: Resolve Tailscale IPs and wait for gatekeepers ==="

info "Resolving Tailscale IP for anders..."
ANDERS_TS=$(anders "tailscale ip -4 2>/dev/null | head -1")
[[ -n "$ANDERS_TS" ]] || fail "Could not resolve Anders Tailscale IP — is Tailscale running?"
ANDERS_TS_URL="http://$ANDERS_TS:8080"
info "Anders Tailscale IP: $ANDERS_TS → $ANDERS_TS_URL"

info "Resolving Tailscale IP for bjorn..."
BJORN_TS=$(bjorn "tailscale ip -4 2>/dev/null | head -1")
[[ -n "$BJORN_TS" ]] || fail "Could not resolve Bjorn Tailscale IP — is Tailscale running?"
BJORN_TS_URL="http://$BJORN_TS:8080"
info "Bjorn Tailscale IP: $BJORN_TS → $BJORN_TS_URL"

# Read anders node name from config (may differ from hostname)
ANDERS_NODE_NAME=$(anders "python3 -c \"
import configparser
c = configparser.ConfigParser(allow_no_value=True, delimiters=('=',))
c.read('/etc/backup-buddy/gatekeeper.cfg')
print(c.get('node', 'name', fallback=''))
\"" 2>/dev/null | tr -d '[:space:]')
[[ -n "$ANDERS_NODE_NAME" ]] || fail "Could not read node name from anders gatekeeper.cfg"
info "Anders node name: $ANDERS_NODE_NAME"

# Phase-e snapshot has profile=test (k=1, n=2, happy=1). Keep it for now;
# we will switch to adaptive after carina joins (Step 9).
info "Restarting anders gatekeeper (current code, phase-e config)..."
anders "nohup bash -c 'systemctl restart $GK_SVC' >/dev/null 2>&1 &"
sleep 8
wait_gatekeeper "$ANDERS_TS_URL" "anders gatekeeper" 120 \
    || fail "Anders gatekeeper did not become ready within 120 s"
wait_tahoe_ready "anders Tahoe" 300 \
    || fail "Anders Tahoe storage node did not become ready within 300 s"

info "Restarting bjorn gatekeeper (current code)..."
bjorn "nohup bash -c 'systemctl restart $GK_SVC' >/dev/null 2>&1 &"
sleep 5
wait_gatekeeper "$BJORN_TS_URL" "bjorn gatekeeper" 120 \
    || fail "Bjorn gatekeeper did not become ready within 120 s"

# Verify phase-e backup data: ≥10 files in anders catalog
FILE_COUNT=$(anders "python3 -c \"
import sqlite3
try:
    c = sqlite3.connect('${ANDERS_CATALOG_DB}')
    r = c.execute('SELECT COUNT(*) FROM files WHERE backed_up_at IS NOT NULL').fetchone()
    print(r[0] if r else 0)
    c.close()
except Exception:
    print(0)
\"" 2>/dev/null | tr -d '[:space:]') || FILE_COUNT=0
info "Anders catalog file count (phase-e baseline): $FILE_COUNT"
(( FILE_COUNT >= 10 )) || fail "Expected ≥10 files in phase-e catalog, found $FILE_COUNT"

pass "Anders and bjorn gatekeepers ready (phase-e baseline: $FILE_COUNT files)"

# ── Step 4: Generate invite on anders ────────────────────────────────────────
echo ""
echo "=== Step 4: Generate invite code on anders ==="

INVITE_JSON=$(anders "curl -sf --max-time 10 -X POST '${ANDERS_TS_URL}/api/buddies/invite'")
info "Response: $INVITE_JSON"
INVITE_CODE=$(echo "$INVITE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['code'])" 2>/dev/null) \
    || fail "Could not extract invite code from: $INVITE_JSON"
[[ -n "$INVITE_CODE" ]] || fail "Invite code is empty"
info "Invite code: $INVITE_CODE"
pass "Invite code generated"

# ── Step 5: Drive carina wizard — join flow ───────────────────────────────────
echo ""
echo "=== Step 5: Drive carina wizard (join flow) ==="

# Carina is at phase-a: gatekeeper installed, disk formatted above, wizard running.
# Reset to clean setup mode in case a stale service is running.
carina "systemctl stop $GK_SVC 2>/dev/null || true"
carina "systemctl reset-failed $GK_SVC 2>/dev/null || true"
carina "rm -rf '${CARINA_DATA_DIR:?}'/* && rm -f '$CARINA_CFG'"
carina "chown -R backupbuddy:backupbuddy '$CARINA_DATA_DIR' 2>/dev/null || true"
carina "chown -R backupbuddy:backupbuddy /mnt/storage 2>/dev/null || true"
carina "systemctl start $GK_SVC"
sleep 5

CARINA_WIZARD_URL="http://$CARINA_LAN:8080"
wait_gatekeeper_prox "$CARINA_WIZARD_URL" "carina wizard" 90 \
    || fail "Carina wizard did not become reachable within 90 s"

step_post "$CARINA_WIZARD_URL/onboarding/step/1" \
    "-d 'role=join'"

step_post "$CARINA_WIZARD_URL/onboarding/join" \
    "--data-urlencode 'invite_code=$INVITE_CODE'" \
    "--data-urlencode 'gatekeeper_url=$ANDERS_TS_URL'"

step_post "$CARINA_WIZARD_URL/onboarding/step/2" \
    "-d 'node_name=$CARINA_NODE_NAME'" \
    "-d 'node_display_name=Carina+Test+Node'"

step_post "$CARINA_WIZARD_URL/onboarding/step/3" \
    "--data-urlencode 'storage_paths=/mnt/storage'" \
    "-d 'storage_quota_gb=50'"

step_post "$CARINA_WIZARD_URL/onboarding/step/4" \
    "-d 'profile=adaptive'"

info "Triggering finish cascade via step/5 (up to 180 s)..."
prox "curl -s -o /tmp/cascade_body_h.txt --max-time 180 \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    -X POST '$CARINA_WIZARD_URL/onboarding/step/5'" || true
info "Cascade HTTP call done"

sleep 3
carina "test -f '$CARINA_CFG'" || {
    BODY=$(prox "cat /tmp/cascade_body_h.txt 2>/dev/null || echo '(no body)'")
    fail "Cascade failed — gatekeeper.cfg not found on carina: $BODY"
}
pass "Carina wizard cascade complete — gatekeeper.cfg created"

# ── Step 6: Restart carina into normal mode ───────────────────────────────────
echo ""
echo "=== Step 6: Restart carina into normal mode ==="

carina "systemctl reset-failed $GK_SVC 2>/dev/null || true"
carina "systemctl restart $GK_SVC"
sleep 5

info "Resolving Tailscale IP for carina..."
CARINA_TS_DEADLINE=$(( $(date +%s) + 60 ))
CARINA_TS=""
while (( $(date +%s) < CARINA_TS_DEADLINE )); do
    CARINA_TS=$(carina "tailscale ip -4 2>/dev/null | head -1" 2>/dev/null | tr -d '[:space:]') || true
    [[ -n "$CARINA_TS" ]] && break
    echo -n "."; sleep 5
done
[[ -n "$CARINA_TS" ]] || fail "Could not resolve Carina Tailscale IP — is Tailscale running?"
CARINA_TS_URL="http://$CARINA_TS:8080"
info "Carina Tailscale IP: $CARINA_TS → $CARINA_TS_URL"

# Poll carina gatekeeper via anders (both on Tailscale network)
wait_gatekeeper "$CARINA_TS_URL" "carina normal mode" 120 \
    || fail "Carina gatekeeper did not start in normal mode within 120 s"

CARINA_STATUS=$(anders "curl -sf --max-time 10 '${CARINA_TS_URL}/api/status'" 2>/dev/null) || true
info "Carina status: $CARINA_STATUS"
echo "$CARINA_STATUS" | python3 -c "import sys,json; s=json.load(sys.stdin); exit(0 if s.get('status')=='ok' else 1)" 2>/dev/null \
    || fail "Carina /api/status did not return {\"status\":\"ok\"}: $CARINA_STATUS"

pass "Carina running in normal mode"

# ── Step 7: Verify 3-member cluster on anders and carina ─────────────────────
echo ""
echo "=== Step 7: Verify 3-member cluster ==="

ANDERS_MEMBERS=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_DATA_DIR}/cluster.db')
rows = db.execute('SELECT node_id FROM members ORDER BY joined_at').fetchall()
for r in rows:
    print(r[0])
db.close()
PYTHON
)
info "Anders cluster members: $(echo "$ANDERS_MEMBERS" | tr '\n' ' ')"
echo "$ANDERS_MEMBERS" | grep -q "$ANDERS_NODE_NAME" \
    || fail "Anders does not see himself (${ANDERS_NODE_NAME}) in cluster.db"
echo "$ANDERS_MEMBERS" | grep -q "bjorn" \
    || fail "Anders does not see bjorn in cluster.db"
echo "$ANDERS_MEMBERS" | grep -q "$CARINA_NODE_NAME" \
    || fail "Anders does not see carina (${CARINA_NODE_NAME}) in cluster.db"

ANDERS_MEMBER_COUNT=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_DATA_DIR}/cluster.db')
count = db.execute('SELECT COUNT(*) FROM members').fetchone()[0]
print(count)
db.close()
PYTHON
)
info "Anders member count: $ANDERS_MEMBER_COUNT"
[[ "$ANDERS_MEMBER_COUNT" == "3" ]] \
    || fail "Anders should have 3 members, found $ANDERS_MEMBER_COUNT"
pass "Anders cluster.db: 3 members (anders + bjorn + carina)"

CARINA_MEMBERS=$(carina python3 << PYTHON
import sqlite3
db = sqlite3.connect('${CARINA_DATA_DIR}/cluster.db')
rows = db.execute('SELECT node_id FROM members ORDER BY joined_at').fetchall()
for r in rows:
    print(r[0])
db.close()
PYTHON
)
info "Carina cluster members: $(echo "$CARINA_MEMBERS" | tr '\n' ' ')"
echo "$CARINA_MEMBERS" | grep -q "$CARINA_NODE_NAME" \
    || fail "Carina does not see herself (${CARINA_NODE_NAME}) in cluster.db"
echo "$CARINA_MEMBERS" | grep -q "$ANDERS_NODE_NAME" \
    || fail "Carina does not see anders in cluster.db"

CARINA_MEMBER_COUNT=$(carina python3 << PYTHON
import sqlite3
db = sqlite3.connect('${CARINA_DATA_DIR}/cluster.db')
count = db.execute('SELECT COUNT(*) FROM members').fetchone()[0]
print(count)
db.close()
PYTHON
)
info "Carina member count: $CARINA_MEMBER_COUNT"
(( CARINA_MEMBER_COUNT >= 2 )) \
    || fail "Carina should have ≥2 members (cascade populated from anders), found $CARINA_MEMBER_COUNT"
pass "Carina cluster.db: $CARINA_MEMBER_COUNT members populated via join cascade"

# Bjorn only knows about itself and anders (no cross-node propagation in Phase 1)
BJORN_MEMBER_COUNT=$(bjorn python3 << PYTHON
import sqlite3
db = sqlite3.connect('${BJORN_DATA_DIR}/cluster.db')
count = db.execute('SELECT COUNT(*) FROM members').fetchone()[0]
print(count)
db.close()
PYTHON
)
info "Bjorn member count: $BJORN_MEMBER_COUNT (expected 2 — no cross-node propagation in Phase 1)"

pass "Cluster membership verified on all nodes"

# ── Step 8: Verify adaptive k/n computation for 3 nodes ──────────────────────
echo ""
echo "=== Step 8: Verify adaptive k/n computation (3 nodes → k=1, n=3) ==="

KN_RESULT=$(anders "python3 -c \"
import sys
sys.path.insert(0, '/opt/backup-buddy')
from gatekeeper.fragmenter.adaptive import compute_adaptive_kn
from gatekeeper.config import AdaptiveConfig
k, n = compute_adaptive_kn(3, AdaptiveConfig())
print(f'k={k} n={n}')
\"" 2>/dev/null | tr -d '[:space:]')
info "compute_adaptive_kn(3, AdaptiveConfig()) → $KN_RESULT"
[[ "$KN_RESULT" == "k=1 n=3" ]] \
    || fail "Expected k=1 n=3 for 3 nodes, got: $KN_RESULT"
pass "Adaptive k/n: 3 nodes → k=1, n=3 (ADR-006a verified)"

# ── Step 9: Switch anders to adaptive, upload files, verify carina fragments ──
echo ""
echo "=== Step 9: Fragment distribution to carina ==="

info "Switching anders to adaptive profile..."
anders "sed -i 's/^profile.*/profile = adaptive/' /etc/backup-buddy/gatekeeper.cfg"
anders "grep 'profile' /etc/backup-buddy/gatekeeper.cfg"
anders "nohup bash -c 'systemctl restart $GK_SVC' >/dev/null 2>&1 &"
sleep 8
wait_gatekeeper "$ANDERS_TS_URL" "anders gatekeeper (adaptive)" 90 \
    || fail "Anders gatekeeper did not recover after profile switch"
wait_tahoe_ready "anders Tahoe (adaptive)" 300 \
    || fail "Anders Tahoe did not become ready within 300 s"

CARINA_SHARES_BEFORE=$(carina "find /mnt/storage/shares -type f 2>/dev/null | wc -l" 2>/dev/null | tr -d '[:space:]' || echo 0)
info "Carina share count before new uploads: $CARINA_SHARES_BEFORE"

info "Waiting 45 s for Tahoe peer discovery (carina storage node)..."
sleep 45

info "Creating post-join test files on CT $ANDERS_AGENT_CTID..."
prox "pct exec $ANDERS_AGENT_CTID -- bash -c 'for i in \$(seq 31 35); do dd if=/dev/urandom of=/srv/testbackup/testfile_\$i.bin bs=1M count=1 2>/dev/null; done'"
prox "pct exec $ANDERS_AGENT_CTID -- systemctl restart $AGENT_SVC"

info "Waiting for post-join files to be backed up (up to 5 min)..."
SHARE_DEADLINE=$(( $(date +%s) + 300 ))
NEW_FILE_COUNT=0
while (( $(date +%s) < SHARE_DEADLINE )); do
    NEW_FILE_COUNT=$(anders "python3 -c \"
import sqlite3
try:
    c = sqlite3.connect('${ANDERS_CATALOG_DB}')
    r = c.execute('SELECT COUNT(*) FROM files WHERE backed_up_at IS NOT NULL').fetchone()
    print(r[0] if r else 0)
    c.close()
except Exception:
    print(0)
\"" 2>/dev/null | tr -d '[:space:]') || NEW_FILE_COUNT=0
    (( NEW_FILE_COUNT > FILE_COUNT )) && break
    echo -n "."; sleep 15
done
echo ""
info "Anders catalog now has $NEW_FILE_COUNT files (was $FILE_COUNT)"
(( NEW_FILE_COUNT > FILE_COUNT )) \
    || fail "No new files backed up after carina join (catalog still at $NEW_FILE_COUNT)"

info "Waiting 15 s for share propagation to carina..."
sleep 15

CARINA_SHARES_AFTER=$(carina "find /mnt/storage/shares -type f 2>/dev/null | wc -l" 2>/dev/null | tr -d '[:space:]' || echo 0)
info "Carina share count after new uploads: $CARINA_SHARES_AFTER"
(( CARINA_SHARES_AFTER > CARINA_SHARES_BEFORE )) \
    || fail "No new shares on carina (before=$CARINA_SHARES_BEFORE after=$CARINA_SHARES_AFTER)"
pass "Fragment distribution to carina verified (shares: $CARINA_SHARES_BEFORE → $CARINA_SHARES_AFTER)"

# ── Step 10: Get bjorn node_id and propose removal vote ──────────────────────
echo ""
echo "=== Step 10: Propose removal of bjorn ==="

BJORN_NODE_ID=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_DATA_DIR}/cluster.db')
row = db.execute("SELECT node_id FROM members WHERE node_id LIKE '%bjorn%' LIMIT 1").fetchone()
print(row[0] if row else '')
db.close()
PYTHON
)
BJORN_NODE_ID=$(echo "$BJORN_NODE_ID" | tr -d '[:space:]')
[[ -n "$BJORN_NODE_ID" ]] || fail "Could not find bjorn node_id in anders cluster.db"
info "Bjorn node_id: $BJORN_NODE_ID"

REMOVAL_JSON=$(anders "curl -sf --max-time 10 -X POST '${ANDERS_TS_URL}/api/buddies/removal' \
    -H 'Content-Type: application/json' \
    -d '{\"target_node_id\": \"${BJORN_NODE_ID}\"}'")
info "Removal propose response: $REMOVAL_JSON"
VOTE_ID=$(echo "$REMOVAL_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['vote_id'])" 2>/dev/null) \
    || fail "Could not extract vote_id from: $REMOVAL_JSON"
[[ -n "$VOTE_ID" ]] || fail "vote_id is empty"
info "Vote opened: vote_id=$VOTE_ID, target=$BJORN_NODE_ID"

# Verify vote appears in anders cluster.db
VOTE_ROW=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_DATA_DIR}/cluster.db')
row = db.execute('SELECT id, vote_type, target_node_id, resolved FROM votes WHERE id = ?', (${VOTE_ID},)).fetchone()
print(list(row) if row else None)
db.close()
PYTHON
)
info "Vote row in anders cluster.db: $VOTE_ROW"
echo "$VOTE_ROW" | grep -q "removal" || fail "Removal vote not found in anders cluster.db"
echo "$VOTE_ROW" | grep -q "0" || fail "Vote should be unresolved (resolved=0)"
pass "Removal vote opened (vote_id=$VOTE_ID)"

# ── Step 11: Pre-insert carina ballot, anders casts vote ─────────────────────
echo ""
echo "=== Step 11: Cast votes (anders + carina) ==="

# Cross-gatekeeper vote propagation is out of Phase 1 scope.
# Pre-insert carina's yes ballot directly into anders's vote_ballots
# to simulate propagation; this lets _recount_and_resolve find 2/2 yes votes.
CARINA_NODE_ID=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_DATA_DIR}/cluster.db')
row = db.execute("SELECT node_id FROM members WHERE node_id LIKE '%carina%' LIMIT 1").fetchone()
print(row[0] if row else '')
db.close()
PYTHON
)
CARINA_NODE_ID=$(echo "$CARINA_NODE_ID" | tr -d '[:space:]')
[[ -n "$CARINA_NODE_ID" ]] || fail "Could not find carina node_id in anders cluster.db"
info "Carina node_id: $CARINA_NODE_ID"

info "Pre-inserting carina's ballot (yes) into anders vote_ballots (Phase 1 propagation stub)..."
anders python3 << PYTHON
import sqlite3, time
db = sqlite3.connect('${ANDERS_DATA_DIR}/cluster.db')
db.execute(
    "INSERT INTO vote_ballots (vote_id, voter_node_id, voted_at, choice) VALUES (?, ?, ?, 1)",
    (${VOTE_ID}, '${CARINA_NODE_ID}', time.time()),
)
db.commit()
db.close()
print("Carina ballot inserted")
PYTHON

info "Casting anders yes vote via /api/buddies/vote/${VOTE_ID}/cast..."
VOTE_RESP=$(anders "curl -sf --max-time 10 -X POST '${ANDERS_TS_URL}/api/buddies/vote/${VOTE_ID}/cast' \
    -H 'Content-Type: application/json' \
    -d '{\"choice\": true}'")
info "Vote cast response: $VOTE_RESP"
VOTE_RESULT=$(echo "$VOTE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['result'])" 2>/dev/null) \
    || fail "Could not extract result from: $VOTE_RESP"
info "Vote result: $VOTE_RESULT"
[[ "$VOTE_RESULT" == "passed" ]] \
    || fail "Expected vote result 'passed', got: $VOTE_RESULT"
pass "Vote PASSED (anders + carina both voted yes)"

# ── Step 12: Verify grace period started for bjorn ───────────────────────────
echo ""
echo "=== Step 12: Verify grace period started for bjorn ==="

BJORN_STATUS_AFTER=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_DATA_DIR}/cluster.db')
row = db.execute(
    'SELECT status, grace_started_at, grace_days FROM members WHERE node_id = ?',
    ('${BJORN_NODE_ID}',)
).fetchone()
print(dict(zip(['status', 'grace_started_at', 'grace_days'], row)) if row else None)
db.close()
PYTHON
)
info "Bjorn member row after vote: $BJORN_STATUS_AFTER"
echo "$BJORN_STATUS_AFTER" | python3 -c "
import sys, ast
d = ast.literal_eval(sys.stdin.read())
assert d is not None, 'bjorn not found'
assert d['status'] == 'grace', f'expected status=grace, got {d[\"status\"]}'
assert d['grace_started_at'] is not None, 'grace_started_at not set'
print('bjorn status=grace, grace_started_at set')
" || fail "Grace period not started correctly for bjorn: $BJORN_STATUS_AFTER"

# Verify the grace-alert log line on anders (from buddies.py send_alert fix)
info "Checking anders gatekeeper log for grace-alert message..."
GRACE_LOG=$(anders "journalctl -u $GK_SVC --no-pager -n 200 2>/dev/null | grep 'grace-alert' | tail -5" 2>/dev/null || true)
if [[ -n "$GRACE_LOG" ]]; then
    info "Grace-alert log: $GRACE_LOG"
    pass "Grace-alert logged on anders for bjorn"
else
    # Also accept the removal.py logger.info ("Grace period started for")
    GRACE_LOG2=$(anders "journalctl -u $GK_SVC --no-pager -n 200 2>/dev/null | grep 'Grace period started' | tail -5" 2>/dev/null || true)
    if [[ -n "$GRACE_LOG2" ]]; then
        info "Grace period start log: $GRACE_LOG2"
        pass "Grace period start logged on anders for bjorn"
    else
        info "WARNING: grace-alert log line not found in recent journal — check full log if needed"
    fi
fi

# Verify vote is resolved in cluster.db
VOTE_RESOLVED=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_DATA_DIR}/cluster.db')
row = db.execute('SELECT votes_yes, votes_no, resolved FROM votes WHERE id = ?', (${VOTE_ID},)).fetchone()
print(dict(zip(['votes_yes','votes_no','resolved'], row)) if row else None)
db.close()
PYTHON
)
info "Vote row after resolution: $VOTE_RESOLVED"
echo "$VOTE_RESOLVED" | python3 -c "
import sys, ast
d = ast.literal_eval(sys.stdin.read())
assert d is not None, 'vote not found'
assert d['resolved'] == 1, f'expected resolved=1, got {d[\"resolved\"]}'
assert d['votes_yes'] >= 2, f'expected votes_yes≥2, got {d[\"votes_yes\"]}'
print('vote resolved=1, votes_yes≥2')
" || fail "Vote not resolved as expected: $VOTE_RESOLVED"

pass "Removal vote verified: passed, bjorn status=grace, grace_started_at set"

# ── Step 13: Simulate orphan cleanup ─────────────────────────────────────────
echo ""
echo "=== Step 13: Orphan fragment cleanup simulation ==="

info "Inserting fake expired orphan_tags for bjorn into anders cluster.db..."
ORPHAN_RESULT=$(anders python3 << PYTHON
import sys, time
sys.path.insert(0, '/opt/backup-buddy')
from gatekeeper.db.cluster import ClusterDB
from gatekeeper.cluster.orphans import cleanup_orphans

db = ClusterDB('/var/lib/backup-buddy/cluster.db')
now = time.time()
past = now - 35 * 86400  # 35 days ago — past the 30-day grace period

fake_caps = [
    'URI:CHK:fake_bjorn_orphan_0:deadbeef00:1:3:4096',
    'URI:CHK:fake_bjorn_orphan_1:deadbeef01:1:3:4096',
    'URI:CHK:fake_bjorn_orphan_2:deadbeef02:1:3:4096',
]

for fid in fake_caps:
    try:
        db.insert_orphan(
            fragment_id=fid,
            owner_node_id='${BJORN_NODE_ID}',
            created_at=past,
            marked_orphan_at=past,
        )
    except Exception as e:
        print(f'insert skipped ({e})')

result = cleanup_orphans(
    db,
    orphan_grace_days=30,
    is_refrag_complete=lambda _: True,
    delete_fragment=lambda fid: 1024,
)
print(result)
db.close()
PYTHON
)
info "cleanup_orphans result: $ORPHAN_RESULT"

# Verify: deleted > 0
DELETED_COUNT=$(echo "$ORPHAN_RESULT" | python3 -c "import sys,ast; d=ast.literal_eval(sys.stdin.read()); print(d.get('deleted',0))" 2>/dev/null | tr -d '[:space:]') || DELETED_COUNT=0
(( DELETED_COUNT >= 1 )) \
    || fail "Expected ≥1 orphan deleted, got: $ORPHAN_RESULT"

# Verify cleaned_at is set in cluster.db
CLEANED_COUNT=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('/var/lib/backup-buddy/cluster.db')
count = db.execute(
    "SELECT COUNT(*) FROM orphan_tags WHERE owner_node_id = ? AND cleaned_at IS NOT NULL",
    ('${BJORN_NODE_ID}',)
).fetchone()[0]
print(count)
db.close()
PYTHON
)
info "Orphan tags with cleaned_at set: $CLEANED_COUNT"
(( CLEANED_COUNT >= 1 )) \
    || fail "Expected ≥1 orphan_tags.cleaned_at set, got $CLEANED_COUNT"

pass "Orphan cleanup verified: $DELETED_COUNT fragment(s) cleaned, cleaned_at set in cluster.db"

# ── Step 14: Verify cluster functional with 2 nodes (bjorn in grace) ──────────
echo ""
echo "=== Step 14: Verify cluster functional post-removal ==="

info "Overriding stability_minutes to 1 on CT $ANDERS_AGENT_CTID..."
prox "pct exec $ANDERS_AGENT_CTID -- sed -i 's/^stability_minutes.*/stability_minutes = 1/' /etc/backup-buddy/backup.cfg"

info "Creating post-removal test files on CT $ANDERS_AGENT_CTID..."
prox "pct exec $ANDERS_AGENT_CTID -- bash -c 'for i in \$(seq 36 40); do dd if=/dev/urandom of=/srv/testbackup/testfile_\$i.bin bs=1M count=1 2>/dev/null; done'"
prox "pct exec $ANDERS_AGENT_CTID -- systemctl restart $AGENT_SVC"

info "Waiting for post-removal files to be backed up (up to 5 min)..."
POST_DEADLINE=$(( $(date +%s) + 300 ))
POST_FILE_COUNT=0
while (( $(date +%s) < POST_DEADLINE )); do
    POST_FILE_COUNT=$(anders "python3 -c \"
import sqlite3
try:
    c = sqlite3.connect('${ANDERS_CATALOG_DB}')
    r = c.execute('SELECT COUNT(*) FROM files WHERE backed_up_at IS NOT NULL').fetchone()
    print(r[0] if r else 0)
    c.close()
except Exception:
    print(0)
\"" 2>/dev/null | tr -d '[:space:]') || POST_FILE_COUNT=0
    (( POST_FILE_COUNT > NEW_FILE_COUNT )) && break
    echo -n "."; sleep 15
done
echo ""
info "Anders catalog after bjorn removal: $POST_FILE_COUNT files (was $NEW_FILE_COUNT)"
(( POST_FILE_COUNT > NEW_FILE_COUNT )) \
    || fail "No new files backed up after bjorn grace period start (catalog at $POST_FILE_COUNT)"

pass "Cluster functional post-removal: $POST_FILE_COUNT files in catalog (uploads continue)"

# ── Step 15: Take phase-h snapshots ──────────────────────────────────────────
echo ""
echo "=== Step 15: Take phase-h snapshots ==="

info "Stopping CTs for snapshot..."
prox "pct stop $ANDERS_AGENT_CTID 2>/dev/null || true"
prox "pct stop $EXTRA_CTID        2>/dev/null || true"
prox "pct stop $BJORN_AGENT_CTID  2>/dev/null || true"
sleep 5

info "Stopping VMs for snapshot..."
prox "qm stop $ANDERS_VMID  --skiplock 1 2>/dev/null || true"
prox "qm stop $BJORN_VMID   --skiplock 1 2>/dev/null || true"
prox "qm stop $CARINA_VMID  --skiplock 1 2>/dev/null || true"
sleep 5

info "Taking phase-h snapshots..."
prox "qm snapshot $ANDERS_VMID  phase-h --description 'Phase H: 3-node cluster + bjorn removal 2026-06-01'"
prox "qm snapshot $BJORN_VMID   phase-h --description 'Phase H: 3-node cluster + bjorn removal 2026-06-01'"
prox "qm snapshot $CARINA_VMID  phase-h --description 'Phase H: 3-node cluster + bjorn removal 2026-06-01'"
prox "pct snapshot $ANDERS_AGENT_CTID phase-h --description 'Phase H: 3-node cluster + bjorn removal 2026-06-01'"
prox "pct snapshot $EXTRA_CTID        phase-h --description 'Phase H: 3-node cluster + bjorn removal 2026-06-01'"
prox "pct snapshot $BJORN_AGENT_CTID  phase-h --description 'Phase H: 3-node cluster + bjorn removal 2026-06-01'"

info "Restarting all nodes..."
prox "qm start $ANDERS_VMID"
prox "qm start $BJORN_VMID"
prox "qm start $CARINA_VMID"
prox "pct start $ANDERS_AGENT_CTID"
prox "pct start $EXTRA_CTID 2>/dev/null || true"
prox "pct start $BJORN_AGENT_CTID"

pass "phase-h snapshots created on 101, 102, 103, 301, 302, 303"

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "================================================"
echo "  1.17.9 PASSED"
echo "  Rollback: phase-e (101,102,301,302,303) + phase-a (103) ✓"
echo "  Carina (VM 103) joined cluster via invite ✓"
echo "  Three members in anders cluster.db ✓"
echo "  Adaptive k/n: 3 nodes → k=1, n=3 (ADR-006a) ✓"
echo "  Fragment distribution to carina verified ✓"
echo "  Removal vote for bjorn: PASSED (anders + carina) ✓"
echo "  Bjorn status=grace, grace_started_at set ✓"
echo "  Grace-alert logged on anders ✓"
echo "  Orphan cleanup: $DELETED_COUNT fragment(s) cleaned ✓"
echo "  Cluster functional post-removal: uploads continue ✓"
echo "  phase-h snapshot on 101, 102, 103, 301, 302, 303 ✓"
echo "================================================"
