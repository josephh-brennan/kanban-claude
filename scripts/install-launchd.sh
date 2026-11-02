#!/usr/bin/env bash
# Install a macOS LaunchAgent so server.py starts at login and restarts if it exits.
# Re-running this script replaces the existing agent. Uninstall with uninstall-launchd.sh.
set -euo pipefail

LABEL="com.joseph.kanban-board"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$(command -v python3)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$REPO_DIR/data/logs"
DOMAIN="gui/$(id -u)"

mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$REPO_DIR/server.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$REPO_DIR</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/server.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/server.log</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST" >/dev/null

# Replace any previously loaded copy, then start this one.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"

echo "Installed $LABEL (python: $PYTHON)"
echo "Plist: $PLIST"
echo "Board: http://localhost:8765   Logs: $LOG_DIR/server.log"
