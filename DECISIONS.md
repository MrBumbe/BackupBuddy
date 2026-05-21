# DECISIONS.md

> Architecture Decision Records (ADRs) for BackupBuddy.
> Documents WHY we made key technical choices — not just what we chose.
> Claude Code must read relevant ADRs before implementing related features.
> Never override a decision here without discussion with the project owner.
> If a decision changes, update this file and note the superseded ADR.

---

## ADR-001 — Tahoe-LAFS as the base system

**Status:** Accepted

**Decision:** Fork Tahoe-LAFS as the foundation for BackupBuddy rather than
building erasure coding and distributed storage from scratch.

**Rationale:**
- Tahoe-LAFS provides battle-tested client-side encryption and erasure coding
- Storage nodes are zero-knowledge by design — they cannot read stored content
- The k-of-n erasure coding model maps directly to the buddy group use case
- Existing introducer mechanism handles node discovery without extra infrastructure
- Python codebase is accessible for modification and extension
- Licensed under GPL2+ which permits forking

**Consequences:**
- We inherit Tahoe-LAFS's dependency footprint (Twisted, zfec, etc.)
- We take on maintenance responsibility for the fork
- We must not expose Tahoe-LAFS internals (FURLs, caps, shares) in any user-facing UI
- Tahoe-LAFS terminology is internal only — all user-facing language is our own

**What Tahoe-LAFS handles:** erasure coding, encryption, fragment distribution,
introducer-based node discovery, capability URIs, servers-of-happiness check.

**What we build on top:** GUI, agent model, invite flow, lifeboat, monitoring,
adaptive fragmentation, onboarding, storage pool management, quota enforcement.

---

## ADR-002 — Tailscale as the network and security layer

**Status:** Accepted

**Decision:** Use Tailscale (WireGuard) as the exclusive network layer between
gatekeeper nodes. No direct TCP/IP connections between home networks.

**Rationale:**
- Eliminates the need for port forwarding — critical for homelab beginners
- WireGuard provides strong encryption between all nodes without configuration
- MagicDNS gives stable, human-readable hostnames (gatekeeper-anders.tailnet.ts.net)
- Subnet routing enables GUI access from remote devices without extra setup
- Tailscale ACLs can restrict which nodes communicate with which (future use)
- Tailscale handles NAT traversal transparently

**Consequences:**
- All cluster participants must have a Tailscale account
- Tailscale availability is a dependency (outage affects new node discovery,
  but existing connections between nodes may persist via direct WireGuard)
- Tailscale join and cluster join are two separate steps (Phase 1 decision)
- The gatekeeper GUI must bind exclusively to the Tailscale interface

**Not in scope:** we do not integrate with the Tailscale API for automated
device enrollment (Phase 1). Users handle Tailscale setup manually.

---

## ADR-003 — One gatekeeper per home network

**Status:** Accepted

**Decision:** Each participant runs exactly one gatekeeper node that acts as the
single outward-facing component for their entire home network.

**Rationale:**
- Simplifies Tailscale setup (one device per person in the tailnet)
- Agents inside the home network need no Tailscale configuration
- A single gatekeeper can pool storage from multiple local devices
- Clear separation: gatekeeper handles cluster concerns, agents handle local concerns
- Easier to reason about security boundaries (one external exposure point)

**Consequences:**
- The gatekeeper must run on always-on hardware (Raspberry Pi, NAS, small server)
- If the gatekeeper goes offline, all agents in that home network lose cluster access
- The lifeboat mechanism (ADR-007) is critical precisely because of this centralization
- A future hot-standby gatekeeper is a Phase 2 concern, not Phase 1

---

## ADR-004 — Agent model with backup.cfg

**Status:** Accepted

**Decision:** Each device to be backed up runs a lightweight agent. The agent
reads a local backup.cfg to determine what to back up. The gatekeeper cannot
browse the agent's filesystem.

**Rationale:**
- Privacy by default: the gatekeeper sees only what the agent explicitly sends
- Consistent with zero-knowledge design throughout the system
- backup.cfg is a simple, human-readable, version-controllable file
- Agents can be on any OS — they only need to run the agent process
- Separating backup scope (agent) from cluster coordination (gatekeeper) is clean

**Consequences:**
- The gatekeeper cannot initiate backups on behalf of an agent without the agent's cooperation
- Backup scope changes require editing backup.cfg on the agent, not in the GUI
- The GUI can show backup status per agent but cannot show or modify what is backed up
  (it can only display the backup.cfg contents if share_log = true is set)
- Folder-level granularity only in Phase 1 — no individual file selection

---

## ADR-005 — Transparent storage pool with per-path quotas

**Status:** Accepted

**Decision:** Fragment storage across multiple local paths is managed by a thin
scheduler in the gatekeeper, not by a kernel-level union mount (mergerfs) or
separate Tahoe storage nodes per path.

**Rationale:**
- No kernel module dependency — works on any Linux system including LXC containers
- Simple to reason about: fill the path with most free space first
- Hard quotas per path are enforced in software, not at the filesystem level
- Tahoe sees one storage directory — less complexity in the Tahoe config

**Consequences:**
- If the gatekeeper process crashes mid-write, a fragment may be orphaned in a pool path
  (handled by the orphan cleanup job — ADR-011)
- No automatic load balancing across pool paths based on I/O performance
- Pool path availability must be checked at startup and on each write

**Storage pool paths must always be excluded from backup scope.**
This exclusion is enforced in code and cannot be overridden by user configuration.
See SECURITY.md → Section 5.

---

## ADR-006 — Fragmentation profiles as user-facing abstraction

**Status:** Accepted

**Decision:** Expose four named profiles (Balanced, Secure, Paranoid, Adaptive) instead
of raw k/n parameters. Profiles map to Tahoe-LAFS shares.needed/happy/total values.

| Profile  | k | n  | Description |
|----------|---|----|-------------|
| Balanced | 3 | 5  | Balanced default |
| Secure   | 3 | 7  | Extra margin |
| Paranoid | 3 | 10 | Maximum redundancy |
| Adaptive | — | —  | Scales with cluster size (see ADR-006a) |

**Rationale:**
- Raw k/n parameters are meaningless to non-technical users
- Named profiles communicate the intent, not the mechanism
- Profiles are per-node/per-buddy — not cluster-wide
- Tahoe-LAFS enforces that shares.happy cannot exceed available nodes

**Consequences:**
- If a user selects Paranoid (3-of-10) but the cluster has fewer than 10 nodes,
  uploads will fail. The GUI must warn proactively before this happens.
- Tahoe terminology (shares.needed, shares.happy, shares.total) must never
  appear in the user-facing UI

---

## ADR-006a — Adaptiv profile: 1/3 ratio scaling

**Status:** Accepted

**Decision:** The Adaptiv profile maintains an approximate 1/3 ratio (any ~1/3 of
fragments sufficient for restore) and scales k and n with cluster size.

Small-cluster rules (applied before the formula):
- 1–2 nodes: k = n = node_count (all-of-n encoding — no redundancy possible)

Formula for 3+ nodes:
```
n = min(node_count, max_n)
k = max(round(n × ratio), min_k)
```

Reference table (ratio=0.33, min_k=1, max_n=20):
```
1 buddy    → k=1, n=1  (all-of-n)
2 buddies  → k=2, n=2  (all-of-n)
3 buddies  → k=1, n=3
4 buddies  → k=1, n=4
5 buddies  → k=2, n=5
6 buddies  → k=2, n=6
9 buddies  → k=3, n=9
20 buddies → k=7, n=20
25 buddies → k=7, n=20  (max_n cap)
```

Parameters: ratio=0.33, min_k=1, max_n=20.

**Rationale:**
- Each buddy holds exactly one fragment regardless of cluster size
- Scales naturally without manual reconfiguration as the group grows
- 1/3 threshold provides meaningful redundancy without excessive overhead
- min_k=1 allows honest 1-of-3 encoding for small clusters (3–4 nodes)
  rather than forcing k=2 and effectively requiring majority-of-cluster for restore
- max_n=20 prevents fragment count from becoming unmanageable in large clusters
- All-of-n for 1–2 nodes: with so few nodes there is no redundancy anyway

**Consequences:**
- Nightly rebalance job required to adjust k/n as cluster size changes
- Re-fragmentation may be triggered when cluster size crosses hysteresis boundaries
- The Adaptiv profile requires the rebalance scheduler to be running correctly

---

## ADR-007 — Lifeboat mechanism

**Status:** Accepted

**Decision:** The gatekeeper's critical state is encrypted and distributed as a bundle
to all local agents hourly. Encryption uses a locally stored random key for day-to-day
operations. A separate "recovery kit" encrypted with a user passphrase handles full
disaster recovery.

Two-layer approach:

**Layer 1 — Runtime lifeboat (auto-restart compatible)**
- Random 32-byte key generated at first setup
- Stored at `/etc/backup-buddy/lifeboat.key` (permissions 0600)
- Used to encrypt all lifeboat bundles distributed to agents
- Gatekeeper reads this key at startup — no user input required
- Bundle contents: node.privkey, root_dir.cap, catalog.db, gatekeeper.cfg
- Encryption: AES-256-GCM with the local random key

**Layer 2 — Recovery kit (disaster recovery only)**
- Generated once at first setup, never updated automatically
- Contents: node.privkey + root_dir.cap encrypted with user passphrase via Argon2id
- User saves this externally (USB drive, password manager)
- Passphrase only required at this moment AND at full disaster recovery
- catalog.db not included — rebuilt via "call home" after recovery

**Rationale:**
- Auto-restart works without user input — gatekeeper is a stable background service
- Passphrase only entered twice in the system's lifetime: at setup and at disaster recovery
- Mirrors the "emergency kit" pattern used by 1Password, Bitwarden etc.
- Local key file is protected by OS file permissions — sufficient for the threat model
- Agents on the local LAN hold bundles they cannot decrypt (no local key)

**Consequences:**
- If `lifeboat.key` is lost along with the gatekeeper hardware, recovery kit + passphrase
  is the fallback — makes external storage of the recovery kit critical
- The GUI must strongly prompt the user to save the recovery kit externally at setup
- Recovery kit confirmation (like root_dir.cap) must be a required step in onboarding
- Verification of the lifeboat must be part of the nightly verify job

---

## ADR-008 — "Call home" catalog reconstruction

**Status:** Accepted

**Decision:** If catalog.db is lost, it can be reconstructed by traversing the
Tahoe file tree using root_dir.cap and decrypting per-file metadata tags.

Each file uploaded to Tahoe receives an encrypted metadata tag containing:
original_path and agent name (encrypted with a key derived from root_dir.cap),
and backed_up_at timestamp (plaintext).

**Rationale:**
- catalog.db loss without this mechanism forces fully manual file recovery
- Metadata stored in Tahoe is always co-located with the file — never lost separately
- Only the root_dir.cap holder can decrypt the tags — consistent with zero-knowledge design
- Reconstruction requires no interaction with other cluster nodes beyond normal Tahoe access

**Consequences:**
- Metadata tags must be written at upload time — this cannot be retrofitted to
  existing uploads without re-uploading
- Reconstruction time scales with the number of files (may take minutes for large catalogs)
- The GUI must provide a clear "rebuild catalog" flow in the emergency restore section

**Implementation note:**
`TahoeClient.upload()` (gatekeeper/tahoe/client.py) accepts a `metadata` parameter
but does not store it. Metadata tags are written by the fragmenter (task 1.7.1) when
the file cap is linked into a Tahoe directory entry. This split is intentional:
the HTTP client layer has no knowledge of directory structure or encryption keys.

---

## ADR-009 — Invite system: open model with single-use codes

**Status:** Accepted

**Decision:** Any cluster member can generate an invite code. Codes are single-use,
expire after 48 hours, and are revocable before use. No designated admin role.

Code format: two words + number (e.g. kaffe-trumpet-7). Human-readable,
easy to transmit via any channel.

Under the hood: wraps Tahoe-LAFS `tahoe invite` / `tahoe create-client --join`.

**Rationale:**
- A designated admin creates a dependency on one person being available
- Single-use + expiry limits the damage of a leaked code
- Human-readable codes can be shared verbally, by SMS, via any channel
- All cluster members seeing open invites provides transparency

**Consequences:**
- Any member can invite anyone — the group must communicate outside the system
  about who is being invited (social contract, not technical enforcement)
- Revocation is only possible before the code is used
- Phase 1 uses two separate steps: Tailscale join (manual) then cluster join (code)
- One-step joining (Tailscale + cluster combined) is a Phase 2 improvement

---

## ADR-010 — Node removal: majority vote with grace period

**Status:** Accepted

**Decision:** Any member can propose removal of another member. Simple majority
vote is required. Removed node receives a 7-day grace period for re-fragmentation.
The removed node does not see the vote while it is in progress.

Grace period is extendable by cluster majority vote (for known hardware failures,
travel, etc.).

**Rationale:**
- No single member should be able to unilaterally remove another
- 7-day grace period ensures data is re-fragmented before the node leaves
- Grace period extension handles legitimate cases (Anders in Thailand)
- The removed node not seeing the vote prevents gaming the system

**Consequences:**
- During the grace period, the removed node's data must be re-fragmented
  at the normal 3%/night rate (may not complete in 7 days for large datasets —
  grace period extension may be necessary)
- Orphan fragments from the removed node are cleaned up after orphan_grace_days
  following confirmed re-fragmentation completion

---

## ADR-011 — Re-fragmentation: hysteresis + stability threshold + gradual rate

**Status:** Accepted

**Decision:** Re-fragmentation is triggered only when:
1. Cluster size change exceeds the hysteresis zone (±2 nodes from baseline), AND
2. The cluster has been stable for stability_days (default: 7 days)

When triggered, re-fragmentation proceeds at max 3% of files per night.

Priority order: files below minimum k first, then oldest, then largest, then rest.
New fragments are verified before old fragments are deleted.

**Rationale:**
- Naive re-fragmentation on every membership change causes massive churn
- The hysteresis zone absorbs short-term join/leave without triggering work
- The stability threshold prevents work for hit-and-run nodes (join + leave < 7 days)
- Gradual 3%/night rate prevents network saturation and keeps the system usable

**Consequences:**
- After a large cluster change, full re-fragmentation may take weeks
  (acceptable — data is safe throughout, just not optimally distributed)
- Files below minimum k are always treated as critical and re-fragmented first
  regardless of hysteresis or stability threshold

---

## ADR-012 — Orphan fragment cleanup

**Status:** Accepted

**Decision:** Fragments from departed or lost nodes are cleaned up automatically
after orphan_grace_days (default: 30) following confirmed re-fragmentation.

Each fragment carries an encrypted owner tag. Cleanup is performed by a daily job.
The grace period is configurable and extendable by cluster majority vote.

**Rationale:**
- 30 days covers realistic outage scenarios (hardware failure, extended travel)
- Without cleanup, departed nodes leave permanent dead weight in the storage pool
- Configurable grace period adapts to the group's needs
- Cleanup must not happen before re-fragmentation is confirmed complete

**Consequences:**
- Storage is not immediately reclaimed when a node leaves — up to 30 days delay
- The orphan grace period and the removal grace period (ADR-010) are separate:
  removal grace (7 days) = time to re-fragment data
  orphan grace (30 days) = time to clean up the former node's stored fragments

---

## ADR-013 — Storage ratio: 1:1 minimum, 1.2x recommended

**Status:** Accepted

**Decision:** Each buddy must contribute at least as much storage as they want
backed up (1:1 ratio). The recommended buffer is 1.2x. The system warns but
does not hard-block below 1.2x as long as 1:1 is maintained.

**Rationale:**
- Below 1:1, the cluster mathematically cannot guarantee the backup
- 1.2x provides margin for overhead and growth
- Hard-blocking at 1:1 rather than 1.2x avoids penalizing users with limited storage
- Ratio abuse (large imbalance) is handled by GUI visibility, not automatic enforcement
  in Phase 1 (throttling is a Phase 2 feature)

**Consequences:**
- The GUI must show a clear per-buddy contribution vs usage table
- Warning at < 1.2x, error at < 1.0x
- Users with high-redundancy profiles (Paranoid, 3-of-10) have higher effective
  overhead per backed-up GB — the GUI should make this visible

---

## ADR-014 — SQLite for catalog.db and cluster.db

**Status:** Accepted

**Decision:** Use SQLite for both the backup catalog (catalog.db) and cluster
state (cluster.db). No external database server.

**Rationale:**
- No external service dependency — keeps gatekeeper self-contained
- SQLite is reliable, well-understood, and appropriate for single-writer workloads
- The gatekeeper is the sole writer for both databases
- Database files are included in the lifeboat bundle — easy to backup and restore

**Consequences:**
- All queries must use parameterized statements (see SECURITY.md → Section 4)
- WAL mode should be enabled for better concurrent read performance
- Database files must have permissions 0600
- Migrations must be handled explicitly as the schema evolves

---

## ADR-015 — FastAPI for the web GUI backend

**Status:** Accepted

**Decision:** Use FastAPI (Python) for the gatekeeper web GUI backend.

**Rationale:**
- Python — same language and ecosystem as Tahoe-LAFS, no polyglot stack
- Async-native — handles background jobs (watcher, verifier, rebalancer) alongside
  serving the GUI without blocking
- Built-in Pydantic validation for all inbound data
- Lightweight enough to run comfortably on a Raspberry Pi
- No separate web server required — runs as a standalone process

**Consequences:**
- The GUI is server-rendered or served as a single-page app from FastAPI
- Background tasks (watcher, verifier, rebalancer) run as FastAPI background tasks
  or separate threads managed by the same process
- The GUI process must not start if Tailscale is not running (ADR-002)

---

## ADR-016 — Phase 1 scope boundary

**Status:** Accepted

**Decision:** Phase 1 (PoC) is strictly limited to the features in docs/roadmap.md
→ Phase 1. No Phase 2 features are implemented until Phase 1 is complete and
tested end-to-end per docs/testing.md.

**Phase 1 deliberately excludes:**
- Incremental backups (full upload only)
- VM snapshot support
- Gossip protocol (introducer node used instead)
- Automatic quota negotiation
- Upload throttling for ratio abuse
- Hot-standby gatekeeper
- One-step Tailscale + cluster join
- File-level granularity (folder-level only)

**Rationale:**
- A working minimal system is more valuable than a partially-working full system
- Phase 1 validates the core assumptions before investing in advanced features
- The test scenarios in docs/testing.md must all pass before Phase 2 begins

**Consequences:**
- If a task would require implementing a Phase 2 feature to complete a Phase 1 task,
  flag it to the project owner before proceeding
- Phase 2 features must not be "snuck in" as part of Phase 1 implementation

---

## ADR-017 — Dual-listener architecture: Tailscale GUI + LAN agent API

**Status:** Accepted

**Decision:** The gatekeeper runs two independent HTTP servers:

1. **GUI / cluster API** — bound to the Tailscale IP, port 8080 (default).
   Handles the web GUI, cluster-to-cluster communication, and any endpoint
   that buddies in other home networks need to reach via Tailscale.

2. **Agent API** — bound to the local LAN IP, port 8081 (default).
   Handles agent registration, fragment upload, and lifeboat distribution.
   Accepts connections from the local subnet only; never reachable via
   Tailscale or the public internet.

Both servers are started with `asyncio.gather()` inside a single process.

**Rationale:**
- SECURITY.md §3 requires the GUI to bind to Tailscale only, but agents
  on the home LAN cannot reach the Tailscale interface.
- Separating the two listeners enforces the network boundary in code:
  a compromised cluster node cannot reach the agent API even if it has
  Tailscale access to the gatekeeper.
- Binding each server to a specific interface is simpler and more auditable
  than a single 0.0.0.0 server with runtime IP-filtering middleware.

**Consequences:**
- The gatekeeper detects its LAN IP at startup using psutil (same library
  used for Tailscale detection). If no LAN IP is found, the agent API is
  skipped with a warning — the gatekeeper still starts in GUI-only mode.
- The agent API is only activated when `[agent_api] token` is set in
  gatekeeper.cfg. An empty token disables the agent API.
- Agent token is stored in plaintext in both backup.cfg and gatekeeper.cfg
  for Phase 1. Encrypted storage is a Phase 2/3 improvement.
- Authentication: pre-shared token in `Authorization: Bearer <token>` header,
  validated with `secrets.compare_digest` to prevent timing attacks.
- Agent → gatekeeper communication uses the LAN URL (e.g. http://192.168.1.50:8081).
  Cluster → cluster communication uses Tailscale hostnames.

---

## ADR-018 — Fragmentation profiles are node-wide settings, not per-upload

**Status:** Accepted

**Decision:** A fragmentation profile (Balanced, Secure, Paranoid, Adaptive) applies to
all uploads from a given gatekeeper node at any point in time. Changing the active profile
requires updating the Tahoe node configuration and restarting the node.

**Background — what the fragmentation profile controls:**

When BackupBuddy stores a file in the cluster, it splits the file into fragments.
The profile determines two numbers:

- **k** — how many fragments are needed to restore the file (e.g. any 3 of 5)
- **n** — how many fragments are created in total (e.g. 5 spread across 5 buddy nodes)

A higher n means more redundancy: the file can survive more nodes going offline.

**Why per-upload k/n is not possible (Phase 1):**

Tahoe-LAFS controls k and n through its node configuration file (`tahoe.cfg`), not through
per-upload parameters in its HTTP API. The upload endpoint (`PUT /uri`) does not accept k
or n as query parameters — verified in the fork source (`src/allmydata/web/unlinked.py`).

This means all files uploaded at any moment use the k/n values that are currently written
in `tahoe.cfg`. You cannot upload one file as Balanced (3,5) and another as Secure (3,7)
in the same session without modifying the config and restarting the node.

**What this means in practice:**

- The active profile is stored in `gatekeeper.cfg` and written to `tahoe.cfg` when the
  Tahoe node starts up.
- All backups run with that profile until the user changes it in the GUI.
- If a user switches from Balanced to Secure, BackupBuddy updates `tahoe.cfg` and restarts
  the Tahoe node. New uploads from that point use the new k/n.
- Files already in the cluster keep their original k/n until the nightly rebalance job
  re-fragments them (task 1.11).
- The fragmenter records the k/n in `catalog.db` per file so the rebalance job always
  knows what each file was originally uploaded with.

**What this does NOT affect:**

- The `catalog.db` record for each file always stores the exact k/n used at upload time.
  Normal restore (task 1.12.1) and call-home reconstruction (task 1.12.2) both work correctly
  regardless of which profile is currently active.
- Files from different periods may have different k/n values in the catalog — that is
  expected and handled correctly by the restore and rebalance logic.

**Phase 2 note:**

Per-upload k/n overrides could be implemented by patching the Tahoe upload path at the
Python API level (bypassing the HTTP gateway). This is a Phase 2 improvement and must not
be implemented as part of Phase 1.

---

_BackupBuddy DECISIONS.md_
_Read relevant ADRs before implementing any related feature._
