'use strict';
// Worker thread running a Win32 low-level keyboard hook (WH_KEYBOARD_LL).
//
// This exists because Electron's `globalShortcut.register('Super+V')` fails
// on Windows — Windows Clipboard History has already claimed Win+V via the
// built-in RegisterHotKey API. A low-level hook sits *below* that system
// shortcut layer and can swallow the key before Windows Clipboard History
// sees it.
//
// Why a worker thread (not Electron's main thread):
//   1. LL hooks are called via messages posted to the installing thread, so
//      that thread must pump its own GetMessage loop. Electron's main thread
//      has a Chromium message pump that doesn't guarantee LL hook delivery
//      and can starve it under load.
//   2. LowLevelHooksTimeout defaults to 300ms — if any JS task on the main
//      thread runs longer, Windows silently unregisters the hook. Isolating
//      it in a worker with a dedicated GetMessage loop removes that risk.
//
// State sharing with main thread uses a SharedArrayBuffer rather than
// postMessage because the worker is blocked in a synchronous GetMessageW
// call and can't process its JS event loop.

const { parentPort, workerData } = require('worker_threads');
const koffi = require('koffi');

// --- Shared state (worker reads, main writes) ---
// Layout: [popupVisible, slot1, slot2, ..., slot9, reserved, ...]
const sharedState = workerData && workerData.sharedStateBuffer
  ? new Uint8Array(workerData.sharedStateBuffer)
  : new Uint8Array(16);

// --- Win32 constants ---
const WH_KEYBOARD_LL = 13;
const WM_KEYDOWN = 0x0100;
const WM_SYSKEYDOWN = 0x0104;
const VK_LWIN = 0x5B;
const VK_RWIN = 0x5C;
const VK_V = 0x56;
const VK_NUMPAD1 = 0x61;
const VK_NUMPAD9 = 0x69;

// --- koffi types ---
// KBDLLHOOKSTRUCT is the struct Windows passes to a WH_KEYBOARD_LL hook via
// lParam. We declare it so koffi can decode the pointer in the callback.
const KBDLLHOOKSTRUCT = koffi.struct('KBDLLHOOKSTRUCT', {
  vkCode: 'uint32',
  scanCode: 'uint32',
  flags: 'uint32',
  time: 'uint32',
  dwExtraInfo: 'uintptr_t',
});

// MSG is needed for the GetMessageW pump loop below.
const MSG = koffi.struct('MSG', {
  hwnd: 'void *',
  message: 'uint32',
  wParam: 'uintptr_t',
  lParam: 'intptr_t',
  time: 'uint32',
  pt_x: 'int32',
  pt_y: 'int32',
  lPrivate: 'uint32',
});

// Hook procedure prototype. Declaring lParam as KBDLLHOOKSTRUCT* means koffi
// gives us an External we can both decode *and* pass straight back through
// to CallNextHookEx without reinterpreting the pointer ourselves.
const HookProc = koffi.proto(
  'intptr_t __stdcall HookProc(int nCode, uintptr_t wParam, KBDLLHOOKSTRUCT *lParam)'
);

// --- Load user32.dll and bind functions ---
let user32;
try {
  user32 = koffi.load('user32.dll');
} catch (err) {
  parentPort.postMessage({ type: 'error', error: 'koffi.load user32.dll: ' + String(err) });
  process.exit(1);
}

const SetWindowsHookExW = user32.func(
  'void * __stdcall SetWindowsHookExW(int idHook, HookProc *lpfn, void *hmod, uint32 dwThreadId)'
);
const UnhookWindowsHookEx = user32.func(
  'int __stdcall UnhookWindowsHookEx(void *hhk)'
);
const CallNextHookEx = user32.func(
  'intptr_t __stdcall CallNextHookEx(void *hhk, int nCode, uintptr_t wParam, KBDLLHOOKSTRUCT *lParam)'
);
const GetMessageW = user32.func(
  'int __stdcall GetMessageW(_Out_ MSG *lpMsg, void *hWnd, uint32 wMsgFilterMin, uint32 wMsgFilterMax)'
);
const TranslateMessage = user32.func(
  'int __stdcall TranslateMessage(MSG *lpMsg)'
);
const DispatchMessageW = user32.func(
  'intptr_t __stdcall DispatchMessageW(MSG *lpMsg)'
);
const GetAsyncKeyState = user32.func(
  'int16 __stdcall GetAsyncKeyState(int vKey)'
);

// --- Hook callback ---
// Fires synchronously on this worker thread each time a keyboard event is
// delivered to the thread's message queue. Return non-zero to swallow the
// event (it will not propagate to the rest of the hook chain or any app).
const hookCallback = koffi.register((nCode, wParam, lParamExt) => {
  try {
    if (nCode >= 0 && (wParam === WM_KEYDOWN || wParam === WM_SYSKEYDOWN)) {
      const kb = koffi.decode(lParamExt, KBDLLHOOKSTRUCT);
      const vk = kb.vkCode;

      // Win+V → show popup. Checked before Windows Clipboard History can.
      if (vk === VK_V) {
        const winHeld =
          (GetAsyncKeyState(VK_LWIN) & 0x8000) !== 0 ||
          (GetAsyncKeyState(VK_RWIN) & 0x8000) !== 0;
        if (winHeld) {
          parentPort.postMessage({ type: 'show' });
          return 1;
        }
      }

      // Plain Numpad1-9 → quick-paste when a slot is assigned, or assign
      // current item when the popup is open. Passes through otherwise so
      // normal numpad typing still works.
      if (vk >= VK_NUMPAD1 && vk <= VK_NUMPAD9) {
        const slot = vk - VK_NUMPAD1 + 1;
        const popupVisible = sharedState[0] === 1;
        const slotAssigned = sharedState[slot] === 1;
        if (popupVisible || slotAssigned) {
          parentPort.postMessage({ type: 'numpad', slot });
          return 1;
        }
      }
    }
  } catch (err) {
    // Never let JS exceptions escape the hook — Windows will stall the
    // whole keyboard chain. Best-effort report and fall through to
    // CallNextHookEx so the key still reaches its target.
    try { parentPort.postMessage({ type: 'error', error: String(err) }); } catch {}
  }
  return CallNextHookEx(null, nCode, wParam, lParamExt);
}, koffi.pointer(HookProc));

// --- Install hook ---
// hMod=null + dwThreadId=0 → install a global LL hook owned by the current
// process. LL hooks are the one case where a null hMod is explicitly allowed.
const hook = SetWindowsHookExW(WH_KEYBOARD_LL, hookCallback, null, 0);
if (!hook) {
  parentPort.postMessage({ type: 'error', error: 'SetWindowsHookExW returned null' });
  process.exit(1);
}

parentPort.postMessage({ type: 'ready' });

// --- Message pump ---
// Blocks this worker thread pumping messages so Windows can deliver hook
// callbacks. Exits on WM_QUIT or worker termination from the main thread.
const msg = {};
while (true) {
  const ret = GetMessageW(msg, null, 0, 0);
  if (ret <= 0) break;
  TranslateMessage(msg);
  DispatchMessageW(msg);
}

// --- Cleanup (only reached on clean WM_QUIT; worker.terminate skips this) ---
try { UnhookWindowsHookEx(hook); } catch {}
try { koffi.unregister(hookCallback); } catch {}
