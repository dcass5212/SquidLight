"""The app: ``python -m rgbapp`` (or ``SquidLight.exe`` when packaged).

    python -m rgbapp                    # starts (or reuses) OpenRGB, opens the window
    python -m rgbapp --minimized        # straight to the tray
    python -m rgbapp --backend fake     # no hardware: synthetic devices

Used by the installer and uninstaller (run elevated, no window):

    SquidLight.exe --register-openrgb-task   # the "run OpenRGB with highest privileges" task
    SquidLight.exe --uninstall-cleanup       # stop OpenRGB, remove the task and the autostart entry
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from .logging_setup import setup_logging

log = logging.getLogger("rgbapp")

APP_USER_MODEL_ID = "SquidLight.SquidLight"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m rgbapp", description="SquidLight RGB lighting controller")
    ap.add_argument("--backend", choices=["openrgb", "fake"], default="openrgb")
    ap.add_argument("--port", type=int, default=None, help="OpenRGB SDK server port (default: from Settings)")
    ap.add_argument("--no-manager", action="store_true", help="don't start/supervise OpenRGB; use a running server")
    ap.add_argument("--minimized", action="store_true", help="start in the tray")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--register-openrgb-task", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--uninstall-cleanup", action="store_true", help=argparse.SUPPRESS)
    return ap


def register_openrgb_task() -> int:
    """Installer step (elevated): register the task that runs the bundled OpenRGB."""
    from .engine.openrgb_manager import locate_exe, server_args
    from .model.app_settings import AppSettingsStore
    from .paths import data_dir
    from .system.openrgb_task import SchtasksControl, TaskError, build_action

    settings = AppSettingsStore().load()
    exe = locate_exe(settings.openrgb_path or None)
    if exe is None:
        log.error("register task: OpenRGB.exe not found")
        return 2
    config = data_dir() / "openrgb"
    config.mkdir(parents=True, exist_ok=True)
    try:
        SchtasksControl().create(build_action(server_args(exe, settings.openrgb_port, config)))
    except TaskError as exc:
        log.error("register task failed: %s", exc)
        return 1
    log.info("registered the OpenRGB task for %s", exe)
    return 0


def uninstall_cleanup() -> int:
    """Uninstaller step: stop our OpenRGB, delete its task, drop the autostart entry.
    Always succeeds (the uninstaller must not get stuck on a missing piece)."""
    from .system import autostart
    from .system.openrgb_task import SchtasksControl, TaskError

    try:
        SchtasksControl().delete()
        log.info("removed the OpenRGB task")
    except TaskError as exc:
        log.info("no OpenRGB task to remove (%s)", exc)
    try:
        autostart.disable()
    except OSError as exc:
        log.warning("could not remove the autostart entry: %s", exc)
    return 0


def _set_app_user_model_id() -> None:
    """Group our windows and tray balloons under SquidLight, not python.exe."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # A packaged (windowed) exe has no console: sys.stderr is None.
    setup_logging(logging.DEBUG if args.verbose else logging.INFO, console=sys.stderr is not None)

    if args.register_openrgb_task:
        return register_openrgb_task()
    if args.uninstall_cleanup:
        return uninstall_cleanup()

    _set_app_user_model_id()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .app import App
    from .engine import Engine, EngineHost, FakeBackend, OpenRGBBackend, OpenRGBManager
    from .model.app_settings import AppSettingsStore, MemorySettingsStore
    from .model.store import SettingsStore
    from .ui.single_instance import SingleInstance

    qapp = QApplication(sys.argv[:1])

    instance = None
    if args.backend != "fake":  # the fake backend touches no hardware: any number may run
        instance = SingleInstance()
        if not instance.acquire():
            log.info("SquidLight is already running; asked it to show its window")
            return 0

    # The fake backend is for trying things out: don't let it overwrite the real setup.
    app_store = AppSettingsStore() if args.backend != "fake" else MemorySettingsStore()
    settings = app_store.load()
    port = args.port or settings.openrgb_port

    if args.backend == "fake":
        engine = Engine(FakeBackend())
    else:
        manager = None if args.no_manager else OpenRGBManager(exe_path=settings.openrgb_path or None, port=port)
        engine = Engine(OpenRGBBackend(port=port, settle_seconds=15.0), manager=manager)
    engine.set_write_cap(settings.write_cap_hz)
    engine.set_input_options(settings.audio_device, settings.lhm_url)
    log.info("starting SquidLight (%s backend)", args.backend)

    store = SettingsStore() if args.backend != "fake" else None
    app = App(qapp, EngineHost(engine, store=store), start_minimized=args.minimized, app_store=app_store)
    if instance is not None:
        instance.activate_requested.connect(app.window.bring_to_front)

    # Ctrl+C in the terminal quits cleanly: Python only sees the signal when Qt hands
    # control back, so wake the interpreter a few times a second.
    signal.signal(signal.SIGINT, lambda *_: QTimer.singleShot(0, app.quit))
    wake = QTimer()
    wake.timeout.connect(lambda: None)
    wake.start(200)

    app.start()
    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())
