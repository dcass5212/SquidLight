"""Keyboard input: a global low-level hook (ctypes, no extra dependency) feeding a KeyTracker.

Privacy: only which physical key (as an LED name) went down or up is kept, for at most a
few seconds, in memory. Nothing is logged or written to disk, and no text is reconstructed.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)

EVENT_KEEP_S = 3.0
MAX_EVENTS = 64


@dataclass(frozen=True)
class KeyEvent:
    seq: int  # increasing; lets an effect process each press once
    name: str  # OpenRGB LED name, e.g. "Key: A"
    age: float  # seconds since the press, as of the snapshot


@dataclass(frozen=True)
class KeySnapshot:
    pressed: frozenset = frozenset()  # LED names currently held
    events: tuple = ()  # recent KeyEvent presses, oldest first
    active: bool = False
    error: str = ""


class KeyTracker:
    """Pure state: feed it presses and releases, read snapshots. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pressed: dict[str, int] = {}
        self._events: deque = deque(maxlen=MAX_EVENTS)  # (seq, name, t)
        self._seq = 0

    def press(self, name: str, t: float) -> None:
        with self._lock:
            if name in self._pressed:  # auto-repeat of a held key
                return
            self._seq += 1
            self._pressed[name] = self._seq
            self._events.append((self._seq, name, t))

    def release(self, name: str) -> None:
        with self._lock:
            self._pressed.pop(name, None)

    def clear_held(self) -> None:
        with self._lock:
            self._pressed.clear()

    def snapshot(self, now: float, active: bool = True, error: str = "") -> KeySnapshot:
        with self._lock:
            events = tuple(KeyEvent(s, n, max(now - t, 0.0)) for s, n, t in self._events if now - t <= EVENT_KEEP_S)
            return KeySnapshot(frozenset(self._pressed), events, active, error)


# -- virtual key -> OpenRGB LED name (K70 naming)

_NAMES: dict[int, str] = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x13: "Pause/Break", 0x14: "Caps Lock", 0x1B: "Escape",
    0x20: "Space", 0x21: "Page Up", 0x22: "Page Down", 0x23: "End", 0x24: "Home", 0x25: "Left Arrow",
    0x26: "Up Arrow", 0x27: "Right Arrow", 0x28: "Down Arrow", 0x2C: "Print Screen", 0x2D: "Insert",
    0x2E: "Delete", 0x5B: "Left Windows", 0x5D: "Menu", 0x6A: "Number Pad *",
    0x6B: "Number Pad +", 0x6D: "Number Pad -", 0x6E: "Number Pad .", 0x6F: "Number Pad /", 0x90: "Num Lock",
    0x91: "Scroll Lock", 0xA0: "Left Shift", 0xA1: "Right Shift", 0xA2: "Left Control", 0xA3: "Right Control",
    0xA4: "Left Alt", 0xA5: "Right Alt", 0xAD: "Media Mute", 0xB0: "Media Next", 0xB1: "Media Previous",
    0xB2: "Media Stop", 0xB3: "Media Play/Pause", 0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".",
    0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\ (ANSI)", 0xDD: "]", 0xDE: "'",
}
_NAMES.update({0x30 + i: str(i) for i in range(10)})
_NAMES.update({0x41 + i: chr(0x41 + i) for i in range(26)})
_NAMES.update({0x70 + i: f"F{i + 1}" for i in range(12)})
_NAMES.update({0x60 + i: f"Number Pad {i}" for i in range(10)})


def led_name_for(vk: int, extended: bool = False) -> Optional[str]:
    """The K70 LED name for a Windows virtual-key code, or None for keys it doesn't have."""
    if vk == 0x0D and extended:
        return "Key: Number Pad Enter"
    if vk == 0x5C:  # right Windows: the K70 has no such key; Right Fn is the closest
        return "Key: Right Fn"
    name = _NAMES.get(vk)
    return f"Key: {name}" if name else None


class KeyHook:
    """Runs the Windows low-level keyboard hook on its own thread (it needs a message loop)."""

    def __init__(self, tracker: Optional[KeyTracker] = None, clock: Optional[Callable[[], float]] = None) -> None:
        self.tracker = tracker or KeyTracker()
        self._clock = clock or time.perf_counter
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._error = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if sys.platform != "win32":
            self._error = "Keyboard reactions are only available on Windows."
            return
        self._ready.clear()
        self._error = ""
        self._thread = threading.Thread(target=self._run, name="key-hook", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)

    def stop(self) -> None:
        import ctypes

        t = self._thread
        if t and t.is_alive() and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
            t.join(2.0)
        self._thread = None
        self.tracker.clear_held()

    def snapshot(self, now: float) -> KeySnapshot:
        alive = bool(self._thread and self._thread.is_alive()) and not self._error
        return self.tracker.snapshot(now, active=alive, error=self._error)

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
            ]

        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        hook = wintypes.HHOOK()
        tracker, clock = self.tracker, self._clock

        def proc(code, wparam, lparam):
            # Must be quick and must always pass the event on, whatever happens here.
            try:
                if code == 0:  # HC_ACTION
                    info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    name = led_name_for(info.vkCode, bool(info.flags & 0x01))
                    if name:
                        if wparam in (0x100, 0x104):  # WM_KEYDOWN, WM_SYSKEYDOWN
                            tracker.press(name, clock())
                        elif wparam in (0x101, 0x105):  # WM_KEYUP, WM_SYSKEYUP
                            tracker.release(name)
            except Exception:
                pass
            return user32.CallNextHookEx(hook, code, wparam, lparam)

        cb = HOOKPROC(proc)  # keep a reference for as long as the hook lives
        self._thread_id = kernel32.GetCurrentThreadId()
        hook = user32.SetWindowsHookExW(13, cb, kernel32.GetModuleHandleW(None), 0)  # WH_KEYBOARD_LL
        if not hook:
            self._error = f"Could not hook the keyboard (error {ctypes.get_last_error()})."
            log.warning(self._error)
            self._ready.set()
            return
        log.info("keyboard hook installed")
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            user32.UnhookWindowsHookEx(hook)
            log.info("keyboard hook removed")
