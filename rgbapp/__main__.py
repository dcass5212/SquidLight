"""The app: ``python -m rgbapp``.

    python -m rgbapp                    # starts (or reuses) OpenRGB, opens the window
    python -m rgbapp --minimized        # straight to the tray
    python -m rgbapp --backend fake     # no hardware: synthetic devices
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from .logging_setup import setup_logging


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m rgbapp", description="SquidLight RGB lighting controller")
    ap.add_argument("--backend", choices=["openrgb", "fake"], default="openrgb")
    ap.add_argument("--port", type=int, default=6742, help="OpenRGB SDK server port")
    ap.add_argument("--no-manager", action="store_true", help="don't start/supervise OpenRGB; use a running server")
    ap.add_argument("--minimized", action="store_true", help="start in the tray")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .app import App
    from .engine import Engine, EngineHost, FakeBackend, OpenRGBBackend, OpenRGBManager

    if args.backend == "fake":
        engine = Engine(FakeBackend())
    else:
        manager = None if args.no_manager else OpenRGBManager(port=args.port)
        engine = Engine(OpenRGBBackend(port=args.port, settle_seconds=15.0), manager=manager)

    qapp = QApplication(sys.argv[:1])
    from .model.store import SettingsStore

    # The fake backend is for trying things out: don't let it overwrite the real setup.
    store = SettingsStore() if args.backend != "fake" else None
    app = App(qapp, EngineHost(engine, store=store), start_minimized=args.minimized)

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
