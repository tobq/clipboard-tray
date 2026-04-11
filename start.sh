#!/bin/bash
cd "$(dirname "$0")"

# Kill any existing instance and verify
./kill.sh

# Double check nothing left
if pgrep -f "$(pwd)/node_modules/electron" >/dev/null 2>&1; then
  echo "ERROR: Failed to kill existing instance. Aborting."
  exit 1
fi

# Start in background
nohup npx electron . > /dev/null 2>&1 &
echo "Clipboard tray started (PID $!)."
