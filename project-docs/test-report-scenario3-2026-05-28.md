# Integration Test Report — Scenario 3 (Disaster Recovery via Recovery Kit)
**Date:** 2026-05-28  
**Environment:** Proxmox VE 9.2.2 (gatekeeper-anders, VM 101)  
**BackupBuddy version:** commit 7040b731d  
**Tested by:** Automated via Claude Code (task 1.16.8)

---

## What was tested

Passphrase-based disaster recovery (Option A — Phase 1):

1. `recovery_kit.enc` is created correctly using `create_recovery_kit(passphrase, node_privkey, root_dir_cap)`
2. Emergency restore endpoint (`POST /api/restore/emergency`) accepts `recovery_kit_b64` + `passphrase`
3. Endpoint correctly extracts `root_dir_cap` from the encrypted kit
4. Catalog is reconstructed from Tahoe by traversal of `root_dir_cap`
5. Files are restorable from the reconstructed catalog with correct SHA-256

---

## Setup

- Pre-test snapshot created: `pre116test`
- New code deployed to `/opt/backupbuddy/gatekeeper/gui/routes/` and `/templates/`
- `recovery_kit.enc` created manually on VM using existing `node_privkey` + `root_dir_cap` with passphrase `test-passphrase-scenario3`
- Catalog baseline: **53 files** with SHA-256 values

---

## Test steps

1. Stopped gatekeeper service
2. Deleted `catalog.db`, `catalog.db-shm`, `catalog.db-wal`
3. Restarted gatekeeper service
4. Confirmed catalog empty: `GET /api/restore/catalog` → `{"total": 0}`
5. Called `POST /api/restore/emergency` with `recovery_kit_b64` + `passphrase`
6. Polled job until `status: done`
7. Verified catalog count and restored a test file

---

## Results

| Step | Result |
|------|--------|
| Passphrase accepted, root_dir_cap extracted from kit | ✅ OK |
| Catalog reconstruction job started | ✅ `job_id` returned immediately |
| Job completed | ✅ `status: done` (first poll, < 3s) |
| Files reconstructed | ✅ **53/53** (matches pre-wipe count) |
| `scenario1_testfile.txt` found in catalog | ✅ agent=anders-pc |
| File restored to `/tmp/restored_scenario3.txt` | ✅ `success: true` |
| SHA-256 verification | ✅ `ceaa7ca994f9cc59239fffec19d6e17d24e1e4534687eddfa651edcb467468b3` — exact match |

---

## Wrong-passphrase rejection test

Not run in this session, but the code path (`IntegrityError` → 400 with clear message) was inspected
and matches the AES-GCM tag verification in `recovery_kit.py`. Covered by existing unit test in
`tests/unit/test_lifeboat.py`.

---

## What was NOT tested in this run

| Item | Reason |
|------|--------|
| Wizard UI passphrase flow (step 5 form) | Gatekeeper was bootstrapped manually — wizard cannot re-run without wiping config. Template changes are deployed but not UI-tested. |
| Full VM destruction + fresh install + wizard | Deferred — the essential new code paths (passphrase decryption → cap extraction → reconstruction) are verified. The OS install + wizard flow is separate from the code under test. |
| Wrong passphrase → clear error message in GUI | API code path tested via code review; UI tested manually when GUI is exercised |

The full wizard flow (passphrase collected in Step 5 → `recovery_kit.enc` created) will be validated
as part of the first real-world deployment or task 1.16.10 (cluster join wizard test).

---

## Conclusion

The passphrase-based emergency restore path works end-to-end at the API level.
`recovery_kit.enc` decrypts correctly, `root_dir_cap` is extracted, catalog is rebuilt
from Tahoe, and files restore with correct SHA-256.

**Verdict: task 1.16.8 PASS.**
