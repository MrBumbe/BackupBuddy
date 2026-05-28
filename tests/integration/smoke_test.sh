#!/usr/bin/env bash
# ============================================================================
# BackupBuddy smoke test — task 1.16.2
#
# Prerequisites (run on the test server — 192.168.1.50):
#   - BackupBuddy venv active (backupbuddy-gatekeeper on PATH)
#   - Tailscale running (required by the gatekeeper daemon)
#   - Python 3.11+ in the venv
#
# Port assignments:
#   GK1 GUI:       <TAILSCALE_IP>:8580
#   GK1 agent API: 192.168.1.50:8581
#   GK1 Tahoe:     127.0.0.1:8582
#   GK2 Tahoe:     127.0.0.1:8592
#
# Usage:
#   cd /path/to/BackupBuddy
#   bash tests/integration/smoke_test.sh
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

# ── Configuration ─────────────────────────────────────────────────────────────
SMOKE_DIR="${TMPDIR:-/tmp}/bb-smoke"
GK1_DATA="$SMOKE_DIR/gk1"
GK1_STORAGE="$SMOKE_DIR/gk1-storage"
GK2_DATA="$SMOKE_DIR/gk2"
GK2_STORAGE="$SMOKE_DIR/gk2-storage"
RESTORE_DIR="$SMOKE_DIR/restore"
GK1_KEY="$SMOKE_DIR/gk1-lifeboat.key"
GK2_KEY="$SMOKE_DIR/gk2-lifeboat.key"

LAN_IP="192.168.1.50"
GK1_GUI_PORT=8580
GK1_AGENT_PORT=8581
GK1_TAHOE_PORT=8582
GK2_TAHOE_PORT=8592

AGENT_TOKEN="smoke-token-$(head -c 8 /dev/urandom | base64 | tr -d '/+=')"
GK1_NODE_NAME="gk1-smoke"
GK2_NODE_NAME="gk2-smoke"

GK1_PID=""
GK2_PID=""

# ── Cleanup ───────────────────────────────────────────────────────────────────
cleanup() {
    local rc=$?
    echo ""
    echo "--- Cleanup (exit code: $rc) ---"
    if [[ -n "$GK1_PID" ]] && kill -0 "$GK1_PID" 2>/dev/null; then
        echo "Stopping GK1 (pid $GK1_PID)"
        kill "$GK1_PID" 2>/dev/null || true
        wait "$GK1_PID" 2>/dev/null || true
    fi
    if [[ -n "$GK2_PID" ]] && kill -0 "$GK2_PID" 2>/dev/null; then
        echo "Stopping GK2 Tahoe node (pid $GK2_PID)"
        kill "$GK2_PID" 2>/dev/null || true
        wait "$GK2_PID" 2>/dev/null || true
    fi
    echo "Removing smoke dir: $SMOKE_DIR"
    rm -rf "$SMOKE_DIR"
    echo "Cleanup done."
}
trap cleanup EXIT

# ── Helpers ───────────────────────────────────────────────────────────────────
wait_for_tcp() {
    local host="$1" port="$2" label="$3" timeout="${4:-30}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "Waiting for $label ($host:$port)..."
    while (( $(date +%s) < deadline )); do
        if bash -c "echo >/dev/tcp/$host/$port" 2>/dev/null; then
            echo " OK"
            return 0
        fi
        echo -n "."
        sleep 1
    done
    echo " TIMEOUT"
    return 1
}

wait_for_http_ok() {
    local url="$1" label="$2" timeout="${3:-60}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "Waiting for $label ($url)..."
    while (( $(date +%s) < deadline )); do
        if curl -sf --max-time 3 "$url" -o /dev/null 2>/dev/null; then
            echo " OK"
            return 0
        fi
        echo -n "."
        sleep 2
    done
    echo " TIMEOUT"
    return 1
}

# ── Step 1: Bootstrap GK1 (primary — creates introducer + root_dir.cap) ───────
echo "=== Step 1: Bootstrap GK1 ==="
mkdir -p "$SMOKE_DIR"

"$PYTHON" "$SCRIPT_DIR/bootstrap_gk.py" \
    --data-dir    "$GK1_DATA" \
    --storage-dir "$GK1_STORAGE" \
    --node-name   "$GK1_NODE_NAME" \
    --web-port    "$GK1_TAHOE_PORT" \
    --profile     test \
    --key-path    "$GK1_KEY" \
    2>&1 | tee "$SMOKE_DIR/bootstrap_gk1.log"

GK1_FURL_LINE=$(grep '^FURL=' "$SMOKE_DIR/bootstrap_gk1.log" | head -1 || true)
if [[ -z "$GK1_FURL_LINE" ]]; then
    echo "ERROR: bootstrap_gk.py did not output a FURL line" >&2
    exit 1
fi
GK1_FURL="${GK1_FURL_LINE#FURL=}"
echo "GK1 FURL obtained (length=${#GK1_FURL})"

# ── Step 2: Bootstrap GK2 (secondary — storage node only) ─────────────────────
echo ""
echo "=== Step 2: Bootstrap GK2 ==="
"$PYTHON" "$SCRIPT_DIR/bootstrap_gk.py" \
    --data-dir        "$GK2_DATA" \
    --storage-dir     "$GK2_STORAGE" \
    --node-name       "$GK2_NODE_NAME" \
    --web-port        "$GK2_TAHOE_PORT" \
    --profile         test \
    --key-path        "$GK2_KEY" \
    --introducer-furl "$GK1_FURL" \
    2>&1 | tee "$SMOKE_DIR/bootstrap_gk2.log"

echo "GK2 storage node directory created"

# ── Step 3: Write gatekeeper.cfg for GK1 ─────────────────────────────────────
echo ""
echo "=== Step 3: Write GK1 gatekeeper.cfg ==="

# NOTE: [storage-pool] uses hyphen and path = quota format (e.g. "/path = 1 GB")
cat > "$GK1_DATA/gatekeeper.cfg" <<EOF
[node]
name         = $GK1_NODE_NAME
display_name = Smoke Test GK1

[web]
port = $GK1_GUI_PORT

[tahoe]
run_introducer = true
tahoe_web_port = $GK1_TAHOE_PORT

[fragmentation]
profile = test

[agent_api]
enabled = true
port    = $GK1_AGENT_PORT
token   = $AGENT_TOKEN

[storage-pool]
$GK1_STORAGE = 1 GB

[lifeboat]
enabled = false
EOF

echo "gatekeeper.cfg written to $GK1_DATA/gatekeeper.cfg"

# ── Step 4: Start GK1 gatekeeper daemon ───────────────────────────────────────
echo ""
echo "=== Step 4: Start GK1 gatekeeper daemon ==="

BACKUPBUDDY_LIFEBOAT_KEY_PATH="$GK1_KEY" \
    backupbuddy-gatekeeper \
    --config    "$GK1_DATA/gatekeeper.cfg" \
    --data-dir  "$GK1_DATA" \
    --log-level INFO \
    >> "$SMOKE_DIR/gk1.log" 2>&1 &
GK1_PID=$!
echo "GK1 started (pid $GK1_PID)  log: $SMOKE_DIR/gk1.log"

# Wait for Tahoe gateway port (confirms the storage node is listening)
wait_for_tcp 127.0.0.1 "$GK1_TAHOE_PORT" "GK1 Tahoe gateway" 90

# Determine the Tailscale IP and wait for GK1 GUI to respond
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null | head -1 || echo "")
if [[ -z "$TAILSCALE_IP" ]]; then
    echo "ERROR: Tailscale is not running" >&2
    exit 1
fi
echo "Tailscale IP: $TAILSCALE_IP"

wait_for_http_ok "http://$TAILSCALE_IP:$GK1_GUI_PORT/api/status" "GK1 status" 90

GK1_STATUS=$(curl -sf "http://$TAILSCALE_IP:$GK1_GUI_PORT/api/status")
echo "GK1 status: $GK1_STATUS"
if ! echo "$GK1_STATUS" | grep -q '"status":"ok"'; then
    echo "ERROR: GK1 is not ready — check $SMOKE_DIR/gk1.log" >&2
    tail -30 "$SMOKE_DIR/gk1.log" >&2
    exit 1
fi

# ── Step 5: Start GK2 Tahoe storage node ──────────────────────────────────────
echo ""
echo "=== Step 5: Start GK2 Tahoe storage node ==="

"$PYTHON" "$SCRIPT_DIR/run_tahoe_node.py" \
    --basedir "$GK2_DATA/tahoe/storage_node" \
    >> "$SMOKE_DIR/gk2.log" 2>&1 &
GK2_PID=$!
echo "GK2 Tahoe node started (pid $GK2_PID)  log: $SMOKE_DIR/gk2.log"

wait_for_tcp 127.0.0.1 "$GK2_TAHOE_PORT" "GK2 Tahoe gateway" 90

# Give both storage nodes time to discover each other via the introducer
echo "Waiting 10s for peer discovery..."
sleep 10

# ── Step 6: Scenario 1 — backup + restore + verify ────────────────────────────
echo ""
echo "=== Step 6: Scenario 1 — backup + restore + verify ==="

"$PYTHON" "$SCRIPT_DIR/smoke_scenario_1.py" \
    --agent-api-url   "http://$LAN_IP:$GK1_AGENT_PORT" \
    --agent-api-token "$AGENT_TOKEN" \
    --agent-name      smoke-agent \
    --gk-data-dir     "$GK1_DATA" \
    --tahoe-url       "http://127.0.0.1:$GK1_TAHOE_PORT" \
    --lan-ip          "$LAN_IP" \
    --restore-dir     "$RESTORE_DIR"

echo ""
echo "=== Step 7: Scenario 3 — lifeboat bundle restore ==="

"$PYTHON" "$SCRIPT_DIR/smoke_scenario_3.py" \
    --gk-data-dir  "$GK1_DATA" \
    --key-path     "$GK1_KEY" \
    --tahoe-url    "http://127.0.0.1:$GK1_TAHOE_PORT" \
    --restore-dir  "$RESTORE_DIR"

echo ""
echo "=== Step 8: Scenario 7 — fragment corruption detection ==="

"$PYTHON" "$SCRIPT_DIR/smoke_scenario_7.py" \
    --gk-data-dir       "$GK1_DATA" \
    --gk1-storage-dir   "$GK1_STORAGE" \
    --gk2-storage-dir   "$GK2_STORAGE" \
    --tahoe-url         "http://127.0.0.1:$GK1_TAHOE_PORT" \
    --restore-dir       "$RESTORE_DIR"

echo ""
echo "======================================"
echo "  SMOKE TEST PASSED"
echo "======================================"
