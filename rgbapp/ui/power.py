"""Resume from sleep: Windows sends WM_POWERBROADCAST to every top-level window.

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
DEBOUNCE_S = 5.0  # both resume messages usually arrive together


class PowerEventFilter(QAbstractNativeEventFilter):
    def __init__(self, on_resume: Callable[[], None], clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._on_resume = on_resume
        self._clock = clock
        self._last = -1e9

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32" and bytes(event_type) == b"windows_generic_MSG":
            try:
                from ctypes import wintypes

                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_POWERBROADCAST:
                    self.handle(int(msg.wParam))
            except Exception:
                log.exception("power event filter failed")
        return False, 0

    def handle(self, wparam: int) -> None:
        if wparam not in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
            return
        now = self._clock()
        if now - self._last < DEBOUNCE_S:
            return
        self._last = now
        log.info("resumed from sleep; reapplying lighting")
        self._on_resume()

