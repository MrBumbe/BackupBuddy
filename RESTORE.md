# BackupBuddy Restore Guide

---

## What this guide covers

This guide explains how to get your data back in three different situations:

- **[Section 1](#1-restore-individual-files-or-folders)** — Restoring individual files or folders from a healthy gatekeeper
- **[Section 2](#2-disaster-recovery--rebuilding-a-lost-gatekeeper)** — Your gatekeeper machine is gone or destroyed and you need to rebuild it from scratch
- **[Section 3](#3-emergency-recovery-without-a-gatekeeper)** — You have lost everything and need to contact your buddy to get your data back

---

## 1. Restore individual files or folders

Use this when your gatekeeper is running and you just need to get a file back.

### 1.1 Single file

1. Open your gatekeeper dashboard in a browser (`http://<your-tailscale-ip>:8080`).
2. Click **Restore** in the navigation menu.
3. Type part of the file name or path in the search box.
4. Click the file you want to restore.
5. Enter a destination folder on the gatekeeper, for example `/home/yourname/restored-files`.
   This can be any absolute path on the gatekeeper — you are not limited to the original location.
6. Click **Restore file**.

BackupBuddy downloads and reassembles the file from your buddies' nodes. When done, the
file appears in the folder you chose.

> Restored files land on the **gatekeeper machine**. To copy them to your laptop or
> original machine, use a USB drive, a file share (e.g. Samba), or `scp`:
> ```bash
> scp yourname@<gatekeeper-ip>:/home/yourname/restored-files/myfile.pdf ~/Desktop/
> ```

### 1.2 Entire folder

1. Open your gatekeeper dashboard → **Restore**.
2. Leave the search box empty and click **Restore folder**.
3. Enter the original folder path as it was on the agent machine (e.g. `/home/yourname/documents`).
4. Enter a destination folder on the gatekeeper. You can use any path — the original
   subfolder structure will be recreated inside the destination you choose.
5. Click **Restore folder**.

BackupBuddy restores all files in that folder, recreating the original directory structure
inside the destination you chose.

### 1.3 If a restore fails

**"No connected storage nodes"** — At least one of your buddies' nodes must be reachable.
Check that your buddy's gatekeeper is online in the Buddies section of your dashboard. If
they are offline, ask them to start their gatekeeper and ensure Tailscale is connected on
their machine.

**"File not found in catalog"** — The file may not have been backed up yet, or it was
excluded by a pattern in `backup.cfg`. Check the agent log:
```bash
journalctl -u backup-buddy-agent | grep <filename>
```

**"Hash mismatch after restore"** — The restored file's checksum did not match the recorded
value. This can happen if a storage node returned corrupt data. BackupBuddy will log the
error. Try again — with erasure coding the system can recover using data from other nodes.
If it fails consistently, contact your buddy to verify their storage pool is intact.

---

## 2. Disaster recovery — rebuilding a lost gatekeeper

Use this when your gatekeeper machine has been destroyed, lost, or needs to be reinstalled
from scratch. You need two things:

- **Your recovery kit file** (`recovery-kit.enc`) — you saved this during setup
- **Your recovery passphrase** — the one you chose during setup

If you have lost either of these, see [Section 3](#3-emergency-recovery-without-a-gatekeeper).

### What the recovery kit contains

Your recovery kit file (`recovery-kit.enc`) is encrypted with your passphrase. It contains:

- Your private node key (so your node is recognised by the cluster)
- Your root directory capability (the pointer to all your backed-up files in the grid)

With these two pieces and at least one buddy online, BackupBuddy can rebuild your catalog
and restore all your files.

### 2.1 Install a fresh gatekeeper

On your replacement machine (a new VM, a reinstalled server, or a spare computer):

> If `git` is not installed: `sudo apt-get install -y git`

```bash
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
sudo bash /opt/backup-buddy/install/gatekeeper.sh
sudo tailscale up
```

Log in to Tailscale when prompted. Use the same Tailscale account you used before —
your new node will get a new Tailscale IP, but it will still be on the same tailnet as
your buddies.

### 2.2 Open the recovery wizard

Open the setup wizard in your browser (`http://<new-gatekeeper-LAN-IP>:8080`).

On Step 1, select **"Recover from backup"** (or **"I have a recovery kit"** — the exact
label depends on the version). You will be asked to:

1. Upload your `recovery-kit.enc` file.
2. Enter your recovery passphrase.

BackupBuddy decrypts the kit and extracts your node identity and root directory capability.

### 2.3 Complete the wizard

Continue through the remaining wizard steps:

- **Node ID and display name:** Use the same node ID and display name you had before.
  Your buddies' nodes will recognise you by this ID.
- **Storage path:** Enter a path for your buddy's data. If you are on a new disk, prepare
  the directory first:
  ```bash
  sudo mkdir -p /mnt/buddy-storage
  sudo chown backupbuddy:backupbuddy /mnt/buddy-storage
  ```
- **Profile:** Leave as Adaptive (or match your previous setting).
- **Notifications:** Re-enter if you had them configured.

When the wizard finishes, BackupBuddy connects to the grid and begins rebuilding your
catalog from the file tree your buddies are storing. This may take a few minutes.

### 2.4 Verify the rebuild

Open your dashboard. The catalog rebuild runs automatically in the background. After a
few minutes you should see your backed-up files appearing in the **Restore** view.

> **Note:** After a disaster recovery, the catalog is reconstructed from the file tree —
> SHA-256 checksums for existing files are not stored in the rebuilt catalog. The nightly
> verifier will log "hash unknown" warnings for these files until each file is re-backed-up
> by the agent. New backups after recovery will have correct checksums.

Verify the restore works:
1. Pick a file you know was backed up.
2. Restore it to a temporary folder.
3. Compare its checksum to the original if you have it recorded:
   ```bash
   sha256sum /tmp/restored/myfile.pdf
   ```

### 2.5 If recovery fails

**"Invalid passphrase or corrupt kit"** — Double-check your passphrase. If the file is
corrupted, check if your gatekeeper's Settings → Lifeboat page has a re-download of the
kit from before the disaster (only works if the gatekeeper was running before it was
destroyed). Otherwise see Section 3.

**"Cannot contact cluster"** — Your buddies' nodes are not reachable. Ensure Tailscale is
running on your new machine and that at least one buddy is online.

**"Node ID already in use"** — Another node with the same ID is active in the cluster.
This can happen if a ghost of your old node is still running somewhere. Contact your buddy
and ask them to remove the old node from the Buddies page on their dashboard.

---

## 3. Emergency recovery without a gatekeeper

Use this if:
- Your gatekeeper is gone AND you have lost `recovery-kit.enc` or your passphrase
- You cannot complete Section 2

This is the most difficult recovery path. Whether it is possible depends on whether your
buddy kept a copy of your lifeboat file.

### 3.1 What is the lifeboat?

When BackupBuddy is running, it periodically encrypts your full node state (node key,
root directory cap, catalog) and sends a copy to your agent machines. This is the
**lifeboat file** (`lifeboat.enc`), and it is stored at the path configured in your
`backup.cfg` under `lifeboat_path` (default: `/etc/backup-buddy/lifeboat.enc`).

> **Important:** The lifeboat file is encrypted with a key that is stored only in memory
> on your gatekeeper. If the gatekeeper is completely gone, the lifeboat key is also gone
> unless you have `recovery-kit.enc`. There is currently no way to decrypt the lifeboat
> without the recovery kit.

This means: **there is no recovery path if both the gatekeeper and `recovery-kit.enc`
are lost.**

### 3.2 What you can do

If you have lost everything, your options are:

1. **Check for a copy of `recovery-kit.enc`** — look in your email attachments, USB drives,
   password manager, or any second computer where you may have saved it.

2. **Check for the recovery kit on your agent machine** — by default BackupBuddy does not
   store the recovery kit on the agent, but if you manually copied it there it may still exist:
   ```bash
   ls /etc/backup-buddy/
   ```

3. **Contact your buddy** — they cannot decrypt your data (your files are zero-knowledge),
   but they can help you verify what is still stored on the grid and give you time to
   locate your recovery kit.

4. **Start fresh** — if the data is truly unrecoverable, install a new gatekeeper, go
   through the wizard again, and begin backing up from scratch. Ask your buddy to free up
   the space your old node was using from their Buddies page.

---

## Quick reference

| Situation | What you need | Go to |
|-----------|--------------|-------|
| Restore a file from a running gatekeeper | Gatekeeper online | Section 1 |
| Rebuild after gatekeeper hardware failure | `recovery-kit.enc` + passphrase | Section 2 |
| Lost everything including recovery kit | Your buddy's patience | Section 3 |

---

## Re-downloading your recovery kit

If your gatekeeper is still running and you cannot find your original `recovery-kit.enc`,
you can download it again from the dashboard:

1. Open your gatekeeper dashboard → **Settings** → **Lifeboat**.
2. Click **Download recovery kit**.

Save the downloaded file somewhere safe before you need it. This is the same file you
received at the end of the setup wizard.

---

_BackupBuddy — back up what matters._
