"""When should the lights be off? Locked screen, no input for a while, or a nightly schedule.

``AwayPolicy`` is pure (no Windows calls, no Qt) so it is easy to test. ``idle_seconds``
reads the time since the last keyboard or mouse input from Windows.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from datetime import time as dtime
from typing import Optional


@dataclass(frozen=True)
class AwayPolicy:
    off_on_lock: bool = True
    idle_enabled: bool = False
    idle_minutes: int = 10
    schedule_enabled: bool = False
    schedule_off: str = "23:00"  # lights go out at this time of day...
    schedule_on: str = "07:00"  # ...and come back at this one (may be earlier: crosses midnight)

    def reasons(self, now: dtime, idle_s: float, locked: bool) -> list[str]:
        """Why the lights should be off right now (empty: keep them on)."""
        out = []
        if self.off_on_lock and locked:
            out.append("locked")
        if self.idle_enabled and idle_s >= self.idle_minutes * 60:
            out.append("idle")
        if self.schedule_enabled and in_window(now, parse_clock(self.schedule_off), parse_clock(self.schedule_on)):
            out.append("schedule")
        return out


def parse_clock(text: str, default: Optional[dtime] = None) -> dtime:
    """'HH:MM' -> time. Bad text gives ``default`` (midnight if none)."""
    try:
        h, m = str(text).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return dtime(h, m)
    except (ValueError, TypeError):
        pass
    return default or dtime(0, 0)


def in_window(now: dtime, start: dtime, end: dtime) -> bool:
    """True from ``start`` up to (not including) ``end``; a window that ends before it
    starts runs through midnight. Equal times mean no window."""
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input (0 if Windows can't say)."""
    if sys.platform != "win32":
        return 0.0
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        tick = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0  # wraps every ~49 days
    except Exception:
        return 0.0
