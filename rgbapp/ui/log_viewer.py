"""Log viewer for Settings: the tail of ``app.log``, filtered by level, following new lines.

It only reads the file while it is visible (a 1 s check of the file size), so it costs
nothing when the window is hidden. Rotation (the file shrinking) triggers a full reload.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase, QGuiApplication, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .theme import T, qcolor
from .widgets import guard_wheel

TAIL_BYTES = 256 * 1024  # how much of the file to show on load
MAX_LINES = 3000
REFRESH_MS = 1000

# "2026-11-05 10:12:13,456 WARNING rgbapp.engine.loop: message"
_RECORD = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} (\w+)\s")
LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
FILTERS = (("Everything", 0), ("Info and up", 20), ("Warnings and errors", 30), ("Errors only", 40))


def filter_lines(lines: list[str], min_level: int, start_visible: bool = True) -> tuple[list[str], bool]:
    """Keep records at ``min_level`` or above. A traceback's lines follow their record.
    Returns the kept lines and whether the last record was kept (for appending later)."""
    out: list[str] = []
    visible = start_visible
    for line in lines:
        m = _RECORD.match(line)
        if m:
            visible = LEVELS.get(m.group(1), 20) >= min_level
        if visible:
            out.append(line)
    return out, visible


class _Highlighter(QSyntaxHighlighter):
    def __init__(self, doc) -> None:
        super().__init__(doc)
        self._warn = QTextCharFormat()
        self._warn.setForeground(qcolor(T.warn))
        self._error = QTextCharFormat()
        self._error.setForeground(qcolor(T.error))
        self._muted = QTextCharFormat()
        self._muted.setForeground(qcolor(T.muted))

    def highlightBlock(self, text: str) -> None:
        m = _RECORD.match(text)
        if m is None:
            # traceback lines: color like the record they belong to
            state = self.previousBlockState()
            self.setCurrentBlockState(state)
            if state == 40:
                self.setFormat(0, len(text), self._error)
            return
        level = LEVELS.get(m.group(1), 20)
        self.setCurrentBlockState(level)
        self.setFormat(0, 23, self._muted)  # the timestamp
        if level >= 40:
            self.setFormat(24, len(text) - 24, self._error)
        elif level >= 30:
            self.setFormat(24, len(text) - 24, self._warn)


class LogViewer(QWidget):
    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self._offset = 0  # bytes of the file already shown
        self._partial = ""  # an unfinished last line
        self._last_visible = True
        self._min_level = 20

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(T.unit)

        bar = QHBoxLayout()
        bar.setSpacing(T.unit)
        self.level = QComboBox()
        self.level.setAccessibleName("Show log entries")
        for label, value in FILTERS:
            self.level.addItem(label, value)
        self.level.setCurrentIndex(1)
        self.level.currentIndexChanged.connect(self._level_changed)
        guard_wheel(self.level)
        bar.addWidget(self.level)
        bar.addStretch(1)
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setToolTip("Copy the lines shown to the clipboard")
        self.copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text.toPlainText()))
        bar.addWidget(self.copy_btn)
        self.open_btn = QPushButton("Open log folder")
        self.open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path.parent)))
        )
        bar.addWidget(self.open_btn)
        col.addLayout(bar)

        self.text = QPlainTextEdit()
        self.text.setObjectName("LogText")
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setMaximumBlockCount(MAX_LINES)
        self.text.setAccessibleName("Log")
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        for family in ("Cascadia Mono", "Consolas"):
            if family in QFontDatabase.families():
                font = QFont(family)
                break
        font.setPixelSize(T.caption)
        self.text.setFont(font)
        self.text.setMinimumHeight(260)
        self._highlighter = _Highlighter(self.text.document())
        col.addWidget(self.text, 1)

        self.where = QLabel(str(self.path))
        self.where.setObjectName("Caption")
        self.where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(self.where)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)

    # -- visibility drives the polling

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.reload()
        self._timer.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._timer.stop()

    # -- reading

    def _level_changed(self) -> None:
        self._min_level = int(self.level.currentData())
        self.reload()

    def reload(self) -> None:
        """Show the tail of the file again from scratch."""
        self.text.clear()
        self._offset, self._partial, self._last_visible = 0, "", True
        size = self._size()
        if size is None:
            self.text.setPlainText("No log yet.")
            return
        start = max(0, size - TAIL_BYTES)
        chunk = self._read_from(start)
        if chunk is None:
            return
        if start > 0:  # started mid-line: drop the fragment
            chunk = chunk.split("\n", 1)[1] if "\n" in chunk else ""
        self._append(chunk)
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def refresh(self) -> None:
        """Append what was written since the last read (or reload after rotation)."""
        size = self._size()
        if size is None:
            return
        if size < self._offset:  # rotated
            self.reload()
            return
        if size == self._offset:
            return
        bar = self.text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 2
        chunk = self._read_from(self._offset)
        if chunk is None:
            return
        self._append(chunk)
        if at_bottom:
            bar.setValue(bar.maximum())

    def _size(self) -> Optional[int]:
        try:
            return self.path.stat().st_size
        except OSError:
            return None

    def _read_from(self, offset: int) -> Optional[str]:
        try:
            with open(self.path, "rb") as f:
                f.seek(offset)
                data = f.read()
        except OSError as exc:
            self.text.setPlainText(f"Could not read the log: {exc}")
            return None
        self._offset = offset + len(data)
        return data.decode("utf-8", errors="replace")

    def _append(self, chunk: str) -> None:
        text = self._partial + chunk.replace("\r\n", "\n")
        lines = text.split("\n")
        self._partial = lines.pop()  # "" when the chunk ended with a newline
        kept, self._last_visible = filter_lines(lines, self._min_level, self._last_visible)
        if kept:
            self.text.appendPlainText("\n".join(kept))
