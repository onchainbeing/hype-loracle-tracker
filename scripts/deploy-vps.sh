#!/bin/bash
#
# Hyperliquid Smart Money Tracker - VPS Deployment Script
#
# This script deploys the tracker to a fresh Ubuntu 22.04 VPS.
# Run as root or with sudo.
#
# Usage:
#   curl -sSL <raw_script_url> | sudo bash
#   or
#   sudo bash deploy-vps.sh
#

set -e

# ============================================================
# Configuration
# ============================================================
INSTALL_DIR="/opt/hype-tracker"
SERVICE_USER="tracker"
SERVICE_NAME="hype-tracker"
REPO_URL="${REPO_URL:-}"  # Set this to your git repo URL if needed
PYTHON_VERSION="3.10"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ============================================================
# Helper Functions
# ============================================================
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

check_ubuntu() {
    if ! grep -q "Ubuntu" /etc/os-release 2>/dev/null; then
        log_warn "This script is designed for Ubuntu. Other distributions may require modifications."
    fi
}

# ============================================================
# Installation Steps
# ============================================================

install_system_deps() {
    log_info "Updating system packages..."
    apt-get update

    log_info "Installing system dependencies..."
    apt-get install -y \
        python${PYTHON_VERSION} \
        python${PYTHON_VERSION}-venv \
        python3-pip \
        git \
        curl \
        wget
}

create_user() {
    if id "$SERVICE_USER" &>/dev/null; then
        log_info "User '$SERVICE_USER' already exists"
    else
        log_info "Creating service user '$SERVICE_USER'..."
        useradd --system --shell /bin/false --home-dir "$INSTALL_DIR" "$SERVICE_USER"
    fi
}

setup_directory() {
    log_info "Setting up installation directory..."

    # Create directory structure
    mkdir -p "$INSTALL_DIR"/{src,config,data,logs}

    # If we're running from the project directory, copy files
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

    if [[ -f "$PROJECT_DIR/pyproject.toml" ]]; then
        log_info "Copying project files from $PROJECT_DIR..."
        cp -r "$PROJECT_DIR/src" "$INSTALL_DIR/"
        cp "$PROJECT_DIR/pyproject.toml" "$INSTALL_DIR/"

        # Copy config if exists (but don't overwrite)
        if [[ -d "$PROJECT_DIR/config" ]]; then
            cp -rn "$PROJECT_DIR/config/"* "$INSTALL_DIR/config/" 2>/dev/null || true
        fi

        # Copy .env.example as template
        if [[ -f "$PROJECT_DIR/.env.example" ]]; then
            cp "$PROJECT_DIR/.env.example" "$INSTALL_DIR/.env.example"
        fi
    elif [[ -n "$REPO_URL" ]]; then
        log_info "Cloning from git repository..."
        git clone "$REPO_URL" "$INSTALL_DIR/repo"
        cp -r "$INSTALL_DIR/repo/src" "$INSTALL_DIR/"
        cp "$INSTALL_DIR/repo/pyproject.toml" "$INSTALL_DIR/"
        cp -rn "$INSTALL_DIR/repo/config/"* "$INSTALL_DIR/config/" 2>/dev/null || true
        rm -rf "$INSTALL_DIR/repo"
    else
        log_error "No source files found. Either run this script from the project directory or set REPO_URL."
        exit 1
    fi
}

setup_venv() {
    log_info "Creating Python virtual environment..."
    python${PYTHON_VERSION} -m venv "$INSTALL_DIR/venv"

    log_info "Installing Python dependencies..."
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
    "$INSTALL_DIR/venv/bin/pip" install -e "$INSTALL_DIR"
}

setup_config() {
    # Create .env file if not exists
    if [[ ! -f "$INSTALL_DIR/.env" ]]; then
        if [[ -f "$INSTALL_DIR/.env.example" ]]; then
            log_warn "Creating .env from .env.example - please configure it!"
            cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
        else
            log_warn "No .env file found. Creating empty one - please configure it!"
            cat > "$INSTALL_DIR/.env" << 'EOF'
# Email Configuration
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email@gmail.com
SMTP_PASSWORD=your_app_password
EMAIL_TO=recipient@example.com
EOF
        fi
        log_warn "IMPORTANT: Edit $INSTALL_DIR/.env with your configuration!"
    fi

    # Create default config.yaml if not exists
    if [[ ! -f "$INSTALL_DIR/config/config.yaml" ]]; then
        log_info "Creating default config.yaml..."
        mkdir -p "$INSTALL_DIR/config"
        cat > "$INSTALL_DIR/config/config.yaml" << 'EOF'
# Hyperliquid Smart Money Tracker Configuration

wallets:
  - name: "Example Wallet"
    address: "0x0000000000000000000000000000000000000000"
    enabled: false

notifications:
  email:
    enabled: true
  daily_summary_time: "08:00"
  timezone: "UTC"

tracking:
  position_change_threshold: 0.10  # 10% change threshold

hyperliquid:
  rest_url: "https://api.hyperliquid.xyz"
  ws_url: "wss://api.hyperliquid.xyz/ws"

logging:
  level: "INFO"
EOF
        log_warn "IMPORTANT: Edit $INSTALL_DIR/config/config.yaml with your wallets!"
    fi
}

set_permissions() {
    log_info "Setting permissions..."
    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
    chmod 700 "$INSTALL_DIR"
    chmod 600 "$INSTALL_DIR/.env" 2>/dev/null || true
}

install_systemd_service() {
    log_info "Installing systemd service..."

    # Copy service file
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if [[ -f "$SCRIPT_DIR/hype-tracker.service" ]]; then
        cp "$SCRIPT_DIR/hype-tracker.service" /etc/systemd/system/
    else
        # Create service file inline
        cat > /etc/systemd/system/hype-tracker.service << 'EOF'
[Unit]
Description=Hyperliquid Smart Money Tracker
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tracker
Group=tracker
WorkingDirectory=/opt/hype-tracker
ExecStart=/opt/hype-tracker/venv/bin/python -m tracker
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=10
TimeoutStopSec=30
MemoryMax=512M
CPUQuota=80%
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/hype-tracker/data /opt/hype-tracker/logs
PrivateTmp=true
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hype-tracker

[Install]
WantedBy=multi-user.target
EOF
    fi

    # Reload systemd
    systemctl daemon-reload
}

enable_service() {
    log_info "Enabling service to start on boot..."
    systemctl enable "$SERVICE_NAME"
}

print_summary() {
    echo ""
    echo "============================================================"
    echo -e "${GREEN}Installation Complete!${NC}"
    echo "============================================================"
    echo ""
    echo "Installation directory: $INSTALL_DIR"
    echo ""
    echo -e "${YELLOW}IMPORTANT: Before starting the service, configure:${NC}"
    echo "  1. Edit $INSTALL_DIR/.env with your email settings"
    echo "  2. Edit $INSTALL_DIR/config/config.yaml with your wallets"
    echo ""
    echo "Management commands:"
    echo "  sudo systemctl start $SERVICE_NAME     # Start the service"
    echo "  sudo systemctl stop $SERVICE_NAME      # Stop the service"
    echo "  sudo systemctl restart $SERVICE_NAME   # Restart the service"
    echo "  sudo systemctl status $SERVICE_NAME    # Check status"
    echo "  sudo journalctl -u $SERVICE_NAME -f    # View logs"
    echo ""
    echo "To start the service now:"
    echo "  sudo systemctl start $SERVICE_NAME"
    echo ""
}

# ============================================================
# Main
# ============================================================

main() {
    log_info "Starting Hyperliquid Smart Money Tracker deployment..."
    echo ""

    check_root
    check_ubuntu

    install_system_deps
    create_user
    setup_directory
    setup_venv
    setup_config
    set_permissions
    install_systemd_service
    enable_service

    print_summary
}

# Run main function
main "$@"
