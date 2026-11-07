"""Finds, starts, supervises and stops the bundled OpenRGB.exe.

OpenRGB stays a separate, unmodified process (GPL): we only launch it with command-line
flags and talk to it over the SDK socket, never import its code.

Two modes:

- **dev** (running from source): spawn the exe directly. It must be allowed to run
  elevated (start your terminal as Administrator, or start OpenRGB yourself: we then
  use it as ``external``).
- **installed** (the packaged app, or ``RGBAPP_OPENRGB_MODE=installed``): never spawn.
  Trigger the "run with highest privileges" scheduled task the installer registered
  (``rgbapp.system.openrgb_task``). If the task is missing or points at another exe or
  port, recreate it once through a UAC prompt. Stopping ends the task. A server that is
  already up counts as ours when its process is our bundled exe (the task also runs at
  logon); anything else is ``external`` and left alone.
"""

from __future__ import annotations

import enum
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Protocol

from ..paths import data_dir
from .backend import Backoff

log = logging.getLogger(__name__)

DEFAULT_PORT = 6742
HOST = "127.0.0.1"  # the SDK server must never listen on anything else
ERROR_ELEVATION_REQUIRED = 740


class ManagerState(enum.Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"  # we started it (or our task did) and it is up
    EXTERNAL = "external"  # someone else's server answered; we leave it alone
    FAILED = "failed"


class ProcessHandle(Protocol):
    def poll(self) -> Optional[int]: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: Optional[float] = None) -> int: ...


class TaskControl(Protocol):
    """The scheduled task (``rgbapp.system.openrgb_task.SchtasksControl``; tests fake it)."""

    def query(self): ...  # -> Optional[TaskAction]
    def run(self) -> None: ...
    def end(self) -> None: ...
    def create_elevated(self, action) -> None: ...


Launcher = Callable[[list[str], Optional[str]], ProcessHandle]


def popen_launcher(args: list[str], cwd: Optional[str]) -> ProcessHandle:
    """Start a process with no console and a hidden window (Windows)."""
    kwargs: dict = {"cwd": cwd, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = startup
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(args, **kwargs)  # type: ignore[return-value]


def port_is_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def bundled_exe() -> Optional[Path]:
    """Where the packaged app keeps OpenRGB (``<install dir>\\openrgb\\OpenRGB.exe``), or
    ``vendor/openrgb`` when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "openrgb" / "OpenRGB.exe"
    return Path(__file__).resolve().parents[2] / "vendor" / "openrgb" / "OpenRGB.exe"


def locate_exe(explicit: Optional[os.PathLike | str] = None) -> Optional[Path]:
    """Find OpenRGB.exe: explicit path, $RGBAPP_OPENRGB, installed layout, then vendor/."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("RGBAPP_OPENRGB"):
        candidates.append(Path(os.environ["RGBAPP_OPENRGB"]))
    if getattr(sys, "frozen", False):  # packaged: <install dir>\openrgb\OpenRGB.exe
        candidates.append(Path(sys.executable).parent / "openrgb" / "OpenRGB.exe")
    candidates.append(Path(__file__).resolve().parents[2] / "vendor" / "openrgb" / "OpenRGB.exe")
    for c in candidates:
        if c.is_file():
            return c
    return None


def default_mode() -> str:
    env = os.environ.get("RGBAPP_OPENRGB_MODE", "").lower()
    if env in ("dev", "installed"):
        return env
    return "installed" if getattr(sys, "frozen", False) else "dev"


def server_args(exe: Path, port: int, config_dir: Path) -> list[str]:
    """OpenRGB's command line (verified against 1.0.0). Shared with the scheduled task."""
    return [
        str(exe), "--server", "--server-host", HOST, "--server-port", str(port),
        "--startminimized", "--config", str(config_dir),
    ]


class OpenRGBManager:
    def __init__(
        self,
        exe_path: Optional[os.PathLike | str] = None,
        port: int = DEFAULT_PORT,
        config_dir: Optional[os.PathLike | str] = None,
        launcher: Launcher = popen_launcher,
        port_probe: Callable[[str, int], bool] = port_is_open,
        start_timeout: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.25,
        mode: Optional[str] = None,
        task: Optional[TaskControl] = None,
        is_ours: Optional[Callable[[Path], bool]] = None,
    ) -> None:
        self._exe_arg = exe_path
        self.port = port
        self._config_dir = Path(config_dir) if config_dir else None
        self._launcher = launcher
        self._probe = port_probe
        self.start_timeout = start_timeout
        self._clock = clock
        self._sleep = sleep
        self._poll_interval = poll_interval
        self.mode = mode or default_mode()
        if self.mode not in ("dev", "installed"):
            raise ValueError(f"unknown OpenRGB manager mode {self.mode!r}")
        self._task = task
        self._is_ours = is_ours

        self.state = ManagerState.STOPPED
        self.reason = ""
        self.listeners: list[Callable[[ManagerState, str], None]] = []
        self._proc: Optional[ProcessHandle] = None
        self._task_owned = False  # installed mode: our task's OpenRGB is the server
        # The user said No to the UAC prompt: don't ask again on every retry, only after
        # they ask for it (Restart OpenRGB calls configure()).
        self._uac_declined = False
        self._port_misses = 0
        self._lock = threading.RLock()
        self._backoff = Backoff()
        self._next_restart = 0.0
        self._permanent_failure = False
        self._supervisor: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # -- public

    @property
    def external(self) -> bool:
        return self.state is ManagerState.EXTERNAL

    @property
    def ready(self) -> bool:
        return self.state in (ManagerState.RUNNING, ManagerState.EXTERNAL)

    @property
    def configured_exe(self) -> str:
        """The exe path the user chose ("" means the bundled copy)."""
        return str(self._exe_arg or "")

    def configure(self, exe_path: Optional[os.PathLike | str], port: int) -> None:
        """Use another exe or port from the next start on (Settings)."""
        with self._lock:
            self._exe_arg = exe_path or None
            self.port = int(port)
            self._uac_declined = False

    def start(self) -> ManagerState:
        """Make sure an SDK server is answering. Blocks up to ``start_timeout`` (plus a
        UAC prompt, the one time the installed-mode task has to be set up)."""
        with self._lock:
            self._stop_event.clear()
            self._permanent_failure = False
            self._start_locked()
            return self.state

    def stop(self) -> None:
        """Stop supervising; stop OpenRGB only if we (or our task) started it."""
        self._stop_event.set()
        sup = self._supervisor
        if sup and sup is not threading.current_thread():
            sup.join(timeout=3.0)
        self._supervisor = None
        with self._lock:
            self._terminate_locked()
            self._set(ManagerState.STOPPED, "")

    def start_supervisor(self, interval: float = 1.0) -> None:
        if self._supervisor and self._supervisor.is_alive():
            return
        self._stop_event.clear()
        self._supervisor = threading.Thread(
            target=self._supervise_loop, args=(interval,), name="openrgb-supervisor", daemon=True
        )
        self._supervisor.start()

    def supervise_once(self) -> None:
        """One supervision tick: restart a dead server, take over from a vanished external."""
        with self._lock:
            if self.state is ManagerState.RUNNING:
                if self.mode == "dev":
                    code = self._proc.poll() if self._proc else -1
                    if code is not None:
                        log.warning("OpenRGB exited unexpectedly (code %s)", code)
                        self._proc = None
                        self._next_restart = self._clock() + self._backoff.next_delay()
                        self._set(ManagerState.STARTING, f"OpenRGB exited (code {code}); restarting")
                else:
                    # We have no process handle for the task's OpenRGB: watch its port.
                    # Two misses in a row, so one slow answer doesn't restart anything.
                    self._port_misses = 0 if self._probe(HOST, self.port) else self._port_misses + 1
                    if self._port_misses >= 2:
                        log.warning("bundled OpenRGB stopped answering; starting the task again")
                        self._port_misses = 0
                        self._next_restart = self._clock() + self._backoff.next_delay()
                        self._set(ManagerState.STARTING, "OpenRGB stopped; restarting")
            elif self.state is ManagerState.EXTERNAL:
                if not self._probe(HOST, self.port):
                    log.info("external OpenRGB went away; starting our own")
                    self._next_restart = 0.0
                    self._set(ManagerState.STARTING, "external OpenRGB went away")
            if self.state in (ManagerState.STARTING, ManagerState.FAILED) and not self._permanent_failure:
                if self._clock() >= self._next_restart:
                    self._start_locked()

    # -- internals

    def _supervise_loop(self, interval: float) -> None:
        while not self._stop_event.wait(interval):
            try:
                self.supervise_once()
            except Exception:
                log.exception("supervisor tick failed")

    def _set(self, state: ManagerState, reason: str = "") -> None:
        changed = (state, reason) != (self.state, self.reason)
        self.state, self.reason = state, reason
        if changed:
            log.info("OpenRGB manager: %s %s", state.value, reason)
            for cb in list(self.listeners):
                try:
                    cb(state, reason)
                except Exception:
                    log.exception("manager listener failed")

    def _fail(self, reason: str, permanent: bool = False) -> None:
        self._permanent_failure = permanent
        self._next_restart = self._clock() + self._backoff.next_delay()
        self._set(ManagerState.FAILED, reason)

    def _args(self, exe: Path) -> list[str]:
        config = self._config_dir or (data_dir() / "openrgb")
        config.mkdir(parents=True, exist_ok=True)
        return server_args(exe, self.port, config)

    def _start_locked(self) -> None:
        if self.mode == "installed":
            self._start_installed_locked()
            return
        if self._probe(HOST, self.port):
            self._set(ManagerState.EXTERNAL, f"using the OpenRGB server already on {HOST}:{self.port}")
            return
        exe = locate_exe(self._exe_arg)
        if exe is None:
            self._fail("OpenRGB.exe not found (expected vendor/openrgb/OpenRGB.exe)", permanent=True)
            return
        args = self._args(exe)

        self._set(ManagerState.STARTING, "launching OpenRGB")
        try:
            self._proc = self._launcher(args, str(exe.parent))
        except OSError as exc:
            if getattr(exc, "winerror", None) == ERROR_ELEVATION_REQUIRED:
                self._fail(
                    "OpenRGB needs administrator rights. Run from an elevated terminal, "
                    "or start OpenRGB yourself (as admin) and we will use it.",
                    permanent=True,
                )
            else:
                self._fail(f"could not launch OpenRGB: {exc}")
            return

        deadline = self._clock() + self.start_timeout
        while self._clock() < deadline:
            code = self._proc.poll()
            if code is not None:
                self._proc = None
                self._fail(f"OpenRGB exited during startup (code {code})")
                return
            if self._probe(HOST, self.port):
                self._backoff.reset()
                self._set(ManagerState.RUNNING, "")
                return
            self._sleep(self._poll_interval)
        self._terminate_locked()
        self._fail(f"OpenRGB did not open port {self.port} within {self.start_timeout:.0f}s")

    def _start_installed_locked(self) -> None:
        from ..system.openrgb_task import TaskError, build_action

        exe = locate_exe(self._exe_arg)
        if exe is None:
            self._fail("the bundled OpenRGB.exe is missing. Reinstall SquidLight.", permanent=True)
            return
        if self._probe(HOST, self.port):
            if self._ours(exe):
                self._task_owned = True
                self._port_misses = 0
                self._backoff.reset()
                self._set(ManagerState.RUNNING, "")
            else:
                self._set(ManagerState.EXTERNAL, f"using the OpenRGB server already on {HOST}:{self.port}")
            return

        task = self._task_control()
        want = build_action(self._args(exe))
        declined = (
            "OpenRGB needs administrator rights once to set up its background task. "
            "Use Restart OpenRGB in Settings to be asked again."
        )
        try:
            have = task.query()
            if have is None or not have.same_as(want):
                if self._uac_declined:
                    self._fail(declined, permanent=True)
                    return
                log.info("OpenRGB task %s; asking for administrator rights to set it up",
                         "missing" if have is None else "out of date")
                self._set(ManagerState.STARTING, "waiting for administrator permission to set up OpenRGB")
                task.create_elevated(want)
            self._set(ManagerState.STARTING, "starting OpenRGB")
            task.run()
        except TaskError as exc:
            if exc.cancelled:
                self._uac_declined = True
                self._fail(declined, permanent=True)
            else:
                self._fail(str(exc))
            return
        self._task_owned = True

        deadline = self._clock() + self.start_timeout
        while self._clock() < deadline:
            if self._probe(HOST, self.port):
                self._port_misses = 0
                self._backoff.reset()
                self._set(ManagerState.RUNNING, "")
                return
            self._sleep(self._poll_interval)
        self._terminate_locked()
        self._fail(f"OpenRGB did not open port {self.port} within {self.start_timeout:.0f}s")

    def _task_control(self) -> TaskControl:
        if self._task is None:
            from ..system.openrgb_task import SchtasksControl

            self._task = SchtasksControl()
        return self._task

    def _ours(self, exe: Path) -> bool:
        check = self._is_ours
        if check is None:
            from ..system.openrgb_task import server_process_running as check
        try:
            return bool(check(exe))
        except Exception:
            log.exception("could not tell whose OpenRGB is running; treating it as external")
            return False

    def _terminate_locked(self) -> None:
        if self._task_owned:
            self._task_owned = False
            self._end_task_locked()
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    log.warning("OpenRGB did not exit on terminate; killing it")
                    proc.kill()
                    proc.wait(timeout=5.0)
        except Exception:
            log.exception("failed to stop OpenRGB")

    def _end_task_locked(self) -> None:
        from ..system.openrgb_task import TaskError

        try:
            self._task_control().end()
        except TaskError as exc:
            log.warning("%s", exc)
            return
        deadline = self._clock() + 5.0
        while self._clock() < deadline and self._probe(HOST, self.port):
            self._sleep(self._poll_interval)
        log.info("stopped the bundled OpenRGB task")


def openrgb_version(exe: Optional[Path] = None) -> str:
    """The bundled OpenRGB's version from ``OPENRGB_VERSION`` (beside the exe when packaged,
    ``vendor/OPENRGB_VERSION`` in the repo), or "" if unknown."""
    exe = exe or locate_exe()
    if exe is None:
        return ""
    for candidate in (exe.parent / "OPENRGB_VERSION", exe.parent.parent / "OPENRGB_VERSION"):
        try:
            return candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""
