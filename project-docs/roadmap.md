# Roadmap

---

## Phase 1 — Proof of concept

Goal: working backup and restore between a small group of buddies.
Scope: reliability, reachability, security, and basic usability.

### Networking
- [x] Tailscale as network layer (WireGuard, NAT traversal, MagicDNS)
- [x] One gatekeeper per home network (subnet router)
- [x] Tailscale join and cluster join as two separate manual steps
- [x] Tahoe-LAFS introducer node for cluster discovery

### Gatekeeper
- [x] FastAPI web GUI on port 8080 (Tailscale interface only)
- [x] Storage pool with multiple paths and per-path hard quotas
- [x] Automatic exclusion of storage pool paths from backup scope
- [x] Transparent fragment distribution across pool paths (own scheduler)
- [x] catalog.db (SQLite) — caps, hashes, original paths, agents, timestamps
- [x] cluster.db — membership, votes, orphan tracking

### Agent
- [x] backup.cfg — user-defined backup paths (folder-level, not file-level)
- [x] backup.log — optional, share_log controlled by agent
- [x] File watcher with stability detection (30 min + open handle check)
- [x] Local fragmentation and encryption before sending to gatekeeper
- [x] CPU and IO throttling (nice +19, ionice -c 3)
- [x] Hash verification before and after fragmentation

### Fragmentation
- [x] Four profiles: Lagom (3-of-5), Trygg (3-of-7), Paranoid (3-of-10), Adaptiv
- [x] Adaptiv: 1/3 ratio, scales with cluster size, min_k=2, max_n=20
- [x] Profile is per node (not cluster-wide)
- [x] Streaming upload (fragments sent as ready, katalog.db updated last)

### Lifeboat
- [x] Encrypted bundle: node.privkey + root_dir.cap + katalog.db + gatekeeper.cfg
- [x] Distributed to all local agents hourly
- [x] Passphrase-protected (never stored on disk)
- [x] GUI prompt to save root_dir.cap externally (USB / password manager)
- [x] "Call home" reconstruction: rebuild katalog.db from Tahoe file tree
      using encrypted metadata tags stored per file at upload time

### Invite and removal
- [x] Invite: any member generates single-use code, 48h expiry
- [x] Join flow: Tahoe invite wrapper with human-readable code
- [x] Removal: any member proposes, majority vote, 7-day grace period
- [x] Grace period extendable by cluster majority vote
- [x] Orphan fragment cleanup after grace period (30-day default)

### Verification
- [x] Daily verify job at 04:00:
      root_dir.cap integrity, katalog.db vs cluster, test restore, lifeboat age
- [x] Test restore: N random files restored and hash-verified nightly
- [x] All failures generate SMTP and/or webhook notifications

### Monitoring and notifications
- [x] SMTP notifications (password stored encrypted via GUI)
- [x] Webhook notifications (generic JSON, Discord/Slack/Ntfy compatible)
- [x] Configurable alert thresholds (node count, storage %, offline time)
- [x] Per-event notification granularity (success silent by default)

### Storage ratio
- [x] GUI shows per-buddy contribution vs usage table
- [x] Warning if contribution < own backup size
- [x] Recommended buffer: 1.2x

### Re-fragmentation
- [x] Stability threshold: 7 days before rebalancing starts
- [x] Hysteresis zone: ±2 nodes from baseline — no trigger
- [x] Gradual: max 3% of files per night
- [x] Priority: critical files first, then oldest, then largest
- [x] Verify new fragments before deleting old

---

## Phase 2 — Maturity

Goal: incremental backups, VM support, improved resilience, self-healing cluster.

### Backups
- [ ] Incremental backups (delta since last backup, not full re-upload)
- [ ] VM snapshot support (Proxmox vzdump integration, incremental snapshots)
- [ ] Per-file versioning with configurable retention

### Cluster
- [ ] Gossip protocol replacing the Tahoe-LAFS introducer node
      (fully serverless, no single point of failure)
- [ ] Automatic quota negotiation between buddies
- [ ] Upload throttling for free-riders (ratio > 3:1)
- [ ] Hot-standby gatekeeper promotion (semi-automatic failover)

### GUI
- [ ] Improved restore UI with version history per file
- [ ] Cluster health timeline (historical uptime, storage trends)
- [ ] One-step invite flow (Tailscale + cluster join combined)

### Phase 2 note on VM snapshots
VM snapshot files (.qcow2, .vmdk, .img) can be multi-terabyte.
Incremental snapshot strategy required — only delta since last snapshot.
Proxmox Backup Server (PBS) format to be evaluated as a potential layer.
Full file re-upload of VM images is not acceptable at any scale.

---

## Phase 3 — Public cluster (speculative)

Goal: open cluster where anyone can participate. Social trust replaced
by cryptographic proof and incentive mechanisms.

**Note: Phase 3 is a side concept. It must not drive Phase 1 or Phase 2 decisions.**

### Storage requirements
- [ ] 1:2 ratio requirement (contribute 2, use 1)
- [ ] Enforced by protocol, not social contract

### Erasure coding
- [ ] 3-of-10 as default (tolerates 7 simultaneous node departures)
- [ ] Higher n required for mass-departure resilience in public setting

### Trust mechanisms
- [ ] Proof of Storage via Merkle-tree hash challenges
      (node proves it holds a fragment without sending the fragment)
- [ ] Node leveling system:
      new node → limited quota, grows over time with uptime and reliability
- [ ] Automatic re-fragmentation on Proof of Storage failure
- [ ] Sybil attack resistance (prevent fake node farms)

### Merkle-tree challenge flow
```
System sends challenge: "Prove you hold fragment F — hash it with nonce N"
Node responds with: HASH(fragment_F + nonce_N)
Correct response → fragment confirmed present
No response or wrong hash → trigger re-fragmentation to another node
```

### Level system sketch
```
Level 1 (new):   backup 1 GB,  store 2 GB for others
Level 2 (30d):   backup 5 GB,  store 10 GB for others
Level 3 (90d):   backup 20 GB, store 40 GB for others
...
Node offline after leveling → level reset on return
```
