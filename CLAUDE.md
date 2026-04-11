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

## Windows Specifics — Low-Level Keyboard Hook

**Why not `globalShortcut.register('Super+V')`?** On Windows, Windows Clipboard History (Settings → System → Clipboard) claims Win+V at the RegisterHotKey layer. Electron's globalShortcut uses RegisterHotKey internally, so registration silently fails — the return value is `false`. Same applies to Win+Numpad1-9. You cannot win this fight with the high-level API.

**What we do instead.** `lib/windows-hook-worker.js` installs a `WH_KEYBOARD_LL` hook via koffi FFI on a dedicated worker thread. LL hooks sit *below* system shortcut handling, so we see (and can swallow) Win+V before Windows Clipboard History does. This matches the approach the pre-Electron Python version used with ctypes.

**Worker thread, not main thread.** The hook must be installed on a thread that runs a GetMessage loop — Windows delivers LL hook calls via messages posted to the installing thread's queue. Running it on Electron's main thread works for Win+V but risks hitting `LowLevelHooksTimeout` (default 300ms) whenever JS blocks the main thread, at which point Windows silently unregisters the hook. A dedicated worker with a tight GetMessage loop avoids that entirely.

**SharedArrayBuffer for state.** The worker is synchronously blocked inside `GetMessageW`, so it can't process messages from the main thread via `parentPort.on('message')`. For decisions that need real-time state (is the popup open? is slot N assigned?), main thread writes to a `SharedArrayBuffer` and the worker reads it from inside the hook callback. Layout: `[popupVisible, slot1..slot9, reserved]` as `Uint8Array`.

**Numpad UX.** Plain Num1-9 (no Win) is intercepted only if:
- The popup is open (→ assign current item to slot), OR
- The slot is already assigned (→ paste slot contents).

Otherwise the key passes through so normal numpad typing works. Main thread calls `windowsHook.setPopupVisible()` on show/hide and `windowsHook.setSlotAssignments(Set)` whenever history is saved (`syncHookState()` in main.js).

**koffi over native addon.** koffi is pure JS FFI with prebuilt binaries for every Electron ABI — no `electron-rebuild`, no C++ toolchain, no breakage across Electron upgrades. The Node modules that *do* block system shortcuts all require native compilation or don't actually block Windows-reserved keys (`node-global-key-listener` explicitly can't override them).

**Shutdown.** `worker.terminate()` kills the thread; Windows reclaims the hook on thread exit. A cleaner `PostThreadMessageW(WM_QUIT)` path would need the worker thread ID exposed via postMessage at startup — not worth the extra FFI surface for a quit-only code path.

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

- **`start.sh`/`start.bat`** — call kill script, verify no leftover processes, abort if kill failed, then launch `npx electron .` in background
- **`kill.sh`/`kill.bat`** — match processes by `$SCRIPT_DIR/node_modules/electron` commandline to avoid killing other Electron apps (VS Code, Discord, etc.). Verify with a second check and force-retry if something survived
- **`kill.bat` WMIC path matching**: WQL uses `\\` as escaped backslash in LIKE patterns. Pattern is built by `set "SCRIPT_DIR=%SCRIPT_DIR:\=\\%"` then used via delayed expansion inside the WMIC query — `wmic process where "... commandline like '%%!SCRIPT_DIR!node_modules%%'"`
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
