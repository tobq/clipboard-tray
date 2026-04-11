#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if command -v pkill &>/dev/null; then
  # Kill by matching the Electron binary inside our node_modules
  pkill -9 -f "$SCRIPT_DIR/node_modules/electron" 2>/dev/null
  # Also kill any node process that launched electron from our dir
  pkill -9 -f "node.*$SCRIPT_DIR" 2>/dev/null
else
  taskkill //F //IM "electron.exe" 2>/dev/null
fi

# Wait and verify
sleep 0.5
if pgrep -f "$SCRIPT_DIR/node_modules/electron" >/dev/null 2>&1; then
  echo "Warning: some processes still running, force killing..."
  pkill -9 -f "$SCRIPT_DIR/node_modules/electron" 2>/dev/null
  sleep 0.5
fi

echo "Clipboard tray stopped."
