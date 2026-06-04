# BackupBuddy Installation Guide

---

## 1. What is BackupBuddy?

BackupBuddy lets you and a friend back up each other's files — without paying for cloud storage
and without trusting any company with your data. You each run a small server (a "gatekeeper node")
at home. Your files are encrypted on your own machine, split into pieces, and stored across your
friends' nodes. Only you can put those pieces back together. If your gatekeeper crashes, you can
rebuild it from scratch using a recovery key you saved when you first set up.

---

## 2. What you need

### For each gatekeeper node (the server that coordinates backups):

- A computer, virtual machine, or small server (e.g. Proxmox VM, a spare PC, a NUC)
- **Operating system:** Ubuntu 22.04 or Ubuntu 24.04 — no other OS is supported
- **RAM:** 1 GB minimum, 2 GB recommended
- **CPU:** Any 64-bit processor — x86-64 or ARM64 (e.g. Raspberry Pi 4 or 5 running Ubuntu arm64)
- **Storage (two separate parts):**
  - A small system disk: 20 GB for the OS and BackupBuddy itself
  - A larger disk or folder where your buddy's encrypted data will be stored (at least 10 GB,
    more is better — 1 TB is typical)
- **Network:** A stable internet connection (home broadband is fine)
- **Tailscale account:** Free at [tailscale.com](https://tailscale.com) — both you and your buddy
  need an account, and you must both be on the same Tailscale network (you invite your buddy
  to your network from the Tailscale admin panel, or use a shared account)

### For each agent (the computer whose files you want to back up):

- A computer running **Ubuntu 22.04 or Ubuntu 24.04**
- Must be on the same local network as its gatekeeper node (same house/office)
- No special hardware requirements — any machine that can run Ubuntu will work

### Before you start:

- Create a free Tailscale account at [tailscale.com/start](https://tailscale.com/start)
- Know your gatekeeper's local IP address (e.g. `192.168.1.50`) — you can find this by
  running `hostname -I` on that machine, or checking your router's device list

---

## 3. Install the first node (gatekeeper)

Do this on the machine that will be your gatekeeper — the server that manages your cluster.

> If `git` is not installed on your machine, install it first:
> ```bash
> sudo apt-get install -y git
> ```

**Open a terminal on your gatekeeper machine and run:**

```bash
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
sudo bash /opt/backup-buddy/install/gatekeeper.sh
```

The installer takes 2–5 minutes. When it finishes, you will see:

```
  [✓] Service backup-buddy-gatekeeper is running

  Next steps:

  1. Authenticate Tailscale (required before finishing setup):
       sudo tailscale up

  2. Open the setup wizard in your browser:
       http://192.168.1.50:8080
```

**Step 3a — Connect Tailscale:**

Run the command the installer printed:

```bash
sudo tailscale up
```

Tailscale will print a URL, for example:

```
To authenticate, visit:
  https://login.tailscale.com/a/abc123
```

Open that URL in your browser and log in to your Tailscale account.
Once you approve the device, the terminal prompt returns. Tailscale is now active.

**Step 3b — Open the setup wizard:**

On any computer on your home network, open a browser and go to the address the
installer printed (replace `192.168.1.50` with your actual IP):

```
http://192.168.1.50:8080
```

The BackupBuddy setup wizard will open. Continue to section 4.

---

## 4. Open the wizard and create a cluster

The wizard walks you through five steps. Your progress is saved automatically — if you
close the browser and come back, you pick up where you left off.

**Step 1 — Choose your role:**

Select **"Start a new cluster"** (you are the first buddy). Click **Continue**.

**Step 2 — Name your node:**

- **Node ID:** A short identifier for this node, e.g. `anders-home`. Use only lowercase
  letters, numbers, and hyphens. This is used internally.
- **Display name:** A friendly name your buddies will see, e.g. `Anders home node`.

Click **Continue**.

**Step 3 — Choose where to store your buddy's data:**

Enter the full path to a folder where your buddy's encrypted data will be stored.
Then set a quota in GB — this is how much space you are willing to give.

The simplest choice is `/var/lib/backup-buddy/storage` — the installer creates this
directory and sets the correct ownership automatically, so the wizard can use it
without any extra steps.

If you prefer a path on a separate disk (for example `/mnt/buddy-storage`), the parent
directory must be writable by the `backupbuddy` service user. Directories like `/mnt`
are owned by root by default, so the wizard cannot create subdirectories inside them.
Create the directory manually before entering it in the wizard:

```bash
sudo mkdir -p /mnt/buddy-storage
sudo chown backupbuddy:backupbuddy /mnt/buddy-storage
```

Replace `/mnt/buddy-storage` with your chosen path.

> The storage directory is automatically excluded from your own backups — you cannot
> accidentally back it up.

Click **Continue**.

**Step 4 — Choose a backup profile:**

For most people, leave this set to **Adaptive** (the default). It adjusts automatically
as your group grows. Click **Continue**.

**Step 5 — Notifications and recovery passphrase:**

- **Notifications** (optional): You can enter an email address or webhook URL to receive
  alerts if a backup fails. You can skip this now and set it up later.
- **Recovery passphrase** (required): Choose a strong passphrase and enter it twice.
  This passphrase protects your recovery key — the only way to rebuild your cluster
  if your gatekeeper is destroyed. **Write it down and store it somewhere safe.**
  A password manager works well. If you lose this passphrase, you cannot recover your backups.

Click **Finish setup**.

BackupBuddy will take about 30 seconds to finish setting up. When it is done, you will see:

**Save your recovery key:**

A file called `recovery-kit.enc` will be shown for download. Click **Download as file**
and save it somewhere safe — a USB drive, a password manager, or a second computer.
This file is useless without your passphrase, so it is safe to store digitally.

Click **"I have saved my recovery key"** — you must click this before you can continue.

**Your invite code:**

After saving your recovery key, you will see an invite code, for example:

```
kaffe-trumpet-7
```

This code is for inviting your first buddy. It expires in 48 hours and can only be used once.
Copy it — you will need it in section 6.

Click **"Go to dashboard"**.

**After setup — use the Tailscale address:**

Once setup is complete, BackupBuddy binds its dashboard to your Tailscale address for
security. The new address will be shown on the completion screen, for example:

```
http://100.64.0.1:8080
```

Use this Tailscale address to open your dashboard from now on.
You can find your Tailscale IP at any time by running `tailscale ip` on the gatekeeper.

---

## 5. Install an agent on the computer you want to back up

The agent is a small program that watches your files and sends them to your gatekeeper.
Run this on the computer whose files you want to back up (it must be on the same local
network as your gatekeeper).

> If `git` is not installed, install it first: `sudo apt-get install -y git`

**Open a terminal on the computer you want to back up and run:**

```bash
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
sudo bash /opt/backup-buddy/install/agent.sh
```

> **Running in a container or over SSH without a TTY?**
> Pass the required values as environment variables to skip interactive prompts:
> ```bash
> BB_GATEKEEPER_IP=<gatekeeper-ip> BB_AGENT_NAME=<name> sudo -E bash /opt/backup-buddy/install/agent.sh
> ```
> `BB_GATEKEEPER_IP` — the LAN IP of this agent's gatekeeper (e.g. `10.99.0.11`)
> `BB_AGENT_NAME` — a short name for this machine (e.g. `anders-laptop`)

The installer will ask you two questions:

```
  What is your gatekeeper's IP address? [192.168.1.50]
  What should this agent be called? [this-pc]
```

- For the IP address, enter your gatekeeper's local IP (e.g. `192.168.1.50`).
- For the agent name, enter something descriptive (e.g. `anders-laptop`).

Press Enter to confirm each answer.

**Step 5a — Tell the agent which folders to back up:**

Open the agent configuration file in a text editor:

```bash
sudo nano /etc/backup-buddy/backup.cfg
```

Replace the file contents with the complete example below, filling in your own values:

```ini
[schedule]
full_scan = 24h
stability_minutes = 1

[backup]
/home/yourname/documents
/home/yourname/pictures

[exclude]

[node]
share_log = false

[gatekeeper]
url = http://<gatekeeper-ip>:8081
token = <token-from-gatekeeper-dashboard>
name = <this-machine-name>
lifeboat_path = /etc/backup-buddy/lifeboat.enc

[lifeboat_server]
enabled = true
port = 8082
```

- `url` — replace `<gatekeeper-ip>` with your gatekeeper's local IP (e.g. `192.168.1.50`).
- `token` — found in the gatekeeper dashboard under **Settings → Agent token**.
- `name` — a short, unique name for this machine within the cluster (e.g. `anders-laptop`).
  Each agent in the cluster must have a different name.

Save the file (in nano: press `Ctrl+O`, then Enter, then `Ctrl+X`).

**Step 5b — Connect the agent to the gatekeeper:**

The agent generates a secret token during installation. You need to give this token
to your gatekeeper. The installer printed the token at the end:

```
  2. Add this token to your gatekeeper's gatekeeper.cfg:

       [agent_api]
       token = abc123...
```

On your **gatekeeper machine**, open the gatekeeper configuration file:

```bash
sudo nano /etc/backup-buddy/gatekeeper.cfg
```

Find the `[agent_api]` section and update the token:

```ini
[agent_api]
enabled = true
port = 8081
token = abc123...
```

Save the file, then restart the gatekeeper:

```bash
sudo systemctl restart backup-buddy-gatekeeper
```

**Step 5c — Start the agent:**

```bash
sudo systemctl start backup-buddy-agent
```

The agent will start monitoring your folders. New and changed files will be queued for
backup automatically. The first backup may take a while depending on how many files you have.

---

## 6. Invite a friend (buddy) and have them join your cluster

To join your cluster, your buddy needs:

- Their own gatekeeper machine (with Ubuntu 22.04 or 24.04)
- To be on the same Tailscale network as you (you can invite them from the
  [Tailscale admin panel](https://login.tailscale.com/admin/users))
- Your invite code (from section 4) and your gatekeeper's Tailscale address

**Your buddy runs the installer on their machine:**

> If `git` is not installed, install it first: `sudo apt-get install -y git`

```bash
git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
sudo bash /opt/backup-buddy/install/gatekeeper.sh
```

After the installer finishes, your buddy runs:

```bash
sudo tailscale up
```

And logs in to the shared Tailscale network using the URL Tailscale provides.

**Your buddy opens the wizard:**

They open the wizard at `http://<their-gatekeeper-LAN-IP>:8080`.

- **Step 1:** Select **"Join an existing cluster"** and click **Continue**.
- **Join screen:** Enter the invite code you gave them (e.g. `kaffe-trumpet-7`) and your
  gatekeeper's Tailscale address (e.g. `http://100.64.0.1:8080`). Click **Continue**.
- **Steps 2–5:** Same as starting a new cluster — name their node, choose a storage folder,
  choose a profile, and optionally set up notifications.

When the wizard finishes, their node is part of your cluster. You will both see each other
in the dashboard under **"Buddies"**.

> After joining, the invite code is used up. If you want to add another buddy, generate
> a new invite code from the **Buddies** page in your dashboard.

---

## 7. Verify your first backup was made

Open your gatekeeper dashboard (at `http://<tailscale-ip>:8080`).

The dashboard shows:

- **Connected nodes:** Your gatekeeper and any buddies that are online
- **Last backup:** When the most recent file was successfully backed up
- **Files backed up:** Total number of files in your backup catalog

A backup has been made when you see a timestamp under **"Last backup"** and a non-zero
file count. The first backup may take several hours if you have many large files.

You can also check the agent log on your agent machine:

```bash
journalctl -u backup-buddy-agent -f
```

Lines marked `SUCCESS` confirm files that have been backed up. Lines marked `FAILED`
indicate files that could not be backed up — the reason is shown at the end of the line.

---

## 8. How to restore a file

Open your gatekeeper dashboard and click **"Restore"** in the navigation menu.

**To restore a single file:**

1. Type part of the file name or path in the search box.
2. Click the file you want to restore.
3. Enter a destination folder on the gatekeeper (e.g. `/home/yourname/restored-files`).
4. Click **"Restore file"**.

BackupBuddy will download and reassemble the file from your buddies' nodes. When the
restore is done, the file will appear in the destination folder you chose.

**To restore a whole folder:**

1. Leave the search box empty and click **"Restore folder"**.
2. Enter the original folder path (e.g. `/home/yourname/documents`).
3. Enter a destination folder on the gatekeeper.
4. Click **"Restore folder"**.

> Restored files land on the gatekeeper machine. Copy them to your laptop or original
> machine using a USB drive, a file share (e.g. Samba), or `scp`.

---

## 9. Troubleshooting

### The wizard does not open after install

**Cause:** The address printed by the installer may be wrong if the machine has multiple
network interfaces.

**Fix:** Find your machine's local IP by running `hostname -I` on the gatekeeper. Use the
IP address that matches your home network (usually `192.168.x.x`). Then open
`http://<that-IP>:8080`.

---

### The dashboard does not open after the wizard completes

**Cause:** After setup, BackupBuddy switches to listening on the Tailscale address only.
If Tailscale is not running or not connected, the dashboard is unreachable.

**Fix:** On the gatekeeper, run:

```bash
sudo tailscale up
tailscale ip
```

Log in to Tailscale if prompted, then use the IP address printed by `tailscale ip` to
open the dashboard: `http://<tailscale-ip>:8080`.

---

### The agent is installed but no files appear in the dashboard

There are two common reasons:

**a) The agent has not been started yet.**

Run on the agent machine:

```bash
sudo systemctl status backup-buddy-agent
```

If the status is `inactive`, start it:

```bash
sudo systemctl start backup-buddy-agent
```

**b) No backup paths are configured in `backup.cfg`.**

Check the `[backup]` section of `/etc/backup-buddy/backup.cfg` — it must contain at
least one folder path that is not commented out (no `#` at the start of the line).

---

### The agent connects but the gatekeeper shows "token mismatch" or "unauthorized"

**Cause:** The token in the agent's `backup.cfg` does not match the token in the gatekeeper's
`gatekeeper.cfg`.

**Fix:**

1. On the agent machine, find the token:
   ```bash
   sudo grep token /etc/backup-buddy/backup.cfg
   ```
2. On the gatekeeper, open `/etc/backup-buddy/gatekeeper.cfg` and paste that token
   into the `[agent_api]` section under `token =`.
3. Restart the gatekeeper:
   ```bash
   sudo systemctl restart backup-buddy-gatekeeper
   ```

---

### I lost my recovery key file

If you lost `recovery-kit.enc` but still have your passphrase and your gatekeeper is
running, **no action is needed** — your backups are intact.

If your gatekeeper machine is destroyed and you have lost both `recovery-kit.enc` and
your passphrase, your backups cannot be recovered. This is why saving both is critical.

If you still have access to your gatekeeper and want to generate a new recovery kit,
contact the BackupBuddy project for guidance — this will be added to the GUI in a future release.

---

### A buddy's node shows as offline in my dashboard

**Cause:** Tailscale may have disconnected on their machine, or their gatekeeper service
may have stopped.

**Fix (tell your buddy to run on their gatekeeper):**

```bash
sudo tailscale up
sudo systemctl status backup-buddy-gatekeeper
sudo systemctl start backup-buddy-gatekeeper
```

If the service fails to start, check the logs for an error message:

```bash
journalctl -u backup-buddy-gatekeeper -n 50
```

---

_BackupBuddy — back up what matters._
