'use strict';
// Fast Windows paste primitives via koffi FFI.
//
// Replaces the previous `cscript` + VBScript SendKeys approach which had two
// problems:
//   1. Spawning cscript per paste costs 200-500ms (cold start). That was the
//      "delay" users noticed vs. the pre-Electron Python version which used
//      direct Win32 calls via ctypes.
//   2. VBScript `WshShell.SendKeys "^v"` has a documented Win32 quirk where
//      it can flip NumLock state on some systems, triggering OEM keyboard
//      software notifications (e.g. Logitech "NumLock ON" overlay).
//
// Uses SendInput (the modern Win32 API recommended for new code — see
// https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput).
// Benefits over the older keybd_event:
//   - Events are atomic: other input from the user or from other processes
//     cannot be interleaved between our Ctrl down / V down / V up / Ctrl up.
//   - Microsoft explicitly flags keybd_event as superseded for new dev.
//
// The INPUT struct uses a tagged union in C. We target x64 (the only arch
// Electron ships by default on Windows) and use a flat struct with explicit
// padding that matches the x64 memory layout (40 bytes total).

const koffi = require('koffi');

if (process.platform !== 'win32') {
  module.exports = {
    sendCtrlV() {},
    getForegroundWindow() { return null; },
    setForegroundWindow() {},
  };
  return;
}

const user32 = koffi.load('user32.dll');

// x64 INPUT struct layout (40 bytes):
//   offset 0:  type      (DWORD, 4)
//   offset 4:  padding   (4)      — union aligns to 8 (ULONG_PTR alignment)
//   offset 8:  wVk       (WORD, 2)
//   offset 10: wScan     (WORD, 2)
//   offset 12: dwFlags   (DWORD, 4)
//   offset 16: time      (DWORD, 4)
//   offset 20: padding   (4)      — align dwExtraInfo to 8
//   offset 24: dwExtraInfo (ULONG_PTR, 8)
//   offset 32: padding   (8)      — pad to 40, matching MOUSEINPUT's size
const INPUT = koffi.struct('INPUT', {
  type: 'uint32',
  _pad0: 'uint32',
  wVk: 'uint16',
  wScan: 'uint16',
  dwFlags: 'uint32',
  time: 'uint32',
  _pad1: 'uint32',
  dwExtraInfo: 'uintptr_t',
  _pad2: koffi.array('uint8', 8),
});

const INPUT_KEYBOARD = 1;
const VK_CONTROL = 0x11;
const VK_V = 0x56;
const KEYEVENTF_KEYUP = 0x0002;

const SendInput = user32.func(
  'uint32 __stdcall SendInput(uint32 cInputs, INPUT *pInputs, int cbSize)'
);
const GetForegroundWindow = user32.func(
  'void * __stdcall GetForegroundWindow()'
);
const SetForegroundWindow = user32.func(
  'int __stdcall SetForegroundWindow(void *hWnd)'
);

const INPUT_SIZE = koffi.sizeof(INPUT);

function keyInput(vk, keyup) {
  return {
    type: INPUT_KEYBOARD,
    _pad0: 0,
    wVk: vk,
    wScan: 0,
    dwFlags: keyup ? KEYEVENTF_KEYUP : 0,
    time: 0,
    _pad1: 0,
    dwExtraInfo: 0,
    _pad2: [0, 0, 0, 0, 0, 0, 0, 0],
  };
}

function sendCtrlV() {
  // Four events delivered atomically: Ctrl down, V down, V up, Ctrl up.
  SendInput(4, [
    keyInput(VK_CONTROL, false),
    keyInput(VK_V, false),
    keyInput(VK_V, true),
    keyInput(VK_CONTROL, true),
  ], INPUT_SIZE);
}

function getForegroundWindow() {
  return GetForegroundWindow();
}

function setForegroundWindow(hwnd) {
  if (!hwnd) return;
  SetForegroundWindow(hwnd);
}

module.exports = { sendCtrlV, getForegroundWindow, setForegroundWindow };
