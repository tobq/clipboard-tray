"""Clipboard history tray app. Run: pip install pystray pyperclip pillow keyboard pywebview && python clipboard-tray.py"""

import threading
import time
import json
import re
import ctypes
import ctypes.wintypes
import io
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
import os
import pyperclip
import pystray
import keyboard
import webview
from PIL import Image, ImageDraw, ImageGrab

PORT = 9123
lock = threading.Lock()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, 'clipboard-history.json')
HTML_PATH = os.path.join(SCRIPT_DIR, 'clipboard-ui.html')
IMG_DIR = os.path.join(SCRIPT_DIR, 'clipboard-images')
SETTINGS_PATH = os.path.join(SCRIPT_DIR, 'clipboard-settings.json')
os.makedirs(IMG_DIR, exist_ok=True)

# --- AHK presets for first-run seeding ---
_AHK_PRESETS = {
    1: "does that all make sense or is there any clarifying questions you have to make for the best output? Maybe even suggestions?\n\nMake sure to look through the code/documentation/etc, and consult back to me before you start - we need to make sure were on the same page first. Think very hard",
    2: "does that all make sense or is there any clarifying questions you have to make for the best output? Maybe even suggestions? Think very hard",
    3: "Nothing else? Any suggestions? Maybe have a final look over of the stuff we've just done. Do you reckon what we have so far is the best/cleanest way to impl this. if not impl a production ready clean minimal version. Think very hard",
    4: "ok that solved that issue, do you reckon what we have so far is the best/cleanest way to impl this. if not impl a production ready clean minimal version. Think very hard",
    5: "Think very hard",
    6: "Would it help if you added comprehensive test logs temporarily and i retest then give you the results to help you pin point the solution? Think very hard",
    7: "I think this is good to go, before you start impl, can you just write down a super technical/detailed plan in markdown format in file. Include key findings from your research, so whoever reading this has context from where to start from, before they move on to the new task at hand. When I say technical, you don't need to write out actual full code implementations, but I mean detail the sorts of tables needing reworking, libraries/methods used, etc... Pseudocode at most unless reference small snippets of code. This conversation can get interrupted/cleared/compacted so we need to be able to impl this from the info in this file. Let me know if that all makes sense or is there any clarifying questions you have to make for the best output? Maybe even suggestions? Think very hard",
    8: "here's where we left off before our conversation got condensed:\n===================\n\n===================",
    9: "We AGGRESSIVELY should try to minimise code/logic duplication and maximise/unify/reuse shared components across projects.\nOften it's better to adapt existing components, further strengthening them as opposed to creating new variants which will likely lead to duplicated effort down the line.\nWe can still have inheritance/composition - doesn't have to be everything in 1 monster function/class, but the core logic should be shared/reused.\nThis must be taken into account at every step of thinking/planning.\nThis reduces maintenance cost and chance of bugs, and makes it easier to understand and adapt code in future",
}

# --- Settings (numpad stored on history items via pinned: 1-9, not in settings) ---
DEFAULT_SETTINGS = {'max_age_days': 7, 'max_size_gb': 10, 'regex_search': False}

def load_settings():
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
            return {**DEFAULT_SETTINGS, **json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)

def save_settings_file():
    # Strip any leftover numpad_slots from settings before saving
    s = {k: v for k, v in settings.items() if k != 'numpad_slots'}
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=2)

settings = load_settings()

def load_history():
    try:
        with open(DB_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_history():
    with open(DB_PATH, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False)

history = load_history()

def get_storage_bytes():
    total = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    for fname in os.listdir(IMG_DIR):
        fpath = os.path.join(IMG_DIR, fname)
        if os.path.isfile(fpath):
            total += os.path.getsize(fpath)
    return total

def prune_history():
    now = time.time()
    max_age = settings['max_age_days'] * 86400
    max_bytes = settings['max_size_gb'] * 1024 ** 3
    changed = False
    for i in range(len(history) - 1, -1, -1):
        item = history[i]
        if not item.get('pinned') and (now - item.get('ts', 0)) > max_age:
            _remove_item_image(item)
            history.pop(i)
            changed = True
    while get_storage_bytes() > max_bytes:
        idx = next((i for i in range(len(history) - 1, -1, -1) if not history[i].get('pinned')), None)
        if idx is None:
            break
        _remove_item_image(history[idx])
        history.pop(idx)
        changed = True
    if changed:
        save_history()

def _remove_item_image(item):
    if item.get('type') != 'image':
        return
    fname = item.get('image', '')
    if sum(1 for h in history if h.get('image') == fname) <= 1:
        fpath = os.path.join(IMG_DIR, fname)
        if os.path.exists(fpath):
            try: os.remove(fpath)
            except OSError: pass

# --- Migration: old numpad_slots -> unified pinned field on history items ---
def migrate_numpad():
    old_slots = settings.get('numpad_slots')
    has_int_pinned = any(isinstance(h.get('pinned'), int) for h in history)

    if old_slots:
        # Migrate existing slots to history items
        for num_str, slot in old_slots.items():
            num = int(num_str)
            if slot.get('type') == 'image':
                match = next((h for h in history if h.get('type') == 'image' and h.get('image') == slot.get('image')), None)
                if match:
                    match['pinned'] = num
                else:
                    history.insert(0, {'type': 'image', 'image': slot['image'], 'ts': time.time(), 'pinned': num})
            else:
                text = slot.get('text', '')
                match = next((h for h in history if h.get('type') != 'image' and h.get('text') == text), None)
                if match:
                    match['pinned'] = num
                else:
                    history.insert(0, {'type': 'text', 'text': text, 'ts': time.time(), 'pinned': num})
        settings.pop('numpad_slots', None)
        save_history()
        save_settings_file()
    elif not has_int_pinned and not history:
        # First run with empty history: seed AHK presets
        for num in sorted(_AHK_PRESETS.keys(), reverse=True):
            history.insert(0, {'type': 'text', 'text': _AHK_PRESETS[num], 'ts': time.time(), 'pinned': num})
        save_history()

# --- Win32 clipboard function declarations (64-bit safe) ---
CF_DIB = 8
CF_UNICODETEXT = 13

_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

_u32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
_u32.OpenClipboard.restype = ctypes.wintypes.BOOL
_u32.CloseClipboard.argtypes = []
_u32.CloseClipboard.restype = ctypes.wintypes.BOOL
_u32.EmptyClipboard.argtypes = []
_u32.EmptyClipboard.restype = ctypes.wintypes.BOOL
_u32.EnumClipboardFormats.argtypes = [ctypes.c_uint]
_u32.EnumClipboardFormats.restype = ctypes.c_uint
_u32.GetClipboardData.argtypes = [ctypes.c_uint]
_u32.GetClipboardData.restype = ctypes.c_void_p
_u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
_u32.SetClipboardData.restype = ctypes.c_void_p
_u32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
_u32.IsClipboardFormatAvailable.restype = ctypes.wintypes.BOOL

_k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
_k32.GlobalAlloc.restype = ctypes.c_void_p
_k32.GlobalLock.argtypes = [ctypes.c_void_p]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_k32.GlobalUnlock.restype = ctypes.wintypes.BOOL
_k32.GlobalSize.argtypes = [ctypes.c_void_p]
_k32.GlobalSize.restype = ctypes.c_size_t

def clipboard_has_image():
    _u32.OpenClipboard(None)
    try:
        return bool(_u32.IsClipboardFormatAvailable(CF_DIB))
    finally:
        _u32.CloseClipboard()

def grab_clipboard_image():
    try:
        img = ImageGrab.grabclipboard()
        if isinstance(img, Image.Image):
            return img
    except Exception:
        pass
    return None

def image_hash(img):
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return hashlib.md5(buf.getvalue()).hexdigest()[:12]

def save_clipboard_image(img):
    h = image_hash(img)
    fname = f"{h}.png"
    fpath = os.path.join(IMG_DIR, fname)
    if not os.path.exists(fpath):
        img.save(fpath, format='PNG')
    return fname

def copy_image_to_clipboard(img_path):
    img = Image.open(img_path)
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    buf = io.BytesIO()
    img.save(buf, format='BMP')
    dib_data = buf.getvalue()[14:]
    _u32.OpenClipboard(None)
    _u32.EmptyClipboard()
    hmem = _k32.GlobalAlloc(0x0042, len(dib_data))
    ptr = _k32.GlobalLock(hmem)
    ctypes.memmove(ptr, dib_data, len(dib_data))
    _k32.GlobalUnlock(hmem)
    _u32.SetClipboardData(CF_DIB, hmem)
    _u32.CloseClipboard()

# --- Clipboard backup/restore (full format, like AHK ClipboardAll) ---
def backup_clipboard():
    formats = []
    if not _u32.OpenClipboard(None):
        return formats
    try:
        fmt = 0
        while True:
            fmt = _u32.EnumClipboardFormats(fmt)
            if fmt == 0:
                break
            handle = _u32.GetClipboardData(fmt)
            if not handle:
                continue
            size = _k32.GlobalSize(handle)
            if not size:
                continue
            ptr = _k32.GlobalLock(handle)
            if not ptr:
                continue
            try:
                data = ctypes.string_at(ptr, size)
                formats.append((fmt, data))
            finally:
                _k32.GlobalUnlock(handle)
    except Exception:
        pass
    finally:
        _u32.CloseClipboard()
    return formats

def restore_clipboard(formats):
    if not formats:
        return
    if not _u32.OpenClipboard(None):
        return
    _u32.EmptyClipboard()
    for fmt, data in formats:
        hmem = _k32.GlobalAlloc(0x0042, len(data))
        if not hmem:
            continue
        ptr = _k32.GlobalLock(hmem)
        if not ptr:
            continue
        ctypes.memmove(ptr, data, len(data))
        _k32.GlobalUnlock(hmem)
        _u32.SetClipboardData(fmt, hmem)
    _u32.CloseClipboard()

# --- Numpad quick-paste (unified: pinned = true | 1-9 | false) ---
_poll_gate = threading.Event()
_poll_gate.set()

def numpad_paste(slot_num):
    with lock:
        item = next((h for h in history if h.get('pinned') == slot_num), None)
    if not item:
        return False
    # AHK-style clipboard juggling: backup -> set -> Ctrl+V -> sleep -> restore
    _poll_gate.clear()
    backup = backup_clipboard()
    try:
        if item.get('type') == 'image':
            img_path = os.path.join(IMG_DIR, item['image'])
            if not os.path.exists(img_path):
                return False
            copy_image_to_clipboard(img_path)
        else:
            pyperclip.copy(item.get('text', ''))
        time.sleep(0.05)
        keyboard.send('ctrl+v')
        time.sleep(0.15)
        restore_clipboard(backup)
    finally:
        _poll_gate.set()
    return True

# --- Clipboard poller ---
def poll_clipboard():
    last_text = ""
    last_img_hash = ""
    while True:
        _poll_gate.wait()
        try:
            if clipboard_has_image():
                img = grab_clipboard_image()
                if img:
                    h = image_hash(img)
                    if h != last_img_hash:
                        last_img_hash = h
                        last_text = ""
                        fname = save_clipboard_image(img)
                        w, ht = img.size
                        with lock:
                            if not (history and history[0].get('type') == 'image' and history[0].get('image') == fname):
                                old_pinned = None
                                for i, item in enumerate(history):
                                    if item.get('type') == 'image' and item.get('image') == fname:
                                        old_pinned = item.get('pinned')
                                        history.pop(i)
                                        break
                                entry = {"type": "image", "image": fname, "ts": time.time(), "width": w, "height": ht}
                                if old_pinned:
                                    entry['pinned'] = old_pinned
                                history.insert(0, entry)
                                prune_history()
                                save_history()
            else:
                current = pyperclip.paste()
                if current and current != last_text:
                    last_text = current
                    last_img_hash = ""
                    with lock:
                        if history and history[0].get("text") == current:
                            pass
                        else:
                            old_pinned = None
                            for i, item in enumerate(history):
                                if item.get("text") == current:
                                    old_pinned = item.get('pinned')
                                    history.pop(i)
                                    break
                            entry = {"type": "text", "text": current, "ts": time.time()}
                            if old_pinned:
                                entry['pinned'] = old_pinned
                            history.insert(0, entry)
                            prune_history()
                            save_history()
        except Exception:
            pass
        time.sleep(0.4)

# --- HTTP server ---
def load_html():
    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        return f.read()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/history':
            self._json(history)
        elif self.path == '/api/settings':
            with lock:
                size_bytes = get_storage_bytes()
            self._json({**settings, 'storage_bytes': size_bytes, 'item_count': len(history)})
        elif self.path.startswith('/images/'):
            fname = self.path.split('/')[-1]
            fpath = os.path.join(IMG_DIR, fname)
            if os.path.exists(fpath):
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.end_headers()
                with open(fpath, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(load_html().encode())

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == '/api/paste':
            idx = body.get('index')
            with lock:
                if isinstance(idx, int) and 0 <= idx < len(history):
                    item = history[idx]
                    if item.get('type') == 'image':
                        img_path = os.path.join(IMG_DIR, item['image'])
                        if os.path.exists(img_path):
                            copy_image_to_clipboard(img_path)
                    else:
                        pyperclip.copy(item.get('text', ''))
            self._ok()
            threading.Thread(target=_paste_sequence, daemon=True).start()

        elif self.path == '/api/copy':
            pyperclip.copy(body.get('text', ''))
            self._ok()

        elif self.path == '/api/delete':
            idx = body.get('index')
            with lock:
                if isinstance(idx, int) and 0 <= idx < len(history):
                    _remove_item_image(history[idx])
                    history.pop(idx)
                    save_history()
            self._ok()

        elif self.path == '/api/delete-all':
            with lock:
                removed = [item for item in history if not item.get('pinned')]
                for item in removed:
                    _remove_item_image(item)
                history[:] = [item for item in history if item.get('pinned')]
                save_history()
            self._ok()

        elif self.path == '/api/pin':
            idx = body.get('index')
            with lock:
                if isinstance(idx, int) and 0 <= idx < len(history):
                    p = history[idx].get('pinned')
                    if isinstance(p, int):
                        history[idx]['pinned'] = True  # keep pinned, just remove numpad number
                    else:
                        history[idx]['pinned'] = not p
                    save_history()
            self._ok()

        elif self.path == '/api/numpad-assign':
            idx = body.get('index')
            slot = body.get('slot')
            with lock:
                if isinstance(idx, int) and isinstance(slot, int) and 1 <= slot <= 9 and 0 <= idx < len(history):
                    for h in history:
                        if h.get('pinned') == slot:
                            h['pinned'] = True  # keep pinned, remove number
                    history[idx]['pinned'] = slot
                    save_history()
            self._ok()

        elif self.path == '/api/numpad-unassign':
            slot = body.get('slot')
            with lock:
                if isinstance(slot, int) and 1 <= slot <= 9:
                    for h in history:
                        if h.get('pinned') == slot:
                            h['pinned'] = True
                            save_history()
                            break
            self._ok()

        elif self.path == '/api/settings':
            if 'max_age_days' in body:
                settings['max_age_days'] = max(1, int(body['max_age_days']))
            if 'max_size_gb' in body:
                settings['max_size_gb'] = max(0.1, float(body['max_size_gb']))
            if 'regex_search' in body:
                settings['regex_search'] = bool(body['regex_search'])
            save_settings_file()
            with lock:
                prune_history()
            self._ok()

        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        with lock:
            self.wfile.write(json.dumps(data).encode())

    def _ok(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a): pass

# --- Tray icon ---
def make_icon():
    img = Image.new('RGB', (64, 64), '#7c7cf0')
    d = ImageDraw.Draw(img)
    d.rectangle([12, 8, 52, 56], fill='#1a1a2e', outline='#e0e0e0', width=2)
    d.rectangle([18, 18, 46, 24], fill='#e0e0e0')
    d.rectangle([18, 30, 40, 36], fill='#e0e0e0')
    d.rectangle([18, 42, 34, 48], fill='#e0e0e0')
    return img

# --- Win32 helpers ---
class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

def get_mouse_pos():
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

def get_monitor_work_area(mx, my):
    hmon = ctypes.windll.user32.MonitorFromPoint(POINT(mx, my), 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    r = mi.rcWork
    return r.left, r.top, r.right, r.bottom

def get_foreground_window():
    return ctypes.windll.user32.GetForegroundWindow()

def set_foreground_window(hwnd):
    ctypes.windll.user32.SetForegroundWindow(hwnd)

# --- pywebview popup ---
WIN_W, WIN_H = 460, 520
win = None
prev_hwnd = None
_popup_shown = False

class Api:
    def hide_popup(self):
        hide_popup()

    def paste_and_hide(self, index):
        threading.Thread(target=_do_paste_index, args=(index,), daemon=True).start()

def _do_paste_index(index):
    with lock:
        if 0 <= index < len(history):
            item = history[index]
            if item.get('type') == 'image':
                img_path = os.path.join(IMG_DIR, item['image'])
                if os.path.exists(img_path):
                    copy_image_to_clipboard(img_path)
            else:
                pyperclip.copy(item.get('text', ''))
    _paste_sequence()

def _paste_sequence():
    time.sleep(0.05)
    hide_popup()
    time.sleep(0.08)
    if prev_hwnd:
        set_foreground_window(prev_hwnd)
        time.sleep(0.08)
        keyboard.send('ctrl+v')

def hide_popup():
    global _popup_shown
    _popup_shown = False
    if win:
        try: win.hide()
        except Exception: pass

def show_popup():
    global prev_hwnd, _popup_shown
    prev_hwnd = get_foreground_window()
    mx, my = get_mouse_pos()
    left, top, right, bottom = get_monitor_work_area(mx, my)
    x = min(max(left, mx - WIN_W // 2), right - WIN_W)
    y = min(max(top, my - 50), bottom - WIN_H)
    if win:
        win.move(x, y)
        win.show()
        _popup_shown = True

# --- Low-level keyboard hook ---
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_V = 0x56
VK_NUMPAD1 = 0x61
VK_NUMPAD9 = 0x69

_win_held = False
_last_popup = 0.0
DEBOUNCE_MS = 500

user32 = ctypes.windll.user32
LRESULT = ctypes.c_longlong
HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
user32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.wintypes.HINSTANCE, ctypes.wintypes.DWORD]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [ctypes.wintypes.HHOOK, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", ctypes.wintypes.DWORD), ("scanCode", ctypes.wintypes.DWORD),
                 ("flags", ctypes.wintypes.DWORD), ("time", ctypes.wintypes.DWORD),
                 ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

_hook_handle = None

@HOOKPROC
def ll_keyboard_hook(nCode, wParam, lParam):
    global _win_held, _last_popup
    if nCode >= 0:
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk = kb.vkCode
        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            if vk in (VK_LWIN, VK_RWIN):
                _win_held = True
            elif vk == VK_V and _win_held:
                now = time.time() * 1000
                if now - _last_popup > DEBOUNCE_MS:
                    _last_popup = now
                    threading.Thread(target=show_popup, daemon=True).start()
                return 1
            elif VK_NUMPAD1 <= vk <= VK_NUMPAD9:
                slot_num = vk - VK_NUMPAD1 + 1
                if _popup_shown:
                    try: win.evaluate_js(f'assignNumpad({slot_num})')
                    except Exception: pass
                    return 1
                else:
                    with lock:
                        has_slot = any(h.get('pinned') == slot_num for h in history)
                    if has_slot:
                        threading.Thread(target=numpad_paste, args=(slot_num,), daemon=True).start()
                        return 1
        elif wParam in (WM_KEYUP, WM_SYSKEYUP):
            if vk in (VK_LWIN, VK_RWIN):
                _win_held = False
    return user32.CallNextHookEx(_hook_handle, nCode, wParam, lParam)

def hotkey_listener():
    global _hook_handle
    _hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, ll_keyboard_hook, None, 0)
    if not _hook_handle:
        print("Warning: Could not install keyboard hook")
        return
    msg = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

def open_ui(icon, item):
    threading.Thread(target=show_popup, daemon=True).start()

def quit_app(icon, item):
    icon.stop()
    os._exit(0)

if __name__ == '__main__':
    migrate_numpad()

    server = HTTPServer(('127.0.0.1', PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=poll_clipboard, daemon=True).start()
    threading.Thread(target=hotkey_listener, daemon=True).start()

    icon = pystray.Icon(
        "clipboard", make_icon(), "Clipboard History",
        menu=pystray.Menu(
            pystray.MenuItem("Open", open_ui, default=True),
            pystray.MenuItem("Quit", quit_app),
        )
    )
    threading.Thread(target=icon.run, daemon=True).start()

    print(f"Tray running, Win+V to open popup, web UI also at http://localhost:{PORT}")
    time.sleep(0.5)

    api = Api()
    win = webview.create_window(
        'Clipboard', f'http://127.0.0.1:{PORT}',
        width=WIN_W, height=WIN_H,
        frameless=True, on_top=True, hidden=True,
        js_api=api,
    )
    webview.start()
