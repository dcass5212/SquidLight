"""Resume from sleep and screen lock: Windows tells top-level windows about both.

Resume arrives as WM_POWERBROADCAST. Lock and unlock arrive as WM_WTSSESSION_CHANGE, but
only after the window registers for it (``register_session_notifications``).

Qt hands native messages to filters installed on the application. The main window must
have a native handle for this (``App.start`` makes sure it does, even when it starts
hidden in the tray). The render loop also notices the stall on its own; this just
reacts faster and more than once while USB devices come back.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter

log = logging.getLogger(__name__)

WM_POWERBROADCAST = 0x0218
PBT_APMRESUMESUSPEND = 0x0007  # resumed because of user input
PBT_APMRESUMEAUTOMATIC = 0x0012  # resumed (always sent)
WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0
DEBOUNCE_S = 5.0  # both resume messages usually arrive together


def register_session_notifications(hwnd: int) -> bool:
    """Ask Windows to send this window lock/unlock messages. False if it refused."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.wtsapi32.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION))
    except Exception:
        log.exception("could not register for lock/unlock notifications")
        return False


def session_is_locked() -> bool:
    """True if the input desktop isn't ours (the lock screen), e.g. when we start while locked.
    Windows only reports changes, so this covers the state at startup."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        desktop = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not desktop:
            return True  # can't open it: the secure desktop (lock screen) has input
        user32.CloseDesktop(desktop)
        return False
    except Exception:
        return False


class PowerEventFilter(QAbstractNativeEventFilter):
    def __init__(
        self,
        on_resume: Callable[[], None],
        clock: Callable[[], float] = time.monotonic,
        on_lock_changed: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__()
        self._on_resume = on_resume
        self._on_lock_changed = on_lock_changed
        self._clock = clock
        self._last = -1e9

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32" and bytes(event_type) == b"windows_generic_MSG":
            try:
                from ctypes import wintypes

                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_POWERBROADCAST:
                    self.handle(int(msg.wParam))
                elif msg.message == WM_WTSSESSION_CHANGE:
                    self.handle_session(int(msg.wParam))
            except Exception:
                log.exception("power event filter failed")
        return False, 0

    def handle_session(self, wparam: int) -> None:
        if self._on_lock_changed is None or wparam not in (WTS_SESSION_LOCK, WTS_SESSION_UNLOCK):
            return
        self._on_lock_changed(wparam == WTS_SESSION_LOCK)

    def handle(self, wparam: int) -> None:
        if wparam not in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
            return
        now = self._clock()
        if now - self._last < DEBOUNCE_S:
            return
        self._last = now
        log.info("resumed from sleep; reapplying lighting")
        self._on_resume()

