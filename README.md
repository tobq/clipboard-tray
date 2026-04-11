# Clipboard Tray

Cross-platform clipboard history manager. Runs as a system tray app with a frameless popup UI.

## Features

- **Hotkey popup** — Cmd+Shift+V (Mac) / Win+V (Windows), positioned at cursor
- **Text & image history** — captures both, with automatic deduplication
- **Numpad quick-paste (1-9)** — assign clipboard items to numpad slots, paste from anywhere
- **Pin system** — star items to prevent auto-pruning, optionally assign a numpad shortcut
- **Custom groups** — label/tag items, filter by group
- **Google Drive sync** — merge-based sync across machines, no data loss
- **Regex search** — toggle regex mode in the search bar
- **Open in editor** — edit text items externally, changes saved back
- **Save image to Downloads** — one-click copy with path on clipboard
- **Clipboard juggling** — backup/set/paste/restore so your clipboard isn't overwritten
- **Auto-pruning** — configurable max age and storage size
- **Launch on startup** — toggle in settings

## Install

```bash
# macOS / Linux
./install.sh

# Windows
install.bat
```

## Usage

```bash
# macOS / Linux
./start.sh        # Kill existing + start in background
./kill.sh          # Stop

# Windows
start.bat
kill.bat
```

### Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| **Cmd+Shift+V** (Mac) / **Win+V** (Win) | Toggle popup |
| **Cmd+Numpad 1-9** / **Win+Numpad 1-9** | Quick-paste slot |
| **1-9 in search** | Quick-paste slot (when search empty) |
| **Arrow keys** | Navigate items |
| **Enter** | Paste selected item |
| **Escape** | Close popup |

## Tech

- **Electron** — cross-platform, single-instance, tray app
- **IPC** via contextBridge (no HTTP server)
- **Custom protocol** (`clip-img://`) for serving clipboard images
- Data: `clipboard-history.json`, `clipboard-images/`, `clipboard-settings.json`
