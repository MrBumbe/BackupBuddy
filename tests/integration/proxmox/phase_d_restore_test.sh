#!/usr/bin/env bash
# Integration test 1.17.5: Phase D — file restore, folder restore, hash mismatch detection
#
# Prerequisites:
#   - Proxmox host reachable at 192.168.1.60
#   - phase-c snapshot exists on VM 101 (anders) and CT 301 (agent-anders-pc)
#   - SHA-256 reference for testfile_1.bin: 9d20cb463e6f14168eda326be0304ae0faac4003c2dc0a4dc45aafa84cb73124
#
# Run from the dev machine:
#   bash tests/integration/proxmox/phase_d_restore_test.sh

set -euo pipefail

PROXMOX="root@192.168.1.60"
ANDERS_LAN="10.99.0.11"
ANDERS_VMID=101
AGENT_CTID=301
SHA256_REF="9d20cb463e6f14168eda326be0304ae0faac4003c2dc0a4dc45aafa84cb73124"
CATALOG_DB="/var/lib/backup-buddy/catalog.db"
ANDERS_SVC="backup-buddy-gatekeeper"

SSH_OPTS="-q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 -o ServerAliveInterval=15"

anders() { ssh $SSH_OPTS -J "$PROXMOX" "root@$ANDERS_LAN" "$@"; }
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

wait_gatekeeper_ready() {
    local base_url="$1" deadline=$(( $(date +%s) + 120 ))
    echo -n "  Waiting for gatekeeper at $base_url..."
    while (( $(date +%s) < deadline )); do
        if anders "curl -sf --max-time 5 '${base_url}/api/status' -o /dev/null" 2>/dev/null; then
            echo " OK"; return 0
        fi
        echo -n "."; sleep 5
    done
    echo " TIMEOUT"; return 1
}

# Poll a restore job until status != running.
# Returns 0 (done/success) or 1 (failed/timeout).
# Also sets POLL_STATUS and POLL_RESP globals for inspection.
POLL_STATUS=""
POLL_RESP=""
poll_job() {
    local base_url="$1" job_id="$2" timeout="${3:-180}"
    local deadline=$(( $(date +%s) + timeout ))
    echo -n "  Polling job $job_id..."
    while (( $(date +%s) < deadline )); do
        POLL_RESP=$(anders "curl -sf --max-time 10 '${base_url}/api/restore/jobs/${job_id}'" 2>/dev/null) \
            || { echo -n "?"; sleep 4; continue; }
        POLL_STATUS=$(echo "$POLL_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null) \
            || { echo -n "?"; sleep 4; continue; }
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
        echo -n "."; sleep 4
    done
    POLL_STATUS="timeout"
    echo " TIMEOUT"
    return 1
}

# ── Main test ──────────────────────────────────────────────────────────────────

echo "=============================================="
echo "  1.17.5 — Phase D: File restore"
echo "=============================================="
echo ""

# ── Step 1: Rollback 101 and 301 to phase-c ───────────────────────────────────
echo "=== Step 1: Rollback to phase-c ==="
info "Stopping and rolling back anders (VM $ANDERS_VMID)..."
prox "qm stop $ANDERS_VMID --skiplock 1 2>/dev/null || true; sleep 3; qm rollback $ANDERS_VMID phase-c && qm start $ANDERS_VMID"

info "Stopping and rolling back agent-anders-pc (CT $AGENT_CTID)..."
prox "pct stop $AGENT_CTID 2>/dev/null || true; sleep 2; pct rollback $AGENT_CTID phase-c && pct start $AGENT_CTID"

wait_ssh_anders || fail "anders did not come up after rollback within 150 s"

# Guard: verify Tahoe shares are non-empty (snapshot taken on a running VM
# can produce 0-byte placeholder files — fail fast rather than waste time).
info "Checking Tahoe share sizes..."
ZERO_SHARES=$(anders "find /mnt/storage/shares -type f -size 0 2>/dev/null | wc -l" 2>/dev/null | tr -d '[:space:]')
TOTAL_SHARES=$(anders "find /mnt/storage/shares -type f 2>/dev/null | wc -l" 2>/dev/null | tr -d '[:space:]')
info "Shares total=$TOTAL_SHARES zero-byte=$ZERO_SHARES"
(( TOTAL_SHARES >= 50 )) || fail "Too few shares ($TOTAL_SHARES < 50) — snapshot may not include backup data"
(( ZERO_SHARES == 0 )) || fail "$ZERO_SHARES zero-byte share files found — snapshot was taken on running VM; redo with VM stopped"

pass "Both nodes rolled back and started"

# ── Resolve Tailscale IP ───────────────────────────────────────────────────────
echo ""
info "Resolving Tailscale IP for anders..."
ANDERS_TS=$(anders "tailscale ip -4 2>/dev/null | head -1")
[[ -n "$ANDERS_TS" ]] || fail "Could not resolve Anders Tailscale IP — is Tailscale running?"
BASE_URL="http://$ANDERS_TS:8080"
info "Anders Tailscale IP: $ANDERS_TS"
info "Gatekeeper URL:      $BASE_URL"

wait_gatekeeper_ready "$BASE_URL" || fail "Gatekeeper did not become ready within 120 s"
pass "Gatekeeper reachable"

# ── Step 2: Single file restore ───────────────────────────────────────────────
echo ""
echo "=== Step 2: Single file restore ==="
RESTORE_DEST="/tmp/restore_test/testfile_1.bin"
info "Restoring /srv/testbackup/testfile_1.bin → $RESTORE_DEST"

RESP=$(anders "curl -sf --max-time 15 -X POST '${BASE_URL}/api/restore/start/file' \
  -H 'Content-Type: application/json' \
  -d '{\"original_path\":\"/srv/testbackup/testfile_1.bin\",\"agent\":\"agent-anders-pc\",\"dest_path\":\"${RESTORE_DEST}\"}'" 2>/dev/null)
info "Start response: $RESP"

JOB_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null) \
    || fail "Could not extract job_id from response: $RESP"

poll_job "$BASE_URL" "$JOB_ID" 120 || fail "Single file restore job failed (status=$POLL_STATUS)"

# Verify SHA-256 of restored file
ACTUAL_SHA=$(anders "sha256sum '${RESTORE_DEST}' 2>/dev/null | awk '{print \$1}'" 2>/dev/null)
info "Restored SHA-256: $ACTUAL_SHA"
info "Expected SHA-256: $SHA256_REF"
[[ "$ACTUAL_SHA" == "$SHA256_REF" ]] \
    || fail "SHA-256 mismatch: expected $SHA256_REF got $ACTUAL_SHA"
pass "Single file restore: SHA-256 verified ✓"

# ── Step 3: Folder restore ────────────────────────────────────────────────────
echo ""
echo "=== Step 3: Folder restore ==="
FOLDER_DEST="/tmp/restore_folder"
info "Restoring /srv/testbackup → $FOLDER_DEST"

RESP=$(anders "curl -sf --max-time 15 -X POST '${BASE_URL}/api/restore/start/folder' \
  -H 'Content-Type: application/json' \
  -d '{\"folder_path\":\"/srv/testbackup\",\"agent\":\"agent-anders-pc\",\"dest_path\":\"${FOLDER_DEST}\"}'" 2>/dev/null)
info "Start response: $RESP"

JOB_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null) \
    || fail "Could not extract job_id from folder restore response: $RESP"

# Folder restore with 15 files can take a while
poll_job "$BASE_URL" "$JOB_ID" 600 || fail "Folder restore job failed (status=$POLL_STATUS)"

# Verify at least 15 testfile_*.bin files restored
FILE_COUNT=$(anders "find '${FOLDER_DEST}' -maxdepth 1 -name 'testfile_*.bin' 2>/dev/null | wc -l" 2>/dev/null | tr -d '[:space:]')
info "testfile_*.bin count in $FOLDER_DEST: $FILE_COUNT"
(( FILE_COUNT >= 15 )) || fail "Expected ≥15 testfile_*.bin in $FOLDER_DEST, found $FILE_COUNT"

# Spot-check SHA-256 of testfile_1.bin
FOLDER_SHA=$(anders "sha256sum '${FOLDER_DEST}/testfile_1.bin' 2>/dev/null | awk '{print \$1}'" 2>/dev/null)
info "Folder restore testfile_1.bin SHA-256: $FOLDER_SHA"
[[ "$FOLDER_SHA" == "$SHA256_REF" ]] \
    || fail "Folder restore SHA-256 mismatch for testfile_1.bin: expected $SHA256_REF got $FOLDER_SHA"
pass "Folder restore: $FILE_COUNT files, SHA-256 verified ✓"

# ── Step 4: Hash mismatch detection ──────────────────────────────────────────
echo ""
echo "=== Step 4: Hash mismatch detection ==="
info "Finding catalog record id for testfile_1.bin by sha256..."
FILE_ID=$(anders "sqlite3 '${CATALOG_DB}' \"SELECT id FROM files WHERE sha256='${SHA256_REF}' LIMIT 1\"" 2>/dev/null | tr -d '[:space:]')
[[ -n "$FILE_ID" ]] || fail "Could not find catalog record with SHA-256=$SHA256_REF"
info "Found catalog id=$FILE_ID — corrupting sha256..."

GARBAGE_SHA="aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"
anders "sqlite3 '${CATALOG_DB}' \"UPDATE files SET sha256='${GARBAGE_SHA}' WHERE id=${FILE_ID}\"" 2>/dev/null

# Verify corruption took effect
STORED=$(anders "sqlite3 '${CATALOG_DB}' \"SELECT sha256 FROM files WHERE id=${FILE_ID}\"" 2>/dev/null | tr -d '[:space:]')
[[ "$STORED" == "$GARBAGE_SHA" ]] || fail "sha256 corruption did not persist in catalog.db"
info "Catalog sha256 corrupted. Triggering restore..."

CORRUPT_DEST="/tmp/restore_integrity_test/testfile_1.bin"
RESP=$(anders "curl -sf --max-time 15 -X POST '${BASE_URL}/api/restore/start/file' \
  -H 'Content-Type: application/json' \
  -d '{\"original_path\":\"/srv/testbackup/testfile_1.bin\",\"agent\":\"agent-anders-pc\",\"dest_path\":\"${CORRUPT_DEST}\"}'" 2>/dev/null)
info "Start response: $RESP"

JOB_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null) \
    || fail "Could not extract job_id: $RESP"

# Expect the job to fail due to hash mismatch (_MAX_RETRIES = 1 → 2 attempts then error)
if poll_job "$BASE_URL" "$JOB_ID" 120; then
    fail "Expected restore to fail on hash mismatch but job reported success"
fi
[[ "$POLL_STATUS" == "failed" ]] \
    || fail "Expected job status=failed, got status=$POLL_STATUS (poll timed out?)"
info "Job failed as expected — checking gatekeeper log for integrity error..."

# Allow a few seconds for the log to flush
sleep 2
INTEGRITY_LINES=$(anders "journalctl -u ${ANDERS_SVC} --since '-5min' --no-pager 2>/dev/null \
    | grep -iE 'integrit|hash.mismatch|RestoreIntegrity' | tail -5" 2>/dev/null || true)
[[ -n "$INTEGRITY_LINES" ]] \
    || fail "No integrity-related log entry found in ${ANDERS_SVC} journal — verify manually"
info "Integrity log entries:"
echo "$INTEGRITY_LINES" | while IFS= read -r line; do info "  $line"; done

info "Reverting catalog sha256 corruption..."
anders "sqlite3 '${CATALOG_DB}' \"UPDATE files SET sha256='${SHA256_REF}' WHERE id=${FILE_ID}\"" 2>/dev/null
REVERTED=$(anders "sqlite3 '${CATALOG_DB}' \"SELECT sha256 FROM files WHERE id=${FILE_ID}\"" 2>/dev/null | tr -d '[:space:]')
[[ "$REVERTED" == "$SHA256_REF" ]] || fail "Failed to revert catalog sha256 — catalog may be in inconsistent state"
pass "Hash mismatch detection: RestoreIntegrityError triggered and logged, catalog reverted ✓"

# ── Step 5: Take phase-d snapshot on 101 ─────────────────────────────────────
echo ""
echo "=== Step 5: Take phase-d snapshot on VM $ANDERS_VMID ==="
info "Stopping anders for snapshot..."
prox "qm stop $ANDERS_VMID"
sleep 5
prox "qm snapshot $ANDERS_VMID phase-d --description 'Phase D: restore verified 2026-05-30'"
prox "qm start $ANDERS_VMID"
pass "phase-d snapshot created on VM $ANDERS_VMID"

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "=============================================="
echo "  1.17.5 PASSED"
echo "  Single file restore ✓"
echo "  Folder restore (≥15 files) ✓"
echo "  Hash mismatch detection ✓"
echo "  phase-d snapshot on 101 ✓"
echo "=============================================="
