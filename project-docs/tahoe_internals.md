# Tahoe-LAFS internals reference

> **Internal document — developer reference only.**
> Tahoe-LAFS concepts documented here must never appear in user-facing UI,
> log messages visible to users, or any external communication.
> All user-facing language uses BackupBuddy terminology instead.

---

## Concepts used internally

### Capability URI (cap)

A capability URI (cap) is a long opaque string that encodes everything needed to
retrieve and decrypt a stored object from the Tahoe grid. Example shape:

```
URI:CHK:...
URI:DIR2:...
```

- **Immutable file cap (`URI:CHK:`)** — used for backed-up files
- **Directory cap (`URI:DIR2:`)** — used for the root directory tree

The root directory cap (`root_dir.cap`) is the master pointer to all backed-up data.
Without it, stored fragments are cryptographically inaccessible.

Caps are stored encrypted in catalog.db. They never appear in user-facing output.

### FURL (Fully Qualified Resource Locator)

A FURL is a Tahoe connection string that tells a client how to reach a specific
Tahoe node (introducer or storage server). Example shape:

```
pb://...@host:port/...
```

FURLs are used internally for node discovery. They are:
- Written to `private/introducer.furl` at node creation time
- Read by storage nodes and clients to connect to the introducer
- Never displayed in the UI, never logged to user-visible logs

### Shares (k-of-n erasure coding)

Tahoe splits files into `n` shares using erasure coding. Any `k` shares are
sufficient to reconstruct the original file. The parameters map to:

| Tahoe parameter      | BackupBuddy meaning |
|----------------------|---------------------|
| `shares.needed` (k)  | Minimum shares to reconstruct |
| `shares.happy` (k)   | Minimum servers that must confirm placement |
| `shares.total` (n)   | Total shares to produce |

BackupBuddy exposes these as named profiles:

| Profile  | k | n  |
|----------|---|----|
| Balanced | 3 | 5  |
| Secure   | 3 | 7  |
| Paranoid | 3 | 10 |
| Adaptive | — | —  |

### Servers-of-happiness

Tahoe will not confirm an upload until at least `shares.happy` distinct storage
servers have each accepted at least one share. This prevents all shares ending
up on one server. BackupBuddy always sets `shares.happy = shares.needed = k`.

### Storage index

A SHA-256-derived identifier for each file's share set. Used internally by Tahoe
for deduplication and retrieval. Stored on disk as directory names under the Tahoe
storage server's storage directory. Never visible to users.

### Introducer

The Tahoe introducer is a well-known rendezvous point. Storage nodes and clients
announce themselves to the introducer at startup. The introducer does not store
data — it only keeps a list of currently connected nodes. Nodes discover each
other through the introducer and then communicate directly.

One introducer per BackupBuddy cluster. Can run on any always-on gatekeeper.

### Node private key

Each Tahoe node (gatekeeper) has an asymmetric key pair. The private key is stored
at `{nodedir}/private/node.privkey` (permissions 0600). It is used to authenticate
the node to the cluster. BackupBuddy includes this key in the lifeboat bundle.

### tahoe.cfg

The main configuration file for each Tahoe node. Located at `{nodedir}/tahoe.cfg`.
Configures storage paths, quotas, introducer FURL, and k/n parameters. BackupBuddy
writes this file programmatically — users never interact with it directly.

---

## Key file locations

| File | Purpose |
|------|---------|
| `{nodedir}/tahoe.cfg` | Node configuration |
| `{nodedir}/private/introducer.furl` | Introducer FURL (introducer node only) |
| `{nodedir}/private/node.privkey` | Node identity key (permissions 0600) |
| `{nodedir}/private/storage.furl` | Storage server FURL (storage nodes) |
| `{nodedir}/storage/` | Stored shares (storage node only) |
| `{nodedir}/node.url` | Web API URL for the local node |

---

## What BackupBuddy wraps

BackupBuddy wraps the following Tahoe operations programmatically:

| Tahoe operation | BackupBuddy wrapper |
|-----------------|---------------------|
| `tahoe create-introducer` | `gatekeeper/tahoe/introducer.py` |
| `tahoe create-node` (storage) | `gatekeeper/tahoe/storage_node.py` |
| `tahoe run` | Both of the above (managed subprocess) |
| `tahoe backup` / PUT | `gatekeeper/tahoe/client.py → upload()` |
| `tahoe get` / GET | `gatekeeper/tahoe/client.py → download()` |
| Directory traversal | `gatekeeper/tahoe/client.py → ls()` |

All Tahoe output (stdout/stderr) is captured. None of it is forwarded to users.

---

_BackupBuddy project-docs/tahoe_internals.md_
_Internal reference — never expose Tahoe concepts to users._
