#!/bin/bash
# Setup script for Hyperliquid Smart Money Tracker

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Setting up Hyperliquid Smart Money Tracker..."
echo "Project directory: $PROJECT_DIR"

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
REQUIRED_VERSION="3.10"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "Error: Python $REQUIRED_VERSION or higher is required (found $PYTHON_VERSION)"
    exit 1
fi

echo "Python version: $PYTHON_VERSION"

# Create virtual environment
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$PROJECT_DIR/.venv"
fi

# Activate virtual environment
source "$PROJECT_DIR/.venv/bin/activate"

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install -e "$PROJECT_DIR"

# Create .env file if not exists
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "Creating .env file from template..."
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo ""
    echo "IMPORTANT: Edit .env file and add your Gmail App Password:"
    echo "  $PROJECT_DIR/.env"
fi

# Create directories
mkdir -p "$PROJECT_DIR/data"
mkdir -p "$PROJECT_DIR/logs"

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env file with your Gmail credentials:"
echo "   SMTP_USERNAME=your-gmail@gmail.com"
echo "   SMTP_PASSWORD=your-app-password"
echo ""
echo "2. Review config/config.yaml for wallet addresses and settings"
echo ""
echo "3. Run the tracker:"
echo "   source .venv/bin/activate"
echo "   python -m tracker"
echo ""
echo "4. (Optional) Install as macOS service:"
echo "   ./scripts/install-launchd.sh"
