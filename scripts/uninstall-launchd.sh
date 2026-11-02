#!/usr/bin/env bash
# Stop and remove the LaunchAgent installed by install-launchd.sh.
set -euo pipefail

LABEL="com.joseph.kanban-board"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Removed $LABEL"
