#!/usr/bin/env bash
# Integration test 1.17.13: Phase K — Cross-gatekeeper vote propagation
#
# Prerequisites:
#   - Proxmox host reachable at 192.168.1.60
#   - phase-h snapshots present on VMs 101, 102, 103 and CTs 301, 302, 303
#   - Phase-h cluster state: anders=active, bjorn=grace, carina=active
#
# Scenario:
#   - Anders proposes grace_extension (+7 days) for bjorn
#   - Vote is pushed to all peers via POST /api/cluster/sync/vote
#   - Carina's cluster.db receives the synced vote
#   - Anders casts yes from ANDERS_TS_URL (proposer path, result=pending)
#   - Carina casts yes from CARINA_TS_URL (her own GUI, non-proposer path)
#     → ballot forwarded to anders via POST /api/cluster/sync/ballot
#     → anders identifies carina by sender Tailscale IP (ADR-021)
#     → vote passes (2 of 2 eligible voters)
#   - apply_grace_extension: bjorn.grace_days increases by 7
#   - Carina has a local ballot record for already_voted UI tracking
#
# Run from repo root on the dev machine:
#   bash tests/integration/proxmox/phase_k_vote_propagation_test.sh

set -euo pipefail

PROXMOX="root@192.168.1.60"
ANDERS_LAN="10.99.0.11"
BJORN_LAN="10.99.0.12"
CARINA_LAN="10.99.0.13"
ANDERS_VMID=101
BJORN_VMID=102
CARINA_VMID=103
ANDERS_AGENT_CTID=301
EXTRA_CTID=302
BJORN_AGENT_CTID=303

ANDERS_DATA_DIR="/var/lib/backup-buddy"
BJORN_DATA_DIR="/var/lib/backup-buddy"
CARINA_DATA_DIR="/var/lib/backup-buddy"
ANDERS_CLUSTER_DB="${ANDERS_DATA_DIR}/cluster.db"
CARINA_CLUSTER_DB="${CARINA_DATA_DIR}/cluster.db"

GK_SVC="backup-buddy-gatekeeper"

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

_fix_tailscale() {
    local node="$1" lan="$2" cache_file="$3"
    local _ts_ip
    _ts_ip=$(ssh $SSH_OPTS -J "$PROXMOX" "root@$lan" "tailscale ip -4 2>/dev/null | head -1" 2>/dev/null || true)
    if [[ -n "$_ts_ip" ]]; then
        info "$node Tailscale already connected: $_ts_ip"; return 0
    fi
    info "$node Tailscale not connected — trying cached state restore..."
    if prox "test -s $cache_file"; then
        ssh $SSH_OPTS -J "$PROXMOX" "root@$lan" "systemctl stop tailscaled 2>/dev/null || true"
        sleep 2
        prox "cat $cache_file" | ssh $SSH_OPTS -J "$PROXMOX" "root@$lan" "tar -xzf - -C /var/lib 2>/dev/null"
        ssh $SSH_OPTS -J "$PROXMOX" "root@$lan" "systemctl start tailscaled 2>/dev/null || true"
        sleep 8
        _ts_ip=$(ssh $SSH_OPTS -J "$PROXMOX" "root@$lan" "tailscale ip -4 2>/dev/null | head -1" 2>/dev/null || true)
        if [[ -n "$_ts_ip" ]]; then info "$node reconnected via cached state: $_ts_ip"; return 0; fi
    fi
    info "$node still not connected — running tailscale up..."
    ssh $SSH_OPTS -J "$PROXMOX" "root@$lan" "nohup tailscale up --accept-routes >/tmp/ts_up.log 2>&1 &"
    sleep 6
    local _ts_url
    _ts_url=$(ssh $SSH_OPTS -J "$PROXMOX" "root@$lan" \
        "grep -Eo 'https://login\\.tailscale\\.com/[A-Za-z0-9/]+' /tmp/ts_up.log 2>/dev/null | head -1" || true)
    if [[ -n "$_ts_url" ]]; then
        echo ""
        echo "  ╔══════════════════════════════════════════════════════════════╗"
        echo "  ║  ACTION REQUIRED: $node Tailscale needs re-auth after rollback"
        echo "  ║  Open this URL in your browser: $_ts_url"
        echo "  ╚══════════════════════════════════════════════════════════════╝"
        echo ""
    fi
    echo -n "  Waiting for $node Tailscale IP (up to 3 min)..."
    local _dl=$(( $(date +%s) + 180 ))
    while (( $(date +%s) < _dl )); do
        _ts_ip=$(ssh $SSH_OPTS -J "$PROXMOX" "root@$lan" "tailscale ip -4 2>/dev/null | head -1" 2>/dev/null || true)
        [[ -n "$_ts_ip" ]] && { echo " OK ($_ts_ip)"; break; }
        echo -n "."; sleep 5
    done
    [[ -n "$_ts_ip" ]] || fail "Tailscale not authenticated on $node within 3 min"
}

# ── Main test ──────────────────────────────────────────────────────────────────

echo "================================================"
echo "  1.17.13 — Phase K: Cross-gatekeeper vote propagation"
echo "================================================"
echo ""

# ── Step 1: Rollback all nodes to phase-h ──────────────────────────────────────
echo "=== Step 1: Rollback all nodes to phase-h ==="

info "Stopping CTs..."
prox "pct stop $ANDERS_AGENT_CTID 2>/dev/null || true"
prox "pct stop $EXTRA_CTID        2>/dev/null || true"
prox "pct stop $BJORN_AGENT_CTID  2>/dev/null || true"
sleep 3

info "Stopping VMs..."
prox "qm stop $ANDERS_VMID  --skiplock 1 2>/dev/null || true"
prox "qm stop $BJORN_VMID   --skiplock 1 2>/dev/null || true"
prox "qm stop $CARINA_VMID  --skiplock 1 2>/dev/null || true"
sleep 5

info "Rolling back anders (VM $ANDERS_VMID) to phase-h..."
prox "qm rollback $ANDERS_VMID phase-h && qm start $ANDERS_VMID"
info "Rolling back bjorn (VM $BJORN_VMID) to phase-h..."
prox "qm rollback $BJORN_VMID phase-h && qm start $BJORN_VMID"
info "Rolling back carina (VM $CARINA_VMID) to phase-h..."
prox "qm rollback $CARINA_VMID phase-h && qm start $CARINA_VMID"

info "Rolling back CT $ANDERS_AGENT_CTID to phase-h..."
prox "pct rollback $ANDERS_AGENT_CTID phase-h && pct start $ANDERS_AGENT_CTID"
info "Rolling back CT $EXTRA_CTID to phase-h..."
prox "pct rollback $EXTRA_CTID phase-h && pct start $EXTRA_CTID 2>/dev/null || true"
info "Rolling back CT $BJORN_AGENT_CTID to phase-h..."
prox "pct rollback $BJORN_AGENT_CTID phase-h && pct start $BJORN_AGENT_CTID"

wait_ssh "$ANDERS_LAN" "anders" || fail "Anders did not come up within 150 s"
wait_ssh "$BJORN_LAN"  "bjorn"  || fail "Bjorn did not come up within 150 s"
wait_ssh "$CARINA_LAN" "carina" || fail "Carina did not come up within 150 s"

info "Mounting storage disks (sdb not auto-mounted after LVM snapshot rollback)..."
for _LAN in "$ANDERS_LAN" "$BJORN_LAN" "$CARINA_LAN"; do
    ssh $SSH_OPTS -J "$PROXMOX" "root@$_LAN" \
        "mountpoint -q /mnt/storage || mount /dev/sdb /mnt/storage"
done

# Sync current gatekeeper code to all VMs (includes new cluster/sync.py)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
info "Syncing gatekeeper code to anders..."
tar -czf - -C "$REPO_ROOT" gatekeeper | ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "tar -xzf - -C /opt/backup-buddy/"
info "Syncing gatekeeper code to bjorn..."
tar -czf - -C "$REPO_ROOT" gatekeeper | ssh $SSH_OPTS -J "$PROXMOX" "root@$BJORN_LAN"  "tar -xzf - -C /opt/backup-buddy/"
info "Syncing gatekeeper code to carina..."
tar -czf - -C "$REPO_ROOT" gatekeeper | ssh $SSH_OPTS -J "$PROXMOX" "root@$CARINA_LAN" "tar -xzf - -C /opt/backup-buddy/"

info "Reinstalling requirements (fixes 0-byte venv files from LVM snapshot)..."
anders "cd /opt/backup-buddy && .venv/bin/pip install -q -r requirements.txt --force-reinstall 2>&1 | tail -3 && .venv/bin/pip install -q -e . --force-reinstall 2>&1 | tail -3 && .venv/bin/python -c 'from gatekeeper.cluster.sync import VoteSyncMessage, BallotSyncMessage; print(\"anders: OK\")'"
bjorn  "cd /opt/backup-buddy && .venv/bin/pip install -q -r requirements.txt --force-reinstall 2>&1 | tail -3 && .venv/bin/pip install -q -e . --force-reinstall 2>&1 | tail -3 && .venv/bin/python -c 'from gatekeeper.cluster.sync import VoteSyncMessage, BallotSyncMessage; print(\"bjorn: OK\")'"
carina "cd /opt/backup-buddy && .venv/bin/pip install -q -r requirements.txt --force-reinstall 2>&1 | tail -3 && .venv/bin/pip install -q -e . --force-reinstall 2>&1 | tail -3 && .venv/bin/python -c 'from gatekeeper.cluster.sync import VoteSyncMessage, BallotSyncMessage; print(\"carina: OK\")'"

pass "All nodes rolled back to phase-h, new code synced"

# ── Step 2: Fix Tailscale and start gatekeepers ────────────────────────────────
echo ""
echo "=== Step 2: Fix Tailscale and restart gatekeepers ==="

info "Fixing Tailscale on all nodes..."
_fix_tailscale "anders" "$ANDERS_LAN" "/tmp/anders_tailscale_state.tar.gz"
_fix_tailscale "bjorn"  "$BJORN_LAN"  "/tmp/bjorn_tailscale_state.tar.gz"
_fix_tailscale "carina" "$CARINA_LAN" "/tmp/carina_tailscale_state.tar.gz"

info "Resolving Tailscale IPs..."
ANDERS_TS=$(anders "tailscale ip -4 2>/dev/null | head -1" | tr -d '[:space:]')
BJORN_TS=$(bjorn   "tailscale ip -4 2>/dev/null | head -1" | tr -d '[:space:]')
CARINA_TS=$(carina "tailscale ip -4 2>/dev/null | head -1" | tr -d '[:space:]')
[[ -n "$ANDERS_TS" ]] || fail "Could not resolve Anders Tailscale IP"
[[ -n "$BJORN_TS"  ]] || fail "Could not resolve Bjorn Tailscale IP"
[[ -n "$CARINA_TS" ]] || fail "Could not resolve Carina Tailscale IP"
ANDERS_TS_URL="http://$ANDERS_TS:8080"
BJORN_TS_URL="http://$BJORN_TS:8080"
CARINA_TS_URL="http://$CARINA_TS:8080"
info "Anders TS: $ANDERS_TS → $ANDERS_TS_URL"
info "Bjorn  TS: $BJORN_TS  → $BJORN_TS_URL"
info "Carina TS: $CARINA_TS → $CARINA_TS_URL"

info "Fixing peer tailscale_hostname entries (snapshot stores node names, not IPs)..."
anders "python3 << 'PYEOF'
import sqlite3
db = sqlite3.connect('${ANDERS_DATA_DIR}/cluster.db')
db.execute('UPDATE members SET tailscale_hostname=? WHERE node_id=?', ('${BJORN_TS}',  'bjorn'))
db.execute('UPDATE members SET tailscale_hostname=? WHERE node_id=?', ('${CARINA_TS}', 'carina'))
db.commit()
db.close()
print('anders cluster.db patched')
PYEOF"
bjorn "python3 << 'PYEOF'
import sqlite3
db = sqlite3.connect('${BJORN_DATA_DIR}/cluster.db')
db.execute('UPDATE members SET tailscale_hostname=? WHERE node_id=?', ('${ANDERS_TS}', 'anders'))
db.execute('UPDATE members SET tailscale_hostname=? WHERE node_id=?', ('${CARINA_TS}', 'carina'))
db.commit()
db.close()
print('bjorn cluster.db patched')
PYEOF"
carina "python3 << 'PYEOF'
import sqlite3
db = sqlite3.connect('${CARINA_DATA_DIR}/cluster.db')
db.execute('UPDATE members SET tailscale_hostname=? WHERE node_id=?', ('${ANDERS_TS}', 'anders'))
db.execute('UPDATE members SET tailscale_hostname=? WHERE node_id=?', ('${BJORN_TS}',  'bjorn'))
db.commit()
db.close()
print('carina cluster.db patched')
PYEOF"

info "Restarting gatekeepers with new code..."
anders "systemctl reset-failed $GK_SVC 2>/dev/null || true && systemctl restart $GK_SVC"
bjorn  "systemctl reset-failed $GK_SVC 2>/dev/null || true && systemctl restart $GK_SVC"
carina "systemctl reset-failed $GK_SVC 2>/dev/null || true && systemctl restart $GK_SVC"
sleep 8

wait_gatekeeper "$ANDERS_TS_URL" "anders gatekeeper" 120 || fail "Anders gatekeeper did not start"
wait_gatekeeper "$CARINA_TS_URL" "carina gatekeeper" 120 || fail "Carina gatekeeper did not start"

pass "All gatekeepers running with new sync code"

# ── Step 3: Verify phase-h cluster state ───────────────────────────────────────
echo ""
echo "=== Step 3: Verify phase-h cluster state ==="

BJORN_STATUS_ROW=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_CLUSTER_DB}')
row = db.execute(
    "SELECT node_id, status, grace_days FROM members WHERE node_id LIKE '%bjorn%'"
).fetchone()
print(list(row) if row else None)
db.close()
PYTHON
)
info "Bjorn member row: $BJORN_STATUS_ROW"
echo "$BJORN_STATUS_ROW" | python3 -c "
import sys, ast
row = ast.literal_eval(sys.stdin.read())
assert row is not None
_, status, _ = row
assert status == 'grace', f'expected bjorn status=grace, got {status}'
print('OK: bjorn is in grace status')
" || fail "Phase-h state mismatch: bjorn not in grace — did you rollback to the right snapshot?"

BJORN_NODE_ID=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_CLUSTER_DB}')
row = db.execute("SELECT node_id FROM members WHERE node_id LIKE '%bjorn%' LIMIT 1").fetchone()
print(row[0] if row else '')
db.close()
PYTHON
)
BJORN_NODE_ID=$(echo "$BJORN_NODE_ID" | tr -d '[:space:]')
[[ -n "$BJORN_NODE_ID" ]] || fail "Could not find bjorn node_id in anders cluster.db"
info "Bjorn node_id: $BJORN_NODE_ID"

BJORN_GRACE_DAYS_BEFORE=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_CLUSTER_DB}')
row = db.execute(
    "SELECT grace_days FROM members WHERE node_id = ?", ('${BJORN_NODE_ID}',)
).fetchone()
print(row[0] if row else 0)
db.close()
PYTHON
)
BJORN_GRACE_DAYS_BEFORE=$(echo "$BJORN_GRACE_DAYS_BEFORE" | tr -d '[:space:]')
info "Bjorn initial grace_days: $BJORN_GRACE_DAYS_BEFORE"

pass "Phase-h state verified: bjorn in grace (grace_days=${BJORN_GRACE_DAYS_BEFORE})"

# ── Step 4: Anders proposes grace_extension (+7 days) for bjorn ────────────────
echo ""
echo "=== Step 4: Anders proposes grace_extension (+7 days) for bjorn ==="

GRACE_EXTEND_JSON=$(anders "curl -sf --max-time 10 -X POST '${ANDERS_TS_URL}/api/buddies/grace-extend' \
    -H 'Content-Type: application/json' \
    -d '{\"target_node_id\": \"${BJORN_NODE_ID}\", \"days\": 7}'")
info "Grace-extend response: $GRACE_EXTEND_JSON"
VOTE_ID=$(echo "$GRACE_EXTEND_JSON" | python3 -c \
    "import sys,json; print(json.load(sys.stdin)['vote_id'])" 2>/dev/null) \
    || fail "Could not extract vote_id from: $GRACE_EXTEND_JSON"
[[ -n "$VOTE_ID" ]] || fail "vote_id is empty"
info "Grace extension vote opened: vote_id=$VOTE_ID (target=$BJORN_NODE_ID, +7 days)"

# Verify vote in anders's cluster.db
VOTE_IN_ANDERS=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_CLUSTER_DB}')
row = db.execute(
    "SELECT id, vote_type, target_node_id, grace_extension_days, resolved FROM votes WHERE id = ?",
    (${VOTE_ID},)
).fetchone()
print(list(row) if row else None)
db.close()
PYTHON
)
info "Vote in anders cluster.db: $VOTE_IN_ANDERS"
echo "$VOTE_IN_ANDERS" | grep -q "grace_extension" \
    || fail "Vote not found or wrong type in anders cluster.db"

pass "Grace extension vote opened (vote_id=$VOTE_ID)"

# ── Step 5: Verify vote synced to carina's cluster.db ─────────────────────────
echo ""
echo "=== Step 5: Verify vote propagated to carina's cluster.db ==="

info "Polling carina's cluster.db for vote_id=$VOTE_ID (up to 15 s)..."
SYNC_DEADLINE=$(( $(date +%s) + 15 ))
VOTE_IN_CARINA="None"
while (( $(date +%s) < SYNC_DEADLINE )); do
    VOTE_IN_CARINA=$(carina python3 << PYTHON 2>/dev/null || echo "None"
import sqlite3
db = sqlite3.connect('${CARINA_CLUSTER_DB}')
row = db.execute(
    'SELECT id, vote_type, target_node_id FROM votes WHERE id = ?',
    (${VOTE_ID},)
).fetchone()
print(list(row) if row else 'None')
db.close()
PYTHON
    )
    [[ "$VOTE_IN_CARINA" != "None" && -n "$VOTE_IN_CARINA" ]] && break
    echo -n "."; sleep 2
done
echo ""
info "Vote in carina cluster.db: $VOTE_IN_CARINA"
[[ "$VOTE_IN_CARINA" != "None" && -n "$VOTE_IN_CARINA" ]] \
    || fail "Vote $VOTE_ID not synced to carina within 15 s — sync push failed"
echo "$VOTE_IN_CARINA" | grep -q "grace_extension" \
    || fail "Vote type mismatch in carina cluster.db: $VOTE_IN_CARINA"

pass "Vote propagated to carina's cluster.db ✓"

# ── Step 6: Anders casts yes (proposer path) ───────────────────────────────────
echo ""
echo "=== Step 6: Anders casts yes on vote $VOTE_ID (proposer path) ==="

ANDERS_CAST=$(anders "curl -sf --max-time 10 -X POST '${ANDERS_TS_URL}/api/buddies/vote/${VOTE_ID}/cast' \
    -H 'Content-Type: application/json' \
    -d '{\"choice\": true}'")
info "Anders cast response: $ANDERS_CAST"
ANDERS_RESULT=$(echo "$ANDERS_CAST" | python3 -c \
    "import sys,json; print(json.load(sys.stdin)['result'])" 2>/dev/null) \
    || fail "Could not extract result from: $ANDERS_CAST"
[[ "$ANDERS_RESULT" == "pending" ]] \
    || fail "Expected result=pending after anders votes (1/2 eligible), got: $ANDERS_RESULT"

pass "Anders voted yes — result=pending (1 of 2 eligible voters) ✓"

# ── Step 7: Carina casts yes from HER OWN GUI (non-proposer path) ─────────────
echo ""
echo "=== Step 7: Carina casts yes from CARINA_TS_URL ==="
echo "    (non-proposer path: ballot forwarded to anders via sync/ballot)"

# curl runs FROM carina's SSH session → hits CARINA's FastAPI on Tailscale IP.
# carina's buddies.py detects anders is proposer, forwards ballot to anders.
# Anders's sync/ballot identifies carina by sender Tailscale IP (ADR-021).
CARINA_CAST=$(carina "curl -sf --max-time 30 -X POST '${CARINA_TS_URL}/api/buddies/vote/${VOTE_ID}/cast' \
    -H 'Content-Type: application/json' \
    -d '{\"choice\": true}'")
info "Carina cast response: $CARINA_CAST"
CARINA_RESULT=$(echo "$CARINA_CAST" | python3 -c \
    "import sys,json; print(json.load(sys.stdin)['result'])" 2>/dev/null) \
    || fail "Could not extract result from: $CARINA_CAST"
[[ "$CARINA_RESULT" == "passed" ]] \
    || fail "Expected result=passed after carina votes (2/2 eligible), got: $CARINA_RESULT"

pass "Carina voted yes via her own GUI — result=passed ✓"

# ── Step 8: Verify grace_days increased on bjorn ──────────────────────────────
echo ""
echo "=== Step 8: Verify bjorn grace_days increased by 7 ==="

BJORN_GRACE_DAYS_AFTER=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_CLUSTER_DB}')
row = db.execute(
    "SELECT grace_days FROM members WHERE node_id = ?", ('${BJORN_NODE_ID}',)
).fetchone()
print(row[0] if row else 0)
db.close()
PYTHON
)
BJORN_GRACE_DAYS_AFTER=$(echo "$BJORN_GRACE_DAYS_AFTER" | tr -d '[:space:]')
info "Bjorn grace_days: $BJORN_GRACE_DAYS_BEFORE → $BJORN_GRACE_DAYS_AFTER"

EXPECTED=$(( BJORN_GRACE_DAYS_BEFORE + 7 ))
[[ "$BJORN_GRACE_DAYS_AFTER" == "$EXPECTED" ]] \
    || fail "Expected bjorn grace_days=$EXPECTED, got $BJORN_GRACE_DAYS_AFTER"

# Verify vote fully resolved
VOTE_FINAL=$(anders python3 << PYTHON
import sqlite3
db = sqlite3.connect('${ANDERS_CLUSTER_DB}')
row = db.execute(
    'SELECT votes_yes, votes_no, resolved FROM votes WHERE id = ?', (${VOTE_ID},)
).fetchone()
print(list(row) if row else None)
db.close()
PYTHON
)
info "Vote final state: $VOTE_FINAL"
echo "$VOTE_FINAL" | python3 -c "
import sys, ast
row = ast.literal_eval(sys.stdin.read())
yes, no, resolved = row
assert resolved == 1, f'expected resolved=1, got {resolved}'
assert yes >= 2, f'expected votes_yes>=2, got {yes}'
print(f'OK: votes_yes={yes} resolved={resolved}')
" || fail "Vote state unexpected: $VOTE_FINAL"

pass "Bjorn grace_days: $BJORN_GRACE_DAYS_BEFORE → $BJORN_GRACE_DAYS_AFTER (+7) ✓"

# ── Step 9: Verify carina has local ballot record (already_voted tracking) ─────
echo ""
echo "=== Step 9: Verify carina has local ballot record ==="

CARINA_BALLOT=$(carina python3 << PYTHON
import sqlite3
db = sqlite3.connect('${CARINA_CLUSTER_DB}')
row = db.execute(
    'SELECT voter_node_id, choice FROM vote_ballots WHERE vote_id = ?',
    (${VOTE_ID},)
).fetchone()
print(list(row) if row else 'None')
db.close()
PYTHON
)
info "Carina vote_ballots for vote $VOTE_ID: $CARINA_BALLOT"
[[ "$CARINA_BALLOT" != "None" && -n "$CARINA_BALLOT" ]] \
    || fail "No ballot record in carina's cluster.db — already_voted tracking broken"
echo "$CARINA_BALLOT" | python3 -c "
import sys, ast
row = ast.literal_eval(sys.stdin.read())
voter, choice = row
assert choice == 1, f'expected choice=1 (yes), got {choice}'
print(f'OK: voter={voter} choice={choice}')
" || fail "Carina ballot record invalid: $CARINA_BALLOT"

pass "Carina local ballot record confirmed (already_voted tracking works) ✓"

# ── Step 10: Take phase-k snapshots ───────────────────────────────────────────
echo ""
echo "=== Step 10: Take phase-k snapshots ==="

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

prox "qm snapshot $ANDERS_VMID  phase-k --description 'Phase K: vote propagation 2026-06-02'"
prox "qm snapshot $BJORN_VMID   phase-k --description 'Phase K: vote propagation 2026-06-02'"
prox "qm snapshot $CARINA_VMID  phase-k --description 'Phase K: vote propagation 2026-06-02'"
prox "pct snapshot $ANDERS_AGENT_CTID phase-k --description 'Phase K: vote propagation 2026-06-02'"
prox "pct snapshot $EXTRA_CTID        phase-k --description 'Phase K: vote propagation 2026-06-02'"
prox "pct snapshot $BJORN_AGENT_CTID  phase-k --description 'Phase K: vote propagation 2026-06-02'"

info "Restarting all nodes..."
prox "qm start $ANDERS_VMID"
prox "qm start $BJORN_VMID"
prox "qm start $CARINA_VMID"
prox "pct start $ANDERS_AGENT_CTID"
prox "pct start $EXTRA_CTID 2>/dev/null || true"
prox "pct start $BJORN_AGENT_CTID"

pass "phase-k snapshots created on 101, 102, 103, 301, 302, 303"

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "================================================"
echo "  1.17.13 PASSED"
echo "  Rollback: phase-h (anders=active, bjorn=grace, carina=active) ✓"
echo "  Code: gatekeeper.cluster.sync deployed to all nodes ✓"
echo "  Vote sync (POST /api/cluster/sync/vote): grace_extension pushed to carina ✓"
echo "  Proposer path: anders voted yes → result=pending ✓"
echo "  Non-proposer path: carina voted yes from CARINA_TS_URL → result=passed ✓"
echo "  Ballot security: voter identity derived from sender Tailscale IP (ADR-021) ✓"
echo "  Grace extension applied: bjorn grace_days ${BJORN_GRACE_DAYS_BEFORE} → ${BJORN_GRACE_DAYS_AFTER} (+7) ✓"
echo "  Already-voted tracking: carina local ballot record confirmed ✓"
echo "  phase-k snapshots on 101, 102, 103, 301, 302, 303 ✓"
echo "================================================"
