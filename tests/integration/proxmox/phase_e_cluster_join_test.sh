#!/usr/bin/env bash
# Integration test 1.17.6: Phase E — Multi-node cluster join (bjorn joins anders)
#
# Prerequisites:
#   - Proxmox host reachable at 192.168.1.60
#   - anders (VM 101): phase-b snapshot exists
#   - bjorn (VM 102): phase-a snapshot exists (disk /dev/sdb raw, wizard mode)
#   - agent-anders-pc (CT 301): phase-c snapshot exists (15 test files, stability=1min)
#   - agent-bjorn-pc (CT 303): phase-a snapshot exists (agent installed, not started)
#   - CT 302: present on Proxmox (snapshot taken at end, no setup needed)
#   - Tailscale active on both VMs
#
# Run from the dev machine:
#   bash tests/integration/proxmox/phase_e_cluster_join_test.sh

set -euo pipefail

PROXMOX="root@192.168.1.60"
ANDERS_LAN="10.99.0.11"
BJORN_LAN="10.99.0.12"
ANDERS_VMID=101
BJORN_VMID=102
ANDERS_AGENT_CTID=301
BJORN_EXTRA_CTID=302
BJORN_AGENT_CTID=303

ANDERS_DATA_DIR="/var/lib/backup-buddy"
BJORN_DATA_DIR="/var/lib/backup-buddy"
BJORN_CFG="/etc/backup-buddy/gatekeeper.cfg"
BJORN_AGENT_CFG="/etc/backup-buddy/backup.cfg"
ANDERS_CATALOG_DB="/var/lib/backup-buddy/catalog.db"

GK_SVC="backup-buddy-gatekeeper"
AGENT_SVC="backup-buddy-agent"

ANDERS_NODE_NAME=""   # resolved dynamically from gatekeeper.cfg after rollback
BJORN_NODE_NAME="bjorn"

SSH_OPTS="-q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 -o ServerAliveInterval=15"

anders() { ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "$@"; }
bjorn()   { ssh $SSH_OPTS -J "$PROXMOX" "root@$BJORN_LAN"  "$@"; }
prox()    { ssh $SSH_OPTS "$PROXMOX" "$@"; }

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

wait_tahoe_ready() {
    local label="${1:-tahoe}" timeout="${2:-150}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "  Waiting for $label storage node to accept CHK uploads..."
    # Use 4 KB payload — above the 55-byte LIT threshold, forces real CHK encoding
    # which requires the storage node to be connected.
    while (( $(date +%s) < deadline )); do
        local resp
        resp=$(anders "dd if=/dev/urandom bs=4096 count=1 2>/dev/null | curl -sf --max-time 15 -X PUT 'http://127.0.0.1:3456/uri' --data-binary @- 2>/dev/null" 2>/dev/null || true)
        if [[ "$resp" == URI:CHK:* ]]; then
            echo " OK"
            return 0
        fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

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

# step_post: POST to a wizard URL via prox, expect HTTP 303.
step_post() {
    local step_url="$1"; shift
    local code
    code=$(prox "curl -sw '%{http_code}' -o /dev/null -X POST '$step_url' $*" | tail -c 3)
    info "$step_url → HTTP $code"
    [[ "$code" == "303" ]] || fail "Expected 303 from $step_url, got $code"
}

# ── Main test ──────────────────────────────────────────────────────────────────

echo "=============================================="
echo "  1.17.6 — Phase E: Multi-node cluster join"
echo "=============================================="
echo ""

# ── Step 1: Rollback all nodes ────────────────────────────────────────────────
echo "=== Step 1: Rollback all nodes ==="

info "Stopping CTs $ANDERS_AGENT_CTID and $BJORN_AGENT_CTID..."
prox "pct stop $ANDERS_AGENT_CTID 2>/dev/null || true; sleep 2; pct stop $BJORN_AGENT_CTID 2>/dev/null || true"

info "Stopping VMs $BJORN_VMID and $ANDERS_VMID..."
prox "qm stop $BJORN_VMID --skiplock 1 2>/dev/null || true; sleep 2; qm stop $ANDERS_VMID --skiplock 1 2>/dev/null || true; sleep 3"

info "Rolling back anders (VM $ANDERS_VMID) to phase-b..."
prox "qm rollback $ANDERS_VMID phase-b && qm start $ANDERS_VMID"

info "Rolling back bjorn (VM $BJORN_VMID) to phase-a..."
prox "qm rollback $BJORN_VMID phase-a && qm start $BJORN_VMID"

info "Rolling back agent-anders-pc (CT $ANDERS_AGENT_CTID) to phase-c..."
prox "pct rollback $ANDERS_AGENT_CTID phase-c && pct start $ANDERS_AGENT_CTID"

info "Rolling back agent-bjorn-pc (CT $BJORN_AGENT_CTID) to phase-a..."
prox "pct rollback $BJORN_AGENT_CTID phase-a && pct start $BJORN_AGENT_CTID"

wait_ssh "$ANDERS_LAN" "anders" || fail "Anders did not come up within 150 s"
wait_ssh "$BJORN_LAN"  "bjorn"  || fail "Bjorn did not come up within 150 s"
pass "All nodes rolled back and started"

# ── Step 2: Prep bjorn storage disk ──────────────────────────────────────────
echo ""
echo "=== Step 2: Prep bjorn storage disk ==="
info "Formatting /dev/sdb and mounting /mnt/storage on bjorn..."
bjorn "mkfs.ext4 -F /dev/sdb"
bjorn "mkdir -p /mnt/storage && mount /dev/sdb /mnt/storage"
bjorn "chown -R backupbuddy:backupbuddy /mnt/storage"
pass "Bjorn storage disk ready at /mnt/storage"

# ── Step 3: Resolve Tailscale IPs, wait for anders gatekeeper ─────────────────
echo ""
echo "=== Step 3: Resolve Tailscale IPs and wait for anders gatekeeper ==="

info "Resolving Tailscale IP for anders..."
ANDERS_TS=$(anders "tailscale ip -4 2>/dev/null | head -1")
[[ -n "$ANDERS_TS" ]] || fail "Could not resolve Anders Tailscale IP — is Tailscale running?"
ANDERS_TS_URL="http://$ANDERS_TS:8080"
info "Anders Tailscale IP: $ANDERS_TS  →  $ANDERS_TS_URL"

wait_gatekeeper "$ANDERS_TS_URL" "anders gatekeeper" 120 \
    || fail "Anders gatekeeper did not become ready within 120 s"

# Read node name dynamically — it may differ from hostname if wizard was run with a custom name
ANDERS_NODE_NAME=$(anders "python3 -c \"
import configparser
c = configparser.ConfigParser(allow_no_value=True, delimiters=('=',))
c.read('/etc/backup-buddy/gatekeeper.cfg')
print(c.get('node', 'name', fallback=''))
\"" 2>/dev/null | tr -d '[:space:]')
[[ -n "$ANDERS_NODE_NAME" ]] || fail "Could not read node name from anders' gatekeeper.cfg"
info "Anders node name: $ANDERS_NODE_NAME"

# Switch to adaptive profile so single-node uploads succeed (balanced requires
# shares.happy=5 distinct servers, which a single-node cluster can never satisfy).
info "Switching anders fragmentation profile to adaptive..."
anders "sed -i 's/^profile.*/profile = adaptive/' /etc/backup-buddy/gatekeeper.cfg"
anders "grep profile /etc/backup-buddy/gatekeeper.cfg"
anders "systemctl restart $GK_SVC"
sleep 3
wait_gatekeeper "$ANDERS_TS_URL" "anders gatekeeper (post-profile-change)" 60 \
    || fail "Anders gatekeeper did not recover after profile switch"

wait_tahoe_ready "anders Tahoe" 150 \
    || fail "Anders Tahoe storage node did not become ready within 150 s"
pass "Anders gatekeeper and Tahoe storage ready (profile=adaptive)"

# ── Step 4: Back up ≥10 files to anders ──────────────────────────────────────
echo ""
echo "=== Step 4: Back up ≥10 files to anders (via agent 301) ==="

# CT 301 (phase-c) backup.cfg has stability_minutes=30 (default).
# Override to 1 minute so uploads finish quickly during the test.
info "Overriding stability_minutes to 1 on CT $ANDERS_AGENT_CTID..."
prox "pct exec $ANDERS_AGENT_CTID -- sed -i 's/^stability_minutes.*/stability_minutes = 1/' /etc/backup-buddy/backup.cfg"

# Create new post-rollback testfiles to guarantee fresh uploads regardless of
# any local tracking state the agent may have retained from the previous run.
info "Creating new test files on CT $ANDERS_AGENT_CTID..."
prox "pct exec $ANDERS_AGENT_CTID -- bash -c 'mkdir -p /srv/testbackup && for i in \$(seq 16 25); do dd if=/dev/urandom of=/srv/testbackup/testfile_\$i.bin bs=1M count=1 2>/dev/null; done'"
info "Ensuring agent service is running on CT $ANDERS_AGENT_CTID..."
prox "pct exec $ANDERS_AGENT_CTID -- systemctl restart $AGENT_SVC"

# Wait up to 8 minutes for ≥10 files to appear in anders' catalog
info "Polling anders catalog.db for ≥10 backed-up files (up to 8 min)..."
CATALOG_DEADLINE=$(( $(date +%s) + 480 ))
FILE_COUNT=0
while (( $(date +%s) < CATALOG_DEADLINE )); do
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
    info "Catalog file count: $FILE_COUNT"
    (( FILE_COUNT >= 10 )) && break
    sleep 20
done
(( FILE_COUNT >= 10 )) || fail "Expected ≥10 backed-up files in anders catalog, found $FILE_COUNT after 8 min"
pass "Anders catalog has $FILE_COUNT backed-up files"

# ── Step 5: Generate invite code on anders ────────────────────────────────────
echo ""
echo "=== Step 5: Generate invite code on anders ==="
INVITE_JSON=$(anders "curl -sf --max-time 10 -X POST '${ANDERS_TS_URL}/api/buddies/invite'")
info "Response: $INVITE_JSON"
INVITE_CODE=$(echo "$INVITE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['code'])" 2>/dev/null) \
    || fail "Could not extract invite code from: $INVITE_JSON"
[[ -n "$INVITE_CODE" ]] || fail "Invite code is empty"
info "Invite code: $INVITE_CODE"
pass "Invite code generated"

# ── Step 6: Reset bjorn gatekeeper to clean setup mode ───────────────────────
echo ""
echo "=== Step 6: Reset bjorn to clean setup mode ==="
bjorn "systemctl stop $GK_SVC 2>/dev/null || true"
bjorn "systemctl reset-failed $GK_SVC 2>/dev/null || true"
bjorn "rm -rf '${BJORN_DATA_DIR:?}'/* && rm -f '$BJORN_CFG'"
bjorn "chown -R backupbuddy:backupbuddy '$BJORN_DATA_DIR' 2>/dev/null || true"
bjorn "chown -R backupbuddy:backupbuddy /mnt/storage 2>/dev/null || true"
bjorn "systemctl start $GK_SVC"
info "Bjorn restarted in setup mode"

BJORN_WIZARD_URL="http://$BJORN_LAN:8080"
wait_gatekeeper_prox "$BJORN_WIZARD_URL" "bjorn wizard" 90 \
    || fail "Bjorn wizard did not become reachable within 90 s"
pass "Bjorn wizard reachable at $BJORN_WIZARD_URL"

# ── Step 7: Drive bjorn wizard — join flow ────────────────────────────────────
echo ""
echo "=== Step 7: Drive bjorn wizard (join flow) ==="

step_post "$BJORN_WIZARD_URL/onboarding/step/1" \
    "-d 'role=join'"

step_post "$BJORN_WIZARD_URL/onboarding/join" \
    "--data-urlencode 'invite_code=$INVITE_CODE'" \
    "--data-urlencode 'gatekeeper_url=$ANDERS_TS_URL'"

step_post "$BJORN_WIZARD_URL/onboarding/step/2" \
    "-d 'node_name=$BJORN_NODE_NAME'" \
    "-d 'node_display_name=Bjorn+Test+Node'"

step_post "$BJORN_WIZARD_URL/onboarding/step/3" \
    "--data-urlencode 'storage_paths=/mnt/storage'" \
    "-d 'storage_quota_gb=50'"

step_post "$BJORN_WIZARD_URL/onboarding/step/4" \
    "-d 'profile=adaptive'"

# step/5 triggers the full join cascade: initiate_join → Tahoe start → gatekeeper.cfg
# Allow up to 3 minutes; do not fail on curl error since the response is a redirect.
info "Triggering finish cascade via step/5 (up to 180 s)..."
prox "curl -s -o /tmp/cascade_body_e.txt --max-time 180 \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    -X POST '$BJORN_WIZARD_URL/onboarding/step/5'" || true
info "Cascade HTTP call done"

sleep 3
bjorn "test -f '$BJORN_CFG'" || {
    BODY=$(prox "cat /tmp/cascade_body_e.txt 2>/dev/null || echo '(no body)'")
    fail "Cascade failed — gatekeeper.cfg not found on bjorn: $BODY"
}
pass "Wizard cascade complete — gatekeeper.cfg created on bjorn"

# ── Step 8: Restart bjorn into normal mode ────────────────────────────────────
echo ""
echo "=== Step 8: Restart bjorn into normal mode ==="
bjorn "systemctl reset-failed $GK_SVC 2>/dev/null || true"
bjorn "systemctl restart $GK_SVC"
sleep 5

info "Resolving Tailscale IP for bjorn..."
BJORN_TS=$(bjorn "tailscale ip -4 2>/dev/null | head -1")
[[ -n "$BJORN_TS" ]] || fail "Could not resolve Bjorn Tailscale IP — is Tailscale running?"
BJORN_TS_URL="http://$BJORN_TS:8080"
info "Bjorn Tailscale IP: $BJORN_TS  →  $BJORN_TS_URL"

# Bjorn in normal mode binds to Tailscale; poll via anders (both on Tailscale network)
wait_gatekeeper "$BJORN_TS_URL" "bjorn normal mode" 120 \
    || fail "Bjorn gatekeeper did not start in normal mode within 120 s"

BJORN_STATUS=$(anders "curl -sf --max-time 10 '${BJORN_TS_URL}/api/status'" 2>/dev/null) || true
info "Bjorn status: $BJORN_STATUS"
echo "$BJORN_STATUS" | python3 -c "import sys,json; s=json.load(sys.stdin); exit(0 if s.get('status')=='ok' else 1)" 2>/dev/null \
    || fail "Bjorn /api/status did not return {\"status\":\"ok\"}: $BJORN_STATUS"
pass "Bjorn running in normal mode"

# ── Step 9: Verify cluster membership on both nodes ───────────────────────────
echo ""
echo "=== Step 9: Verify cluster membership ==="

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
echo "$ANDERS_MEMBERS" | grep -q "$BJORN_NODE_NAME" \
    || fail "Anders does not see bjorn (${BJORN_NODE_NAME}) in cluster.db"
pass "Anders cluster.db: both members present"

BJORN_MEMBERS=$(bjorn python3 << PYTHON
import sqlite3
db = sqlite3.connect('${BJORN_DATA_DIR}/cluster.db')
rows = db.execute('SELECT node_id FROM members ORDER BY joined_at').fetchall()
for r in rows:
    print(r[0])
db.close()
PYTHON
)
info "Bjorn cluster members: $(echo "$BJORN_MEMBERS" | tr '\n' ' ')"
echo "$BJORN_MEMBERS" | grep -q "$BJORN_NODE_NAME" \
    || fail "Bjorn does not see himself (${BJORN_NODE_NAME}) in cluster.db"
echo "$BJORN_MEMBERS" | grep -q "$ANDERS_NODE_NAME" \
    || fail "Bjorn does not see anders (${ANDERS_NODE_NAME}) in cluster.db"
pass "Bjorn cluster.db: both members present"

# ── Step 10: Verify fragment distribution to bjorn's storage ──────────────────
echo ""
echo "=== Step 10: Verify fragment distribution to bjorn's storage ==="

BJORN_SHARES_BEFORE=$(bjorn "find /mnt/storage/shares -type f 2>/dev/null | wc -l" 2>/dev/null | tr -d '[:space:]' || echo 0)
info "Bjorn share count before new uploads: $BJORN_SHARES_BEFORE"

# Wait for Tahoe peer discovery so bjorn's storage node is known to anders' client
info "Waiting 45 s for Tahoe peer discovery..."
sleep 45

# Create new test files on CT 301 to trigger fresh fragment uploads after join
info "Creating post-join test files on CT $ANDERS_AGENT_CTID..."
prox "pct exec $ANDERS_AGENT_CTID -- bash -c 'for i in \$(seq 26 30); do dd if=/dev/urandom of=/srv/testbackup/testfile_\$i.bin bs=1M count=1 2>/dev/null; done'"
prox "pct exec $ANDERS_AGENT_CTID -- systemctl restart $AGENT_SVC"

# Wait for new files to appear in anders catalog
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
    || fail "No new files backed up after join (catalog still at $NEW_FILE_COUNT)"

# Allow share propagation to bjorn's storage node
info "Waiting 15 s for share propagation..."
sleep 15

BJORN_SHARES_AFTER=$(bjorn "find /mnt/storage/shares -type f 2>/dev/null | wc -l" 2>/dev/null | tr -d '[:space:]' || echo 0)
info "Bjorn share count after new uploads: $BJORN_SHARES_AFTER"
(( BJORN_SHARES_AFTER > BJORN_SHARES_BEFORE )) \
    || fail "No new shares on bjorn (before=$BJORN_SHARES_BEFORE after=$BJORN_SHARES_AFTER)"
pass "Fragment distribution verified (bjorn shares: $BJORN_SHARES_BEFORE → $BJORN_SHARES_AFTER)"

# ── Step 11: Verify dashboards show 2 active members ─────────────────────────
echo ""
echo "=== Step 11: Verify both dashboards show 2 members ==="

ANDERS_MEMBER_COUNT=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_DATA_DIR}/cluster.db')
count = db.execute('SELECT COUNT(*) FROM members').fetchone()[0]
print(count)
db.close()
PYTHON
)
info "Anders cluster member count: $ANDERS_MEMBER_COUNT"
[[ "$ANDERS_MEMBER_COUNT" == "2" ]] \
    || fail "Anders should have 2 members, found $ANDERS_MEMBER_COUNT"

BJORN_MEMBER_COUNT=$(bjorn python3 << PYTHON
import sqlite3
db = sqlite3.connect('${BJORN_DATA_DIR}/cluster.db')
count = db.execute('SELECT COUNT(*) FROM members').fetchone()[0]
print(count)
db.close()
PYTHON
)
info "Bjorn cluster member count: $BJORN_MEMBER_COUNT"
[[ "$BJORN_MEMBER_COUNT" == "2" ]] \
    || fail "Bjorn should have 2 members, found $BJORN_MEMBER_COUNT"
pass "Both nodes show 2 cluster members"

# ── Step 12: Configure agent-bjorn-pc (CT 303) and verify registration ────────
echo ""
echo "=== Step 12: Configure agent-bjorn-pc (CT $BJORN_AGENT_CTID) ==="

# Read bjorn's agent_api token from gatekeeper.cfg
BJORN_AGENT_TOKEN=$(bjorn "python3 -c \"
import configparser
c = configparser.ConfigParser(allow_no_value=True, delimiters=('=',))
c.read('$BJORN_CFG')
print(c.get('agent_api', 'token', fallback=''))
\"" 2>/dev/null | tr -d '[:space:]')
[[ -n "$BJORN_AGENT_TOKEN" ]] || fail "Could not read agent_api token from bjorn's gatekeeper.cfg"
info "Bjorn agent_api token: (read, not shown)"

# Create test backup directory and files on CT 303
info "Creating test files on CT $BJORN_AGENT_CTID..."
prox "pct exec $BJORN_AGENT_CTID -- bash -c 'mkdir -p /srv/testbackup && for i in \$(seq 1 5); do dd if=/dev/urandom of=/srv/testbackup/testfile_bjorn_\$i.bin bs=512K count=1 2>/dev/null; done'"

# Write backup.cfg on CT 303
info "Writing backup.cfg on CT $BJORN_AGENT_CTID..."
BJORN_AGENT_CFG_CONTENT="[schedule]
full_scan = 24h
stability_minutes = 1

[backup]
/srv/testbackup

[gatekeeper]
url = http://${BJORN_LAN}:8081
token = ${BJORN_AGENT_TOKEN}
name = agent-bjorn-pc
"
# Write via here-string: prox writes to a temp file on the Proxmox host, then pushes into CT
printf '%s' "$BJORN_AGENT_CFG_CONTENT" | prox "cat - | pct exec $BJORN_AGENT_CTID -- tee /etc/backup-buddy/backup.cfg > /dev/null"
prox "pct exec $BJORN_AGENT_CTID -- chown backupbuddy:backupbuddy /etc/backup-buddy/backup.cfg"
prox "pct exec $BJORN_AGENT_CTID -- chmod 0600 /etc/backup-buddy/backup.cfg"

# Start agent service on CT 303
info "Starting agent service on CT $BJORN_AGENT_CTID..."
prox "pct exec $BJORN_AGENT_CTID -- systemctl start $AGENT_SVC"
sleep 5
prox "pct exec $BJORN_AGENT_CTID -- systemctl is-active $AGENT_SVC" \
    || fail "Agent service failed to start on CT $BJORN_AGENT_CTID"

# Wait up to 3 minutes for agent-bjorn-pc to appear in bjorn's catalog
info "Waiting for agent-bjorn-pc files to be backed up (up to 3 min)..."
AGENT303_DEADLINE=$(( $(date +%s) + 180 ))
BJORN_CATALOG_COUNT=0
while (( $(date +%s) < AGENT303_DEADLINE )); do
    BJORN_CATALOG_COUNT=$(bjorn "python3 -c \"
import sqlite3
try:
    c = sqlite3.connect('${BJORN_DATA_DIR}/catalog.db')
    r = c.execute('SELECT COUNT(*) FROM files WHERE backed_up_at IS NOT NULL').fetchone()
    print(r[0] if r else 0)
    c.close()
except Exception:
    print(0)
\"" 2>/dev/null | tr -d '[:space:]') || BJORN_CATALOG_COUNT=0
    (( BJORN_CATALOG_COUNT >= 1 )) && break
    echo -n "."; sleep 15
done
echo ""
(( BJORN_CATALOG_COUNT >= 1 )) \
    || fail "Agent-bjorn-pc did not register/back up any files within 3 min (catalog_count=$BJORN_CATALOG_COUNT)"
pass "Agent-bjorn-pc registered with bjorn: $BJORN_CATALOG_COUNT files in catalog"

# ── Step 13: Take phase-e snapshots ──────────────────────────────────────────
echo ""
echo "=== Step 13: Take phase-e snapshots ==="

info "Stopping CTs for snapshot..."
prox "pct stop $ANDERS_AGENT_CTID 2>/dev/null || true"
prox "pct stop $BJORN_EXTRA_CTID  2>/dev/null || true"
prox "pct stop $BJORN_AGENT_CTID  2>/dev/null || true"
sleep 5

info "Stopping VMs for snapshot..."
prox "qm stop $ANDERS_VMID --skiplock 1 2>/dev/null || true"
prox "qm stop $BJORN_VMID  --skiplock 1 2>/dev/null || true"
sleep 5

info "Taking snapshots..."
prox "qm snapshot $ANDERS_VMID phase-e --description 'Phase E: cluster join verified 2026-05-31'"
prox "qm snapshot $BJORN_VMID  phase-e --description 'Phase E: cluster join verified 2026-05-31'"
prox "pct snapshot $ANDERS_AGENT_CTID phase-e --description 'Phase E: cluster join verified 2026-05-31'"
prox "pct snapshot $BJORN_EXTRA_CTID  phase-e --description 'Phase E: cluster join verified 2026-05-31'"
prox "pct snapshot $BJORN_AGENT_CTID  phase-e --description 'Phase E: cluster join verified 2026-05-31'"

info "Restarting all nodes..."
prox "qm start $ANDERS_VMID"
prox "qm start $BJORN_VMID"
prox "pct start $ANDERS_AGENT_CTID"
prox "pct start $BJORN_EXTRA_CTID 2>/dev/null || true"
prox "pct start $BJORN_AGENT_CTID"

pass "phase-e snapshots created on 101, 102, 301, 302, 303"

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  1.17.6 PASSED"
echo "  Rollback: anders phase-b, bjorn phase-a ✓"
echo "  Backup ≥10 files to anders ✓"
echo "  Bjorn joined cluster via invite code ✓"
echo "  cluster.db consistent on both nodes ✓"
echo "  Fragments distributed to bjorn's storage ✓"
echo "  Both dashboards: 2 active members ✓"
echo "  agent-bjorn-pc (CT 303) registered ✓"
echo "  phase-e snapshot on 101, 102, 301, 302, 303 ✓"
echo "=============================================="
