# BoardClip - CLAUDE.md

## Architecture

- **Electron app** — main process (`main.js`) handles clipboard polling, tray, global shortcuts, IPC, sync
- **Preload bridge** (`preload.js`) — contextBridge exposing API to renderer
- **Single-file UI** (`index.html`) — loaded via `loadFile`, images served via `clip-img://` custom protocol
- **Cross-platform**: macOS + Windows. Platform differences handled inline with `process.platform` checks
- Data: `clipboard-history.json`, `clipboard-images/`, `clipboard-settings.json`

## Key Data Model

- **History item ids**: text items use a sha256 content key (`txt:{hash}`); image items use their content-addressed image filename (`img:{file}`).
- **`pin` field** on history items: `null`/absent means unpinned; an object means pinned. Shape is `{ number?: 1-9, groups?: string[], updatedAt?: number }`.
- **Legacy migration**: `lib/clipboard-model.js` migrates old `pinned`/`group` fields into the unified `pin` object before merging or rendering.
- **Groups**: group names live in `settings.groups`; item membership lives in `item.pin.groups`.
- **Tombstones**: deleted items and groups are retained for 30 days in settings so sync cannot resurrect removals from stale providers.
- **Version-guarded delete tombstones (2026-07-14)**: because ids are content hashes, a bare tombstone would clobber a *legitimate re-copy/edit of the same content after a delete* (the cross-device "surprise" — you delete, re-copy the same text, next sync drops your fresh copy because the tombstone still syncs from the other device). `mergeHistories` now uses `tombstoneMap` (id → `deletedAt`) not a plain id-Set, and drops an item **only if `itemMutationClock(item) <= deletedAt`**. A copy touched AFTER the delete (newer capture `ts`/`updatedAt`, pin, title, or `tsUpdatedAt`) beats the tombstone and survives; a stale pre-delete copy on a lagging provider still stays deleted (resurrection guard intact). Convergent + idempotent — the tombstone stays in settings and ages out at 30 days without re-dropping the live item. Guard is on the **plain-delete branch only** (`!targetKey`); the supersede/edit-lineage branch is untouched. Tests in `test/clipboard-model.test.js` (re-add-after-delete survives, stale copy stays deleted, pin-touch-after-delete survives).
- **Content-addressed images**: filenames are md5 hash of PNG content (`{hash}.png`), naturally deduplicates.

## Clipboard Operations

- **Polling** every 400ms via `clipboard.readImage()` / `clipboard.readText()`
- **`addToHistory(entry, matchFn)`** — shared helper that deduplicates, preserves pinned/group metadata, and prunes
- **`setClipboardToItem(item)`** — shared helper to write text or image to clipboard
- **Backup/restore**: `backupClipboard()` saves text/html/rtf/image, `restoreClipboard()` writes them back. Used by numpad quick-paste.
- **`pollGate`** flag pauses polling during paste sequences to prevent interference

## Paste Simulation

- **macOS**: native `CGEvent` Cmd+V (`lib/macos-paste.js` `sendCommandV`), falling back to `osascript` (activate frontmost app + `keystroke "v"`) when a target app must be re-activated after hide.
- **Windows**: native `SendInput` Ctrl+V (`lib/windows-paste.js` `sendCtrlV`). The old `cscript`/VBScript `SendKeys` path is gone (200-500ms cold start + NumLock quirks).

## Quick-Paste (numpad macros) — robust, race-free by default

The numpad quick-paste used to paste STALE previously-copied content (worse under
lag; users had to retry). Root cause = the **clipboard backup/restore race**:
set macro on clipboard → Ctrl+V (async: the target reads the clipboard whenever it
drains its input queue) → restore old clipboard on a FIXED 150ms timer. Under lag
the target reads AFTER the restore → pastes the old clip. Proven + measured in
`scripts/qa-numpad-race.js` (real Electron clipboard; naive path goes stale at a
~160ms target read).

- **`lib/quick-paste.js` (`createQuickPaster`)** is the pure, dependency-injected
  orchestrator (unit-tested in `test/numpad-paste.test.js` with a fake clipboard +
  fake late-reading target). It: **serializes** requests through a promise chain
  (rapid presses queue, never dropped — kills "press it 3 times"); **coalesces**
  same-`coalesceKey` repeats within 90ms; **verifies** the clipboard write landed
  before pasting; **safe-restores** (only if the clipboard still holds our macro —
  never clobber a copy the user made mid-sequence); and applies a **lag-adaptive**
  restore delay (floor `quick_paste_restore_delay_ms` default 400ms, + `3× measured
  scheduler-lag`, capped 1200ms) for the clipboard path.
- **ONE delivery mechanism: the REAL clipboard paste.** Quick-paste puts the item
  on the clipboard, synthesizes Ctrl/Cmd+V, and safe-restores — the SAME primitive
  (`setClipboardToItem` + `simulatePaste`) the panel-click paste (`pasteAndHide`)
  uses. Exact content pasted atomically, immune to the target app's autocomplete/IME.
- **Keystroke-injection "type" mode was REMOVED (2026-07-07) — do NOT reintroduce it.**
  It typed the macro as raw key events, so `\n` became a real Enter; a numpad slot
  holding multi-paragraph boilerplate fired ~22 unintended sends into a chat composer.
  The owner had already rejected typing as the default ("super slow + buggy, newlines
  fire Enter, I didn't want manual-type shit"), and it was the ONLY reason numpad
  diverged from the working panel-click path — so it, `lib/keystroke-inject.js`, the
  orchestrator `strategy`/`skipClipboard`/`fallback` seam, the `quick_paste_mode`
  setting, and the "Paste as" UI control were all deleted. `test/numpad-paste.test.js`
  #7 guards it: a multi-line snippet must paste in ONE clipboard write + ONE Ctrl/Cmd+V
  with newlines intact, never as Enter presses.
- **Why NOT delayed-render clipboard ownership** (an earlier plan): its only extra
  signal (`WM_RENDERFORMAT`) is spoofable by passive clipboard readers (Windows
  Clipboard History et al. render right after we take ownership) → false "consumed"
  → early restore → the real late read still stale. It doesn't beat a longer/adaptive
  delay and adds ~500 lines of risky FFI. Rejected on evidence.
- **Settings** (per-machine, not synced; excluded in `remoteSettingsPayload`):
  `quick_paste_restore` (restore the previous clipboard afterwards) and
  `quick_paste_restore_delay_ms` (floor restore delay, adapts up under lag). There is
  no paste-mode setting anymore.
- **Dispatch is unified**: hardware numpad (Windows LL hook `handleNumpad`), panel
  number keys (`numpadPasteAndHide`), and the global quick-paste shortcut
  (`handleQuickPaste`) ALL route through `runNumpadSlotAction` → `numpadPaste` →
  `getQuickPaster().request()`. `handleNumpad` no longer has a bespoke path.
- **Hook auto-repeat suppression** (`lib/windows-hook-worker.js`): a held/lag-
  stretched Numpad key emits repeated `WM_KEYDOWN` with no `WM_KEYUP`; the worker
  tracks `numpadHeld`/`numpadIntercepted` so the paste fires exactly ONCE and the
  paired keyup is swallowed too. Kills double/triple pastes.

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
- **Template tray icon**: `trayIcon.setTemplateImage(true)` for menu bar dark/light mode.
  It MUST be `iconTemplate.png` (+`@2x`): the monochrome clipboard glyph, black on transparent,
  drawn by `scripts/sync-icons.ps1` (`Save-TrayTemplate`, same source as the app icon). macOS
  keeps only a template's alpha channel, so the full-colour `icon.png` (an opaque rounded
  square) rendered as a solid white box in the menu bar (fixed 2026-09-03). Never `resize()` the
  template; `createFromPath` picks the `@2x` itself.
- **`~/Applications/BoardClip.app` launcher** (`scripts/create-macos-launcher.sh`, rebuilt by
  `update.sh`): Finder shows a bundle icon ONLY from an `.icns` named by `CFBundleIconFile`;
  the script builds `Resources/icon.icns` from `icon@2x.png` with `sips` + `iconutil` and
  `lsregister -f`s the bundle so the cached blank icon is dropped (fixed 2026-09-03).

## Native Cloud Sync

- **Default-on providers**: detected Google Drive, OneDrive, iCloud, and any legacy custom `sync_path` folder are enabled automatically. Settings stores only local opt-outs in `sync_disabled_paths`; provider choices are not synced between machines.
- **Multi-target convergence**: `syncMerge()` reads every enabled provider, folds all remote states into one canonical local state, then writes that canonical state back to every enabled provider. This makes multiple providers useful redundancy instead of separate silos.
- **Merge algorithm**: shared pure helpers in `lib/clipboard-model.js` merge histories by stable item id/content key, merge pin/group metadata, preserve tombstones, and dedupe numpad slots.
- **`syncMerge()`** runs on startup + every 30s + debounced 500ms after local changes.
- **`insideSync` flag** prevents overlapping sync passes and prevents `saveHistory()`/`saveSettingsFile()` from re-triggering sync while a merge is already running.
- **Only writes if changed** — compares JSON strings of remote files before atomic writes to skip no-op churn.
- **Images synced bidirectionally** — content-addressed filenames mean no conflicts.
- **Remote settings exclusions**: `sync_path`, `sync_disabled_paths`, and legacy `numpad_slots` are excluded from remote settings writes.
- **Cloud account discovery** lives in `lib/cloud-accounts.js`.
- **P2P discovery = `lib/p2p-discovery.js` (2026-09-03)**: ONE UDP socket joined to
  `239.255.43.21:45454` on EVERY real IPv4 interface (`addMembership(group, ifaceIp)` per
  adapter; re-enumerated every 30 s) and announcing once per interface via
  `setMulticastInterface`. A bare `addMembership(group)` lets Windows pick ONE adapter and on
  this PC it picked the Hyper-V Default Switch, so the Mac's Wi-Fi announcements were never
  heard and every copy took the 30-90 s cloud path. Announcements are ALSO unicast to every peer
  heard in the last 5 min (multicast is often one-directional). P2P HTTP prefers the fixed port
  45455 (ephemeral fallback on EADDRINUSE). Peers carry `transport` (`lan`|`tailnet`, CGNAT
  100.64/10 = Tailscale); `p2p.peer.seen`/`p2p.peer.lost` diagnostics answer "did the Mac ever
  show up". Unit: `test/p2p-discovery.test.js`; real two-instance loopback check:
  `node scripts/qa-sync-two-instances.js` (seeds A/B, asserts mutual discovery + convergence).
  Full overhaul plan (delta P2P + delta cloud journals + Tailscale + AES-GCM): `SYNC-P2P-PLAN.md`.
- **Sync v2 = ONE delta changes feed for P2P AND cloud (2026-09-03, `SYNC-P2P-PLAN.md`)**:
  `lib/sync-delta.js` `createChangeTracker` stamps every entry (item / tombstone / group
  tombstone / supersede / conflict record / small synced settings) with the LOCAL revision at
  which it last changed on this device, whatever its source; `deltaSince(cursor)` = CouchDB
  `_changes?since=seq`. Revisions are per-device monotonic (start = max(persisted+1, Date.now())),
  so cursors are "the sender's revision" and never compare clocks across devices. Persisted
  lazily to `sync-state.json` (local only: tracker entries + `p2pCursors` + `journalCursors`);
  an entry whose arrival was lost in the lazy window is re-sent once (over-send is idempotent,
  under-send would be a silent hole). `observeLocalChange()` runs in EVERY save path
  (history/settings/conflicts), including saves that apply remote state - what arrived from one
  peer must reach the others. Applying a delta = the existing `foldRemoteState` union merge
  (partial history can only add/update; deletes need tombstones), change detection is
  O(delta) via `historyChangedBy(before, after, touchedIds)` - NOT a full 8 MB stringify.
- **P2P v2**: `/delta?since=` (GET) + `/delta` (POST) carry envelopes sealed with AES-256-GCM
  (`lib/p2p-crypto.js`, key = HKDF(`p2p_secret`)); HMAC still signs the sealed bytes. v1
  `/state` stays for un-updated peers (announcement/manifest carry `protocolMax`). Push = delta
  since the peer's acked cursor (`p2pCursors[id].sent`), pull = `/delta?since=pulled`; after
  applying a peer's delta, `sent` is set to the new revision so it is never echoed back.
  Peers keep an ADDRESS BOOK (`peer.addrs`: LAN + tailnet); `p2pChooseAddress` prefers a
  fresh LAN address, then tailnet. Discovery beyond multicast: synced `p2p_endpoints`
  registry (each device publishes name/port/lan/tailnet ips, newest wins), `tailscale status
  --json` every 60 s (`lib/tailscale.js`), manual `p2p_pinned_peers`; all are unicast
  announcement targets AND `/manifest` probe targets every 30 s (+ on Refresh).
- **Cloud journals** (`lib/sync-journal.js`): each change appends ONE small NEW file
  `sync/<deviceId>/<revision16>.json` (tmp + rename to a fresh name, never rename-over: DriveFS
  forks) per provider; readers apply other devices' files newer than their per-device cursor
  (`journalCursors[folderId].read`), a `since` past the cursor = gap -> snapshot re-read. The
  monolith is now a SNAPSHOT rewritten every 5 min / 50 journal writes (content-compared) and
  own journal files it covers are pruned after 1 h. `fs.watch` on each provider's `sync/` tree
  (recursive, own device dir ignored) applies a peer's file within ~300 ms; the 30 s poll stays
  as the floor. Providers dedupe by a `.boardclip-folder-id` marker (G:/H: = one folder).
  Watchdogs are adaptive (base + 2 s/MB) and a late completion logs `sync.timeout.late`, not an
  error. Telemetry: `sync.latency {source, transport, peer, ms}` (originClock -> applied),
  `sync.delta_apply`, `sync.journal.write/read`, `p2p.peer.seen/lost`; tray tooltip shows
  peers + transport + last sync + last latency; Settings lists peers and the tailnet line.
  QA: `node scripts/qa-sync-two-instances.js all` (p2p + cloud-only scenarios, measured).
- **Editor forks were a state-apply RACE, not divergence (found 2026-09-03 right after P2P first
  paired)**: the v1 `p2pApplyState` folded remote state, then `await`ed the orphan-image scan,
  then replaced `history` with the PRE-await fold. An editor idle-save landing inside that await
  was discarded (its new id gone, the tombstoned old id back), so the next save found no base
  and took the `conflict_created` branch: "saved as a separate clip" toasts + a new copy every
  few seconds while typing (nine copies of one note). Fixed with the same `dataRevision` rebase
  guard `syncMerge` already had (`p2p.state_apply_rebased`); `applyRemoteEnvelope` (v2) has no
  await between fold and commit by construction. `editor.text_applied` now logs `base_found` /
  `base_text_matches` so a fork's cause is readable from the log. RULE: never `await` between a
  `foldRemoteState` and the `history` replacement without re-folding the live history.
- **Search facets (2026-09-03)**: `len:` accepts ranges (`len:50-200`), plus `lines:`/`ln:` and
  `words:`/`wd:` with the same comparators, and `is:url` / `is:multiline` / `is:rich`. The
  user-facing reference is `SYNTAX_HELP` in clip-search.js (ONE table next to the parser),
  rendered by the shared `attachSearchHelp` as the "?" hover/click box beside the search field
  (both app + demo get it via `attachSearchBox`); `.search-help` rides the shared floating-surface
  rule. Add a facet = parser + `SYNTAX_HELP` + `PREFIX_HINTS`/`PREFIX_SHORT`, nothing else.
- **macOS**: detects Google Drive and OneDrive from `~/Library/CloudStorage/`, plus iCloud Drive from `~/Library/Mobile Documents/com~apple~CloudDocs`.
- **Windows**: scans Google DriveFS mount letters and labels from PSDrive descriptions, DriveFS preference cache/WAL strings, and recent DriveFS logs; also detects OneDrive environment folders and common iCloud Drive folders.

### DATA-LOSS BUG (2026-07-06 incident) — sync merge vs content-hash edits — FIXED 2026-07-09 (`61f5cff`)

**Sync RE-ENABLED 2026-09-03** (both devices on eb530dc; first merge folded the Mac's 350 clips into
Windows' 9.8k with zero loss; 4 stale pre-edit Mac copies were pre-tombstoned so they could not
resurrect; the Mac had in fact kept syncing to Google Drive alone the whole time). History: sync
was PAUSED on Windows (`sync_disabled_paths` = all 3 providers, `p2p_enabled: false`) from the
incident until the fix. RE-ENABLE ONLY once BOTH devices run `61f5cff`+ (Windows + Mac) —
old code doesn't understand the supersedes ledger, so a stale device still on the old
build could re-trigger the race.

- **Mechanism**: text ids are content hashes, so every editor save = new id + a
  TOMBSTONE for the old id (`applyTextEdit`). Cloud providers lag; a merge pass can
  read a stale provider that still holds the note under a now-tombstoned id and either
  (a) resurrect an OLD version (the new id lost a race), or (b) drop the live note
  entirely and — because `syncMerge()` writes canonical state back to EVERY provider —
  propagate the deletion everywhere, making it permanent.
- **Born 2026-05-17** (`7e7fa7c` content-hash ids + `686805f` tombstones + `e391b52`
  default multi-provider sync); **practically triggerable since 2026-06-26** (`4e45c7d`
  built-in editor made rapid in-app re-hash-per-save common). Verified against a real
  incident: a heavily-edited pinned note regressed at 13:08 and was dropped at 17:05
  (diagnostics: `sync.merge local_changed=true full_sync=true wrote_remotes=true`),
  deletion propagated to all 3 providers.
- **THE FIX (`61f5cff`)** — an **edit-lineage ledger** in synced settings. `applyTextEdit`
  now returns `supersedes: [{from: oldId, to: newId, updatedAt}]` alongside the old-id
  tombstone; `main.js addSupersede` persists it into `settings.supersedes` (normalized,
  30-day retention like tombstones; in `remoteSettingsPayload` so it rides cloud writes +
  P2P state + fork-heal; merged in `mergeSyncedSettings`). `mergeHistories` builds a
  `supersedeMap` (transitive old→…→new) and routes a stale old-id copy THROUGH the lineage:
  `mergeSupersededStaleIntoTarget` folds its pin/title metadata into the newer target but
  the stale TEXT can NEVER overwrite the target text (the exact regression). Ordering-safe
  (stale seen before its target is stashed in `pendingStaleByTarget`, folded when the
  target lands). If EVERY provider lost the target, the newest stale old-id copy is kept
  rather than converting an edit into data loss. Additive + fully backwards-compatible
  (absent/empty `supersedes` = pre-fix behaviour). Tests: `test/clipboard-model.test.js`
  (stale-provider race repro + `applyTextEdit` supersedes emission); `foldRemoteState`
  merges settings BEFORE history so the lineage is available to `mergeHistories`.
- **Forensics kit**: `clipboard-backups/` (content-addressed history snapshots, see
  Backup subsystem below; 48h/512MB/2000-manifest retention),
  `clipboard-edit-archive/` (raw editor buffers, 1yr/100MB — this is what recovered the
  lost paragraph), `boardclip-diagnostics.jsonl` (64MB cap), plus cloud providers'
  own version history. During any incident, copy relevant backups OUT of the retention
  dirs immediately — pruning runs on every save and destroyed evidence mid-investigation.
  To read a content-addressed snapshot: `backupStore.readSnapshot(dir, manifestPath)`
  (`lib/backup.js`) resolves item hashes back into a full history array.

## Backup subsystem (`lib/backup.js`) — content-addressed local time-machine

- **Roles (the failure-mode matrix)**: LOCAL backups (same drive) guard against
  logic/software bugs (a copy the buggy code didn't touch — this recovered the note);
  they are NOT hardware redundancy (drive dies → all local copies die). HARDWARE
  redundancy = the CLOUD providers (different machines), but cloud PROPAGATES logic-bug
  deletions — so it's only trustworthy once the sync-merge bug (above) is fixed. Decision
  (owner-approved 2026-07-07): keep local lean as the logic-bug time-machine; cloud is the
  hardware-redundancy layer AFTER the sync fix. No separate off-drive target.
- **Content-addressed store**: `clipboard-backups/objects/{sha256}.json` is a shared pool
  of stored items (+ the settings object); a snapshot is a small manifest
  `clipboard-backups/snapshots/{stamp}-{reason}.json` listing the ordered item hashes.
  Unchanged items across snapshots share ONE blob, so an edit to one note costs ~one
  object + a manifest, not a full ~4.5MB history copy (verified on the real 5670-item
  history: 1 edit = 1 new object). Everything stays plain-text JSON (greppable in an
  incident). Reuses `lib/blob-store` (atomic write/dirs) + `lib/retention` (planRetention).
- **Retention** = `backupStore.pruneBackups(dir, {maxAgeMs:48h, maxBytes:512MB,
  maxManifests:2000, now})`: evict manifests by age+count, then mark-sweep GC any pool
  object no surviving manifest references, then drop oldest manifests until under the byte
  cap. Legacy full `{stamp}-{reason}-{hash12}.json` snapshots are still read (`readSnapshot`
  handles both shapes) and age out — no risky bulk migration.
- **`main.js` wiring**: `maybeBackupHistoryBeforeWrite` keeps the change-detection +
  60s throttle (app state), then calls `backupStore.writeSnapshot`; on ANY error it FALLS
  BACK to a full-JSON write (`history.backup.fallback` diagnostic) so a backup is never
  silently skipped. Tests: `test/backup.test.js` (dedup, exact round-trip, one-edit=one-
  object, age-GC, size cap, legacy compat).
- **Phase 2 (not done)**: fold the edit-archive's `done-` finished buffers into the same
  object pool (they overlap it) and move its prune under `lib/backup.js` for one retention
  home. Kept separate for now because its live per-keystroke drafts are a distinct
  crash-recovery role. Working spec: `BACKUP-UNIFY-PLAN.md` (untracked).

## Scripts & Process Management

- **`start.sh`/`start.bat`** — call kill script, verify no leftover processes, abort if kill failed, then launch Electron in background
- **`update.sh`/`update.bat`** — one-step production-safe update: refuse tracked local code edits by default, fast-forward from Git, install dependencies if Electron is missing or package files changed, then call the platform start script to relaunch. Set `BOARDCLIP_UPDATE_ALLOW_DIRTY=1` in a developer checkout to use `git pull --rebase --autostash`.
- **`kill.sh`/`kill.bat`** — match processes by this checkout's Electron binary to avoid killing other Electron apps (VS Code, Discord, etc.). **They EXCLUDE the AI MCP helper** (same `electron.exe`, identified by a `boardclip-mcp.js` arg on the command line — Windows uses `Get-CimInstance Win32_Process` since `Get-Process` can't see the command line; macOS/Linux use `ps -Ao pid=,command=` + `grep -v boardclip-mcp.js`). The MCP helper is spawned + owned by an AI client (Forge/Claude/Codex), so restarting the app (start/update → kill) must NOT take it out — an AI client has no liveness re-spawn for a stdio child that dies AFTER connecting (it just returns "Not connected" forever until the client reconnects). Fixed 2026-07-07 (`1c1eda2`); the Forge-side auto-reconnect that also covers this lives in forge `services/mcp.ts` (`McpConnection.ensureLive`).
- **Single-instance lock** via `app.requestSingleInstanceLock()` — second launch shows popup instead of starting duplicate
- **Auto-launch**: `app.setLoginItemSettings({ openAtLogin: true })` — toggled in Settings UI
- **Windows dev auto-launch**: un-packaged Electron writes `BoardClip.vbs` into the Startup folder and the VBS runs `start.bat` hidden. Avoid pointing login startup directly at `electron.exe`; without a stable working directory it can launch bare Electron or fail to start the app module.

## UI Patterns

- **`icon-btn` base class** — all small clickable icons share 24x24 rounded style. Variants: `.accent` (purple hover), `.danger` (red hover), `.close-btn` (bold ×)
- **Null-guard `it.text`** — always use `(it.text||'')` in templates
- **Filter tags**: shared app/site UI. Left click includes a filter, right click excludes it, and the global clear X resets search plus include/exclude filters.
- **Confirm dialog** shared between numpad reassign, group delete, and clear all
- **Opening a clip in the editor/viewer from the popup (2026-09-02/03):** "open several" gestures
  pass `{ keepPopup: true }`: middle-click or RIGHT-click on the row's own open button (the owner's
  actual ask - right-click there opens, it never shows the row menu), middle-click / alt+click on
  the row body, and the detached right-click/"..." menu's Open in editor / Open image (detected via
  `.bc-menu-item`). A NORMAL click on the row's open button and Ctrl/Alt+Enter stay a hand-off
  (popup closes). middle-click is `controller.onMousedown` (prevents the
  default: the ONLY way to stop Chromium's autoscroll widget, measured) + `controller.onMouseup`
  (opens on an in-place release; a preventDefault'ed middle mousedown makes Chromium SKIP
  `auxclick`, measured 2026-09-03) - `onAuxclick` is only a fallback for consumers that don't
  route mousedown. Both consumers must wire mousedown + mouseup (ui-parity guard).
  The keepPopup gestures
  -> main's `presentSecondaryWindow` shows the window with `showInactive()` and does NOT
  `hidePopup()`, so several results can be opened one after another; the popup still
  blur-hides the moment the user clicks into one of the windows (that is what makes it NOT
  "sticky" - the July complaint was a popup that ignored blur, never revisit a blur-suppress
  flag). Keyboard opens (Ctrl/Alt+Enter) stay a hand-off: show + focus + hidePopup.
- **Settings auto-save** — max age/size save on input change, no Save button
- **Dev auto-reload** — `fs.watch` on `index.html` triggers `reloadIgnoringCache()` (debounced 300ms)

## AI Access (local MCP server)

- **Shape:** `mcp/boardclip-mcp.js` is a stdio MCP server (`@modelcontextprotocol/sdk` v1.x) spawned by AI clients. It reads shared clips straight from the JSON files (works app-closed); anything beyond the allowlist / any mutation / clipboard-write forwards to the running app over a **named pipe / Unix socket** control channel (`lib/control-server.js` in main.js, `lib/control-client.js` in the helper). NOT HTTP, no port. The helper never writes data files -> no races.
- **Allowlist by curation (fully opt-in):** a clip is AI-visible iff it's in a group listed in `settings.groups_shared_with_ai` (the auto-created **"AI"** group is always shared). Non-shared = metadata only. `lib/mcp-core.js` is the PURE boundary (whitelist + shaping), reused by both helper and app. There is deliberately NO "looks like a secret" auto-withholding — group sharing is the single opt-in gate, so a clip the user put in a shared group is shared as-is. (A `secret-guard` heuristic layer existed and was removed as redundant/annoying; don't reintroduce it.)
- **Gating:** `mcpNeedsApproval` -> delete/edit/clipboard-write/paste + beyond-allowlist reads ALWAYS prompt; pin/group/numpad/add are free on *shared* clips. Approval modal = a native frameless BrowserWindow (`mcp-approval.html`, NOT a browser) with once/session/always-per-tool + deny-by-default countdown; `ai_always_allow` persists grants. Modal auto-sizes via the `approval-resize` IPC.
  **The modal must explain the ACTION, not show the clip (2026-09-03, owner: "I never understand
  what is actually happening")**: `buildApprovalRequest` returns `title` (verb + object, e.g. "Add
  clip to group X" / "Remove clip from group X" - assign_group is a TOGGLE, so membership is
  checked), `explain` (one plain sentence: what changes and what does not, e.g. "tagged ..., its
  text is not changed and nothing is deleted" / "removed from your history on every device, text
  stays in the local backups"), `why` (why it is asking: always-gated vs not-in-a-shared-group),
  `facts` ([Clip size, Captured, Groups, Numpad]) and a LABELLED `preview` ("Clip text" / "New
  text" / "Text to append" / "Query"). The modal also states that Always/Session apply to that
  kind of action only. Sandbox proof: `node scripts/qa-approval-shot.js` (isolated instance
  with `ai_access_enabled`, `BOARDCLIP_MCP_DISCOVERY` + `BOARDCLIP_MCP_PIPE_TAG` overrides and a
  fake HOME/APPDATA so the registrar never touches real client configs; screenshot each modal
  over CDP). `BOARDCLIP_MCP_PIPE_TAG` is the test seam that lets a sandbox run its own control
  channel beside the live app instead of colliding on the per-user pipe.
  **Hover pauses the countdown (2026-09-03, owner: "pause the timer while mouse over")**: the
  modal stops ticking on `mouseenter` of `<html>` (label "Paused while your mouse is over this",
  resumes from the same second on `mouseleave`; a resting cursor is caught via `:hover` after
  render) and reports `approval-hold(id, held, remainingSec)`; main's timer was only ever the
  safety net 3 s behind the renderer, so `requestApproval` clears it while held (bounded by
  `APPROVAL_MAX_HOLD_MS` = 15 min, then `timeout`) and re-arms it with the reported seconds on
  resume. Two control-channel rules make the pause SAFE end to end: (1) `ControlServer` passes
  `{ signal }` to `handleRequest`, aborted when the caller's socket closes, and `requestApproval`
  then finishes with `client_gone` (modal closes, nothing executes for a caller that gave up -
  before this an allow clicked after the helper's 60 s timeout still ran the action); (2) a
  client that sends `keepalive` in its envelope gets `{id, pending:true}` every 10 s while the
  request runs and `control-client` treats `timeoutMs` as MAX SILENCE, so the helper waits as
  long as the user reads. Legacy helpers (no flag, still-running MCP children spawned before the
  update) get exactly one line as before. Tests: `test/control-channel.test.js` (keepalive,
  legacy first-line, abort-on-disconnect); sandbox proof `node scripts/qa-approval-hold.js` (5 s
  timeout held past 8 s, resume, client_gone).
- **Discovery:** app writes `~/.boardclip/mcp.json` `{dataDir,pipePath,secret,command,args,env,pid}` on launch when enabled; helper reads it (falls back to default userData for read-only). Registered command is **electron-as-node** (`process.execPath` + `ELECTRON_RUN_AS_NODE=1` + entry path) - works for source + packaged.
- **`edit_clip` tool (replace/append clip text):** because text ids are content-addressed (`txt:{sha256}`), there is NO in-place text mutation - editing changes the id, which is why an "edit" was previously an add+delete dance. The tool REUSES `applyExternalTextEdit` (the same metadata-preserving core the built-in editor + conflict/unify flows use): when `originalText` matches the current item, `clipboardModel.applyTextEdit` mutates the item in place, re-derives its content-key id, keeps pin/groups/numpad, and tombstones the old id - so all metadata survives automatically. Returns the NEW id. `append:true` newline-joins onto existing text (done app-side, so it works on non-shared clips too); else it replaces. In `MCP_ALWAYS_GATED` (lossy overwrite -> prompts like delete, NOT free-on-shared; users can "always allow" per-tool). Images can't be edited. Don't hand-roll add+delete for an edit.
- **Reuse, don't duplicate:** the `apply*` functions in main.js (applyPinToggle/applyGroupAssign/applyDeleteItem/...) are the SINGLE mutation path for BOTH the IPC handlers and the MCP dispatch. HMAC auth is `lib/hmac-auth.js`, shared by P2P + the control channel. DEFAULT_SETTINGS adds `ai_access_enabled/groups_shared_with_ai/ai_always_allow/ai_approval_timeout_sec/mcp_secret` (mcp_secret + the 3 ai_* prefs are excluded from sync in `remoteSettingsPayload`; groups_shared_with_ai DOES sync).
- **Installers:** `lib/mcp-installers.js` - one shared JSON-map adapter factory covers most clients; Codex (TOML), VS Code (`servers`+type), Zed (nested command) are variants. Idempotent + non-clobbering. Settings shows detected-only + a "More" expander.
- **Testability seams:** `BOARDCLIP_DATA_DIR` overrides the data dir; `BOARDCLIP_MCP_DISCOVERY` overrides the discovery-file path. Use a fake HOME (+ USERPROFILE/APPDATA/XDG_CONFIG_HOME) to test the registrar without touching real client configs. `ensureAiGroupShared()` must run on BOTH enable and launch (idempotent) so a pre-enabled restart still has the AI group.
- **Boundary invariants (don't regress):** (1) only SHARED group names are ever exposed (clipView/buildContext filter to `groups_shared_with_ai`); private group names never leave the boundary. (2) `mcpHandleRequest` re-checks `ai_access_enabled` AFTER the approval await, not just before.
- **Per-user control channel:** the pipe (`\\.\pipe\boardclip-mcp-<user>`) / socket is per-user. Production is safe because the single-instance lock allows one BoardClip per user. BUT test instances launched with distinct `--user-data-dir` bypass that lock and will collide on EADDRINUSE + pile up as zombies (npx/electron children don't die from `timeout`/killing the wrapper PID) - always kill leftover `electron.exe` whose commandline contains your temp data-dir, and never kill the ones under `%APPDATA%/BoardClip` (the user's real app).
- **Continue is intentionally NOT installed** - it uses a YAML `mcpServers:` list, not the shared JSON-map adapter. Add a dedicated YAML adapter to support it for real.

## Website Demo + Single-Source UI

The marketing site (`site/`) embeds an interactive demo of the popup. The
desktop app popup (`index.html`) and the demo (`site/index.html`) are a SINGLE
SOURCE — both drive the shared layer in `site/shared/clipboard-ui-core.js`
(`BoardClipCore`): `renderPopupShell` / `renderSettingsBody` / `renderClipItem`
/ `renderClipActions` / `renderFilterBar` (markup), `createDialogs(host)`
(confirm/prompt), and `createClipController(adapter)` (click dispatch + keyboard
nav + the confirm-gated flows: group-delete, numpad-replace, add-group,
clear-all). All popup CSS + theme variables live in `site/shared/clipboard-popup.css`
(`:root[data-theme]` for the app, `.bc-popup[data-theme]` for the demo window).

- **Do NOT add a per-side click handler, dialog, or popup CSS rule.** Extend the
  controller/adapter or the shared renderers. Each side only supplies a backend
  ADAPTER (app → `window.api`; demo → in-memory Core mutators + browser APIs) and
  its own data. This is what stopped the two popups from drifting (a confirm
  dialog used to exist in one but not the other).
- `test/ui-parity.test.js` enforces it: both consumers must call the shared
  renderers + `createClipController`, route through `controller.onClick/onKeydown`,
  never re-inline a bespoke dialog (`pendingAssign`/`confirmOverlay`/`demo-confirm`),
  and keep popup CSS/theme vars only in the shared sheet. Run `npm test`.
- `applyGroupAssign` (main.js) TOGGLES group membership; the per-clip group chip
  is therefore add-or-remove on both sides (no separate unassign endpoint).
- **Editor find highlight** (`createEditor` in `clipboard-ui-core.js`): matches are painted
  by a backdrop `<div class="bc-editor-hl">` that mirrors the textarea's text (transparent
  text + `<mark>` spans) behind a transparent textarea — the standard "highlight in a
  textarea" technique (a textarea can't hold markup; the CSS Custom Highlight API doesn't
  work on textareas). The textarea forces `overflow-y: scroll` (always-on 10px gutter) so
  both layers wrap at an identical width. Escape all mirrored text with `escapeHtml` (XSS).
  **Scroll-to-match MUST measure the current `mark.offsetTop`, NOT a char-index→line-count
  estimate** (`editorScrollTopForIndex` counts only `\n`, so it lands short on soft-wrapped
  lines — the "highlights but doesn't scroll" bug). QA the editor with a doc of LONG WRAPPING
  lines, not `\n`-separated short lines, or the wrap bug hides.
- Theme: `settings.theme_mode` ('system'|'light'|'dark') persists the popup theme;
  whitelisted in the `save-settings` IPC handler + `DEFAULT_SETTINGS`; applied via
  `Core.applyTheme`. The Theme control lives in the shared settings body, so it
  shows in BOTH the app and the demo.

## Search engine + image viewer (2026-07)

- **ONE shared search engine** = `site/shared/clip-search.js` (isomorphic UMD, same
  header as clipboard-ui-core.js). Pure, no DOM. THE authority for query syntax +
  filtering + ranking, consumed by the app popup, the demo, AND `lib/mcp-core.js`
  (`search_clips`). Loaded as a `<script>` BEFORE clipboard-ui-core.js in EVERY html
  window (index.html, site/index.html, editor.html, viewer.html) → `window.BoardClipSearch`;
  Node/tests `require` it. clipboard-ui-core re-exports it as `Core.search`. Tests:
  `test/clip-search.test.js`.
- **The search bar TEXT is the single source of truth.** Facets (`group:`/`is:`/`num:`/
  `since:`…) live as tokens INSIDE the query string; the chip bar's active/excluded state
  is DERIVED via `facetState(parseQuery(q))`. There is NO separate `activeFilters`/
  `excludedFilters` Set anymore (that dual-model was the drift Forge killed). A chip click
  rewrites query tokens through `Core.search.applyFacet(query, token, intent)`; deleting a
  group strips its `group:`/`-group:` tokens. `ui-parity.test.js` #11 guards that neither
  consumer reintroduces filter Sets.
- **Grammar** (colon-uniform, quote-aware, `-` negates): free text · `title:` · `text:`/
  `body:` · `group:` · `is:pinned|image|text|numpad` · `num:1-9` · `since:`/`before:`
  (`7d`/`24h`/`30d`) · `len:>N` · `id:` · `sort:new|best`. `item.ts` is Unix SECONDS.
  Unknown `word:val` → stripped to `val` as free text + recorded in `parsed.unknown` for a
  hint. URL/Windows-path values (`http://`, `C:\`) are left verbatim.
- **Short + long alias for EVERY prefix** — ONE `PREFIX_ALIASES` map in clip-search.js
  feeds parse + `lexQuery` (highlight) + `suggestQuery` (autocomplete): `t`=title, `b`=text/
  body, `g`=group, `n`=num, `s`=since (also `after`), `bf`=before, `l`=len, `o`=sort, plus
  `is`/`id`. Add a new alias in that map ONLY. Autocomplete offers the long form and hints
  the short (`title: · or t:`); both forms color as prefixes.
- **Ranking**: `filterRankIndexes` returns ORIGINAL indexes. Relevance (`relevanceScore`:
  title/body/phrase hits + `fuzzyMatch` abbreviation + recency blend) when a content query
  is present; pure history/caller order when idle. A **Best⇄Recent** `sortBtn` (in the
  shared shell, shown only while searching) forces `sort:best`/`sort:new`. `sort:` token or
  the toggle wins over the default.
- **Live highlight + autocomplete** = `Core.attachSearchBox(input, opts)` — ONE enhancer
  shared by app + demo. Transparent input over a colored backdrop mirror (`lexQuery` →
  `.qh-*` spans); autocomplete dropdown (`suggestQuery`) for prefixes/group names/`is:`/
  `sort:`/`since:` presets/`num:`.
- **In-app "AI search" mode REMOVED (2026-09-02)** - the sparkle/Tab toggle, offline IDF
  "smart ranking" (`rankFuzzyIndexes`/`buildIdf`), the BYO-endpoint agent (`lib/ai-search-agent.js`),
  the `ai-search` IPC and the `ai_search_*` settings are all gone (owner: "remove the shitty AI
  search for now, will impl better later"). Clean excision: keyword/structured search is the ONLY
  mode, `attachSearchBox` no longer takes `getAiMode`, `loadSettings` strips the dead `ai_search_*`
  keys (one held a plaintext API key). `ui-parity.test.js` #11 fails if any piece creeps back.
  Rebuild from git history (`git log -S rankFuzzyIndexes`) when a better version is designed -
  don't resurrect this one.
- **Regression from that removal (shipped 36c82a9 06:18, fixed same day):** `clearSearchAndFilters` still called the
  deleted `resetAiRun()`, so every clear-X / filter-bar-clear threw a ReferenceError AFTER emptying the input
  and `query` but BEFORE `searchBox.refresh()`/`rerenderList()` - the highlight mirror kept showing the old
  query while results showed everything ("search stuck with an old search"). Lessons baked in: (a) when
  excising a block, grep for EVERY identifier it defined (the guard regex now lists them all), (b) the
  sandbox check must exercise the clear paths (clear X, filter-bar clear chip, chip click/right-click),
  (c) renderer exceptions now reach the diagnostics file as `renderer.error` via the shared
  `Core.installRendererErrorReporting` + `record-diagnostics` (forceFile for errors) - a thrown popup
  handler is never silent again.
- **In-app image viewer** = `Core.createImageViewer` (the image twin of `createEditor`;
  SAME `bc-editor-bar` chrome + foot so the two windows read as one family). Fit-to-window
  default, click toggles fit⇄100% at the point, wheel zooms around the cursor, drag pans
  when zoomed (`ResizeObserver` re-fits). `viewer.html` + `viewer-preload.js` mount it;
  main's `openImageViewer` (one window per clip, `viewer_bounds` persisted via the shared
  `windowBoundsFromSettings`/`scheduleWindowBoundsSave`). `viewer_bounds` is explicitly
  defaulted and excluded from `remoteSettingsPayload` like every other machine-local window
  geometry. `open-image` IPC opens it; `open-image-external` keeps the OS-default-app path.
- **Title-bar tag strip** (editor, viewer, and demo) is single-sourced in
  `clipboard-ui-core.js`: `renderClipTagChips` renders inert `.filter-tag.group-tag` chips,
  keyboard-accessible hover/focus × controls, and the `+`; `updateTagStrip` keeps the current
  content-addressed id on the strip. `openGroupPickerAt` reuses `clipGroupTreeHtml` and the
  existing controller group mutations - never duplicate a picker or mutation path. New notes
  supply `ensureClipId`: commit first, re-query the ID, then open the picker; capture the +
  button rect *before* awaiting because the commit refresh replaces it in the DOM. The chip ×
  is a real `<button class="gtag-x mi">` for keyboard access — do NOT give `.filter-tag .gtag-x`
  `font: inherit`, it out-specifies `.mi` and renders the literal word "close" instead of the
  Material Symbols glyph (guarded by `ui-tokens.test.js` #10); the accent ring is focus-visible
  only, never on hover.
- **Hermetic Electron QA**: `BOARDCLIP_DATA_DIR` relocates data and may be a legitimate user
  configuration. Set **`BOARDCLIP_ISOLATED=1`** as well for throwaway instances; only that
  explicit flag suppresses cloud account discovery/sync probing. JSON loaders accept an initial
  UTF-8 BOM because Windows PowerShell tools can produce BOM-prefixed valid JSON.
- **Context-menu parity** across popup rows / editor / viewer: `renderClipMenu` grew a
  `context` option (`'popup'` default | `'editor'` | `'viewer'`). Editor drops "Open in
  editor" (it IS the editor); viewer swaps "Open image" for "Open externally"
  (`open-img-ext`). The standalone windows drive the SAME `createClipController` via a light
  `clip-window-state` IPC snapshot (items for numpad previews, groups, pin state — NO full
  bodies to a second renderer); `controller.openClipMenu(id,x,y)` is the entry for hosts
  with no clip rows. Right-click + the "…" button both open it; **delete closes the window**.
  Editor title renames ride the session commit (`editor.setTitle`+`commit`, not a separate
  clip write); editor delete sets `session.suppressCommit` so the close-commit can't
  resurrect the clip from the draft. `ui-parity.test.js` #12 guards the viewer/menu contexts.
- **QA harness**: an isolated instance via `BOARDCLIP_DATA_DIR` + **`BOARDCLIP_ISOLATED=1`**
  + `--user-data-dir` + CDP, seeded with cloud paths pre-disabled plus p2p/AI off so a
  throwaway instance cannot touch real synced data. The hidden tray popup's `requestAnimation
  Frame` never fires, so a CDP driver must call `rerenderList()` directly after dispatching
  input (don't wait on rAF). Detect real providers with `require('./lib/cloud-accounts')()`.

## Multi-select + bulk actions (Ctrl/Shift-click, bulk Paste/Group/Unify/Delete)

- **Selection is LIFTED into `createClipController`** (`selectedIds` set + `anchorId`
  + `focusId`, replacing the old per-consumer `selectedIdx`). Consumers supply only
  `visibleIds()`, `renderSelection(state)`, `allItems()`, `groupNames()`, and bulk
  backends (`deleteClips`/`restoreClips`/`groupAssignMany`/`pasteMany`/`startUnify`)
  + `offerUndo`. `Core.applySelectionUI` paints `.selected` (focus cursor) +
  `.multi-selected` (checked set) and drives `#selectionBar` (added to
  `renderPopupShell`). Do NOT reintroduce a per-side selection index —
  `test/ui-parity.test.js` #9 + `test/multiselect.test.js` guard it.
- **Row demotion + shared menu**: `renderClipActions` is now the SLIM row (primary
  action + a `clip-menu` "..." button). `rename` (Set title) + `del` are DEMOTED
  into `renderClipMenu` (the complete per-clip surface); `renderBulkMenu` is the
  2+-selection variant. Menu items reuse the SAME `data-action` attrs the controller
  already dispatches — no new dispatch. The menu root carries `data-id`, so the
  `gp-btn`/`np-btn` handlers resolve their target via `closest('[data-id]')` (works
  in the in-row picker AND the detached popover — that's why the resolution changed
  from `closest('.item')`). `createMenu(host)` = the shared click popover; app host =
  `document.body` (tokens on `:root`), demo host = `demoWindowEl` (tokens on
  `.bc-popup`). Bulk Group is tri-state (`renderBulkGroupTree` via `groupMembership`:
  all→remove, some/none→add).
- **Delete = instant + Undo toast**, no confirm dialog. `Core.showActionToast`
  reuses the `.toast` element; Ctrl/Cmd+Z re-invokes the undo. `applyDeleteItems`
  RETAINS the text/image blobs (no `removeItemImage`/blob prune) so restore always
  has content; `applyRestoreItems` clears the item tombstone so sync can't resurrect
  the deletion. Single delete (from the menu) routes through the SAME `deleteIds`
  path as bulk, so it too gets Undo.
- **Unify** (fold N text clips → 1) REUSES the conflict `BrowserWindow` +
  `createReconciliationView` verbatim. `startUnify`→`openUnifyWindow`→`unify-step`
  IPC folds an accumulator oldest→newest; `editor.html`'s `mountReconcile` branches
  on `record.unify` (advance vs `resolveConflict`+close). ATOMIC: sources aren't
  touched until the final step confirms, so closing any step aborts with zero
  changes (`editor-close` handles the `unify:` sessionId prefix like `conflict:`).
  The view takes `record.title`/`saveLabel`/`unify` (hides "Remove conflict").
  Merged clip carries the UNION of sources' groups + pin + numpad slot. Text-only:
  Unify is hidden when any image is selected.
- **Reconciliation view = vendored CodeMirror 5 MergeView** (user asked for
  IntelliJ-style EXPLICITLY in Codex session 019f0e67 2026-06-28; two hand-rolled
  attempts fell short — don't hand-roll a third): Current (read-only) | **Result
  (fully editable)** | Incoming (read-only), gutter arrows pull chunks into the
  middle, `connect:'align'` aligns + syncs panes, `collapseIdentical` folds
  unchanged stretches, `ignoreWhitespace` on by default (bar toggle rebuilds the
  view, preserving Result text). Vendored in `site/shared/vendor/cm5/`
  (codemirror@5.65 lib + merge addon + diff-match-patch browser shim; see its
  README) and loaded by BOTH `editor.html` and the demo — guarded by ui-parity #8.
  Skinned entirely with tokens in clipboard-popup.css (`.bc-merge-host` block).
- **The wrapper (`createReconciliationView`) adds**: change count + prev/next nav,
  red-tinted **conflict** regions (Current & Incoming disagreeing with EACH OTHER,
  computed seed-independently: touching left/right chunk pairs, or a two-sided
  replace when one view is clean — plain chunk-overlap NEVER fires when Result is
  seeded from one side), a clickable conflict chip, merge-all-non-conflicting
  (applies chunks bottom-up outside conflict regions), Alt+Up/Down/Left/Right/B
  keys, title pick-chips when the two titles differ, a save-warning while
  sync-conflict regions remain (SKIPPED for unify — the union seed already holds
  both sides; warning there blocked saves invisibly, caught only by the real-app
  pen-test), and a plain-textarea fallback if the vendor scripts fail to load.
- **Merge seeds**: `base.text` when the record has one (true 3-way) → else
  Current (2-pane: unify + baseless conflicts). `Core.unionMergeText` (shared
  regions once + both sides of every change; built on `diffLineHunks`/
  `lcsSegments`, the in-house pure diff) backs "Keep both". **Unify's
  "Merge & continue" MERGES before saving (2026-09-03)**: `mergeLosslessPending`
  pulls every pending chunk that cannot lose Result text (`losslessChange`:
  insertion, grown line, appended lines); only a chunk that would REPLACE
  Result text stays pending and triggers the "still pending" confirm. Before
  this the button just saved as-is and warned, which read as "merge did
  nothing" (owner hit it unifying nine versions of one note).
  The last step's label is `Merge & finish` (same shape as `Merge & continue`) and the
  primary button has a fixed `min-width`: a shorter last-step label once slid "Accept
  current" under a cursor aimed at "Accept incoming" and dropped the newest clip's tail
  (recovered from `clipboard-edit-archive/`, which keeps the final draft of every session). **CRLF is normalized to LF at the view
  boundary** (`toLF`) — stray `\r` defeats BOTH the addon's chunking and
  `collapseIdentical` (its ignoreWhitespace covers spaces/tabs only) and caused
  the original "all-green wall, zero matched lines" bug.
- **Collapse of identical sections (ignore-whitespace)**: the wrapper's `wsNormText`
  normalizes blank-line RUNS + trailing whitespace for the merge view when the WS
  toggle is on, so regions that differ only in blank spacing become truly identical
  and FOLD (the addon's ignoreWhitespace won't collapse blank-line diffs; extending
  its splice to newlines corrupts line bookkeeping). This DOES mean an ignore-ws
  merge saves normalized blank runs — toggle WS off to preserve every byte. Vendored
  merge.js BOARDCLIP patches make it fold like a real diff viewer: `unclearNearChunks`
  collapses THROUGH quiet chunks, `collapseIdenticalStretches` always keeps `margin`
  edge context (else a fully-identical doc folds line 0 and the Result cursor's
  clearOnEnter instantly unfolds it — the "no differences but not collapsed" bug),
  and `MergeView.bcRecollapse()` re-folds after a merge/decline (wired into the
  wrapper's forceRecompute). Verify collapse with a doc that's identical except
  blank-line spacing — it must fold to a widget, not scroll.
- **Real-app pen-test harness**: `node scripts/qa-app-pentest.js` boots a sandbox
  instance (temp `BOARDCLIP_DATA_DIR` + own `--user-data-dir` + CDP port, driven
  over raw WebSocket CDP — Node ≥21). SAFETY: it pre-disables every detected
  cloud provider in the sandbox settings (else the sandbox would default-enable
  sync and merge QA data into the user's REAL synced folders), p2p + AI off, and
  never triggers `pasteMany` (would Ctrl+V into the focused window). Kills only
  electron processes whose cmdline contains its temp dir.
- **Chord routing when search is always-focused**: Ctrl/Cmd+A and Ctrl/Cmd+Z route
  by whether the focused field HAS TEXT (text → native field behavior; empty →
  clip select-all / delete-undo). Don't gate purely on `isTypingTarget` — the app's
  search box is focused nearly always, which would make the chords unreachable.
- **`installSubmenuAutoflip`** (installed once by the controller on
  `menuHost||document`) flips/clamps hover submenus: bounds = viewport for the app
  window but the `.bc-popup` box for the embedded demo; uses setTimeout not rAF
  (rAF halts in background tabs); `.flip-x` class opens side-submenus leftward.
  `.list .item` is `user-select:none` (shift-click was smearing text selection).
- **Selection bar**: Group is its OWN button (`bulk-group-open` → group-only
  tri-state popover via `bulkGroupTreeHtml`, shared with the bulk menu submenu).
  Never fuse it with a "more" menu; the full bulk menu lives on right-click.
- **ONE floating-surface rule** (`.numpad-picker, .tag-submenu, .bc-menu { ... }`
  in clipboard-popup.css, same shadow as `.dialog`) defines every popup panel's
  bg/radius/shadow/padding — do not re-fork per-surface variants (ui-parity #10
  counts the `--menu-edge` shadows). **Numpad renders in keypad formation**
  (`NUMPAD_LAYOUT` = 7 8 9 / 4 5 6 / 1 2 3 + `.np-row` 3-col grid) via the ONE
  `renderNumpadButtons` shared by the in-row picker AND the "..." menu submenu
  (renderItemPicker's old inline loop was a duplication — don't reintroduce it).
- **Gotcha — verifying `.item` background**: `.item` has a `background` CSS
  transition, so `getComputedStyle` read immediately after toggling
  `.selected`/`.multi-selected` returns the PRE-transition (transparent) value;
  `.selection-bar` has no transition so it reads instantly. Verify row backgrounds
  after >150ms or inject `transition:none` — else you chase a phantom "tint not
  applying" bug (I did; it applies fine).

## Design tokens, appearance variants, native glass

- **ONE token layer** in `site/shared/clipboard-tokens.css`, `@import`ed as the
  FIRST rule of `clipboard-popup.css` (relative path works for both app and site)
  and by `site/styles.css`; also linked directly by `mcp-approval.html`. Three
  tiers: (a) PRIMITIVES on `:root` (graphite `--g-050..--g-950`, `--blue-*`,
  `--teal-*`, functional `--green-500/--amber-500/--red-500`, `--sp-*`, `--r-*`,
  `--fs-*`, `--icon-sm/md/lg`, `--dur`+`--ease`); (b) SEMANTIC on `[data-theme]`
  keeping the EXACT old names (`--bg/--surface/--text/--accent/--line/...`) so
  component CSS needed only value swaps, no renames; `--accent-bg`/`--mark-bg`
  derive via `color-mix` over `--accent`. Palette is **graphite + cool blue** —
  the old purple (`#a78bfa/#7c3aed/#8b5cf6`) is gone (a `ui-tokens.test.js` guard
  fails if it returns). Dark `--active-fg` is DARK ink (`--g-950`) because black
  on `--blue-500` (5.7:1) beats white (3.7:1); light uses white on `--blue-600`.
- **Appearance variants** are `data-*` attributes on the same root that carries
  `data-theme`, swapping a small disjoint token set (see the tier-(c) blocks):
  `data-surface` (glass/solid), `data-accent` (blue/teal/mono), `data-density`
  (normal/compact), `data-corners` (soft/sharp), `data-borders`
  (bordered/borderless). Applied by shared `Core.applyVariants(root, opts)`;
  audited live via `Core.createVariantSwitcher` (reuses `.seg`/`.seg-btn`). The
  app renders AND APPLIES **Surface as a real user setting** + the other axes ONLY when
  `runtime_info.debug_variants` (= `BOARDCLIP_DEBUG_VARIANTS=1`, env-only since 2026-09-02: the old
  `!app.isPackaged ||` leaked the audit axes into every git install, and a stale
  `ui_borders:'borderless'` made tags FILLED chips on one machine while another showed the
  default no-bg style - off the flag every window renders the code's default look);
  the demo renders all axes always-on, persisted to `localStorage`. Ship default
  is graphite+blue+glass-where-supported+normal+soft. New settings keys
  (`surface_style` + `accent_variant/ui_density/ui_corners/ui_borders`) are
  per-machine — whitelisted in `save-settings`, deleted in `remoteSettingsPayload`.
- **Native glass = popup pane ONLY** (editor/conflict/approval stay solid — better
  for a text editor + a security prompt). Centralized in main.js
  `glassSupport()` (macOS→vibrancy; Win build ≥22000→acrylic; else none),
  `resolvedSurfaceStyle()`, `popupSurfaceOptions()` (spread into `createPopup`),
  and `applySurfaceToPopup()` (live toggle, no window recreate: mac keeps
  `transparent:true` always + `setVibrancy`, Win uses `setBackgroundMaterial`).
  `notifyColorSchemeChanged` must NOT stamp an opaque bg while glass is on. The
  renderer scrim (`:root[data-surface="glass"] body::before` with `--glass-tint`
  + `backdrop-filter`) is in `index.html`; the OS provides the real blur behind a
  transparent window (CSS `backdrop-filter` can't blur the desktop). Resolved
  surface reaches the renderer via `runtime_info.surface_style` + the
  `surface-changed` broadcast (`preload.onSurfaceChanged`); editor/conflict get
  the non-surface axes via `appearanceVariantPayload()` on `editor-init`; the
  approval modal via `approval-settings` (`mcp-approval-preload.onSettings`).
- `.mi.sm/.mi.lg/.mi.mid` utilities replaced the ~10 inline icon `style=`s; the
  `ui-tokens.test.js` guard fails if an inline `style="font-size` reappears.

## Deploy (boardclip.app)

**Pushes to `main` auto-deploy `site/` to boardclip.app** via Netlify's native
GitHub integration (connected 2026-06-25). The Netlify project `boardclip-app`
(siteId `4ff28f37-765a-4482-a5ea-162fd7513013`, team TwoShot) is linked to
`tobq/boardclip`, branch `main`, **publish directory `site`** (no build command —
static). CRITICAL: the publish dir MUST stay `site`; the repo ROOT `index.html`
is the desktop-app popup, so publishing the root would put the app popup on the
homepage.

History: for its first ~5 weeks the site was a CLI-only Netlify project (provider
`netlify-git`, not Git-linked), so pushes never deployed — that was the chronic
"live site is stale" bug. The `.github/workflows/netlify.yml` Actions workflow was
a never-finished band-aid (it skips without a `NETLIFY_AUTH_TOKEN` secret) and is
now redundant — the native integration handles deploys.

Manual deploy (fallback, e.g. to publish without a push) — the Netlify CLI is
authenticated as `tobi@twoshot.app`:

```
npx --yes netlify-cli@latest deploy --prod --dir site
```

Verify the edge served new bytes (bypasses browser cache):
`curl -s "https://boardclip.app/shared/clipboard-ui-core.js?cb=$(date +%s)" | grep -c createClipController`.

Desktop app distribution has TWO consistent paths, both driven by `main`:
- **Git/CLI installs** auto-update via `lib/auto-update.js` — polls the latest
  `main` commit (GitHub API) every ~4h + 90s after launch, runs `update.bat`
  (git pull → hot-reload if only `index.html`/`site/shared/*` changed, else
  relaunch). Disabled on dirty checkouts (protects local edits) and on packaged
  builds (no `.git`). **GOTCHA (fixed 2026-07-07, commit 5b4fb07):** "dirty" is
  computed from tracked changes only (`build-info.js` passes `--untracked-files=no`,
  matching `update.bat`). Before the fix it counted UNTRACKED files too, so recovery
  artifacts left in the install dir (`clipboard-RECOVERED/`, `*.PRE-RECOVERY-*.json`,
  `clipboard-edit-archive/`) silently blocked auto-update for hours (heartbeat build
  shows `<sha>-dirty`). Also: `update.bat`'s own `npm install` can rewrite the tracked
  `package-lock.json` and re-block the NEXT update — restore it (`git checkout -- package-lock.json`;
  node_modules is unaffected) so the checkout stays clean.
- **Installer downloads** (`.exe`/`.dmg`): `release-binaries.yml` now runs on
  every push to `main` that touches app code (`paths-ignore: site/**`, docs) and
  republishes a single rolling **`latest`** GitHub release (`make_latest: true`)
  that the site's `/releases/latest/download/...` button points at. So the
  download stays in lockstep with `main` — no version tag needed. (Packaged
  installs still don't self-update; that'd need electron-updater — not wired.)
Tagging is optional/archival now, not required to ship.

## Debugging

- **The user's live app runs from `C:\Users\Tobi\AppData\Local\BoardClip`** (a separate
  clone of this repo), NOT this dev checkout. Editing files here does nothing to the
  running app until the change is mirrored there (copy the changed files, or commit+push
  and run its `update.bat`). Renderer files (editor.html, site/shared/*) are loaded fresh
  per window — a newly opened popup/editor window picks up mirrored changes without an app
  restart, but ALREADY-OPEN windows keep the old code until closed and reopened. main.js
  changes always need a full restart.
- Run `npx electron .` directly (not via start.sh) to see stdout/stderr
- **Silent main-process death = check the System event log FIRST** (`Get-WinEvent -FilterHashtable @{LogName='System'; Id=2004}` / Application Popup 26). 2026-09-01 21:52: BoardClip (9-day uptime) vanished mid-keystroke with NO Event 1000, NO crash dump, NO diag quit event; the tiny MCP helpers survived. Cause was a machine-wide *Out of Virtual Memory* (commit exhausted by chrome.exe 13GB + WSL 5GB + a node 4GB) - the allocating process aborts and WER can't even record it. Not a BoardClip bug. Every editor draft was already idle-committed (verified byte-for-byte against history via `clipboard-edit-archive`), zero data loss.
- **Silent stops #2 and #3 (2026-09-03 05:54 and 06:30 local) = console-close kills of instances
  launched from an agent shell (see the bullet below)**: heartbeats simply ended, RSS flat, no
  Event 1000/2004, no crashpad dump, updater ruled out (`.git/FETCH_HEAD` untouched since 05:15).
  Indistinguishable from a tray Quit because nothing logged exits, so main.js now records
  `app.quit {reason: tray-quit|update-relaunch|quit}` on before-quit, `app.exit {code}` on process
  exit, `main.uncaught_exception` / `main.unhandled_rejection`, and `app.child_process_gone` /
  `app.render_process_gone`. A death with heartbeats and NO `app.quit` line = external kill or
  native crash. Check `git reflog --date=iso` in the install + `.git/FETCH_HEAD` mtime to rule
  the auto-updater in or out (its relaunch is `app.exit(0)` after `update.bat`).
- **NEVER launch the live app as `electron.exe .` from an agent tool shell (2026-09-03, two
  "crashes")**: electron attaches to the parent console, and the harness's hidden PowerShell/Bash
  console is torn down later (26 min and 58 min after launch today) - Windows then sends a console
  control event to every attached process: the Network Service child logged
  `app.child_process_gone exit_code -1073741510` (0xC000013A = STATUS_CONTROL_C_EXIT) and the main
  process died in the same second with NO `app.quit` line. The user's Start Menu shortcut /
  Startup `BoardClip.vbs` run `start.bat` in a NEW hidden console that the app then owns, which is
  why it runs for days. To relaunch from a tool: `Start-Process -FilePath <install>\start.bat
  -WindowStyle Hidden` (ShellExecute gives the batch its own console), or `wscript.exe
  "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\BoardClip.vbs"`. Verify afterwards
  that the new electron.exe's parent has exited (`Win32_Process.ParentProcessId` dead = it owns
  its console). Code hardening (2026-09-03, `lib/windows-console.js`): at startup on win32 the app
  `FreeConsole()`s any console that OTHER processes are attached to (`GetConsoleProcessList` > 1 =
  a shell that may close it); a console it is alone on (the VBS/start.bat path after cmd exits)
  is kept. A "window visible" heuristic was tried first and is WRONG - an agent shell's hidden
  console still reports WS_VISIBLE. `npx electron .` output in the terminal now needs
  `BOARDCLIP_KEEP_CONSOLE=1`. `app.start.console` reports `{action: detached|kept|none, reason,
  attached}` plus `ppid`; stdout/stderr are error-guarded and only logSafe writes to them. Unit:
  `test/windows-console.test.js`; sandbox proof = launch via `Start-Process electron.exe` from a
  tool shell and read `app.start.console` (`detached`, `shared-console`).
- **Electron `crashReporter` is now started at boot (local only, `uploadToServer:false`)**, so a
  native crash of main/GPU/renderer leaves a minidump under `%APPDATA%\BoardClip\Crashpad`.
  `app.start.crash_reporter` says `on`. Third silent stop of 2026-09-03 (11:05 local, an instance
  the user launched from the Start Menu, console owned, WER enabled and working, no dump, no
  `app.quit`, no child event): the only remaining signature is an external `TerminateProcess`,
  e.g. another agent session running `taskkill /IM electron.exe` for its own Electron app (Forge
  is Electron too) - the global rule "never kill by image name" exists for exactly this.
- **Machine-wide OOM kills (2026-09-01 and 2026-09-07) + the WMI wedge that blocks a reopen**:
  both silent stops were Windows *low virtual memory* events (System log Id 2004; 09-07: commit
  101 of 104 GB, chrome.exe 44 GB private, vmmemWSL 3.4 GB) - the allocating process is aborted
  with NO app.quit line, no dump, no Event 1000. The same starvation wedges winmgmt, so every
  `Get-CimInstance` hangs, which used to hang `kill.bat` -> `start.bat` -> the Start Menu shortcut
  and the user's reopen attempts ("randomly closed, reopen won't work"). FIX (2026-09-07):
  main.js writes `boardclip.pid` (`{pid, startedAt, exe}`, app dir, untracked) once it holds the
  single-instance lock and removes it on exit; `kill.bat` now runs `scripts/kill-app.ps1`, which
  stops that pid with plain `Get-Process` (path + start-time verified, so a recycled pid is never
  hit), then runs the old WMI sweep in a `Start-Job` capped at 8 s (skipped when winmgmt does
  not answer). Proof: `node scripts/qa-kill-script.js` (pid-file-only kill with the sweep
  disabled, normal kill.bat, nothing-running, other checkouts' electron count unchanged).
  Diagnose a stop WITHOUT WMI: `netstat -ano | findstr 45454` (the app owns UDP 45454),
  `Get-Process electron` (WMI-free; a 30 MB / 10-thread electron is an MCP helper, the app is
  40+ threads / 300+ MB), `Get-Counter '\Memory\Committed Bytes','\Memory\Commit Limit'`, and
  `Get-WinEvent -FilterHashtable @{LogName='System'; Id=2004}`. Relaunch WITHOUT WMI when the
  scripts predate the fix: a VBS `shell.Run """<app>\node_modules\electron\dist\electron.exe"" .", 0, False`
  with `CurrentDirectory` = the app dir (a hidden console the app is alone on - `app.start` shows
  `console: none`), never `Start-Process electron.exe` from a tool shell.
- **Orphan-draft recovery was DEAD from the `-<seq>` filename change until 2026-09-01**: `EDIT_DRAFT_RE` only matched legacy `boardclip-edit-<12hex>-<ts>.txt`, but sessions write `...-<ts>-<seq>.txt`, so `recoverOrphanedEdits` skipped every in-flight draft (15 lingered since July, never retired). Fixed + guarded by `test/edit-draft-recovery.test.js` (reads the regex + generator out of main.js). Idle-commit had covered the gap in practice. Recovery now also SKIPS a draft whose text already sits inside a longer clip (an older prefix of a note edited after the crash) so the first restart doesn't resurrect stale duplicates - only genuinely unsaved text comes back as a new clip.
- **Popup-open / save hot path (2026-09-02, ~10k items, 7.7MB history) - measured, don't regress:**
  the 1-2s "freeze on open" was FOUR stacked costs, none of them the file write (15ms) or
  stringify (23ms). (1) `backupStore.writeSnapshot` ran INLINE in every save: ~0.9s warm (an
  existsSync per item = 10k stats) / 9.5s cold -> p50 636ms, p90 2.4s, max 5.8s of main-thread
  block per clipboard capture. Now: `lib/backup.js` keeps an in-memory pool index (one readdir,
  invalidated by GC) and main hands the PRE-write JSON strings (cached, never re-read) to
  `lib/backup-worker.js` on a worker thread (one at a time; failure -> async full-JSON fallback).
  (2) `get-settings` (called on EVERY popup open via `refreshGroups`) shipped ~475KB of
  tombstones/supersedes + spawned git (65ms) + walked the blob dirs: now `rendererSettingsView()`
  strips the ledgers, `refreshBuildInfo({maxAgeMs})` and `cachedStorageBytes()` cache. (3) the
  renderer re-cloned all items over IPC on every refresh even when unchanged: `get-history-state`
  takes the renderer's known revision and answers `{unchanged:true}`; `refreshGroupsAndList`
  coalesces (one in flight + one queued) instead of stacking five refreshes. (4) Ctrl+A moved focus
  to the LAST row and the lazy list materialised all 9.7k rows to scroll there: `selectAll` keeps
  the cursor. Guards: `popup-lifecycle.test.js` (source), `backup.test.js` #8-9, `multiselect`.
- Main process errors go to terminal, renderer errors to DevTools (Cmd+Option+I)
- To test the app's renderer (`index.html`) without Electron, serve the repo root
  and load it with a stubbed `window.api` (CDP `Page.addScriptToEvaluateOnNewDocument`)
  — it renders the popup + settings and exercises the shared controller/dialogs.
