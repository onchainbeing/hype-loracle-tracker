#!/bin/bash
# Install launchd service for auto-start on macOS

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_NAME="com.hype.tracker.plist"
PLIST_SRC="$PROJECT_DIR/launchd/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "Installing Hyperliquid Tracker as launchd service..."

# Check if plist exists
if [ ! -f "$PLIST_SRC" ]; then
    echo "Error: Plist file not found at $PLIST_SRC"
    exit 1
fi

# Get Python path
PYTHON_PATH="$PROJECT_DIR/.venv/bin/python"
if [ ! -f "$PYTHON_PATH" ]; then
    echo "Error: Virtual environment not found. Run setup.sh first."
    exit 1
fi

# Create a modified plist with correct paths
echo "Creating plist with correct paths..."

cat > "$PLIST_DST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.hype.tracker</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_PATH</string>
        <string>-m</string>
        <string>tracker</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>$PROJECT_DIR/src</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>ThrottleInterval</key>
    <integer>30</integer>

    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/logs/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/logs/launchd.err.log</string>

    <key>Nice</key>
    <integer>10</integer>
</dict>
</plist>
EOF

# Unload if already loaded
if launchctl list | grep -q "com.hype.tracker"; then
    echo "Unloading existing service..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# Load service
echo "Loading service..."
launchctl load "$PLIST_DST"

echo ""
echo "Service installed successfully!"
echo ""
echo "Commands:"
echo "  Start:   launchctl start com.hype.tracker"
echo "  Stop:    launchctl stop com.hype.tracker"
echo "  Status:  launchctl list | grep hype"
echo "  Logs:    tail -f $PROJECT_DIR/logs/launchd.out.log"
echo ""
echo "To uninstall:"
echo "  launchctl unload ~/Library/LaunchAgents/$PLIST_NAME"
echo "  rm ~/Library/LaunchAgents/$PLIST_NAME"
