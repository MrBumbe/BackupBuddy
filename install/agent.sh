#!/usr/bin/env bash
# BackupBuddy agent install script
# Usage:  curl -sSL https://get.backupbuddy.io/agent | bash
#         sudo bash install/agent.sh
#
# Supported OS: Ubuntu 22.04 (jammy), Ubuntu 24.04 (noble)
# Idempotent: safe to run multiple times — each step is a no-op if already done.
# Generates a pre-shared token and writes it to backup.cfg.  After install, copy
# that token to gatekeeper.cfg [agent_api] token = <TOKEN> and restart the gatekeeper.

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────

REPO_URL="https://github.com/MrBumbe/BackupBuddy.git"
INSTALL_DIR="/opt/backup-buddy"
CONFIG_DIR="/etc/backup-buddy"
DATA_DIR="/var/lib/backup-buddy"
SERVICE_USER="backupbuddy"
SERVICE_GROUP="backupbuddy"
SERVICE_NAME="backup-buddy-agent"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
AGENT_CONFIG="${CONFIG_DIR}/backup.cfg"

# ── Helpers ───────────────────────────────────────────────────────────────────

info()    { printf '  [+] %s\n' "$*"; }
success() { printf '  [✓] %s\n' "$*"; }
warn()    { printf '  [!] %s\n' "$*" >&2; }
die()     { printf '  [✗] %s\n' "$*" >&2; exit 1; }

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        die "This script must be run as root. Try: sudo bash $0"
    fi
}

check_os() {
    [ -f /etc/os-release ] || die "Cannot detect OS. Supported: Ubuntu 22.04, 24.04."
    # shellcheck source=/dev/null
    . /etc/os-release
    [ "${ID:-}" = "ubuntu" ] || die "Requires Ubuntu. Detected: ${PRETTY_NAME:-unknown}"
    case "${VERSION_CODENAME:-}" in
        jammy|noble) ;;
        *) die "Supported: Ubuntu 22.04 (jammy) and 24.04 (noble). Detected: ${PRETTY_NAME:-unknown}" ;;
    esac
    success "OS: ${PRETTY_NAME}"
}

install_system_packages() {
    info "Updating package index..."
    apt-get update -qq
    info "Installing base packages (curl, git)..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl git
    success "Base packages ready"
}

find_or_install_python() {
    PYTHON=""
    for ver in python3.13 python3.12 python3.11; do
        if command -v "$ver" &>/dev/null; then
            PYTHON="$ver"
            success "Python found: $PYTHON ($($PYTHON --version 2>&1))"
            return 0
        fi
    done

    # Python 3.11+ not found — install from deadsnakes PPA (needed on Ubuntu 22.04)
    info "Python 3.11+ not found. Installing from deadsnakes PPA..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq software-properties-common
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3.11 python3.11-venv python3.11-dev
    PYTHON="python3.11"
    success "Python installed: $PYTHON ($($PYTHON --version 2>&1))"
}

clone_or_update_repo() {
    if [ -d "${INSTALL_DIR}/.git" ]; then
        info "Updating BackupBuddy..."
        git -C "$INSTALL_DIR" fetch --quiet origin
        git -C "$INSTALL_DIR" reset --quiet --hard origin/main
        success "BackupBuddy updated at $INSTALL_DIR"
    else
        info "Cloning BackupBuddy..."
        git clone --quiet "$REPO_URL" "$INSTALL_DIR"
        success "BackupBuddy cloned to $INSTALL_DIR"
    fi
}

setup_venv() {
    if [ ! -d "${INSTALL_DIR}/.venv" ]; then
        info "Creating Python virtual environment..."
        "$PYTHON" -m venv "${INSTALL_DIR}/.venv"
    fi

    info "Installing Python dependencies (this may take a minute)..."
    "${INSTALL_DIR}/.venv/bin/pip" install --quiet --upgrade pip
    "${INSTALL_DIR}/.venv/bin/pip" install --quiet \
        "${INSTALL_DIR}" \
        -r "${INSTALL_DIR}/requirements.txt"
    success "Python dependencies installed"
}

create_service_user() {
    if id -u "$SERVICE_USER" &>/dev/null; then
        success "Service user '$SERVICE_USER' already exists"
        return 0
    fi
    useradd \
        --system \
        --no-create-home \
        --shell /usr/sbin/nologin \
        --comment "BackupBuddy service" \
        "$SERVICE_USER"
    success "Service user '$SERVICE_USER' created"
}

create_directories() {
    for dir in "$CONFIG_DIR" "$DATA_DIR"; do
        mkdir -p "$dir"
        chown "${SERVICE_USER}:${SERVICE_GROUP}" "$dir"
        chmod 750 "$dir"
    done
    success "Directories ready: $CONFIG_DIR, $DATA_DIR"
}

# ── Interactive questions ──────────────────────────────────────────────────────
# Read from /dev/tty so this works even when piped through curl | bash.

ask_questions() {
    # Default agent name: system hostname
    local default_name
    default_name="$(hostname -s 2>/dev/null || echo "agent")"

    exec 3</dev/tty

    printf '\n'
    printf '  What is your gatekeeper'"'"'s IP address? [192.168.1.50] '
    read -r GATEKEEPER_IP <&3
    GATEKEEPER_IP="${GATEKEEPER_IP:-192.168.1.50}"

    printf '  What should this agent be called? [%s] ' "$default_name"
    read -r AGENT_NAME <&3
    AGENT_NAME="${AGENT_NAME:-$default_name}"

    exec 3<&-
}

generate_token() {
    if command -v openssl &>/dev/null; then
        openssl rand -hex 32
    else
        "$PYTHON" -c "import secrets; print(secrets.token_hex(32))"
    fi
}

write_backup_cfg() {
    if [ -f "$AGENT_CONFIG" ]; then
        success "backup.cfg already exists — not overwritten (preserving user edits)"
        return 0
    fi

    local token
    token="$(generate_token)"

    # Store token for the completion message (used after this function returns)
    GENERATED_TOKEN="$token"

    # Write config with 0600 permissions — contains the pre-shared token
    install -m 0600 -o "$SERVICE_USER" -g "$SERVICE_GROUP" /dev/null "$AGENT_CONFIG"
    cat > "$AGENT_CONFIG" <<EOF
# BackupBuddy agent configuration
# Add the folders you want to back up under [backup].
# One folder per line. Subfolders are included automatically.
#
# When you are done editing, restart the agent:
#   systemctl restart ${SERVICE_NAME}

[schedule]
# How often to scan all backup folders (default: 24h)
full_scan = 24h
# How long a file must be unchanged before it is queued for backup (default: 30)
stability_minutes = 30

[backup]
# /home/yourname/documents
# /home/yourname/pictures
# /mnt/nas/important

[exclude]
# *.tmp
# .DS_Store

[node]
# Share the backup log with your gatekeeper? (true/false)
# If true, your gatekeeper can see which files succeeded or failed.
# Default: false
share_log = false

[gatekeeper]
url = http://${GATEKEEPER_IP}:8081
token = ${token}
name = ${AGENT_NAME}
lifeboat_path = /etc/backup-buddy/lifeboat.enc

[lifeboat_server]
enabled = true
port = 8082
EOF

    chown "${SERVICE_USER}:${SERVICE_GROUP}" "$AGENT_CONFIG"
    chmod 0600 "$AGENT_CONFIG"
    success "backup.cfg written to $AGENT_CONFIG"
}

read_existing_token() {
    # Extract token from existing backup.cfg so we can show it in the completion message.
    GENERATED_TOKEN=""
    if [ -f "$AGENT_CONFIG" ]; then
        GENERATED_TOKEN="$(grep -E '^\s*token\s*=' "$AGENT_CONFIG" | head -1 | sed 's/.*=\s*//')"
    fi
}

write_systemd_unit() {
    NEW_UNIT=$(cat <<EOF
[Unit]
Description=BackupBuddy Agent
Documentation=https://github.com/MrBumbe/BackupBuddy
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/python -m agent.main \\
    --config ${AGENT_CONFIG}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
EOF
)

    if [ -f "$UNIT_FILE" ] && [ "$(cat "$UNIT_FILE")" = "$NEW_UNIT" ]; then
        success "systemd unit already up to date"
        return 0
    fi

    printf '%s\n' "$NEW_UNIT" > "$UNIT_FILE"
    systemctl daemon-reload
    success "systemd unit written to $UNIT_FILE"
}

enable_service() {
    # Enable but do NOT start — the agent requires at least one [backup] path
    # before it can start.  The user must edit backup.cfg first.
    systemctl enable --quiet "$SERVICE_NAME"
    success "Service $SERVICE_NAME enabled (will start after you edit backup.cfg)"
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
    echo ""
    echo "BackupBuddy Agent Installer"
    echo "==========================="
    echo ""

    check_root
    check_os
    install_system_packages
    find_or_install_python
    clone_or_update_repo
    setup_venv
    create_service_user
    create_directories
    ask_questions
    write_backup_cfg
    read_existing_token
    write_systemd_unit
    enable_service

    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo "  Done. Agent '${AGENT_NAME}' is configured."
    echo ""
    echo "  Next steps:"
    echo ""
    echo "  1. Edit /etc/backup-buddy/backup.cfg"
    echo "     Add the folders you want to back up under [backup]."
    echo ""
    echo "  2. Add this token to your gatekeeper's gatekeeper.cfg:"
    echo ""
    echo "       [agent_api]"
    echo "       token = ${GENERATED_TOKEN}"
    echo ""
    echo "     Then restart the gatekeeper:"
    echo "       systemctl restart backup-buddy-gatekeeper"
    echo ""
    echo "  3. Start the agent:"
    echo "       systemctl start ${SERVICE_NAME}"
    echo ""
    echo "  To view agent logs:"
    echo "       journalctl -u ${SERVICE_NAME} -f"
    echo "═══════════════════════════════════════════════════════"
    echo ""
}

main
