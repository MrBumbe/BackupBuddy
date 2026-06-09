# Architecture

## Topology overview

```
[Home network — Anders]
    Proxmox ──┐
    NAS       ├──→ [Gatekeeper] ──→ Tailscale cluster
    PC ───────┘

[Home network — Björn]
    PC ───────┐
    RPi ──────├──→ [Gatekeeper] ──→ Tailscale cluster
    NAS ──────┘

[Home network — Carina]
    VMware ───┐
    NAS ──────├──→ [Gatekeeper] ──→ Tailscale cluster
```

One gatekeeper per home network. Agents inside the network talk only to their
local gatekeeper. The gatekeeper is the only node exposed to the cluster.

---

## Components

### Gatekeeper (gateway node)

The single outward-facing node per home network. Runs on always-on hardware
(Raspberry Pi, NAS, small server). Responsibilities:

- Coordinates with local agents via LAN
- Connects to the Tahoe-LAFS cluster via Tailscale
- Hosts the web GUI (FastAPI, port 8080)
- Manages the storage pool (fragment storage paths + quotas)
- Runs the file watcher, fragmenter, uploader, verifier
- Maintains catalog.db (backup catalog)
- Manages lifeboat distribution to agents
- Handles cluster communication (invites, voting, re-fragmentation)
- Acts as Tailscale subnet router for the entire home network

### Agent

Lightweight process running on each device to be backed up. Responsibilities:

- Reads backup.cfg to know what to back up
- Detects file stability (30 min idle + no open file handles)
- Fragments and encrypts files locally before sending to gatekeeper
- Optionally shares backup.log with gatekeeper (opt-in in backup.cfg)
- Stores one encrypted lifeboat copy from the gatekeeper

The agent never sees the cluster. It only talks to its local gatekeeper.
The gatekeeper cannot browse the agent filesystem — it only reads what
the agent explicitly sends, as defined in backup.cfg.

### Cluster

All gatekeepers connected via Tailscale. Uses Tahoe-LAFS for:
- Erasure coding (fragment distribution)
- Client-side encryption (gatekeepers never see each other's plaintext)
- Introducer node (gatekeepers find each other at startup)
- Storage servers (each gatekeeper stores fragments for others)

### Introducer node

Standard Tahoe-LAFS introducer. One per cluster. Can run on any always-on
gatekeeper or a dedicated machine. Gatekeepers connect at startup to announce
presence and discover other nodes. Not in the data path after initial discovery.

---

## Networking

### Tailscale as the network layer

Tailscale runs on each gatekeeper. Benefits:
- Automatic NAT traversal (no port forwarding required)
- WireGuard encryption between all nodes
- Stable MagicDNS names (gatekeeper-anders.tailnet.ts.net)
- Subnet routing: gatekeeper advertises its entire home LAN

```bash
tailscale up --advertise-routes=192.168.1.0/24
```

Tailscale join and cluster join are two separate manual steps (Phase 1 design).

### Web GUI access

By default the GUI binds to the **LAN interface only** (ADR-023).
The operator accesses the GUI from inside their own home network.
Cluster peers on Tailscale cannot reach the GUI.

```
Default (gui_on_lan=true, gui_on_tailscale=false):
  http://192.168.1.50:8080   ← accessible from home network

With gui_on_tailscale=true also enabled:
  http://gatekeeper-anders.tailnet.ts.net:8080   ← accessible via Tailscale too
```

The **Tailscale listener always runs** and serves cluster API routes
(`/api/cluster/*`, `/api/verify/*`, `/api/status`) regardless of GUI flags.
GUI routes on that listener are only active when `gui_on_tailscale = true`.

Setting both flags to false disables the GUI entirely; the cluster API
continues to function normally.

---

## Data flow — backup

```
[Agent detects stable file]
    ↓ idle 30 min + no open handles + size stable
[Agent computes SHA-256]
    ↓ compare against catalog.db — already backed up?
[Agent fragments + encrypts locally]
    ↓ erasure coding per fragmentation profile
[Fragments sent to gatekeeper]
    ↓ streamed, low priority (nice +19, ionice -c 3)
[Gatekeeper uploads fragments to cluster via Tahoe-LAFS]
    ↓ distributed across buddy gatekeepers
[Gatekeeper verifies placement (servers-of-happiness)]
    ↓
[Gatekeeper updates catalog.db — cap + hash + metadata]
    ↓
[Gatekeeper pushes updated lifeboat to all local agents]
```

## Data flow — restore (normal)

```
[User selects file in GUI]
    ↓
[Gatekeeper looks up cap in catalog.db]
    ↓
[Requests k fragments from cluster nodes]
    ↓
[Erasure decoding reconstructs file]
    ↓
[SHA-256 verified against catalog.db entry]
    ↓
[File written to restore destination]
```

## Data flow — restore (catalog.db lost, "call home")

```
[User loads root_dir.cap in GUI]
    ↓
[Gatekeeper traverses Tahoe-LAFS file tree from root]
    ↓
[Decrypts encrypted metadata tag on each file's cap]
    ↓ metadata contains: original_path, agent, backed_up_at
[Rebuilds full catalog.db including original paths]
    ↓
[Normal restore flow continues]
```

---

## Storage pool

Fragment storage is distributed transparently across multiple local paths.
The gatekeeper presents a unified pool to Tahoe-LAFS. Each path has a hard
quota. Storage paths are automatically excluded from backup scope
(infinite loop prevention — cannot be overridden by user).

```
/mnt/nas/buddy-storage    → 2000 GB cap
/mnt/data/buddy-storage   →  400 GB cap
/mnt/usb/buddy-storage    →  800 GB cap
─────────────────────────────────────────
Total pool:                  3200 GB
```

Distribution strategy: fill the path with the most free space first.

---

## Security model

- **Zero-knowledge fragments** — storage nodes see only encrypted blobs
- **Client-side encryption** — Tahoe-LAFS encrypts before data leaves the agent
- **Double encryption** — Tailscale WireGuard wraps Tahoe's own encryption
- **Privacy by default** — agents share nothing beyond backup.cfg scope
- **Metadata opt-in** — backup.log sharing requires explicit agent config
- **Lifeboat encryption** — gatekeeper state AES-encrypted with passphrase
- **root_dir.cap** — master key to the file tree; without it fragments are inaccessible
- **Metadata tags** — original paths stored encrypted on file caps in Tahoe,
  readable only with root_dir.cap context
