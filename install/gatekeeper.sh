#!/usr/bin/env bash
# BackupBuddy gatekeeper install script
# Usage:  git clone https://github.com/MrBumbe/BackupBuddy.git /opt/backup-buddy
#         sudo bash /opt/backup-buddy/install/gatekeeper.sh
#
# Supported OS: Ubuntu 22.04 (jammy), Ubuntu 24.04 (noble)
# Idempotent: safe to run multiple times — each step is a no-op if already done.
# No secrets, keys, or tokens are generated here — all deferred to the wizard.

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────

REPO_URL="https://github.com/MrBumbe/BackupBuddy.git"
INSTALL_DIR="/opt/backup-buddy"
CONFIG_DIR="/etc/backup-buddy"
DATA_DIR="/var/lib/backup-buddy"
SERVICE_USER="backupbuddy"
SERVICE_GROUP="backupbuddy"
SERVICE_NAME="backup-buddy-gatekeeper"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
WEB_PORT=8080

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
    info "Installing base packages (curl, git, build tools)..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        curl git python3-venv build-essential python3-dev libffi-dev libssl-dev
    success "Base packages ready"
}

install_tailscale() {
    if command -v tailscale &>/dev/null; then
        success "Tailscale already installed ($(tailscale version 2>/dev/null | head -1))"
        return 0
    fi
    info "Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    success "Tailscale installed"
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
        git -C "$INSTALL_DIR" reset --quiet --hard origin/master
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

    # Remove any corrupted distributions left by a previous partial install.
    # pip warns about these with "Ignoring invalid distribution ~name" on every
    # subsequent run. Safe to remove: package names starting with ~ are always
    # invalid and are never created by a successful install.
    find "${INSTALL_DIR}/.venv" -name "~*" -exec rm -rf {} + 2>/dev/null || true

    info "Installing Python dependencies (this may take a minute)..."
    "${INSTALL_DIR}/.venv/bin/pip" install --quiet --upgrade pip
    # The Tahoe-LAFS fork must be installed in editable mode (-e) so that
    # src/allmydata/ is importable directly. Non-editable install produces
    # empty 0-byte stub files for all modules and the tahoe CLI binary.
    # The second pass force-reinstalls pinned packages from requirements.txt
    # to replace any empty stubs the Tahoe fork installed for its dependencies
    # (cryptography, fastapi, etc.).
    "${INSTALL_DIR}/.venv/bin/pip" install --quiet -e "${INSTALL_DIR}"
    "${INSTALL_DIR}/.venv/bin/pip" install --quiet --force-reinstall \
        -r "${INSTALL_DIR}/requirements.txt"
    # Verify no 0-byte stub .py files remain — a partial force-reinstall
    # (network blip, disk pressure, SIGKILL) leaves stubs intact and causes
    # confusing ImportError at gatekeeper startup.
    zero_byte_files=$(find "${INSTALL_DIR}/.venv/lib" -name "*.py" \
        -not -name "__init__.py" \
        -not -path "*/tests/*" \
        -not -path "*/test/*" \
        -size 0 2>/dev/null)
    if [ -n "$zero_byte_files" ]; then
        warn "Venv integrity check failed — 0-byte .py files found:"
        echo "$zero_byte_files" >&2
        die "Re-run this installer to fix."
    fi
    success "Venv integrity check passed"
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
        --comment "BackupBuddy gatekeeper service" \
        "$SERVICE_USER"
    success "Service user '$SERVICE_USER' created"
}

create_directories() {
    for dir in "$CONFIG_DIR" "$DATA_DIR" "$DATA_DIR/storage"; do
        mkdir -p "$dir"
        chown "${SERVICE_USER}:${SERVICE_GROUP}" "$dir"
        chmod 750 "$dir"
    done
    success "Directories ready: $CONFIG_DIR, $DATA_DIR, $DATA_DIR/storage"
}

write_systemd_unit() {
    # Use a heredoc so the content is easy to read and compare.
    NEW_UNIT=$(cat <<EOF
[Unit]
Description=BackupBuddy Gatekeeper
Documentation=https://github.com/MrBumbe/BackupBuddy
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/python -m gatekeeper.main \\
    --data-dir ${DATA_DIR} \\
    --config ${CONFIG_DIR}/gatekeeper.cfg
Restart=always
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

enable_and_start_service() {
    systemctl enable --quiet "$SERVICE_NAME"

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        info "Service already running — restarting to apply any updates..."
        systemctl restart "$SERVICE_NAME"
    else
        systemctl start "$SERVICE_NAME"
    fi

    success "Service $SERVICE_NAME is running"
}

detect_lan_ip() {
    # hostname -I lists all non-loopback IPs; the first is normally the LAN IP.
    hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1"
}

open_browser_if_desktop() {
    local url="$1"
    if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
        xdg-open "$url" &>/dev/null &
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
    echo ""
    echo "BackupBuddy Gatekeeper Installer"
    echo "================================="
    echo ""

    check_root
    check_os
    install_system_packages
    install_tailscale
    find_or_install_python
    clone_or_update_repo
    setup_venv
    create_service_user
    create_directories
    write_systemd_unit
    enable_and_start_service

    LAN_IP=$(detect_lan_ip)
    WIZARD_URL="http://${LAN_IP}:${WEB_PORT}"

    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo "  BackupBuddy is running."
    echo ""
    echo "  Next steps:"
    echo ""
    echo "  1. Authenticate Tailscale (required before finishing setup):"
    echo "       sudo tailscale up"
    echo ""
    echo "  2. Open the setup wizard in your browser:"
    echo "       ${WIZARD_URL}"
    echo "═══════════════════════════════════════════════════════"
    echo ""

    open_browser_if_desktop "$WIZARD_URL"
}

main
