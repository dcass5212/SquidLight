"""Start with Windows: a value under HKCU\\...\\Run (no admin rights needed).

The installer writes the same value when its "Start with Windows" box is ticked, so the
registry is the single source of truth (``settings.json`` doesn't store it).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "SquidLight"


def launch_command(minimized: bool) -> str:
    """How Windows should start this app: the packaged exe, or ``pythonw -m rgbapp`` from
    source (needs the editable install, ``pip install -e .``)."""
    if getattr(sys, "frozen", False):
        args = [sys.executable]
    else:
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        args = [str(pythonw if pythonw.is_file() else exe), "-m", "rgbapp"]
    if minimized:
        args.append("--minimized")
    return subprocess.list2cmdline(args)


def _open(write: bool):
    import winreg

    if write:
        return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE)
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ)


def current() -> Optional[str]:
    """The registered command, or None when the app doesn't start with Windows."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with _open(False) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("could not read the autostart entry: %s", exc)
        return None


def is_enabled() -> bool:
    return current() is not None


def enable(minimized: bool = True) -> None:
    """Raises OSError if the registry can't be written."""
    import winreg

    cmd = launch_command(minimized)
    with _open(True) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
    log.info("start with Windows: on (%s)", cmd)


def disable() -> None:
    import winreg

    try:
        with _open(True) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return
    log.info("start with Windows: off")
