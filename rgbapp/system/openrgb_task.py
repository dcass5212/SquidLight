"""The scheduled task that runs the bundled OpenRGB elevated (installed mode).

A non-elevated app can't start an elevated process without a UAC prompt, but it *can*
trigger a task that was registered (by an administrator) to run "with highest
privileges" as the same user. The installer registers the task
(``SquidLight.exe --register-openrgb-task``); if it is missing or points at the wrong
exe or port, the app recreates it once through a UAC prompt.

The task runs ``OpenRGB.exe`` directly, so ``schtasks /End`` stops OpenRGB itself.
OpenRGB stays a separate, unmodified process: we only pass it command-line flags.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

log = logging.getLogger(__name__)

TASK_NAME = r"\SquidLight\OpenRGB"
_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
ERROR_CANCELLED = 1223  # the user said No to the UAC prompt


class TaskError(Exception):
    def __init__(self, message: str, cancelled: bool = False) -> None:
        super().__init__(message)
        self.cancelled = cancelled


@dataclass(frozen=True)
class TaskAction:
    command: str
    arguments: str

    def same_as(self, other: "TaskAction") -> bool:
        def norm_cmd(c: str) -> str:
            return os.path.normcase(os.path.normpath(c.strip().strip('"')))

        return norm_cmd(self.command) == norm_cmd(other.command) and (
            " ".join(self.arguments.split()).lower() == " ".join(other.arguments.split()).lower()
        )


def current_user() -> str:
    domain, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def build_action(args: list[str]) -> TaskAction:
    """``args`` is the full command line (exe first), as ``OpenRGBManager`` builds it."""
    return TaskAction(args[0], subprocess.list2cmdline(args[1:]))


def build_xml(action: TaskAction, user: Optional[str] = None) -> str:
    user = escape(user or current_user())
    workdir = escape(str(Path(action.command).parent))
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>SquidLight</Author>
    <Description>Runs the OpenRGB server bundled with SquidLight. It needs administrator rights to reach the RAM and motherboard lighting. SquidLight starts and stops it.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(action.command)}</Command>
      <Arguments>{escape(action.arguments)}</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def parse_action(xml_text: str) -> Optional[TaskAction]:
    """The Exec action of a task definition as ``schtasks /Query /XML`` prints it."""
    # schtasks prints ANSI text but keeps the "UTF-16" declaration, which ElementTree
    # refuses on a str: drop it.
    xml_text = re.sub(r"^\s*<\?xml[^>]*\?>", "", xml_text)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    exec_ = root.find("t:Actions/t:Exec", _NS)
    if exec_ is None:
        return None
    return TaskAction(
        (exec_.findtext("t:Command", "", _NS) or "").strip(),
        (exec_.findtext("t:Arguments", "", _NS) or "").strip(),
    )


def _decode(data: bytes) -> str:
    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("mbcs" if sys.platform == "win32" else "latin-1", errors="replace")


def _run(args: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess:
    kwargs: dict = {"capture_output": True, "timeout": timeout, "stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(args, **kwargs)


class SchtasksControl:
    """Run, end, inspect and (re)create the task with ``schtasks.exe``."""

    def __init__(self, name: str = TASK_NAME) -> None:
        self.name = name

    def query(self) -> Optional[TaskAction]:
        """The task's action, or None if it doesn't exist."""
        try:
            r = _run(["schtasks", "/Query", "/TN", self.name, "/XML", "ONE"])
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("could not query the OpenRGB task: %s", exc)
            return None
        if r.returncode != 0:
            return None
        return parse_action(_decode(r.stdout))

    def run(self) -> None:
        self._schtasks("/Run", "start")

    def end(self) -> None:
        self._schtasks("/End", "stop")

    def create_elevated(self, action: TaskAction) -> None:
        """Register (or replace) the task through a UAC prompt. Blocks until the user
        answers. Raises TaskError (``cancelled`` if they said No)."""
        xml_path = _xml_file(action)
        try:
            code = run_elevated("schtasks.exe", ["/Create", "/TN", self.name, "/XML", str(xml_path), "/F"])
        finally:
            _remove(xml_path)
        if code != 0:
            raise TaskError(f"setting up the OpenRGB task failed (schtasks exit code {code})")
        log.info("registered the OpenRGB task through UAC")

    def create(self, action: TaskAction) -> None:
        """Register (or replace) the task. Needs an elevated caller (the installer)."""
        xml_path = _xml_file(action)
        try:
            r = _run(["schtasks", "/Create", "/TN", self.name, "/XML", str(xml_path), "/F"])
        finally:
            _remove(xml_path)
        if r.returncode != 0:
            raise TaskError(_decode(r.stderr or r.stdout).strip() or f"schtasks exit code {r.returncode}")

    def delete(self) -> None:
        try:
            self.end()
        except TaskError:
            pass
        r = _run(["schtasks", "/Delete", "/TN", self.name, "/F"])
        if r.returncode != 0:
            raise TaskError(_decode(r.stderr or r.stdout).strip() or f"schtasks exit code {r.returncode}")

    def _schtasks(self, verb: str, what: str) -> None:
        try:
            r = _run(["schtasks", verb, "/TN", self.name])
        except (OSError, subprocess.SubprocessError) as exc:
            raise TaskError(f"could not {what} the OpenRGB task: {exc}") from exc
        if r.returncode != 0:
            msg = _decode(r.stderr or r.stdout).strip().removeprefix("ERROR:").strip()
            raise TaskError(f"could not {what} the OpenRGB task: {msg or f'exit code {r.returncode}'}")


def _xml_file(action: TaskAction) -> Path:
    from ..paths import data_dir

    path = data_dir() / "openrgb-task.xml"
    path.write_text(build_xml(action), encoding="utf-16")  # schtasks wants UTF-16 with a BOM
    return path


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def run_elevated(exe: str, args: list[str], timeout_ms: int = 120_000) -> int:
    """Run ``exe`` through a UAC prompt, hidden, and wait for it. Returns its exit code.
    Raises TaskError (``cancelled=True`` if the user declined)."""
    if sys.platform != "win32":
        raise TaskError("elevation is only supported on Windows")
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS, SEE_MASK_NOASYNC, SEE_MASK_FLAG_NO_UI = 0x40, 0x100, 0x400
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = subprocess.list2cmdline(args)
    info.nShow = 0  # SW_HIDE

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == ERROR_CANCELLED:
            raise TaskError("administrator permission was declined", cancelled=True)
        raise TaskError(f"could not ask for administrator rights (error {err})")
    if not info.hProcess:
        return 0
    try:
        kernel32.WaitForSingleObject(info.hProcess, timeout_ms)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


def server_process_running(exe: Path) -> bool:
    """Is ``exe`` (our bundled OpenRGB.exe) running? Used to tell our task's OpenRGB from
    a separate install the user runs themselves."""
    import psutil

    target = os.path.normcase(str(Path(exe).resolve()))
    name = Path(exe).name.lower()
    for p in psutil.process_iter(["name", "exe"]):
        if (p.info.get("name") or "").lower() != name:
            continue
        path = p.info.get("exe")
        if not path:  # can't read the path of this one: it is most likely ours
            return True
        if os.path.normcase(path) == target:
            return True
    return False
