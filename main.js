const { app, BrowserWindow, Tray, Menu, globalShortcut, clipboard, nativeImage,
        ipcMain, protocol, screen } = require('electron');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const os = require('os');
const { exec, spawn } = require('child_process');

// Windows-specific fast input (keybd_event, Get/SetForegroundWindow).
// Module is a no-op on non-Windows platforms so it's safe to require unconditionally.
const winPaste = require('./lib/windows-paste');

app.setName('Clipboard Tray');

// --- Paths ---
const SCRIPT_DIR = __dirname;
const DB_PATH = path.join(SCRIPT_DIR, 'clipboard-history.json');
const SETTINGS_PATH = path.join(SCRIPT_DIR, 'clipboard-settings.json');
const IMG_DIR = path.join(SCRIPT_DIR, 'clipboard-images');

if (!fs.existsSync(IMG_DIR)) fs.mkdirSync(IMG_DIR, { recursive: true });

// --- AHK presets for first-run seeding ---
const AHK_PRESETS = {
  1: "does that all make sense or is there any clarifying questions you have to make for the best output? Maybe even suggestions?\n\nMake sure to look through the code/documentation/etc, and consult back to me before you start - we need to make sure were on the same page first. Think very hard",
  2: "does that all make sense or is there any clarifying questions you have to make for the best output? Maybe even suggestions? Think very hard",
  3: "Nothing else? Any suggestions? Maybe have a final look over of the stuff we've just done. Do you reckon what we have so far is the best/cleanest way to impl this. if not impl a production ready clean minimal version. Think very hard",
  4: "ok that solved that issue, do you reckon what we have so far is the best/cleanest way to impl this. if not impl a production ready clean minimal version. Think very hard",
  5: "Think very hard",
  6: "Would it help if you added comprehensive test logs temporarily and i retest then give you the results to help you pin point the solution? Think very hard",
  7: "I think this is good to go, before you start impl, can you just write down a super technical/detailed plan in markdown format in file. Include key findings from your research, so whoever reading this has context from where to start from, before they move on to the new task at hand. When I say technical, you don't need to write out actual full code implementations, but I mean detail the sorts of tables needing reworking, libraries/methods used, etc... Pseudocode at most unless reference small snippets of code. This conversation can get interrupted/cleared/compacted so we need to be able to impl this from the info in this file. Let me know if that all makes sense or is there any clarifying questions you have to make for the best output? Maybe even suggestions? Think very hard",
  8: "here's where we left off before our conversation got condensed:\n===================\n\n===================",
  9: "We AGGRESSIVELY should try to minimise code/logic duplication and maximise/unify/reuse shared components across projects.\nOften it's better to adapt existing components, further strengthening them as opposed to creating new variants which will likely lead to duplicated effort down the line.\nWe can still have inheritance/composition - doesn't have to be everything in 1 monster function/class, but the core logic should be shared/reused.\nThis must be taken into account at every step of thinking/planning.\nThis reduces maintenance cost and chance of bugs, and makes it easier to understand and adapt code in future",
};

// --- Settings ---
const DEFAULT_SETTINGS = { max_age_days: 7, max_size_gb: 10, regex_search: false, groups: [], sync_path: '' };

function loadSettings() {
  try {
    return { ...DEFAULT_SETTINGS, ...JSON.parse(fs.readFileSync(SETTINGS_PATH, 'utf-8')) };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

function saveSettingsFile() {
  const s = { ...settings };
  delete s.numpad_slots;
  fs.writeFileSync(SETTINGS_PATH, JSON.stringify(s, null, 2));
  scheduleSyncMerge();
}

let settings = loadSettings();

// --- History ---
function loadHistory() {
  try {
    return JSON.parse(fs.readFileSync(DB_PATH, 'utf-8'));
  } catch {
    return [];
  }
}

function saveHistory() {
  fs.writeFileSync(DB_PATH, JSON.stringify(history));
  scheduleSyncMerge();
  syncHookState();
}

// Reflect current history/popup state into the Windows hook's shared
// buffer so the hook worker can synchronously decide whether to swallow
// plain numpad keypresses.
function syncHookState() {
  if (!windowsHook) return;
  const assigned = new Set();
  for (const item of history) {
    if (typeof item.pinned === 'number' && item.pinned >= 1 && item.pinned <= 9) {
      assigned.add(item.pinned);
    }
  }
  windowsHook.setSlotAssignments(assigned);
}

let history = loadHistory();

// --- Helpers ---
function hasNumpadSlot(item, n) {
  return typeof item.pinned === 'number' && item.pinned === n;
}

function getStorageBytes() {
  let total = 0;
  try { total = fs.statSync(DB_PATH).size; } catch {}
  try {
    for (const fname of fs.readdirSync(IMG_DIR)) {
      try { total += fs.statSync(path.join(IMG_DIR, fname)).size; } catch {}
    }
  } catch {}
  return total;
}

function removeItemImage(item) {
  if (item.type !== 'image') return;
  const fname = item.image || '';
  if (history.filter(h => h.image === fname).length <= 1) {
    try { fs.unlinkSync(path.join(IMG_DIR, fname)); } catch {}
  }
}

function pruneHistory() {
  const now = Date.now() / 1000;
  const maxAge = settings.max_age_days * 86400;
  const maxBytes = settings.max_size_gb * 1024 ** 3;
  let changed = false;

  for (let i = history.length - 1; i >= 0; i--) {
    if (!history[i].pinned && (now - (history[i].ts || 0)) > maxAge) {
      removeItemImage(history[i]);
      history.splice(i, 1);
      changed = true;
    }
  }

  while (getStorageBytes() > maxBytes) {
    let idx = -1;
    for (let i = history.length - 1; i >= 0; i--) {
      if (!history[i].pinned) { idx = i; break; }
    }
    if (idx < 0) break;
    removeItemImage(history[idx]);
    history.splice(idx, 1);
    changed = true;
  }

  if (changed) saveHistory();
}

// --- Migration: old numpad_slots -> unified pinned field ---
function migrateNumpad() {
  const oldSlots = settings.numpad_slots;
  const hasIntPinned = history.some(h => typeof h.pinned === 'number');

  if (oldSlots) {
    for (const [numStr, slot] of Object.entries(oldSlots)) {
      const num = parseInt(numStr);
      if (slot.type === 'image') {
        const match = history.find(h => h.type === 'image' && h.image === slot.image);
        if (match) match.pinned = num;
        else history.unshift({ type: 'image', image: slot.image, ts: Date.now() / 1000, pinned: num });
      } else {
        const text = slot.text || '';
        const match = history.find(h => h.type !== 'image' && h.text === text);
        if (match) match.pinned = num;
        else history.unshift({ type: 'text', text, ts: Date.now() / 1000, pinned: num });
      }
    }
    delete settings.numpad_slots;
    saveHistory();
    saveSettingsFile();
  } else if (!hasIntPinned && !history.length) {
    for (const num of [9, 8, 7, 6, 5, 4, 3, 2, 1]) {
      if (AHK_PRESETS[num]) {
        history.unshift({ type: 'text', text: AHK_PRESETS[num], ts: Date.now() / 1000, pinned: num });
      }
    }
    saveHistory();
  }
}

// --- Sync: merge local <-> shared (Google Drive etc) ---
function contentKey(item) {
  if (item.type === 'image') return `img:${item.image}`;
  return `txt:${crypto.createHash('md5').update(item.text || '').digest('hex')}`;
}

function metadataScore(item) {
  let score = 0;
  if (typeof item.pinned === 'number') score += 3;      // numpad slot
  else if (item.pinned === true) score += 2;             // starred
  if (item.group) score += 1;
  return score;
}

function mergeHistories(local, remote) {
  const merged = new Map();

  for (const item of local) merged.set(contentKey(item), item);

  for (const item of remote) {
    const key = contentKey(item);
    const existing = merged.get(key);
    if (!existing) {
      merged.set(key, item);
    } else {
      const remoteScore = metadataScore(item);
      const localScore = metadataScore(existing);
      if (remoteScore > localScore ||
          (remoteScore === localScore && (item.ts || 0) > (existing.ts || 0))) {
        merged.set(key, item);
      }
    }
  }

  return [...merged.values()].sort((a, b) => (b.ts || 0) - (a.ts || 0));
}

function mergeGroups(local, remote) {
  return [...new Set([...(local || []), ...(remote || [])])];
}

function syncImages(remoteImgDir) {
  if (!fs.existsSync(remoteImgDir)) fs.mkdirSync(remoteImgDir, { recursive: true });

  // Copy remote -> local (missing locally)
  try {
    for (const fname of fs.readdirSync(remoteImgDir)) {
      const localPath = path.join(IMG_DIR, fname);
      if (!fs.existsSync(localPath)) {
        fs.copyFileSync(path.join(remoteImgDir, fname), localPath);
      }
    }
  } catch {}

  // Copy local -> remote (missing remotely)
  try {
    for (const fname of fs.readdirSync(IMG_DIR)) {
      const remotePath = path.join(remoteImgDir, fname);
      if (!fs.existsSync(remotePath)) {
        fs.copyFileSync(path.join(IMG_DIR, fname), remotePath);
      }
    }
  } catch {}
}

let lastSyncMtime = 0;
let syncDebounceTimer = null;
let insideSync = false;

function scheduleSyncMerge() {
  if (!settings.sync_path || insideSync) return;
  if (syncDebounceTimer) clearTimeout(syncDebounceTimer);
  syncDebounceTimer = setTimeout(() => { lastSyncMtime = 0; syncMerge(); }, 500);
}

function syncMerge() {
  const syncPath = settings.sync_path;
  if (!syncPath || !fs.existsSync(syncPath)) return;

  insideSync = true;
  try {
    const remoteDbPath = path.join(syncPath, 'clipboard-history.json');
    const remoteSettingsPath = path.join(syncPath, 'clipboard-settings.json');
    const remoteImgDir = path.join(syncPath, 'clipboard-images');

    // Check if remote changed since last sync (skip if unchanged)
    let remoteMtime = 0;
    try { remoteMtime = fs.statSync(remoteDbPath).mtimeMs; } catch {}
    let localMtime = 0;
    try { localMtime = fs.statSync(DB_PATH).mtimeMs; } catch {}
    if (Math.max(remoteMtime, localMtime) <= lastSyncMtime) return;
    lastSyncMtime = Date.now();

    // Load remote data
    let remoteHistory = [];
    try { remoteHistory = JSON.parse(fs.readFileSync(remoteDbPath, 'utf-8')); } catch {}
    let remoteSettings = {};
    try { remoteSettings = JSON.parse(fs.readFileSync(remoteSettingsPath, 'utf-8')); } catch {}

    // Merge histories
    const merged = mergeHistories(history, remoteHistory);
    const localChanged = JSON.stringify(merged) !== JSON.stringify(history);
    const remoteChanged = JSON.stringify(merged) !== JSON.stringify(remoteHistory);

    if (localChanged) {
      history.length = 0;
      history.push(...merged);
    }

    // Merge groups from settings + any groups found on history items
    const historyGroups = history.map(h => h.group).filter(Boolean);
    const mergedGroups = mergeGroups(settings.groups, [...(remoteSettings.groups || []), ...historyGroups]);
    const groupsChanged = JSON.stringify(mergedGroups) !== JSON.stringify(settings.groups);
    if (groupsChanged) settings.groups = mergedGroups;

    // Sync images both ways
    syncImages(remoteImgDir);

    // Only write if something actually changed
    if (localChanged || groupsChanged) {
      saveHistory();
      saveSettingsFile();
    }
    if (remoteChanged || groupsChanged) {
      try { fs.writeFileSync(remoteDbPath, JSON.stringify(merged)); } catch {}
      try {
        const remoteSave = { ...settings };
        delete remoteSave.numpad_slots;
        delete remoteSave.sync_path;
        fs.writeFileSync(remoteSettingsPath, JSON.stringify(remoteSave, null, 2));
      } catch {}
    }

    // Update mtime tracking
    try {
      const rmt = fs.statSync(path.join(syncPath, 'clipboard-history.json')).mtimeMs;
      const lmt = fs.statSync(DB_PATH).mtimeMs;
      lastSyncMtime = Math.max(rmt, lmt);
    } catch {}
  } finally { insideSync = false; }
}

// --- Image helpers ---
function imageHash(buffer) {
  return crypto.createHash('md5').update(buffer).digest('hex').slice(0, 12);
}

function saveClipboardImage(nativeImg) {
  const buf = nativeImg.toPNG();
  const hash = imageHash(buf);
  const fname = `${hash}.png`;
  const fpath = path.join(IMG_DIR, fname);
  if (!fs.existsSync(fpath)) fs.writeFileSync(fpath, buf);
  const size = nativeImg.getSize();
  return { fname, width: size.width, height: size.height };
}

// --- Clipboard polling ---
let lastText = '';
let lastImgHash = '';
let pollGate = true;

function addToHistory(entry, matchFn) {
  // Check if already at top
  if (history.length && matchFn(history[0])) return;
  // Find existing, preserve metadata
  const existIdx = history.findIndex(matchFn);
  if (existIdx >= 0) {
    if (history[existIdx].pinned) entry.pinned = history[existIdx].pinned;
    if (history[existIdx].group) entry.group = history[existIdx].group;
    history.splice(existIdx, 1);
  }
  history.unshift(entry);
  pruneHistory();
  saveHistory();
}

function pollClipboard() {
  if (!pollGate) return;

  try {
    const img = clipboard.readImage();
    if (!img.isEmpty()) {
      const buf = img.toPNG();
      const h = imageHash(buf);
      if (h !== lastImgHash) {
        lastImgHash = h;
        lastText = '';
        const { fname, width, height } = saveClipboardImage(img);
        addToHistory(
          { type: 'image', image: fname, ts: Date.now() / 1000, width, height },
          it => it.type === 'image' && it.image === fname
        );
      }
    } else {
      const text = clipboard.readText();
      if (text && text !== lastText) {
        lastText = text;
        lastImgHash = '';
        addToHistory(
          { type: 'text', text, ts: Date.now() / 1000 },
          it => it.text === text
        );
      }
    }
  } catch {}
}

// --- Clipboard backup/restore (simplified — backs up text/image/html/rtf) ---
function backupClipboard() {
  return {
    text: clipboard.readText(),
    html: clipboard.readHTML(),
    rtf: clipboard.readRTF(),
    image: clipboard.readImage(),
  };
}

function restoreClipboard(backup) {
  if (!backup) return;
  clipboard.clear();
  if (!backup.image.isEmpty()) {
    clipboard.writeImage(backup.image);
  } else if (backup.text) {
    const formats = {};
    if (backup.text) formats.text = backup.text;
    if (backup.html) formats.html = backup.html;
    if (backup.rtf) formats.rtf = backup.rtf;
    clipboard.write(formats);
  }
}

// --- Paste simulation ---
// Sends Ctrl+V (Cmd+V on mac) to the currently focused window. On Windows
// this uses a direct keybd_event call via koffi (microseconds, no side
// effects). On macOS we use osascript because there's no equivalent native
// Electron API — acceptable there because the Mac paste flow is less hot.
function simulatePaste() {
  if (process.platform === 'win32') {
    winPaste.sendCtrlV();
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    exec(`osascript -e 'tell application "System Events" to keystroke "v" using command down'`, () => resolve());
  });
}

// --- Numpad quick-paste ---
async function numpadPaste(slotNum) {
  // Drop the call if a previous paste is still in its restore window —
  // otherwise rapid Num-key presses race and the second call's "backup"
  // captures the first call's pasted content.
  if (!pollGate) return;
  const item = history.find(h => hasNumpadSlot(h, slotNum));
  if (!item) return;

  pollGate = false;
  const backup = backupClipboard();
  setClipboardToItem(item);
  // Minimum delay for Windows clipboard propagation before paste. 15ms is
  // tight but reliable — clipboard.writeText is synchronous and Windows
  // WM_CLIPBOARDUPDATE propagates within a few ms on any modern system.
  await new Promise(r => setTimeout(r, 15));
  await simulatePaste();
  // Fire-and-forget restore: the target app needs ~100-150ms to read from
  // the clipboard after receiving Ctrl+V. We don't block the caller on that.
  setTimeout(() => {
    try { restoreClipboard(backup); } catch {}
    pollGate = true;
  }, 150);
}

// --- Window & state ---
const WIN_W = 460;
const WIN_H = 520;
let win = null;
let tray = null;

function createPopup() {
  win = new BrowserWindow({
    width: WIN_W,
    height: WIN_H,
    frame: false,
    alwaysOnTop: true,
    show: false,
    skipTaskbar: true,
    resizable: false,
    backgroundColor: '#131313',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.loadFile('index.html');

  // Dev: auto-reload UI when index.html changes (debounced)
  let reloadTimer = null;
  fs.watch(path.join(__dirname, 'index.html'), () => {
    if (reloadTimer) clearTimeout(reloadTimer);
    reloadTimer = setTimeout(() => {
      if (win && !win.isDestroyed()) win.webContents.reloadIgnoringCache();
    }, 300);
  });

  // Windows: blur-to-hide works reliably
  if (process.platform === 'win32') {
    win.on('blur', () => {
      setTimeout(() => {
        if (win && !win.isDestroyed() && !win.isFocused()) win.hide();
      }, 150);
    });
  }

  win.on('hide', () => {
    if (windowsHook) windowsHook.setPopupVisible(false);
    // Clear any open modals/state in renderer
    win.webContents.executeJavaScript(`
      document.getElementById('confirmOverlay')?.classList.remove('show');
      document.getElementById('settingsView')?.classList.remove('show');
      document.getElementById('mainView')?.classList.remove('hidden');
    `).catch(() => {});
  });

  win.on('close', (e) => {
    if (!app.isQuitting) {
      e.preventDefault();
      win.hide();
    }
  });
}

// HWND of the app that was frontmost before the popup was shown. We restore
// focus to it before pasting so the user's terminal/editor/etc. receives the
// keystrokes instead of our now-hidden popup.
let savedForegroundWindow = null;

function hidePopup() {
  if (win && !win.isDestroyed()) win.hide();
  if (windowsHook) windowsHook.setPopupVisible(false);
}

function showPopup() {
  if (!win) return;
  if (win.isVisible()) {
    hidePopup();
    return;
  }

  // Capture the currently-focused window *before* showing ours so pasteAndHide
  // can restore focus to it. Electron doesn't do this automatically.
  if (process.platform === 'win32') {
    savedForegroundWindow = winPaste.getForegroundWindow();
  }

  const cursor = screen.getCursorScreenPoint();
  const display = screen.getDisplayNearestPoint(cursor);
  const { x: wx, y: wy, width: ww, height: wh } = display.workArea;

  const x = Math.min(Math.max(wx, cursor.x - WIN_W / 2), wx + ww - WIN_W);
  const y = Math.min(Math.max(wy, cursor.y - 50), wy + wh - WIN_H);

  win.setPosition(Math.round(x), Math.round(y));
  win.show();
  win.moveTop();
  win.focus();
  if (windowsHook) windowsHook.setPopupVisible(true);
}

function setClipboardToItem(item) {
  if (item.type === 'image') {
    const imgPath = path.join(IMG_DIR, item.image);
    if (fs.existsSync(imgPath)) clipboard.writeImage(nativeImage.createFromPath(imgPath));
  } else {
    clipboard.writeText(item.text || '');
  }
}

async function pasteAndHide(index) {
  if (index < 0 || index >= history.length) return;
  const item = history[index];

  pollGate = false;
  try {
    setClipboardToItem(item);
    hidePopup();
    if (process.platform === 'darwin') {
      // macOS: dock-hidden apps don't return focus automatically.
      // Use osascript to activate the frontmost app, then paste.
      await new Promise(r => setTimeout(r, 50));
      await new Promise((resolve) => {
        exec(`osascript -e '
          tell application "System Events"
            set frontApp to name of first application process whose frontmost is true
            tell application process frontApp to set frontmost to true
            delay 0.05
            keystroke "v" using command down
          end tell'`, () => resolve());
      });
    } else {
      // Windows: explicitly restore focus to the app that was frontmost
      // before we showed the popup. Without this, hidePopup() may leave
      // focus on the desktop/shell and Ctrl+V goes nowhere.
      if (savedForegroundWindow) winPaste.setForegroundWindow(savedForegroundWindow);
      await new Promise(r => setTimeout(r, 15));
      await simulatePaste();
    }
  } finally {
    pollGate = true;
  }
}

function createTray() {
  const iconPath = path.join(__dirname, 'icon.png');
  let trayIcon;
  if (fs.existsSync(iconPath)) {
    trayIcon = nativeImage.createFromPath(iconPath).resize({ width: 16, height: 16 });
  } else {
    trayIcon = nativeImage.createEmpty();
  }
  if (process.platform === 'darwin') trayIcon.setTemplateImage(true);

  tray = new Tray(trayIcon);
  tray.setToolTip('Clipboard History');

  const contextMenu = Menu.buildFromTemplate([
    { label: 'Open', click: showPopup },
    { type: 'separator' },
    { label: 'Quit', click: () => { app.isQuitting = true; app.quit(); } },
  ]);

  tray.setContextMenu(contextMenu);
  tray.on('click', showPopup);
  tray.on('double-click', showPopup);
}

// --- Open in editor ---
function openEditor(idx) {
  if (idx < 0 || idx >= history.length || history[idx].type === 'image') return;
  const item = history[idx];
  const originalText = item.text || '';
  const tmpPath = path.join(os.tmpdir(), `clip-${Date.now()}.txt`);
  fs.writeFileSync(tmpPath, originalText, 'utf-8');

  const cmd = process.platform === 'darwin' ? 'open' : 'notepad.exe';
  const args = process.platform === 'darwin' ? ['-t', '-W', tmpPath] : [tmpPath];
  const proc = spawn(cmd, args, { detached: true, stdio: 'ignore' });

  proc.on('exit', () => {
    try {
      const newText = fs.readFileSync(tmpPath, 'utf-8');
      // Use item reference — survives index shifts from deletes/adds
      if (newText !== originalText && item.text === originalText) {
        item.text = newText;
        saveHistory();
      }
    } catch {}
    try { fs.unlinkSync(tmpPath); } catch {}
  });

  proc.unref();
}

// --- IPC handlers ---
function setupIPC() {
  ipcMain.handle('get-history', () => history);

  ipcMain.handle('get-settings', () => ({
    ...settings,
    storage_bytes: getStorageBytes(),
    item_count: history.length,
  }));

  ipcMain.handle('paste', (_, index) => {
    if (typeof index !== 'number' || index < 0 || index >= history.length) return;
    setClipboardToItem(history[index]);
  });

  ipcMain.handle('paste-and-hide', (_, index) => pasteAndHide(index));

  ipcMain.handle('hide-popup', () => hidePopup());

  ipcMain.handle('copy', (_, text) => clipboard.writeText(text || ''));

  ipcMain.handle('delete-item', (_, index) => {
    if (typeof index !== 'number' || index < 0 || index >= history.length) return;
    removeItemImage(history[index]);
    history.splice(index, 1);
    saveHistory();
  });

  ipcMain.handle('delete-all', () => {
    const kept = [];
    for (const item of history) {
      if (item.pinned) kept.push(item);
      else removeItemImage(item);
    }
    history.length = 0;
    history.push(...kept);
    saveHistory();
  });

  ipcMain.handle('pin', (_, index) => {
    if (typeof index !== 'number' || index < 0 || index >= history.length) return;
    const p = history[index].pinned;
    if (typeof p === 'number') {
      history[index].pinned = true; // keep pinned, remove numpad number
    } else {
      history[index].pinned = !p;
    }
    saveHistory();
  });

  ipcMain.handle('numpad-assign', (_, index, slot) => {
    if (typeof index !== 'number' || typeof slot !== 'number' ||
        slot < 1 || slot > 9 || index < 0 || index >= history.length) return;
    for (const h of history) {
      if (hasNumpadSlot(h, slot)) h.pinned = true;
    }
    history[index].pinned = slot;
    saveHistory();
  });

  ipcMain.handle('numpad-unassign', (_, slot) => {
    if (typeof slot !== 'number' || slot < 1 || slot > 9) return;
    for (const h of history) {
      if (hasNumpadSlot(h, slot)) {
        h.pinned = true;
        saveHistory();
        break;
      }
    }
  });

  ipcMain.handle('save-settings', (_, body) => {
    if (body.max_age_days !== undefined) settings.max_age_days = Math.max(1, parseInt(body.max_age_days));
    if (body.max_size_gb !== undefined) settings.max_size_gb = Math.max(0.1, parseFloat(body.max_size_gb));
    if (body.regex_search !== undefined) settings.regex_search = !!body.regex_search;
    saveSettingsFile();
    pruneHistory();
  });

  ipcMain.handle('group-create', (_, name) => {
    if (!name) return;
    if (!settings.groups) settings.groups = [];
    if (!settings.groups.includes(name)) {
      settings.groups.push(name);
      saveSettingsFile();
    }
  });

  ipcMain.handle('group-delete', (_, name) => {
    const groups = settings.groups || [];
    const idx = groups.indexOf(name);
    if (idx >= 0) {
      groups.splice(idx, 1);
      for (const h of history) {
        if (h.group === name) delete h.group;
      }
      saveSettingsFile();
      saveHistory();
    }
  });

  ipcMain.handle('group-assign', (_, index, group) => {
    if (typeof index !== 'number' || index < 0 || index >= history.length || !group) return;
    const item = history[index];
    if (item.group === group) {
      delete item.group;
    } else {
      item.group = group;
      if (!item.pinned) item.pinned = true;
    }
    saveHistory();
  });

  ipcMain.handle('copy-image-path', (_, index) => {
    if (typeof index !== 'number' || index < 0 || index >= history.length ||
        history[index].type !== 'image') return { path: null };
    const fname = history[index].image;
    const src = path.join(IMG_DIR, fname);
    if (!fs.existsSync(src)) return { path: null };
    const dest = path.join(os.homedir(), 'Downloads', fname);
    fs.copyFileSync(src, dest);
    clipboard.writeText(dest);
    return { path: dest };
  });

  ipcMain.handle('open-editor', (_, index) => {
    if (typeof index !== 'number' || index < 0 || index >= history.length ||
        history[index].type === 'image') return;
    openEditor(index);
  });

  ipcMain.handle('set-sync-path', (_, syncPath) => {
    settings.sync_path = syncPath || '';
    saveSettingsFile();
    if (syncPath) {
      // Ensure the sync directory exists
      if (!fs.existsSync(syncPath)) fs.mkdirSync(syncPath, { recursive: true });
      lastSyncMtime = 0; // force next sync
      syncMerge();
    }
  });

  ipcMain.handle('get-cloud-accounts', async () => {
    const accounts = [];
    const seen = new Set();
    const addAccount = (email, myDrivePath) => {
      const drivePath = path.join(myDrivePath, 'clipboard-tray');
      if (seen.has(drivePath)) return;
      seen.add(drivePath);
      accounts.push({ email, path: drivePath });
    };

    if (process.platform === 'darwin') {
      // macOS: ~/Library/CloudStorage/GoogleDrive[-email]/My Drive/
      const cloudBase = path.join(os.homedir(), 'Library', 'CloudStorage');
      try {
        for (const entry of fs.readdirSync(cloudBase)) {
          // Multi-account folder format: GoogleDrive-email@domain.com/
          // Single-account folder format: GoogleDrive/ (no dash)
          if (entry === 'GoogleDrive') {
            const myDrive = path.join(cloudBase, entry, 'My Drive');
            if (fs.existsSync(myDrive)) addAccount('Google Drive', myDrive);
          } else if (entry.startsWith('GoogleDrive-')) {
            const email = entry.replace('GoogleDrive-', '').replace(/_/g, '.');
            const myDrive = path.join(cloudBase, entry, 'My Drive');
            if (fs.existsSync(myDrive)) addAccount(email, myDrive);
          }
        }
      } catch {}
    } else if (process.platform === 'win32') {
      // Windows: Google Drive mounts as a virtual drive (G:, H:, etc.).
      //
      // Mount letter resolution (both paths run — results deduped):
      //   1. Registry — HKCU\Software\Google\DriveFS\PerAccountPreferences
      //      has a JSON blob with mount_point_path per account. Preferred
      //      over drive-letter scanning because it won't false-positive on
      //      unrelated drives that happen to contain a "My Drive" folder.
      //   2. Fallback letter scan G..Z for "\My Drive" — covers misconfigured
      //      or no-registry setups.
      //
      // Account email resolution:
      //   Google Drive doesn't store the email in the registry or in any
      //   plaintext file under DriveFS\<account_id>\. The only user-visible
      //   place is the drive's Windows Explorer "Description" property
      //   ("tobi@example.com - Google Drive"). We query all Google Drive
      //   descriptions in a single PowerShell invocation (cold start ~500ms,
      //   so per-letter calls would be unacceptable).
      const { promisify } = require('util');
      const execAsync = promisify(exec);

      // Step 1: collect candidate mount letters from registry + drive scan.
      const letters = new Set();
      try {
        const { stdout } = await execAsync(
          'reg query "HKCU\\Software\\Google\\DriveFS" /v PerAccountPreferences',
          { windowsHide: true, timeout: 3000 }
        );
        const match = stdout.match(/REG_SZ\s+(.+)/);
        if (match) {
          const prefs = JSON.parse(match[1].trim());
          for (const acct of prefs.per_account_preferences || []) {
            const mp = acct.value && acct.value.mount_point_path;
            if (mp && mp.length === 1) letters.add(mp);
          }
        }
      } catch {}
      for (const letter of 'GHIJKLMNOPQRSTUVWXYZ') {
        try {
          if (fs.existsSync(`${letter}:\\My Drive`)) letters.add(letter);
        } catch {}
      }

      // Step 2: one PowerShell call gets all Google Drive descriptions.
      const emailByLetter = new Map();
      if (letters.size > 0) {
        try {
          const { stdout } = await execAsync(
            'powershell -NoProfile -Command "Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Description -match \'Google Drive\' } | ForEach-Object { \\"$($_.Name)|$($_.Description)\\" }"',
            { windowsHide: true, timeout: 5000 }
          );
          for (const line of stdout.split(/\r?\n/)) {
            const [name, desc] = line.split('|');
            if (!name || !desc) continue;
            const emailMatch = desc.match(/^(\S+@\S+)/);
            if (emailMatch) emailByLetter.set(name.trim(), emailMatch[1]);
          }
        } catch {}
      }

      // Step 3: build account entries.
      for (const letter of letters) {
        const myDrive = `${letter}:\\My Drive`;
        const email = emailByLetter.get(letter);
        addAccount(email || `Google Drive (${letter}:)`, myDrive);
      }
    }
    return accounts;
  });

  ipcMain.handle('sync-now', () => {
    lastSyncMtime = 0;
    syncMerge();
  });

  ipcMain.handle('get-auto-launch', () => {
    return app.getLoginItemSettings().openAtLogin;
  });

  ipcMain.handle('set-auto-launch', (_, enabled) => {
    app.setLoginItemSettings({ openAtLogin: enabled });
  });
}

// --- Global shortcuts ---
let windowsHook = null;

function handleNumpad(slot) {
  if (win && win.isVisible()) {
    // Popup open: assign numpad to selected item
    win.webContents.executeJavaScript(`window.assignNumpad(${slot})`).catch(() => {});
  } else {
    // Popup closed: quick-paste from slot
    numpadPaste(slot);
  }
}

function registerShortcuts() {
  if (process.platform === 'win32') {
    // Windows Clipboard History owns Win+V and Win+Numpad1-9, so we can't use
    // Electron's globalShortcut (RegisterHotKey) here — it silently fails.
    // Instead, install a WH_KEYBOARD_LL hook on a dedicated worker thread that
    // intercepts these keys *before* Windows Clipboard History sees them.
    const { install } = require('./lib/windows-hook');
    windowsHook = install({
      onShowPopup: showPopup,
      onNumpadPaste: handleNumpad,
    });
    // Seed the shared state with current history so plain numpad keys
    // immediately intercept for already-assigned slots.
    syncHookState();
    return;
  }

  // macOS / Linux: Electron globalShortcut works
  const showKey = 'CommandOrControl+Shift+V';
  globalShortcut.register(showKey, showPopup);

  for (let n = 1; n <= 9; n++) {
    const key = `Super+num${n}`;
    const slot = n;
    const registered = globalShortcut.register(key, () => handleNumpad(slot));
    if (!registered) console.log(`Warning: Could not register ${key}`);
  }
}

// --- Single instance lock ---
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  console.log('Another instance is already running. Quitting.');
  app.quit();
}
app.on('second-instance', () => {
  // If user tries to start again, show the popup
  showPopup();
});

// --- Custom protocol for serving clipboard images ---
protocol.registerSchemesAsPrivileged([
  { scheme: 'clip-img', privileges: { bypassCSP: true, supportFetchAPI: true, standard: true, secure: true } }
]);

// --- App lifecycle ---
app.whenReady().then(() => {
  protocol.handle('clip-img', (request) => {
    const url = new URL(request.url);
    const fname = decodeURIComponent(url.hostname + url.pathname).replace(/^\/+/, '').replace(/\/+$/, '');
    const filePath = path.join(IMG_DIR, fname);
    try {
      const data = fs.readFileSync(filePath);
      return new Response(data, { headers: { 'Content-Type': 'image/png' } });
    } catch {
      return new Response('Not found', { status: 404 });
    }
  });

  if (process.platform === 'darwin') app.dock.hide();

  migrateNumpad();
  setupIPC();
  createPopup();
  createTray();
  registerShortcuts();

  setInterval(pollClipboard, 400);

  // Sync with shared folder on startup + every 30s
  syncMerge();
  setInterval(syncMerge, 30000);

  const hotkey = process.platform === 'darwin' ? 'Cmd+Shift+V' : 'Win+V';
  console.log(`Clipboard Tray running. ${hotkey} to open popup.`);
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
  if (windowsHook) windowsHook.uninstall();
});
app.on('window-all-closed', () => { /* keep running as tray app */ });
