# BackupBuddy

A distributed peer-to-peer backup system for friend groups, forked from Tahoe-LAFS.
Built on top of Tailscale for networking and WireGuard encryption between nodes.

## Core concept

Every participant ("buddy") in a trusted group acts as both:
- **Client** — contributes encrypted fragments of their own files to others
- **Host** — stores encrypted fragments on behalf of other buddies

No central server. No single point of failure. Zero-knowledge fragment handling —
nodes never see the content of what they store.

## Documentation

| File | Contents |
|------|----------|
| `project-docs/architecture.md` | System components, topology, data flow |
| `project-docs/design.md` | All design decisions with rationale |
| `project-docs/configuration.md` | Config file schemas (gatekeeper.cfg, backup.cfg) |
| `project-docs/roadmap.md` | Phase 1 (PoC), Phase 2, Phase 3 (speculative) |

## Quick orientation

- **Gatekeeper** — one gateway node per home network. Coordinates everything.
- **Agent** — one per device inside the home network. Reads backup.cfg, fragments locally.
- **Cluster** — all gatekeepers connected via Tailscale, using Tahoe-LAFS for storage.
- **Introducer** — Tahoe-LAFS built-in node that helps gatekeepers find each other.

## Base project

Forked from [Tahoe-LAFS](https://github.com/tahoe-lafs/tahoe-lafs) (Python, GPL2+).
Tahoe-LAFS handles erasure coding, encryption, and fragment distribution.
BackupBuddy adds: GUI, agent model, config simplification, invite flow, monitoring,
lifeboat mechanism, adaptive fragmentation, and automated verification.
