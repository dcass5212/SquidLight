"""One SquidLight per user session.

A second copy (the Start menu shortcut while the autostarted one sits in the tray) would
fight the first over the devices. The named mutex decides atomically who is first; the
second copy then asks the first, over a local socket, to show its window, and exits.
The installer also checks the mutex (Inno Setup ``AppMutex``) to ask before updating.
"""

from __future__ import annotations

import getpass
import logging
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

MUTEX_NAME = "SquidLightAppMutex"  # keep in sync with packaging/squidlight.iss
ERROR_ALREADY_EXISTS = 183


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"SquidLight-{user}"


class SingleInstance(QObject):
    activate_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mutex = None
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        """True if this is the first instance (it then listens for later ones). False if
        another is running: it has been told to show itself."""
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
            if handle and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
                self._poke_first()
                return False
            self._mutex = handle  # held (never closed) for the life of the process
        name = _server_name()
        QLocalServer.removeServer(name)  # a stale one after a crash
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        if not self._server.listen(name):
            log.warning("single-instance listener failed: %s", self._server.errorString())
        return True

    def _poke_first(self) -> None:
        sock = QLocalSocket()
        sock.connectToServer(_server_name())
        if sock.waitForConnected(1000):
            sock.write(b"show\n")
            sock.flush()
            sock.waitForBytesWritten(1000)
            sock.disconnectFromServer()
        else:
            log.warning("another SquidLight is running but didn't answer")

    def _on_connection(self) -> None:
        while self._server is not None and self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: self._read(c))
            conn.disconnected.connect(conn.deleteLater)

    def _read(self, conn: QLocalSocket) -> None:
        if b"show" in bytes(conn.readAll()):
            log.info("another launch asked to show the window")
            self.activate_requested.emit()
