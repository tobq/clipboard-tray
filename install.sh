#!/bin/bash
echo "Installing clipboard-tray (Electron)..."
cd "$(dirname "$0")"
npm install
echo ""
echo "Done! Run ./start.sh to launch."
echo "Auto-start can be toggled in Settings within the app."
