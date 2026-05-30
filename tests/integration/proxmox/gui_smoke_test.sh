#!/usr/bin/env bash
# Integration test 1.16.13: GUI smoke test — all pages accessible, no Tahoe internals in HTML
#
# Prerequisites:
#   - Proxmox host reachable at 192.168.1.60
#   - gatekeeper-anders (10.99.0.11): running in normal mode, Tailscale active
#
# Run from the dev machine:
#   bash tests/integration/proxmox/gui_smoke_test.sh

set -euo pipefail

PROXMOX="root@192.168.1.60"
ANDERS_LAN="10.99.0.11"
SSH_OPTS="-q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 -o ServerAliveInterval=15"

anders() { ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "$@"; }

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }
info() { echo "  → $*"; }

TMPDIR_REMOTE="/tmp/bb-gui-smoke-$$"

cleanup_remote() {
    anders "rm -rf '$TMPDIR_REMOTE'" 2>/dev/null || true
}

echo "=============================================="
echo "  1.16.13 — GUI smoke test"
echo "=============================================="
echo ""

# ── Resolve Tailscale IP ───────────────────────────────────────────────────────
ANDERS_TS=$(anders "tailscale ip -4 2>/dev/null | head -1")
[[ -n "$ANDERS_TS" ]] || fail "Could not resolve Anders Tailscale IP — is Tailscale running?"
ANDERS_TS_URL="http://$ANDERS_TS:8080"
info "Anders Tailscale IP: $ANDERS_TS"
info "GUI base URL: $ANDERS_TS_URL"

# ── Step 1: Verify gatekeeper is in normal mode ────────────────────────────────
echo ""
echo "=== Step 1: Verify normal mode ==="
STATUS=$(anders "curl -sf --max-time 10 '$ANDERS_TS_URL/api/status'" 2>/dev/null) \
    || fail "Could not reach $ANDERS_TS_URL/api/status — gatekeeper not running?"
info "Status response: $STATUS"
echo "$STATUS" | grep -q '"status":"ok"' || fail "Gatekeeper not in normal mode (status != ok)"
pass "Gatekeeper in normal mode"

# ── Step 2: Create temp dir on Anders ─────────────────────────────────────────
anders "mkdir -p '$TMPDIR_REMOTE'"

# ── Step 3: Fetch all GUI pages and check HTTP 200 ────────────────────────────
echo ""
echo "=== Step 2: Fetch GUI pages — check HTTP 200 ==="

PAGES=("/" "/restore" "/settings" "/buddies" "/agents")
ALL_OK=true

for page in "${PAGES[@]}"; do
    url="$ANDERS_TS_URL$page"
    # Slugify page name for file: replace / with _ (leading slash → empty prefix)
    slug=$(echo "$page" | tr '/' '_' | sed 's/^_//')
    [[ -z "$slug" ]] && slug="root"
    outfile="$TMPDIR_REMOTE/page_${slug}.html"

    code=$(anders "curl -sw '%{http_code}' -o '$outfile' --max-time 15 '$url'" 2>/dev/null | tail -c 3)
    if [[ "$code" == "200" ]]; then
        info "$page → HTTP $code"
    else
        echo "[FAIL] $page → HTTP $code (expected 200)" >&2
        ALL_OK=false
    fi
done

$ALL_OK || { info "Keeping $TMPDIR_REMOTE on Anders for inspection"; fail "One or more pages did not return HTTP 200"; }
pass "All pages returned HTTP 200"

# ── Step 4: Grep HTML bodies for Tahoe internals ──────────────────────────────
echo ""
echo "=== Step 3: Check for Tahoe internals in HTML ==="

# Patterns as an extended-regex alternation for a single grep pass.
# :cap is restricted to word-boundary context (pb://... style caps use :cap after node id).
# We check for known Tahoe cap suffixes: :cap, :ro, :rw
PATTERN='FURL|furl|pb://|storage_index|shares\.needed|tahoe:|URI:DIR2|URI:CHK|URI:LIT'

GREP_OUT=$(anders "grep -rn --include='*.html' -E '$PATTERN' '$TMPDIR_REMOTE' 2>/dev/null || true")

if [[ -n "$GREP_OUT" ]]; then
    echo ""
    echo "[FAIL] Tahoe internals found in rendered HTML:" >&2
    echo "$GREP_OUT" >&2
    echo ""
    info "Keeping $TMPDIR_REMOTE on Anders for inspection"
    fail "Tahoe internal strings leaked into GUI HTML"
fi
pass "No Tahoe internals found in any page HTML"

# ── Step 5: Cleanup temp dir ──────────────────────────────────────────────────
cleanup_remote

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  1.16.13 PASSED"
echo "=============================================="
