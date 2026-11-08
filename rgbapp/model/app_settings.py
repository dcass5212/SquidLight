"""App settings (``settings.json``): OpenRGB path and port, write-rate cap, accent color,
audio device, hardware-monitor URL, start minimized, fade length, and when to turn the
lights off (lock, idle, nightly schedule).

"Start with Windows" is not stored here: the Run key in the registry is the truth
(``rgbapp.system.autostart``), so the installer and the app can't disagree.

Same file rules as ``profiles.json``: atomic write, one ``.bak``, a broken or missing file
loads defaults. Unknown keys are kept, so an older build doesn't drop a newer one's settings.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

from ..engine.away import parse_clock
from ..engine.inputs.stats import LHM_URL
from ..engine.loop import WRITE_CAP_CHOICES
from ..paths import data_dir
from .store import SettingsStore

log = logging.getLogger(__name__)

FILE_NAME = "settings.json"
VERSION = 1
DEFAULT_ACCENT = "#44d62c"
DEFAULT_PORT = 6742
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class AppSettings:
    start_minimized: bool = True  # when Windows starts the app, go straight to the tray
    openrgb_path: str = ""  # empty: the bundled copy
    openrgb_port: int = DEFAULT_PORT
    write_cap_hz: int = 30
    accent: str = DEFAULT_ACCENT
    audio_device: str = ""  # empty: follow the Windows default output
    lhm_url: str = LHM_URL
    fade_ms: int = 600  # fade out/in when the lights go away and when a profile changes; 0 = instant
    off_on_lock: bool = True
    idle_enabled: bool = False
    idle_minutes: int = 10
    schedule_enabled: bool = False
    schedule_off: str = "23:00"
    schedule_on: str = "07:00"

    def normalized(self) -> "AppSettings":
        """A copy with every value in range (bad values fall back to the default)."""
        d = AppSettings()
        port = _int(self.openrgb_port, d.openrgb_port)
        cap = _int(self.write_cap_hz, d.write_cap_hz)
        return AppSettings(
            start_minimized=bool(self.start_minimized),
            openrgb_path=str(self.openrgb_path or "").strip(),
            openrgb_port=port if 1 <= port <= 65535 else d.openrgb_port,
            write_cap_hz=cap if cap in WRITE_CAP_CHOICES else d.write_cap_hz,
            accent=self.accent.lower() if isinstance(self.accent, str) and _HEX.match(self.accent) else d.accent,
            audio_device=str(self.audio_device or ""),
            lhm_url=str(self.lhm_url or "").strip() or d.lhm_url,
            fade_ms=min(max(_int(self.fade_ms, d.fade_ms), 0), 3000),
            off_on_lock=bool(self.off_on_lock),
            idle_enabled=bool(self.idle_enabled),
            idle_minutes=min(max(_int(self.idle_minutes, d.idle_minutes), 1), 240),
            schedule_enabled=bool(self.schedule_enabled),
            schedule_off=_clock(self.schedule_off, d.schedule_off),
            schedule_on=_clock(self.schedule_on, d.schedule_on),
        )


def _clock(value, default: str) -> str:
    if not isinstance(value, str):
        return default
    t = parse_clock(value, parse_clock(default))
    return f"{t.hour:02d}:{t.minute:02d}"


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class AppSettingsStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._file = SettingsStore(path or data_dir() / FILE_NAME)  # for its atomic read/write
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._file.path

    def load(self) -> AppSettings:
        with self._lock:
            doc = self._file._read() or {}
        names = {f.name for f in fields(AppSettings)}
        try:
            return AppSettings(**{k: v for k, v in doc.items() if k in names}).normalized()
        except Exception:
            log.exception("could not read %s; using defaults", FILE_NAME)
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        with self._lock:
            doc = self._file._read() or {}
            doc.update(asdict(settings.normalized()))
            doc["version"] = VERSION
            self._file._write(doc)


class MemorySettingsStore(AppSettingsStore):
    """Nothing on disk: the fake backend and tests."""

    def __init__(self, settings: Optional[AppSettings] = None) -> None:
        self._settings = (settings or AppSettings()).normalized()
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return Path("memory-only-settings.json")

    def load(self) -> AppSettings:
        return AppSettings(**asdict(self._settings))

    def save(self, settings: AppSettings) -> None:
        self._settings = settings.normalized()
