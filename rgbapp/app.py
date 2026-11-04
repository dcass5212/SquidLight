"""Builds the GUI around an EngineHost and owns the quit sequence.

Quit hides everything at once, then stops the engine on a worker thread (stopping
restores every device's own mode and, if we started it, stops OpenRGB) and exits the
Qt loop when that finishes. The GUI thread never does device I/O.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication

from .engine.host import EngineHost
from .ui.engine_bridge import EngineBridge
from .ui.main_window import MainWindow
from .ui.theme import app_font, build_qss
from .ui.tray import Tray

log = logging.getLogger(__name__)

QUIT_TIMEOUT_MS = 25_000  # give up waiting for a stuck OpenRGB after this


class App(QObject):
    stopped = Signal()

    def __init__(self, qapp: QApplication, host: EngineHost, start_minimized: bool = False) -> None:
        super().__init__()
        self.qapp = qapp
        self.host = host
        self.start_minimized = start_minimized
        self._quitting = False
        self._hint_shown = False

        qapp.setApplicationName("SquidLight")
        qapp.setQuitOnLastWindowClosed(False)
        qapp.setFont(app_font())
        qapp.setStyleSheet(build_qss())

        self.bridge = EngineBridge(host, self)
        self.window = MainWindow(self.bridge)
        self.tray = Tray(self.bridge, self)
        self.tray_available = Tray.available()
        self.window.tray_available = self.tray_available

        self.window.hidden_to_tray.connect(self._hint_once)
        self.window.quit_requested.connect(self.quit)
        self.tray.open_requested.connect(self.window.bring_to_front)
        self.tray.quit_requested.connect(self.quit)
        # emitted from the engine-stop thread; queued so quit runs on the GUI thread
        self.stopped.connect(qapp.quit, Qt.QueuedConnection)
        qapp.aboutToQuit.connect(self._about_to_quit)

    def start(self) -> None:
        self.host.start()
        self.bridge.start()
        if self.tray_available:
            self.tray.show()
        if not self.start_minimized or not self.tray_available:
            self.window.show()

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        log.info("quitting: restoring devices")
        self.window.allow_close = True
        self.window.hide()
        self.tray.hide()
        self.host.stop_async(done=self.stopped.emit)
        QTimer.singleShot(QUIT_TIMEOUT_MS, self.qapp.quit)

    def _about_to_quit(self) -> None:
        if not self._quitting:  # e.g. Windows is logging off: stop off-thread, wait briefly
            self._quitting = True
            self.host.stop_async().join(10.0)

    def _hint_once(self) -> None:
        if self._hint_shown:
            return
        self._hint_shown = True
        self.tray.notify(
            "SquidLight is still running",
            "Your lighting stays on. Open or quit SquidLight from this tray icon.",
        )
