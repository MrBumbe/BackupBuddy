# Integration Test Report — Proxmox Environment
**Date:** 2026-05-28  
**Environment:** Proxmox VE 9.2.2 on bare-metal laptop  
**BackupBuddy version:** 0.1.dev16125+gf432a6823  
**Tested by:** Automated via Claude Code (task 1.16.3)

---

## Environment

| Node | Role | IP (mgmt) | Status |
|------|------|-----------|--------|
| gatekeeper-anders | Primary gatekeeper + Tahoe storage | 10.99.0.11 | Active |
| gatekeeper-bjorn | Secondary gatekeeper + Tahoe storage | 10.99.0.12 | Active |
| gatekeeper-carina | Tertiary gatekeeper + Tahoe storage | 10.99.0.13 | Active |
| tahoe-introducer | Tahoe-LAFS introducer | 10.99.0.20 | Active |
| agent-anders-pc | Agent container (PC simulation) | 10.99.0.31 | Active |
| agent-anders-nas | Agent container (NAS simulation) | 10.99.0.32 | Active |
| agent-bjorn-pc | Agent container (PC simulation) | 10.99.0.33 | Active |

**Fragmentation profile:** `test` (k=1, n=2, happy=1)  
**Watcher stability:** 1 minute  
**Orphan grace days:** 1  
**Rebalance stability days:** 0  
**Hysteresis nodes:** 2

---

## Test Results

### Scenario 1 — Basic backup and restore ✅ PASS

**What was tested:** End-to-end backup and restore with SHA-256 hash verification.

**Steps:**
1. Created `/home/testuser/documents/scenario1_testfile.txt` on agent-anders-pc with known SHA-256 `ceaa7ca994f9cc59239fffec19d6e17d24e1e4534687eddfa651edcb467468b3`
2. Waited 90 seconds for watcher stability detection
3. Confirmed file appeared in catalog via `GET /api/restore/catalog`
4. Deleted the original file from the agent container
5. Triggered restore via `POST /api/restore/start/file` with `dest_path=/tmp/restored_scenario1.txt`
6. Polled `GET /api/restore/jobs/{id}` until `status: done`
7. Verified SHA-256 of restored file: `ceaa7ca994f9cc59239fffec19d6e17d24e1e4534687eddfa651edcb467468b3`

**Result:** SHA-256 matched exactly. Restore job completed with `success: true`.

Both agents (anders-pc, anders-nas) backed up 25 pre-seeded test files each before the scenario-specific file was created.

---

### Scenario 4 — Catalog reconstruction (call home) ✅ PASS

**What was tested:** Emergency reconstruction of `catalog.db` from the Tahoe file tree using only `root_dir.cap`.

**Steps:**
1. Noted all 26 file paths for agent anders-pc in catalog
2. Snapshotted gatekeeper-anders VM on Proxmox (`scenario4-pre-wipe`)
3. Stopped gatekeeper service, deleted `/root/.backupbuddy/catalog.db`
4. Restarted gatekeeper service
5. Confirmed catalog returned empty via `/api/restore/catalog`
6. Triggered `POST /api/restore/emergency` with `root_dir.cap`
7. Polled job until `status: done`
8. Verified reconstruction: 51 files reconstructed across both agents

**Result:** All 26 files for anders-pc reappeared in catalog. Subsequent restore of `scenario1_testfile.txt` from the reconstructed catalog returned correct SHA-256 `ceaa7ca994f9cc59239fffec19d6e17d24e1e4534687eddfa651edcb467468b3`.

**Note:** Reconstructed catalog entries have no SHA-256 stored (by design — the Tahoe metadata does not include plaintext hashes). The nightly verifier Layer 3 correctly logs "Hash verification skipped: sha256 unknown for reconstructed record" and counts these as warnings, not errors.

---

### Scenario 3 — Lifeboat bundle integrity ✅ PARTIAL PASS

**What was tested:** Lifeboat bundle creation, encryption, distribution, and decryption.

**Steps:**
1. Confirmed `lifeboat.enc` (34 KB) present on agent-anders-nas at `/etc/backup-buddy/lifeboat.enc`
2. Verified distribution log: `lifeboat_status` table shows 2 agents received bundle at 04:55 with `status=ok`
3. Called `POST /api/settings/lifeboat/test-bundle` — returned `ok: true`
4. Manually extracted bundle using gatekeeper's `lifeboat.key` (32-byte AES-256)
5. Confirmed bundle contains all required fields: `version`, `node_privkey`, `root_dir_cap`, `catalog_db_b64`, `gatekeeper_cfg`
6. Verified bundle from agent decrypts correctly using `lifeboat.key`

**Result:** Bundle creation, encryption, distribution, and decryption all work correctly.

**Limitation:** The full "destroy gatekeeper VM and restore from lifeboat" flow was not tested end-to-end. This requires:
- A user passphrase and `recovery_kit.enc` (Argon2id-encrypted)
- The test environment used a custom bootstrap that bypassed the onboarding wizard, so `recovery_kit.enc` was never created
- The passphrase-based recovery path (task 1.8.2) should be tested in a full wizard-based setup

---

### Scenario 7 — Nightly verification ✅ PASS (after bugfix)

**What was tested:** All four verification layers of the nightly job.

**Bug found and fixed:** `TahoeClient.check_cap()` used `?t=check` which the BackupBuddy Tahoe fork does not support (returns HTTP 400: "can only do t=info and t=json"). This caused Layer 2 to report all 51 catalog entries as inaccessible. Fixed in commit `f2c8bd5c7` — switched to `?t=json`.

**Layers after fix:**
| Layer | Description | Result |
|-------|-------------|--------|
| 1 | root_dir.cap accessible | ✅ ok=True |
| 2 | All catalog entries reachable in Tahoe | ✅ ok=True (51 entries verified) |
| 3 | Random test restores with SHA-256 | ✅ ok=True (3 files restored) |
| 4 | Lifeboat bundle age and decrypt | ✅ ok=True |

**Overall:** `overall_ok: True`

---

### Scenario 2 — Node offline during backup ✅ PASS

**What was tested:** Backup completes when one storage node is unavailable.

**Setup note:** All three Tahoe storage nodes had `tub.location = tcp:127.0.0.1:<port>` in `tahoe.cfg`, causing them to advertise localhost as their address. This meant only the local node was reachable from any gatekeeper. Fixed by updating `tub.location` to the correct management IP on all three nodes. After the fix, all 3 storage servers connected successfully.

**Steps:**
1. Confirmed 3/3 Tahoe storage servers connected (anders, bjorn, carina)
2. Stopped bjorn VM via Proxmox API (`qm stop 102`)
3. Created `/home/testuser/documents/scenario2_offline_test.bin` (10 MB random data) on agent-anders-pc — SHA-256 `9f68aa4023b72dab954368a5e0cec4e405fe1eabc8921aadfd11d391a5575a87`
4. Confirmed file appeared in catalog (backup succeeded with bjorn offline)
5. Restored file from catalog: SHA-256 matched exactly
6. Restarted bjorn VM (`qm start 102`)

**Result:** Backup and restore succeeded with 2/3 storage nodes. k=1, happy=1 profile correctly tolerates one node being unavailable.

---

### Scenario 5 — Hysteresis (hit-and-run node) ✅ PASS

**What was tested:** Re-fragmentation is NOT triggered when cluster size changes by fewer nodes than `hysteresis_nodes`.

**Config:** `hysteresis_nodes = 2`

**Steps (direct logic test via `check_and_run`):**
1. Seeded rebalance baseline at 1 node (anders) — `baseline_count=1`
2. Simulated gatekeeper-david joining (+1 node, distance=1)
3. Called `check_and_run` — result: `None` (skipped — within hysteresis)
4. Simulated david leaving (back to 1 node, distance=1 from baseline=2)
5. Called `check_and_run` — result: `None` (skipped — within hysteresis)

**Result:** Both node addition and removal with distance=1 were blocked by hysteresis=2. No re-fragmentation triggered.

---

### Scenario 6 — Orphan fragment cleanup ✅ PASS

**What was tested:** Orphan fragments are retained during grace period and deleted after it expires.

**Steps (direct logic test via `cleanup_orphans`):**
1. Marked 2 fake fragments from `gk-carina` as orphaned
2. Called `cleanup_orphans` with `grace_days=1`, fragments NOT expired:
   - Result: `eligible=2, deleted=0, skipped_grace=2` ✅
3. Backdated `marked_orphan_at` by 2 days (grace period expired)
4. Called `cleanup_orphans` with `grace_days=1`, fragments now expired:
   - Result: `eligible=2, deleted=2, skipped_grace=0` ✅
5. Confirmed `cleaned_at` timestamp set for both entries in `orphan_tags` table

**Result:** Grace period correctly protects orphans from premature deletion. Cleanup triggers after expiry with `is_refrag_complete=True`.

---

## Bugs Found and Fixed

### BUG-001 — `check_cap` uses unsupported Tahoe API endpoint ✅ Fixed

**File:** `gatekeeper/tahoe/client.py`  
**Commit:** `f2c8bd5c7`  
**Severity:** High — nightly verifier Layer 2 always failed, giving false negatives

**Root cause:** `TahoeClient.check_cap()` used `GET /uri/<cap>?t=check&output=json`. The BackupBuddy Tahoe fork only supports `t=info` and `t=json` on file URI endpoints; `t=check` returns HTTP 400.

**Fix:** Changed to `t=json`. File existence is confirmed by a 200 response with a `["filenode", {...}]` payload. Per-file share counts (`shares_good`, `shares_needed`) are not available via this endpoint — both are returned as `1` when accessible. Under-replication detection will require a dedicated fork endpoint in a future update.

---

## Infrastructure Notes (Not Code Changes)

These were configuration issues in the test environment that would also affect production deployments:

### tub.location must use real IP, not 127.0.0.1

Each Tahoe storage node's `tahoe.cfg` had `tub.location = tcp:127.0.0.1:<port>`. This causes the node to announce itself as localhost to the introducer, making it unreachable from other nodes. Every gatekeeper deployment must set `tub.location` to its actual IP address.

**Affected file:** `~/.backupbuddy/tahoe/storage_node/tahoe.cfg` — generated during bootstrap  
**Status:** Fixed manually on all 3 test VMs. Bootstrap/onboarding code should set this automatically (TODO for onboarding task).

### Agent process management

The agent process was started with `nohup ... & disown` and did not survive session termination. A systemd service unit for the agent is needed for production. This is a known gap (agent systemd is blocked by unprivileged LXC limitations in the test environment).

---

## What Was Not Tested

| Item | Reason |
|------|--------|
| Full Scenario 3 (destroy + restore VM from lifeboat) | Requires recovery_kit.enc (passphrase-based) — not created in custom bootstrap setup |
| Multi-gatekeeper cluster (proper invite/vote flow) | Gatekeepers were bootstrapped independently; cluster join wizard flow not tested |
| Fragment corruption detection (Scenario 7 advanced) | Requires placing a real fragment file, then corrupting it; deferred to manual testing |
| GUI interactions | Out of scope for this automated test run |
| Agent systemd service | Unprivileged LXC does not support systemd reliably |

---

## Conclusion

Core backup/restore functionality is working end-to-end. One significant bug was found and fixed (nightly verifier Layer 2). The test environment revealed a configuration requirement (`tub.location`) that must be addressed in the bootstrap/onboarding flow.

Scenarios 1, 2, 4, 5, 6, 7 all pass. Scenario 3 (lifeboat) passes at the bundle level but the full disaster-recovery flow requires a GUI-driven test with the passphrase-based recovery kit.

**Verdict: ready for cautious real-world deployment with the constraints noted above.**
