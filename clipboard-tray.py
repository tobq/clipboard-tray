"""Clipboard history tray app. Run: pip install pystray pyperclip pillow keyboard && python clipboard-tray.py"""

import threading
import time
import json
import re
import ctypes
import ctypes.wintypes
import tkinter as tk
import io
import struct
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
import os
import pyperclip
import pystray
import keyboard  # only used for simulating ctrl+v paste
from PIL import Image, ImageDraw, ImageGrab, ImageTk

PORT = 9123
lock = threading.Lock()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, 'clipboard-history.json')
HTML_PATH = os.path.join(SCRIPT_DIR, 'clipboard-ui.html')
IMG_DIR = os.path.join(SCRIPT_DIR, 'clipboard-images')
SETTINGS_PATH = os.path.join(SCRIPT_DIR, 'clipboard-settings.json')
os.makedirs(IMG_DIR, exist_ok=True)

# --- Settings ---
DEFAULT_SETTINGS = {'max_age_days': 7, 'max_size_gb': 10, 'max_visible': 8, 'regex_search': False}

def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
            return {**DEFAULT_SETTINGS, **json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)

def save_settings_file():
    with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2)

settings = load_settings()

def load_history() -> list[dict]:
    try:
        with open(DB_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_history():
    with open(DB_PATH, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False)

history: list[dict] = load_history()

def get_storage_bytes():
    """Total storage: history JSON + all images on disk."""
    total = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    for fname in os.listdir(IMG_DIR):
        fpath = os.path.join(IMG_DIR, fname)
        if os.path.isfile(fpath):
            total += os.path.getsize(fpath)
    return total

def prune_history():
    """Remove items exceeding age or size limits. Pinned items are kept."""
    now = time.time()
    max_age = settings['max_age_days'] * 86400
    max_bytes = settings['max_size_gb'] * 1024 ** 3
    changed = False

    # Age prune (walk backwards so pop doesn't shift indices)
    for i in range(len(history) - 1, -1, -1):
        item = history[i]
        if not item.get('pinned') and (now - item.get('ts', 0)) > max_age:
            _remove_item_image(item)
            history.pop(i)
            changed = True

    # Size prune (remove oldest non-pinned until under limit)
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
    """Delete an image item's file if no other history entry references it."""
    if item.get('type') != 'image':
        return
    fname = item.get('image', '')
    if sum(1 for h in history if h.get('image') == fname) <= 1:
        fpath = os.path.join(IMG_DIR, fname)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except OSError:
                pass

# --- Clipboard image helpers ---

CF_DIB = 8

def clipboard_has_image():
    """Check if clipboard has an image (CF_DIB format)."""
    ctypes.windll.user32.OpenClipboard(None)
    try:
        return bool(ctypes.windll.user32.IsClipboardFormatAvailable(CF_DIB))
    finally:
        ctypes.windll.user32.CloseClipboard()

def grab_clipboard_image():
    """Grab image from clipboard, return PIL Image or None."""
    try:
        img = ImageGrab.grabclipboard()
        if isinstance(img, Image.Image):
            return img
    except Exception:
        pass
    return None

def image_hash(img):
    """Quick hash of image bytes for dedup."""
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return hashlib.md5(buf.getvalue()).hexdigest()[:12]

def save_clipboard_image(img):
    """Save image to disk, return filename."""
    h = image_hash(img)
    fname = f"{h}.png"
    fpath = os.path.join(IMG_DIR, fname)
    if not os.path.exists(fpath):
        img.save(fpath, format='PNG')
    return fname

def copy_image_to_clipboard(img_path):
    """Put a PNG image back on the clipboard as CF_DIB."""
    img = Image.open(img_path)
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    # Convert to BMP DIB format (no file header)
    buf = io.BytesIO()
    img.save(buf, format='BMP')
    bmp_data = buf.getvalue()
    # BMP file header is 14 bytes, skip it for DIB
    dib_data = bmp_data[14:]

    ctypes.windll.user32.OpenClipboard(None)
    ctypes.windll.user32.EmptyClipboard()
    hmem = ctypes.windll.kernel32.GlobalAlloc(0x0042, len(dib_data))  # GMEM_MOVEABLE | GMEM_ZEROINIT
    ptr = ctypes.windll.kernel32.GlobalLock(hmem)
    ctypes.memmove(ptr, dib_data, len(dib_data))
    ctypes.windll.kernel32.GlobalUnlock(hmem)
    ctypes.windll.user32.SetClipboardData(CF_DIB, hmem)
    ctypes.windll.user32.CloseClipboard()

# --- Clipboard poller ---

def poll_clipboard():
    last_text = ""
    last_img_hash = ""
    while True:
        try:
            # Check for image first
            if clipboard_has_image():
                img = grab_clipboard_image()
                if img:
                    h = image_hash(img)
                    if h != last_img_hash:
                        last_img_hash = h
                        last_text = ""  # reset text tracking
                        fname = save_clipboard_image(img)
                        w, ht = img.size
                        with lock:
                            # Dedup by image filename
                            if not (history and history[0].get('type') == 'image' and history[0].get('image') == fname):
                                for i, item in enumerate(history):
                                    if item.get('type') == 'image' and item.get('image') == fname:
                                        history.pop(i)
                                        break
                                history.insert(0, {"type": "image", "image": fname, "ts": time.time(), "width": w, "height": ht})
                                prune_history()
                                save_history()
            else:
                # Check for text
                current = pyperclip.paste()
                if current and current != last_text:
                    last_text = current
                    last_img_hash = ""  # reset image tracking
                    with lock:
                        if history and history[0].get("text") == current:
                            pass
                        else:
                            for i, item in enumerate(history):
                                if item.get("text") == current:
                                    history.pop(i)
                                    break
                            history.insert(0, {"type": "text", "text": current, "ts": time.time()})
                            prune_history()
                            save_history()
        except Exception:
            pass
        time.sleep(0.4)

# --- HTTP server (for browser fallback at localhost:9123) ---

def load_html():
    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        return f.read()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/history':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            with lock:
                self.wfile.write(json.dumps(history).encode())
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
        ok = lambda: (self.send_response(200), self.send_header('Content-Type', 'application/json'), self.end_headers(), self.wfile.write(b'{"ok":true}'))
        if self.path == '/api/copy':
            pyperclip.copy(body.get('text', ''))
            ok()
        elif self.path == '/api/delete':
            idx = body.get('index')
            with lock:
                if isinstance(idx, int) and 0 <= idx < len(history):
                    history.pop(idx)
                    save_history()
            ok()
        elif self.path == '/api/delete-all':
            with lock:
                history[:] = [item for item in history if item.get('pinned')]
                save_history()
            ok()
        elif self.path == '/api/pin':
            idx = body.get('index')
            with lock:
                if isinstance(idx, int) and 0 <= idx < len(history):
                    history[idx]['pinned'] = not history[idx].get('pinned', False)
                    save_history()
            ok()
        else:
            self.send_response(404)
            self.end_headers()
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

# --- Tkinter popup ---

WIN_W, WIN_H = 460, 520
BG = '#131313'
BG_HOVER = '#1a1a1a'
TEXT = '#cccccc'
TEXT_DIM = '#555555'
ACCENT = '#a78bfa'
PIN_COLOR = '#f59e0b'
RED = '#f87171'
GREEN = '#34d399'

root = None
popup = None
prev_hwnd = None
_tk_images = []  # prevent GC of PhotoImage references
_thumb_cache = {}  # filename -> PhotoImage, persists across popup rebuilds

def init_tk():
    global root
    root = tk.Tk()
    root.withdraw()

def ago(ts):
    s = int(time.time() - ts)
    if s < 3: return 'now'
    if s < 60: return f'{s}s'
    if s < 3600: return f'{s//60}m'
    if s < 86400: return f'{s//3600}h'
    return f'{s//86400}d'

def paste_to_prev_app(item):
    """Copy item to clipboard, refocus the previous app, simulate Ctrl+V."""
    global prev_hwnd
    if item.get('type') == 'image':
        img_path = os.path.join(IMG_DIR, item['image'])
        if os.path.exists(img_path):
            copy_image_to_clipboard(img_path)
    else:
        pyperclip.copy(item.get('text', ''))
    hide_popup()
    if prev_hwnd:
        time.sleep(0.05)
        set_foreground_window(prev_hwnd)
        time.sleep(0.05)
        keyboard.send('ctrl+v')

def hide_popup():
    global popup
    if popup and popup.winfo_exists():
        popup.withdraw()

def show_popup():
    global popup, prev_hwnd
    prev_hwnd = get_foreground_window()
    mx, my = get_mouse_pos()
    left, top, right, bottom = get_monitor_work_area(mx, my)
    x = min(max(left, mx - WIN_W // 2), right - WIN_W)
    # Position so mouse cursor lands on the search bar (~50px from top), not mid-list
    y = min(max(top, my - 50), bottom - WIN_H)

    if root is None:
        return

    root.after(0, lambda: _build_popup(x, y))

BG_SELECTED = '#252525'

def _build_popup(x, y):
    global popup, _tk_images

    if popup and popup.winfo_exists():
        popup.destroy()

    _tk_images = []

    popup = tk.Toplevel(root)
    popup.overrideredirect(True)
    popup.attributes('-topmost', True)
    popup.configure(bg=BG)
    popup.geometry(f'{WIN_W}x{WIN_H}+{x}+{y}')

    popup.bind('<Escape>', lambda e: hide_popup())

    def _on_focus_out(e):
        w = popup.focus_get()
        if w is None:
            root.after(100, hide_popup)
    popup.bind('<FocusOut>', _on_focus_out)

    popup.focus_force()
    popup.lift()
    popup.after(50, lambda: popup.focus_force())

    # Header
    hdr = tk.Frame(popup, bg=BG)
    hdr.pack(fill='x', padx=12, pady=(10, 0))

    with lock:
        items = list(history)

    count_text = f"{len(items)} item{'s' if len(items) != 1 else ''}"
    tk.Label(hdr, text=count_text, fg=TEXT_DIM, bg=BG, font=('Segoe UI', 9)).pack(side='left')

    gear_btn = tk.Label(hdr, text='\u2699', fg=TEXT_DIM, bg=BG, font=('Segoe UI', 11), cursor='hand2')
    gear_btn.pack(side='right')
    gear_btn.bind('<Enter>', lambda e: gear_btn.config(fg=ACCENT))
    gear_btn.bind('<Leave>', lambda e: gear_btn.config(fg=TEXT_DIM))
    gear_btn.bind('<Button-1>', lambda e: _show_settings())

    clear_btn = tk.Label(hdr, text="Clear", fg=TEXT_DIM, bg=BG, font=('Segoe UI', 9), cursor='hand2')
    clear_btn.pack(side='right', padx=(0, 8))
    clear_btn.bind('<Enter>', lambda e: clear_btn.config(fg=RED))
    clear_btn.bind('<Leave>', lambda e: clear_btn.config(fg=TEXT_DIM))
    clear_btn.bind('<Button-1>', lambda e: _do_clear_all())

    # Search row (entry + regex toggle)
    search_row = tk.Frame(popup, bg=BG)
    search_row.pack(fill='x', padx=12, pady=(8, 4))

    search_var = tk.StringVar()
    search = tk.Entry(search_row, textvariable=search_var, bg='#0a0a0a', fg=TEXT, insertbackground=TEXT,
                      relief='flat', font=('Segoe UI', 10), highlightthickness=1, highlightcolor=ACCENT, highlightbackground='#222')
    search.pack(side='left', fill='x', expand=True)

    regex_on = [settings.get('regex_search', False)]
    rx_btn = tk.Label(search_row, text=".*", fg=ACCENT if regex_on[0] else TEXT_DIM, bg='#0a0a0a',
                      font=('Cascadia Code', 10), cursor='hand2', padx=6)
    rx_btn.pack(side='right')

    def _toggle_regex(e=None):
        regex_on[0] = not regex_on[0]
        rx_btn.config(fg=ACCENT if regex_on[0] else TEXT_DIM)
        settings['regex_search'] = regex_on[0]
        save_settings_file()
        apply_filter(search_var.get())

    rx_btn.bind('<Button-1>', _toggle_regex)
    popup.after(100, lambda: search.focus_set())

    # Scrollable list (no visible scrollbar - mousewheel only)
    canvas = tk.Canvas(popup, bg=BG, highlightthickness=0)
    list_frame = tk.Frame(canvas, bg=BG)

    list_frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.create_window((0, 0), window=list_frame, anchor='nw', width=WIN_W - 14)

    canvas.pack(fill='both', expand=True, padx=12, pady=4)

    def _on_mousewheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    canvas.bind_all('<MouseWheel>', _on_mousewheel)

    # --- Selection state ---
    sel = [0]
    rows = []  # current visible rows: [(row_frame, item_dict)]

    def _update_selection(new_idx):
        if not rows:
            return
        new_idx = max(0, min(new_idx, len(rows) - 1))
        old = sel[0]
        sel[0] = new_idx
        if 0 <= old < len(rows):
            _set_row_bg(rows[old][0], BG)
        _set_row_bg(rows[new_idx][0], BG_SELECTED)
        # Scroll into view
        row_widget = rows[new_idx][0]
        canvas.update_idletasks()
        ry = row_widget.winfo_y()
        rh = row_widget.winfo_height()
        ch = canvas.winfo_height()
        top_frac = canvas.yview()[0]
        bot_frac = canvas.yview()[1]
        bbox = canvas.bbox('all')
        if bbox:
            total_h = bbox[3] - bbox[1]
            vis_top = top_frac * total_h
            vis_bot = bot_frac * total_h
            if ry < vis_top:
                canvas.yview_moveto(ry / total_h)
            elif ry + rh > vis_bot:
                canvas.yview_moveto((ry + rh - ch) / total_h)

    # --- Pre-build all item widgets (pack/pack_forget for fast filtering) ---
    # Items stay in natural (chronological) order - pinned items just survive "Clear All"
    all_row_data = []  # [(row_frame, sep_frame, item, search_text)]

    for item in items:
        real_idx = items.index(item)
        pinned = item.get('pinned', False)
        is_image = item.get('type') == 'image'

        row = tk.Frame(list_frame, bg=BG, cursor='hand2')

        if pinned:
            tk.Frame(row, bg=PIN_COLOR, width=2).pack(side='left', fill='y')

        content = tk.Frame(row, bg=BG)
        content.pack(fill='x', expand=True, padx=(8, 4), pady=6)

        if is_image:
            fname = item.get('image', '')
            img_path = os.path.join(IMG_DIR, fname)
            try:
                if fname in _thumb_cache:
                    tk_img = _thumb_cache[fname]
                else:
                    img = Image.open(img_path)
                    img.thumbnail((WIN_W - 120, 60))
                    tk_img = ImageTk.PhotoImage(img)
                    _thumb_cache[fname] = tk_img
                _tk_images.append(tk_img)
                preview = tk.Label(content, image=tk_img, bg=BG, anchor='w')
                preview.pack(fill='x')
            except Exception:
                preview = tk.Label(content, text="[image not found]", fg=TEXT_DIM, bg=BG, anchor='w',
                                 font=('Segoe UI', 9))
                preview.pack(fill='x')
            meta_text = f"{ago(item['ts'])}  {item.get('width', '?')}x{item.get('height', '?')}"
            search_text = 'image'
        else:
            text = item.get('text', '')
            display = text.replace('\r\n', ' ').replace('\n', ' ')
            if len(display) > 80:
                display = display[:77] + '...'
            preview = tk.Label(content, text=display, fg=TEXT, bg=BG, anchor='w',
                             font=('Cascadia Code', 9), wraplength=WIN_W - 80)
            preview.pack(fill='x')
            meta_text = f"{ago(item['ts'])}  {len(text):,} chars"
            search_text = text.lower()

        if pinned:
            meta_text += "  pinned"
        meta = tk.Label(content, text=meta_text, fg=TEXT_DIM, bg=BG, anchor='w', font=('Segoe UI', 8))
        meta.pack(fill='x')

        # Action buttons
        actions = tk.Frame(row, bg=BG)
        actions.pack(side='right', padx=(0, 8))

        pin_lbl = tk.Label(actions, text='\u2605', fg=PIN_COLOR if pinned else TEXT_DIM, bg=BG,
                         font=('Segoe UI', 11), cursor='hand2')
        pin_lbl.pack(side='left', padx=2)
        pin_lbl.bind('<Button-1>', lambda e, idx=real_idx: _do_pin(idx))

        del_lbl = tk.Label(actions, text='\u2715', fg=TEXT_DIM, bg=BG, font=('Segoe UI', 10), cursor='hand2')
        del_lbl.pack(side='left', padx=2)
        del_lbl.bind('<Enter>', lambda e, l=del_lbl: l.config(fg=RED))
        del_lbl.bind('<Leave>', lambda e, l=del_lbl: l.config(fg=TEXT_DIM))
        del_lbl.bind('<Button-1>', lambda e, idx=real_idx: _do_delete(idx))

        # Hover/click: use dynamic index lookup so filtering doesn't break bindings
        def bind_hover(widget, row_frame, it):
            def on_enter(e):
                for i, (r, _) in enumerate(rows):
                    if r is row_frame:
                        _update_selection(i)
                        break
            widget.bind('<Enter>', on_enter)
            widget.bind('<Button-1>', lambda e, item=it: paste_to_prev_app(item))

        for w in [row, content, preview, meta]:
            bind_hover(w, row, item)

        sep = tk.Frame(list_frame, bg='#1a1a1a', height=1)
        all_row_data.append((row, sep, item, search_text))

    empty_label = tk.Label(list_frame, text="Copy something to get started",
                          fg=TEXT_DIM, bg=BG, font=('Segoe UI', 10), pady=40)

    def _matches(query, stext):
        if not query:
            return True
        if regex_on[0]:
            try:
                return bool(re.search(query, stext, re.IGNORECASE))
            except re.error:
                return False
        return query.lower() in stext

    def apply_filter(query=''):
        """Show/hide pre-built widgets instead of destroying and recreating them."""
        for row, sep, _, _ in all_row_data:
            row.pack_forget()
            sep.pack_forget()
        empty_label.pack_forget()

        rows.clear()
        shown = 0
        for row, sep, item, stext in all_row_data:
            if query and not _matches(query, stext):
                continue
            if not query and shown >= settings['max_visible']:
                break
            row.pack(fill='x', pady=0)
            sep.pack(fill='x')
            rows.append((row, item))
            shown += 1

        if not rows:
            empty_label.config(text="No matches" if query else "Copy something to get started")
            empty_label.pack()

        sel[0] = 0
        if rows:
            for r, _ in rows:
                _set_row_bg(r, BG)
            _set_row_bg(rows[0][0], BG_SELECTED)

    # --- Keyboard nav ---
    def _on_key(event):
        if event.keysym == 'Down':
            _update_selection(sel[0] + 1)
            return 'break'
        elif event.keysym == 'Up':
            _update_selection(sel[0] - 1)
            return 'break'
        elif event.keysym == 'Return':
            if rows and 0 <= sel[0] < len(rows):
                paste_to_prev_app(rows[sel[0]][1])
            return 'break'

    search.bind('<Down>', _on_key)
    search.bind('<Up>', _on_key)
    search.bind('<Return>', _on_key)
    popup.bind('<Down>', _on_key)
    popup.bind('<Up>', _on_key)
    popup.bind('<Return>', _on_key)

    # Direct search - no debounce, filtering is just pack/pack_forget
    search_var.trace_add('write', lambda *a: apply_filter(search_var.get()))
    apply_filter()

def _set_row_bg(frame, color):
    frame.config(bg=color)
    for child in frame.winfo_children():
        try:
            child.config(bg=color)
            for sub in child.winfo_children():
                try: sub.config(bg=color)
                except: pass
        except:
            pass

def _maybe_hide():
    if popup and popup.winfo_exists() and popup.state() != 'withdrawn':
        try:
            if not popup.focus_get():
                hide_popup()
        except:
            hide_popup()

def _poll_focus():
    """Periodic check: if popup is visible but lost focus, hide it."""
    if popup and popup.winfo_exists() and popup.state() != 'withdrawn':
        try:
            fg = get_foreground_window()
            # Get the popup's HWND
            popup_hwnd = int(popup.frame(), 16) if popup.frame() else 0
            # If foreground is neither our popup nor a child, dismiss
            if popup.focus_get() is None:
                hide_popup()
        except:
            pass
    if root:
        root.after(300, _poll_focus)

def _show_settings():
    """Settings dialog as a dark themed popup."""
    hide_popup()
    dlg = tk.Toplevel(root)
    dlg.overrideredirect(True)
    dlg.attributes('-topmost', True)
    dlg.configure(bg=BG)

    mx, my = get_mouse_pos()
    left, top, right, bottom = get_monitor_work_area(mx, my)
    dw, dh = 280, 220
    dx = min(max(left, mx - dw // 2), right - dw)
    dy = min(max(top, my - 50), bottom - dh)
    dlg.geometry(f'{dw}x{dh}+{dx}+{dy}')

    dlg.bind('<Escape>', lambda e: dlg.destroy())
    dlg.focus_force()
    dlg.after(50, lambda: dlg.focus_force())

    def _on_focus_out(e):
        if dlg.focus_get() is None:
            root.after(100, dlg.destroy)
    dlg.bind('<FocusOut>', _on_focus_out)

    tk.Label(dlg, text="Settings", fg=TEXT, bg=BG, font=('Segoe UI', 11, 'bold')).pack(padx=12, pady=(12, 8))

    def make_row(parent, label, default):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill='x', padx=12, pady=4)
        tk.Label(f, text=label, fg=TEXT, bg=BG, font=('Segoe UI', 9)).pack(side='left')
        var = tk.StringVar(value=str(default))
        tk.Entry(f, textvariable=var, bg='#0a0a0a', fg=TEXT, insertbackground=TEXT, relief='flat',
                 font=('Segoe UI', 10), width=8, highlightthickness=1, highlightbackground='#222').pack(side='right')
        return var

    age_var = make_row(dlg, "Max age (days)", settings['max_age_days'])
    size_var = make_row(dlg, "Max size (GB)", settings['max_size_gb'])
    vis_var = make_row(dlg, "Visible items", settings['max_visible'])

    # Current usage
    size_mb = get_storage_bytes() / (1024 * 1024)
    tk.Label(dlg, text=f"Current: {len(history)} items, {size_mb:.1f} MB",
             fg=TEXT_DIM, bg=BG, font=('Segoe UI', 8)).pack(pady=(4, 0))

    def save_and_close():
        try:
            settings['max_age_days'] = max(1, int(age_var.get()))
            settings['max_size_gb'] = max(0.1, float(size_var.get()))
            settings['max_visible'] = max(1, min(50, int(vis_var.get())))
            save_settings_file()
            with lock:
                prune_history()
        except ValueError:
            pass
        dlg.destroy()

    save_btn = tk.Label(dlg, text="Save", fg=GREEN, bg='#1a1a1a', font=('Segoe UI', 10),
                        cursor='hand2', padx=16, pady=4)
    save_btn.pack(pady=(8, 0))
    save_btn.bind('<Button-1>', lambda e: save_and_close())
    save_btn.bind('<Enter>', lambda e: save_btn.config(bg='#252525'))
    save_btn.bind('<Leave>', lambda e: save_btn.config(bg='#1a1a1a'))

def _do_clear_all():
    with lock:
        history[:] = [item for item in history if item.get('pinned')]
        save_history()
    show_popup()

def _do_pin(idx):
    with lock:
        if 0 <= idx < len(history):
            history[idx]['pinned'] = not history[idx].get('pinned', False)
            save_history()
    show_popup()

def _do_delete(idx):
    with lock:
        if 0 <= idx < len(history):
            history.pop(idx)
            save_history()
    show_popup()

def open_ui(icon, item):
    show_popup()

def quit_app(icon, item):
    icon.stop()
    os._exit(0)

# --- Low-level keyboard hook ---

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_V = 0x56

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
                    show_popup()
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

if __name__ == '__main__':
    # HTTP server for browser fallback
    server = HTTPServer(('127.0.0.1', PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # Clipboard poller
    threading.Thread(target=poll_clipboard, daemon=True).start()

    # Win+V hotkey via low-level hook
    threading.Thread(target=hotkey_listener, daemon=True).start()

    # System tray
    icon = pystray.Icon(
        "clipboard",
        make_icon(),
        "Clipboard History",
        menu=pystray.Menu(
            pystray.MenuItem("Open", open_ui, default=True),
            pystray.MenuItem("Quit", quit_app),
        )
    )
    threading.Thread(target=icon.run, daemon=True).start()

    print(f"Tray running, Win+V to open popup, web UI also at http://localhost:{PORT}")

    init_tk()
    root.after(300, _poll_focus)
    root.mainloop()
