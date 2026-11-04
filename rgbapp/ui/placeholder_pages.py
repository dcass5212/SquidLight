"""Settings (M8) until that milestone; it already shows the connection details and where
the log lives."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..paths import log_dir
from .theme import T


def _section(title: str, body: str) -> QFrame:
    box = QFrame()
    box.setObjectName("Card")
    col = QVBoxLayout(box)
    col.setContentsMargins(T.unit * 2, T.unit * 2, T.unit * 2, T.unit * 2)
    col.setSpacing(4)
    t = QLabel(title)
    t.setObjectName("CardTitle")
    b = QLabel(body)
    b.setObjectName("Muted")
    b.setWordWrap(True)
    col.addWidget(t)
    col.addWidget(b)
    box.body = b  # type: ignore[attr-defined]
    return box


class SettingsPage(QWidget):
    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(T.unit * 3, T.unit * 3, T.unit * 3, T.unit * 3)
        col.setSpacing(T.unit * 2)

        self.connection = _section("OpenRGB", "")
        self._connection_body = self.connection.body
        col.addWidget(self.connection)

        logs = _section("Log", str(log_dir() / "app.log"))
        logs.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row = QHBoxLayout()
        open_btn = QPushButton("Open log folder")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir()))))
        row.addWidget(open_btn)
        row.addStretch(1)
        logs.layout().addSpacing(T.unit)
        logs.layout().addLayout(row)
        col.addWidget(logs)
        col.addStretch(1)

        bridge.status_changed.connect(self._on_status)
        self._on_status(bridge.status)

    def _on_status(self, status) -> None:
        text = status.text
        if status.external:
            text += (
                "\nThis is an OpenRGB server you started yourself, so the app uses it and leaves it "
                "running when you quit."
            )
        self._connection_body.setText(text)
