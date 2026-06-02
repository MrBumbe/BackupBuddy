#!/usr/bin/env bash
# Integration test 1.17.12: Phase J — Introducer notice in dashboard
#
# Prerequisites:
#   - Proxmox host reachable at 192.168.1.60
#   - anders (VM 101): phase-h snapshot (three-node cluster)
#   - bjorn  (VM 102): phase-h snapshot
#   - carina (VM 103): phase-h snapshot
#   - Tailscale active on all VMs
#
# What this test verifies:
#   1. /api/dashboard on anders returns is_introducer: true
#   2. /api/dashboard on bjorn  returns is_introducer: false
#   3. /api/dashboard on carina returns is_introducer: false
#   4. anders HTML dashboard contains the introducer notice
#   5. bjorn/carina HTML dashboards do NOT contain the introducer notice
#   6. The "introducer" badge appears on anders's own member row in the API data
#
# Run from repo root on the dev machine:
#   bash tests/integration/proxmox/phase_j_introducer_notice_test.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PROXMOX="root@192.168.1.60"
ANDERS_LAN="10.99.0.11"
BJORN_LAN="10.99.0.12"
CARINA_LAN="10.99.0.13"
ANDERS_VMID=101
BJORN_VMID=102
CARINA_VMID=103

GK_SVC="backup-buddy-gatekeeper"
GK_PORT=8080

SSH_OPTS="-q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 -o ServerAliveInterval=15"

anders() { ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "$@"; }
bjorn()  { ssh $SSH_OPTS -J "$PROXMOX" "root@$BJORN_LAN"  "$@"; }
carina() { ssh $SSH_OPTS -J "$PROXMOX" "root@$CARINA_LAN" "$@"; }
prox()   { ssh $SSH_OPTS "$PROXMOX" "$@"; }

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }
info() { echo "  → $*"; }

wait_ssh() {
    local fn="$1" label="$2" deadline=$(( $(date +%s) + 150 ))
    echo -n "  Waiting for SSH on $label..."
    while (( $(date +%s) < deadline )); do
        if $fn "true" 2>/dev/null; then echo " OK"; return 0; fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

wait_gk() {
    local fn="$1" ts_ip="$2" label="$3" deadline=$(( $(date +%s) + 120 ))
    echo -n "  Waiting for gatekeeper on $label..."
    while (( $(date +%s) < deadline )); do
        if $fn "curl -sf --max-time 5 'http://${ts_ip}:${GK_PORT}/api/status' -o /dev/null" 2>/dev/null; then
            echo " OK"; return 0
        fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

echo "════════════════════════════════════════════════════════════"
echo " Phase J — Introducer Notice Dashboard Integration Test"
echo "════════════════════════════════════════════════════════════"

# ── Step 0: Restore phase-h snapshots on all three nodes ─────────────────────

echo ""
echo "Step 0 — Restore phase-h snapshots"
for VMID in $ANDERS_VMID $BJORN_VMID $CARINA_VMID; do
    prox "qm stop $VMID --skiplock 1 2>/dev/null || true"
done
sleep 3
for VMID in $ANDERS_VMID $BJORN_VMID $CARINA_VMID; do
    prox "qm rollback $VMID phase-h"
    prox "qm start $VMID"
done
info "Waiting 30s for VMs to boot..."
sleep 30

wait_ssh "anders" "anders" || fail "anders SSH timeout"
wait_ssh "bjorn"  "bjorn"  || fail "bjorn SSH timeout"
wait_ssh "carina" "carina" || fail "carina SSH timeout"
pass "all VMs SSH ready"

# Mount the storage disk on each node — after LVM rollback the disk exists but
# is not automounted (no fstab entry). The gatekeeper (User=backupbuddy) cannot
# start without a writable /mnt/storage.
for NODE_FN in anders bjorn carina; do
    LAN_VAR="${NODE_FN^^}_LAN"
    LAN_IP="${!LAN_VAR}"
    ssh $SSH_OPTS -J "$PROXMOX" "root@$LAN_IP" "mountpoint -q /mnt/storage || mount /dev/sdb /mnt/storage" 2>/dev/null
done
pass "storage disks mounted on all nodes"

# ── Step 1: Sync code to all three nodes ─────────────────────────────────────

echo ""
echo "Step 1 — Sync gatekeeper code to all nodes"

for NODE_FN in anders bjorn carina; do
    LAN_VAR="${NODE_FN^^}_LAN"
    LAN_IP="${!LAN_VAR}"
    info "Syncing to $NODE_FN..."
    tar -czf - -C "$REPO_ROOT" gatekeeper \
        | ssh $SSH_OPTS -J "$PROXMOX" "root@$LAN_IP" "tar -xzf - -C /opt/backup-buddy/"
done
pass "code synced to all nodes"

# Reinstall requirements on each node (LVM snapshot may have 0-byte venv files)
echo ""
echo "Step 1b — Reinstall venv on all nodes"
for NODE_FN in anders bjorn carina; do
    LAN_VAR="${NODE_FN^^}_LAN"
    LAN_IP="${!LAN_VAR}"
    info "Reinstalling venv on $NODE_FN..."
    ssh $SSH_OPTS -J "$PROXMOX" "root@$LAN_IP" \
        "cd /opt/backup-buddy && .venv/bin/pip install -q -r requirements.txt --force-reinstall 2>&1 | tail -1 && .venv/bin/pip install -q -e . --force-reinstall 2>&1 | tail -1"
done
pass "venv reinstalled on all nodes"

# ── Step 2: (Re)start gatekeeper service on all nodes ────────────────────────

echo ""
echo "Step 2 — Restart gatekeeper services"
anders "systemctl restart $GK_SVC 2>/dev/null || systemctl start $GK_SVC"
bjorn  "systemctl restart $GK_SVC 2>/dev/null || systemctl start $GK_SVC"
carina "systemctl restart $GK_SVC 2>/dev/null || systemctl start $GK_SVC"
sleep 5
pass "gatekeeper services started"

# Discover Tailscale IPs
ANDERS_TS_IP=$(anders "tailscale ip -4 2>/dev/null | head -1" | tr -d '[:space:]')
BJORN_TS_IP=$(bjorn   "tailscale ip -4 2>/dev/null | head -1" | tr -d '[:space:]')
CARINA_TS_IP=$(carina "tailscale ip -4 2>/dev/null | head -1" | tr -d '[:space:]')

[[ -n "$ANDERS_TS_IP" ]] || fail "Could not get anders Tailscale IP"
[[ -n "$BJORN_TS_IP"  ]] || fail "Could not get bjorn Tailscale IP"
[[ -n "$CARINA_TS_IP" ]] || fail "Could not get carina Tailscale IP"
info "anders TS: $ANDERS_TS_IP"
info "bjorn  TS: $BJORN_TS_IP"
info "carina TS: $CARINA_TS_IP"

wait_gk "anders" "$ANDERS_TS_IP" "anders" || fail "anders gatekeeper did not start"
wait_gk "bjorn"  "$BJORN_TS_IP"  "bjorn"  || fail "bjorn gatekeeper did not start"
wait_gk "carina" "$CARINA_TS_IP" "carina" || fail "carina gatekeeper did not start"
pass "all three gatekeepers running"

# ── Step 3: Verify is_introducer in /api/dashboard ───────────────────────────

echo ""
echo "Step 3 — Check is_introducer field in /api/dashboard"

check_dashboard_field() {
    local node_fn="$1" ts_ip="$2" expected="$3"
    local result
    result=$($node_fn "curl -sf --max-time 10 'http://${ts_ip}:${GK_PORT}/api/dashboard'" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(str(d.get('is_introducer','missing')).lower())" 2>/dev/null || echo "error")
    if [[ "$result" == "$expected" ]]; then
        pass "$node_fn: is_introducer = $result"
    else
        fail "$node_fn: expected is_introducer=$expected, got '$result'"
    fi
}

check_dashboard_field anders "$ANDERS_TS_IP" "true"
check_dashboard_field bjorn  "$BJORN_TS_IP"  "false"
check_dashboard_field carina "$CARINA_TS_IP" "false"

# ── Step 4: Verify introducer notice in HTML ──────────────────────────────────

echo ""
echo "Step 4 — Check introducer notice in HTML dashboard"

# The Jinja template renders <div class="notice-warning"> inside
# <div id="introducer-notice"> when is_introducer=true.  The same class name
# also appears inside the <script> block as a JS string literal.  We stop
# parsing at the first <script> tag so only the server-rendered HTML is checked.
check_notice_present() {
    local node_fn="$1" ts_ip="$2"
    $node_fn "curl -sf --max-time 10 'http://${ts_ip}:${GK_PORT}/' \
        | awk '/<script>/{exit} /class=\"notice-warning\"/{found=1} END{print (found ? \"present\" : \"absent\")}'"
}

ANDERS_NOTICE=$(check_notice_present "anders" "$ANDERS_TS_IP")
[[ "$ANDERS_NOTICE" == "present" ]] || fail "anders HTML: introducer notice not found (got '$ANDERS_NOTICE')"
pass "anders HTML: introducer notice present"

BJORN_NOTICE=$(check_notice_present "bjorn" "$BJORN_TS_IP")
[[ "$BJORN_NOTICE" == "absent" ]] || fail "bjorn HTML: notice-warning should NOT appear (got '$BJORN_NOTICE')"
pass "bjorn HTML: no introducer notice (correct)"

CARINA_NOTICE=$(check_notice_present "carina" "$CARINA_TS_IP")
[[ "$CARINA_NOTICE" == "absent" ]] || fail "carina HTML: notice-warning should NOT appear (got '$CARINA_NOTICE')"
pass "carina HTML: no introducer notice (correct)"

# ── Step 5: Verify introducer badge on anders's own member row ────────────────

echo ""
echo "Step 5 — Check introducer badge on anders member row"

ANDERS_MEMBERS=$(anders "curl -sf --max-time 10 'http://${ANDERS_TS_IP}:${GK_PORT}/api/dashboard'" \
    | python3 -c "
import sys, json
d = json.load(sys.stdin)
members = d.get('cluster', {}).get('members', [])
for m in members:
    if m.get('is_introducer'):
        print(m.get('display_name', ''))
" 2>/dev/null || echo "")

[[ -n "$ANDERS_MEMBERS" ]] || fail "No member with is_introducer=true found on anders dashboard"
info "Introducer member: $ANDERS_MEMBERS"
pass "anders: member with is_introducer=true present in dashboard data"

# Confirm bjorn/carina show no member with is_introducer=true
BJORN_INTRO=$(bjorn "curl -sf --max-time 10 'http://${BJORN_TS_IP}:${GK_PORT}/api/dashboard'" \
    | python3 -c "
import sys, json
d = json.load(sys.stdin)
members = d.get('cluster', {}).get('members', [])
print(any(m.get('is_introducer') for m in members))
" 2>/dev/null || echo "error")

[[ "$BJORN_INTRO" == "False" ]] || fail "bjorn: unexpected is_introducer=true in member list (got '$BJORN_INTRO')"
pass "bjorn: no member with is_introducer=true (correct)"

echo ""
echo "════════════════════════════════════════════════════════════"
echo " All phase-j tests passed."
echo "════════════════════════════════════════════════════════════"
