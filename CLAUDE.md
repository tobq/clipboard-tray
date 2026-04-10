# Clipboard Tray - CLAUDE.md

## Architecture

- **Single-file Python app** (`clipboard-tray.py`) — HTTP server + clipboard poller + Win32 keyboard hook + pywebview popup + system tray, all in one process with threading
- **Single-file UI** (`clipboard-ui.html`) — served by the HTTP server at `localhost:9123`, also loaded by pywebview
- **Python 3.9** at `C:\Users\Tobi\AppData\Local\Programs\Python\Python39\pythonw.exe`
- **pywebview** (WebView2 backend) for frameless popup window
- **pystray** for system tray icon
- Data: `clipboard-history.json`, `clipboard-images/`, `clipboard-settings.json`

## Key Data Model

- **`pinned` field** on history items: `false` (not pinned), `true` (pinned, no numpad), or `1-9` (integer, numpad assigned)
- **Python `True == 1` gotcha**: `bool` subclasses `int`, so `True == 1` evaluates to `True`. Must use `isinstance(p, int) and not isinstance(p, bool)` to distinguish starred-only from numpad-assigned. The `has_numpad_slot()` helper handles this.
- **`group` field** on history items: string group name or absent. Groups list stored in `settings['groups']`.

## Win32 Clipboard (ctypes)

- **64-bit safety is critical**: All clipboard functions (`GetClipboardData`, `GlobalSize`, `GlobalLock`, `GlobalAlloc`, `SetClipboardData`) MUST have proper `argtypes`/`restype` declarations. Without them, Python defaults to `c_int` return type which truncates 64-bit handles/pointers, causing silent data corruption (backup captures nothing, restore empties clipboard).
- **AHK-style clipboard juggling**: `backup_clipboard()` → set content → `Ctrl+V` → `restore_clipboard()`. The `_poll_gate` threading.Event pauses the clipboard poller during this sequence to prevent interference.
- **`OpenClipboard` can fail** — always check return value before proceeding.

## Scripts & Process Management

- **`start.bat`** calls `kill.bat` then starts pythonw.exe. It's both start and restart.
- **`kill.bat`** kills both `pythonw.exe` (via taskkill) AND `python.exe` running clipboard-tray.py (via wmic). This is needed because running the script directly with `python.exe` (e.g., for debugging) creates processes that `taskkill /FI "IMAGENAME eq pythonw.exe"` won't catch.
- **Ghost tray icons**: Force-killing pythonw.exe doesn't clean up system tray icons. They persist as ghosts until hovered over. This is a Windows limitation, not a bug.
- **Running .bat from git bash**: Call directly as `C:/path/start.bat` — git bash can execute .bat files natively. Do NOT prefix with `cmd.exe /c`, it's unnecessary and the user dislikes it.

## UI Patterns

- **Actions are icon buttons on hover** (unified in `.actions` div): expand (▾/▴), edit (✎), delete (✕). Expand and edit hover accent, delete hovers red.
- **Null-guard `it.text`** in templates — some items may have missing/undefined text field. Always use `(it.text||'')` when accessing outside the `isImage` branch.
- **Filter tags**: Built-in filters (Pinned/Numbered/Grouped) shown as dashed-border chips, custom group filters as solid-border chips. Both rendered by `renderGroupFilters()`.
- **Confirm dialog** is shared between numpad reassign ("Replace") and group delete ("Delete") — `confirmYes.textContent` is set dynamically based on action type.

## Debugging

- To see Python errors, kill pythonw and run with `python.exe` instead (shows stderr).
- **Always reproduce crashes before fixing** — don't guess at the cause and push a blind fix. Run with visible stderr, trigger the crash, read the traceback.
