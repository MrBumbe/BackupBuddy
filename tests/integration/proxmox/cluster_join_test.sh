#!/usr/bin/env bash
# Integration test 1.16.10: multi-gatekeeper cluster join flow
#
# Prerequisites:
#   - Proxmox host reachable at 192.168.1.60
#   - gatekeeper-anders (10.99.0.11): running in normal mode, Tailscale active
#   - gatekeeper-bjorn  (10.99.0.12): running (will be reset to setup mode)
#   - Updated source already deployed to /opt/backupbuddy on both VMs
#
# Run from the dev machine:
#   bash tests/integration/proxmox/cluster_join_test.sh

set -euo pipefail

PROXMOX="root@192.168.1.60"
ANDERS_LAN="10.99.0.11"
BJORN_LAN="10.99.0.12"
ANDERS_TS="100.68.15.102"
BJORN_TS="100.105.68.77"
ANDERS_TS_URL="http://$ANDERS_TS:8080"
BJORN_WIZARD_URL="http://$BJORN_LAN:8080"
BJORN_TS_URL="http://$BJORN_TS:8080"
ANDERS_AGENT_API="http://$ANDERS_LAN:8081"
ANDERS_TAHOE_URL="http://127.0.0.1:3456"
AGENT_TOKEN="backupbuddy-test-token-proxmox-2026"
BJORN_NODE_NAME="bjorn-rejoin"

SSH_OPTS="-q -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o ServerAliveInterval=15"

# ── SSH helpers using ProxyJump ────────────────────────────────────────────────
# -J proxmox routes the connection through the Proxmox host without double-quoting.
anders() { ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "$@"; }
bjorn()   { ssh $SSH_OPTS -J "$PROXMOX" "root@$BJORN_LAN"  "$@"; }
prox()    { ssh $SSH_OPTS               "$PROXMOX"          "$@"; }

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }
info() { echo "  → $*"; }

wait_for_http_prox() {
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

wait_for_http_anders() {
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

# ══════════════════════════════════════════════════════════════════════════════
echo "=============================================="
echo "  1.16.10 — Multi-gatekeeper cluster join"
echo "=============================================="
echo ""

# ── Step 1: Verify Anders is healthy ──────────────────────────────────────────
echo "=== Step 1: Verify Anders is healthy ==="
anders systemctl restart backupbuddy-gatekeeper
wait_for_http_anders "$ANDERS_TS_URL/api/status" "Anders after restart" 90
STATUS=$(anders curl -sf "$ANDERS_TS_URL/api/status")
info "Status: $STATUS"
echo "$STATUS" | grep -q '"status":"ok"' || fail "Anders not healthy"
pass "Anders healthy"

# ── Step 2: Remove stale bjorn-rejoin member from Anders' cluster.db ──────────
echo ""
echo "=== Step 2: Clean stale test entry from Anders' cluster.db ==="
# Python via stdin — no quoting issues
anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('/root/.backupbuddy/cluster.db')
db.execute("DELETE FROM members WHERE node_id='$BJORN_NODE_NAME'")
db.commit()
count = db.execute('SELECT COUNT(*) FROM members').fetchone()[0]
print('Members remaining:', count)
db.close()
PYTHON
pass "Stale entry removed"

# ── Step 3: Generate invite code on Anders ─────────────────────────────────────
echo ""
echo "=== Step 3: Generate invite code ==="
INVITE_JSON=$(anders curl -sf -X POST "$ANDERS_TS_URL/api/buddies/invite")
info "Response: $INVITE_JSON"
INVITE_CODE=$(echo "$INVITE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['code'])")
[[ -n "$INVITE_CODE" ]] || fail "Could not extract invite code"
info "Code: $INVITE_CODE"
pass "Invite code generated"

# ── Step 4: Reset Björn to setup mode ─────────────────────────────────────────
echo ""
echo "=== Step 4: Reset Björn to setup mode ==="
bjorn systemctl stop backupbuddy-gatekeeper
bjorn 'rm -rf /root/.backupbuddy.bak-1.16.10; cp -r /root/.backupbuddy /root/.backupbuddy.bak-1.16.10 && rm -rf /root/.backupbuddy'
bjorn systemctl start backupbuddy-gatekeeper
info "Björn restarted in setup mode"

# ── Step 5: Wait for Björn wizard ─────────────────────────────────────────────
echo ""
echo "=== Step 5: Wait for Björn wizard ==="
wait_for_http_prox "$BJORN_WIZARD_URL/onboarding/step/1" "Björn wizard" 90

# ── Step 6: Drive Björn wizard ────────────────────────────────────────────────
echo ""
echo "=== Step 6: Drive Björn wizard ==="

step_post() {
    local step_url="$1"; shift
    local code
    # tail -c 3: last 3 chars are always the HTTP code regardless of any prefix output
    code=$(prox curl -sw '%{http_code}' -o /dev/null -X POST "$step_url" "$@" | tail -c 3)
    info "$step_url → HTTP $code"
    [[ "$code" == "303" ]] || fail "Expected 303 from $step_url, got $code"
}

step_post "$BJORN_WIZARD_URL/onboarding/step/1" \
    -d "role=join"

step_post "$BJORN_WIZARD_URL/onboarding/join" \
    --data-urlencode "invite_code=$INVITE_CODE" \
    --data-urlencode "gatekeeper_url=$ANDERS_TS_URL"

step_post "$BJORN_WIZARD_URL/onboarding/step/2" \
    -d "node_name=$BJORN_NODE_NAME" \
    -d "node_display_name=Bjorn+Rejoin+Test"

step_post "$BJORN_WIZARD_URL/onboarding/step/3" \
    --data-urlencode "storage_paths=/mnt/storage" \
    -d "storage_quota_gb=50"

step_post "$BJORN_WIZARD_URL/onboarding/step/4" \
    -d "profile=adaptive"

# step/5 triggers the full cascade: initiate_join → Tahoe start → mkdir
# Allow up to 3 minutes.
info "Triggering finish cascade (up to 180s)..."
prox curl -s -o /tmp/cascade_body.txt \
    --max-time 180 -X POST \
    -H "Content-Type: application/x-www-form-urlencoded" \
    "$BJORN_WIZARD_URL/onboarding/step/5" || true
info "Cascade HTTP call done"

# Verify success by checking gatekeeper.cfg was created on Björn
sleep 2
bjorn 'test -f /root/.backupbuddy/gatekeeper.cfg' \
    || { BODY=$(prox cat /tmp/cascade_body.txt 2>/dev/null || echo "(no body)"); fail "Cascade failed — no gatekeeper.cfg on Björn: $BODY"; }
pass "Wizard cascade complete — gatekeeper.cfg created"

# ── Step 7: Trigger restart and wait for normal mode ──────────────────────────
echo ""
echo "=== Step 7: Restart Björn into normal mode ==="
prox curl -sf -X POST "$BJORN_WIZARD_URL/api/onboarding/restart" -o /dev/null || true
sleep 8

wait_for_http_anders "$BJORN_TS_URL/api/status" "Björn normal mode" 120
BJORN_STATUS=$(anders curl -sf "$BJORN_TS_URL/api/status")
info "Björn status: $BJORN_STATUS"
echo "$BJORN_STATUS" | grep -q '"status":"ok"' || fail "Björn not healthy after join"
pass "Björn running in normal mode"

# ── Step 8: Verify cluster membership on both nodes ───────────────────────────
echo ""
echo "=== Step 8: Verify cluster membership ==="

ANDERS_MEMBERS=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('/root/.backupbuddy/cluster.db')
rows = db.execute('SELECT node_id FROM members ORDER BY joined_at').fetchall()
for r in rows:
    print(r[0])
db.close()
PYTHON
)
info "Anders members: $(echo "$ANDERS_MEMBERS" | tr '\n' ' ')"
echo "$ANDERS_MEMBERS" | grep -q "gatekeeper-anders" || fail "Anders does not see himself"
echo "$ANDERS_MEMBERS" | grep -q "$BJORN_NODE_NAME"  || fail "Anders does not see Björn"
pass "Anders sees both members"

BJORN_MEMBERS=$(bjorn python3 << PYTHON
import sqlite3
db = sqlite3.connect('/root/.backupbuddy/cluster.db')
rows = db.execute('SELECT node_id FROM members ORDER BY joined_at').fetchall()
for r in rows:
    print(r[0])
db.close()
PYTHON
)
info "Björn members: $(echo "$BJORN_MEMBERS" | tr '\n' ' ')"
echo "$BJORN_MEMBERS" | grep -q "$BJORN_NODE_NAME"   || fail "Björn does not see himself"
echo "$BJORN_MEMBERS" | grep -q "gatekeeper-anders"  || fail "Björn does not see Anders"
pass "Björn sees both members"

# ── Step 9: Backup + restore + fragment distribution ──────────────────────────
echo ""
echo "=== Step 9: Backup + restore + fragment distribution ==="

# Count Björn's share files before upload (baseline)
BJORN_SHARES_BEFORE=$(bjorn 'find /mnt/storage/shares -type f 2>/dev/null | wc -l || echo 0')
info "Björn share files before upload: $BJORN_SHARES_BEFORE"

# Wait for Björn's Tahoe storage node to start and accept connections
info "Waiting for Björn's Tahoe gateway..."
deadline=$(( $(date +%s) + 90 ))
while (( $(date +%s) < deadline )); do
    if bjorn 'curl -sf --max-time 3 http://127.0.0.1:3456/ -o /dev/null' 2>/dev/null; then
        info "Björn's Tahoe gateway up"
        break
    fi
    echo -n "."; sleep 5
done
echo ""

# Extra wait for Björn's node to announce itself via introducer and be
# discovered by Anders' Tahoe client (introducer-based peer discovery).
info "Waiting 45s for Tahoe peer discovery..."
sleep 45

# Run smoke_scenario_1 from Anders' VM.
# The agent API requires a LAN-IP source — we use Björn's LAN IP (10.99.0.12)
# as local_address via httpx so the request appears to come from the LAN.
RESTORE_DIR="/tmp/bb-join-test-restore"
# lan-ip must be a local interface on Anders (10.99.0.11); the agent API
# validates that requests come from a non-loopback, non-Tailscale private IP.
SCENARIO_OUT=$(anders "cd /opt/backupbuddy && /opt/bb-venv/bin/python3 tests/integration/smoke_scenario_1.py --agent-api-url $ANDERS_AGENT_API --agent-api-token $AGENT_TOKEN --agent-name join-test-agent --gk-data-dir /root/.backupbuddy --tahoe-url $ANDERS_TAHOE_URL --lan-ip $ANDERS_LAN --restore-dir $RESTORE_DIR 2>&1")
info "Scenario 1 output:"
echo "$SCENARIO_OUT" | sed 's/^/    /'
echo "$SCENARIO_OUT" | grep -q "PASS" || fail "Backup/restore scenario failed"
pass "Backup + restore verified"

# Wait for share distribution to propagate to Björn's node
info "Waiting 15s for share propagation..."
sleep 15

BJORN_SHARES_AFTER=$(bjorn 'find /mnt/storage/shares -type f 2>/dev/null | wc -l || echo 0')
info "Björn share files after upload: $BJORN_SHARES_AFTER"
[[ "$BJORN_SHARES_AFTER" -gt "$BJORN_SHARES_BEFORE" ]] || \
    fail "No new shares on Björn (before=$BJORN_SHARES_BEFORE after=$BJORN_SHARES_AFTER)"
pass "Fragments distributed to Björn's storage (before=$BJORN_SHARES_BEFORE now=$BJORN_SHARES_AFTER)"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  1.16.10 PASSED"
echo "=============================================="
