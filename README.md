# Clipboard Tray

Windows clipboard history manager that replaces the built-in Win+V. Runs as a system tray app with a frameless popup UI.

![Python 3.9](https://img.shields.io/badge/python-3.9-blue) ![Windows](https://img.shields.io/badge/platform-Windows-lightgrey)

## Features

- **Win+V popup** — frameless, always-on-top clipboard history panel positioned at cursor
- **Text & image history** — captures both, with automatic deduplication
- **Numpad quick-paste (1–9)** — assign clipboard items to numpad slots, paste from anywhere without opening the popup
- **Pin system** — star items to prevent auto-pruning, optionally assign a numpad shortcut
- **Custom groups** — label/tag items into groups, filter by group from the main view
- **Combinable filters** — Pinned, Numbered, Images, and custom group chips stack with AND logic
- **Regex search** — toggle regex mode in the search bar
- **Open in editor** — open text items in Notepad, edits saved back to history
- **Save image to Downloads** — one-click copy image to Downloads folder with path copied to clipboard
- **AHK-style clipboard juggling** — backup → set → Ctrl+V → restore, so your clipboard isn't overwritten by paste operations
- **Auto-pruning** — configurable max age (days) and max storage size (GB)
- **Settings panel** — manage numpad slots, groups, storage limits, clear all

## Install

```
install.bat
```

Installs Python dependencies and creates a Windows startup shortcut so it launches on boot.

## Usage

```
start.bat          # Kill existing instances + start in background
kill.bat           # Stop all instances
```

### Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| **Win+V** | Open/toggle clipboard popup |
| **Numpad 1–9** | Paste assigned slot (when popup closed) |
| **Numpad 1–9** | Assign selected item to slot (when popup open) |
| **Arrow keys** | Navigate items in popup |
| **Enter** | Paste selected item |
| **Escape** | Close popup |
| **1–9 in search** | Quick-paste numpad slot (when search is empty) |

## Tech

- **Python 3.9** + pywebview (WebView2 backend) for frameless popup
- **Win32 ctypes** — low-level keyboard hook (WH_KEYBOARD_LL), clipboard backup/restore (full format enumeration, like AHK's ClipboardAll)
- **pystray** — system tray icon
- **Local HTTP server** (localhost:9123) serving the HTML UI + JSON API
- Data stored in `clipboard-history.json`, images in `clipboard-images/`, settings in `clipboard-settings.json`
