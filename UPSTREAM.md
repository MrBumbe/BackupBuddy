# UPSTREAM.md

BackupBuddy is a fork of [Tahoe-LAFS](https://github.com/tahoe-lafs/tahoe-lafs).

## Fork base

| Field        | Value |
|--------------|-------|
| Upstream URL | https://github.com/tahoe-lafs/tahoe-lafs |
| Base commit  | `f002fd0d27d9a3da5c0da162b10725838353bd63` |
| Base date    | 2026-01-21 |
| Base message | Merge pull request #1452 from hacklschorsch/4191.update-tor-gpg-key |

## Local changes on top of upstream

| Commit | Message |
|--------|---------|
| `0bf7b5dee3ac334726243970a3d488bbf5693fd1` | fix(tests): replace deprecated failUnlessRaises with assertRaises |

## Syncing with upstream

To pull upstream changes into BackupBuddy:

```bash
git fetch upstream
git merge upstream/master
# Resolve conflicts, then commit
```

Do not rebase BackupBuddy commits on top of upstream — prefer merge commits to
preserve history.

## Upstream version info

Tahoe-LAFS is developed at https://tahoe-lafs.org.
See `src/allmydata/_version.py` for the upstream version string.
