# Clipboard Tray - CLAUDE.md

## Architecture

- **Electron app** — main process (`main.js`) handles clipboard polling, tray, global shortcuts, IPC, sync
- **Preload bridge** (`preload.js`) — contextBridge exposing API to renderer
- **Single-file UI** (`index.html`) — loaded via `loadFile`, images served via `clip-img://` custom protocol
- **Cross-platform**: macOS + Windows. Platform differences handled inline with `process.platform` checks
- Data: `clipboard-history.json`, `clipboard-images/`, `clipboard-settings.json`

## Key Data Model

- **`pinned` field** on history items: `false` (not pinned), `true` (pinned, no numpad), or `1-9` (integer, numpad assigned)
- In JS, `typeof true === 'boolean'` and `typeof 1 === 'number'` — no Python `True == 1` gotcha. `hasNumpadSlot()` uses `typeof item.pinned === 'number'`.
- **`group` field** on history items: string group name or absent. Groups list stored in `settings.groups`.
- **Content-addressed images**: filenames are md5 hash of PNG content (`{hash}.png`), naturally deduplicates.

## Clipboard Operations

- **Polling** every 400ms via `clipboard.readImage()` / `clipboard.readText()`
- **`addToHistory(entry, matchFn)`** — shared helper that deduplicates, preserves pinned/group metadata, and prunes
- **`setClipboardToItem(item)`** — shared helper to write text or image to clipboard
- **Backup/restore**: `backupClipboard()` saves text/html/rtf/image, `restoreClipboard()` writes them back. Used by numpad quick-paste.
- **`pollGate`** flag pauses polling during paste sequences to prevent interference

## Paste Simulation

- **macOS**: `osascript` — activates frontmost app then sends `keystroke "v" using command down`. Required because `app.dock.hide()` means our app doesn't return focus on hide.
- **Windows**: VBScript `SendKeys "^v"` via temp file + `cscript`. Faster than PowerShell.

## macOS Specifics

- **No click-away-to-close**: `app.dock.hide()` makes blur events unreliable on macOS. Close button (×) shown in header instead. Windows uses blur-to-hide normally.
- **`app.dock.hide()`** hides dock icon — tray-only app
- **Template tray icon**: `trayIcon.setTemplateImage(true)` for menu bar dark/light mode

## Google Drive Sync

- **Merge algorithm**: `mergeHistories()` unions both sides by content key (md5 of text, or image filename). On conflict, picks item with higher `metadataScore()` (numpad > pinned > unpinned, +1 for group). Tie-break by newer `ts`.
- **`syncMerge()`** runs on startup + every 30s + debounced 500ms after local changes
- **`insideSync` flag** prevents `saveHistory()`/`saveSettingsFile()` from re-triggering sync
- **Only writes if changed** — compares JSON.stringify of merged vs current to skip no-op writes
- **Images synced bidirectionally** — content-addressed filenames mean no conflicts
- **`sync_path` not synced** — excluded from remote settings write (per-machine config)
- **macOS**: detects accounts from `~/Library/CloudStorage/GoogleDrive-*/`
- **Windows**: scans drive letters for `My Drive/clipboard-tray`

## Scripts & Process Management

- **`start.sh`/`start.bat`** — kills existing, starts `npx electron .` in background
- **`kill.sh`/`kill.bat`** — matches processes by `$SCRIPT_DIR/node_modules/electron` path to avoid killing other Electron apps
- **Single-instance lock** via `app.requestSingleInstanceLock()` — second launch shows popup instead of starting duplicate
- **Auto-launch**: `app.setLoginItemSettings({ openAtLogin: true })` — toggled in Settings UI

## UI Patterns

- **`icon-btn` base class** — all small clickable icons share 24x24 rounded style. Variants: `.accent` (purple hover), `.danger` (red hover), `.close-btn` (bold ×)
- **Null-guard `it.text`** — always use `(it.text||'')` in templates
- **Filter tags**: built-in (dashed border), custom groups (solid border)
- **Confirm dialog** shared between numpad reassign, group delete, and clear all
- **Settings auto-save** — max age/size save on input change, no Save button
- **Dev auto-reload** — `fs.watch` on `index.html` triggers `reloadIgnoringCache()` (debounced 300ms)

## Debugging

- Run `npx electron .` directly (not via start.sh) to see stdout/stderr
- Main process errors go to terminal, renderer errors to DevTools (Cmd+Option+I)
