"""Custom frameless title bar: app icon + name on the left, minimize / maximize / close on the right.

Dragging uses startSystemMove and double-click toggles maximize; edge resizing is done
through WM_NCHITTEST in MainWindow.nativeEvent.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton

from .theme import T, status_color
from .tray import make_icon
from .widgets import StatusDot

CHIP_MAX_TEXT_PX = 380
BAR_HEIGHT = 44
BUTTON_WIDTH = 46
CLOSE_RED = QColor("#e5484d")


class CaptionButton(QAbstractButton):
    """A flat caption button with a hand-drawn glyph: 'min', 'max', 'restore' or 'close'."""

    def __init__(self, kind: str, tooltip: str, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind
        self._tab_focused = False
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setFixedSize(BUTTON_WIDTH, BAR_HEIGHT)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.ArrowCursor)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # ask the cursor directly: enter/leave events go missing when the window moves or resizes under it
        hot = self.rect().contains(self.mapFromGlobal(QCursor.pos())) and self.window().isActiveWindow()
        close = self.kind == "close"
        if hot:
            p.fillRect(self.rect(), CLOSE_RED if close else QColor(T.hover))
        if self._tab_focused and self.hasFocus():
            p.setPen(QPen(QColor(T.accent), 1.5))
            p.drawRect(self.rect().adjusted(2, 2, -3, -3))
        if self.isDown():
            p.fillRect(self.rect(), CLOSE_RED.darker(125) if close else QColor(T.border))
        ink = QColor("#ffffff") if (close and hot) else QColor(T.text if hot else T.muted)
        p.setPen(QPen(ink, 1.2))
        c = QPointF(self.width() / 2, self.height() / 2)
        s = 5.0
        if self.kind == "min":
            p.drawLine(QPointF(c.x() - s, c.y() + 0.5), QPointF(c.x() + s, c.y() + 0.5))
        elif self.kind == "max":
            p.drawRect(QRectF(c.x() - s, c.y() - s, s * 2, s * 2))
        elif self.kind == "restore":
            p.drawRect(QRectF(c.x() - s, c.y() - s + 2.5, s * 2 - 2.5, s * 2 - 2.5))
            p.drawLine(QPointF(c.x() - s + 2.5, c.y() - s + 2.5), QPointF(c.x() - s + 2.5, c.y() - s))
            p.drawLine(QPointF(c.x() - s + 2.5, c.y() - s), QPointF(c.x() + s, c.y() - s))
            p.drawLine(QPointF(c.x() + s, c.y() - s), QPointF(c.x() + s, c.y() + s - 2.5))
            p.drawLine(QPointF(c.x() + s, c.y() + s - 2.5), QPointF(c.x() + s - 2.5, c.y() + s - 2.5))
        else:  # close
            p.drawLine(QPointF(c.x() - s, c.y() - s), QPointF(c.x() + s, c.y() + s))
            p.drawLine(QPointF(c.x() - s, c.y() + s), QPointF(c.x() + s, c.y() - s))

    def focusInEvent(self, e) -> None:
        self._tab_focused = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason)
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e) -> None:
        self._tab_focused = False
        super().focusOutEvent(e)
        self.update()

    def enterEvent(self, e) -> None:
        super().enterEvent(e)
        self.update()

    def leaveEvent(self, e) -> None:
        super().leaveEvent(e)
        self.update()

    def mouseMoveEvent(self, e) -> None:
        super().mouseMoveEvent(e)
        self.update()

    def event(self, e) -> bool:
        if e.type() in (QEvent.HoverMove, QEvent.HoverEnter, QEvent.HoverLeave):
            self.update()
        return super().event(e)


class StatusChip(QFrame):
    """OpenRGB connection state, always visible in the title bar. Quiet while connected;
    it gets a raised chip when something needs attention."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusChip")
        self.setProperty("problem", False)
        self.setAttribute(Qt.WA_StyledBackground)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 3, 12, 3)
        row.setSpacing(T.unit)
        self.dot = StatusDot()
        self.label = QLabel()
        row.addWidget(self.dot)
        row.addWidget(self.label)
        self.full_text = ""

    def set_status(self, status) -> None:
        self.full_text = status.text
        problem = status.state != "connected"
        if self.property("problem") != problem:
            self.setProperty("problem", problem)
            for w in (self, self.label):
                w.style().unpolish(w)
                w.style().polish(w)
        self.dot.set_color(status_color(status.state))
        metrics = self.label.fontMetrics()
        self.label.setText(metrics.elidedText(status.text, Qt.ElideRight, CHIP_MAX_TEXT_PX))
        self.setToolTip(status.text)
        self.setAccessibleName(f"OpenRGB status: {status.text}")


class TitleBar(QFrame):
    """One bar for the window: icon, current page title and subtitle, then the
    profile Save / Revert, the OpenRGB status chip and the caption buttons."""

    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFixedHeight(BAR_HEIGHT)
        row = QHBoxLayout(self)
        row.setContentsMargins(T.unit * 3, 0, 0, 0)
        row.setSpacing(T.unit)

        icon = QLabel()
        icon.setPixmap(make_icon(True).pixmap(18, 18))
        icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.icon = icon
        row.addWidget(icon)
        row.addSpacing(T.unit)

        self.title = QLabel("Dashboard")
        self.title.setObjectName("PageTitle")
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("PageSubtitle")
        self.subtitle.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.subtitle.hide()
        row.addWidget(self.title)
        row.addWidget(self.subtitle)
        row.addStretch(1)

        self.unsaved = QLabel("Unsaved changes")
        self.unsaved.setObjectName("Unsaved")
        self.unsaved.setAccessibleName("Unsaved changes to this profile")
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.setObjectName("GhostButton")
        self.revert_btn.setToolTip("Throw away changes and go back to the saved profile")
        self.revert_btn.clicked.connect(bridge.revert_profile)
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("AccentButton")
        self.save_btn.setToolTip("Keep these changes in the active profile")
        self.save_btn.clicked.connect(bridge.save_profile)
        for w in (self.unsaved, self.revert_btn, self.save_btn):
            row.addWidget(w)
        row.addSpacing(T.unit)

        self.chip = StatusChip()
        row.addWidget(self.chip)
        row.addSpacing(T.unit)

        self.min_btn = CaptionButton("min", "Minimize")
        self.max_btn = CaptionButton("max", "Maximize")
        self.close_btn = CaptionButton("close", "Close")
        self.min_btn.clicked.connect(self.minimize_clicked)
        self.max_btn.clicked.connect(self.maximize_clicked)
        self.close_btn.clicked.connect(self.close_clicked)
        for b in (self.min_btn, self.max_btn, self.close_btn):
            row.addWidget(b)

    def mousePressEvent(self, e) -> None:
        # Don't start the move here: Windows' move loop would swallow the second click of a
        # double-click. Wait for the pointer to actually move (mouseMoveEvent).
        self._pressed = e.button() == Qt.LeftButton
        if self._pressed:
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if getattr(self, "_pressed", False) and e.buttons() & Qt.LeftButton:
            self._pressed = False
            handle = self.window().windowHandle()
            if handle:
                handle.startSystemMove()  # native drag, with edge snapping
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._pressed = False
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._pressed = False
            self.maximize_clicked.emit()
            e.accept()

    def set_maximized(self, maximized: bool) -> None:
        self.max_btn.kind = "restore" if maximized else "max"
        tip = "Restore" if maximized else "Maximize"
        self.max_btn.setToolTip(tip)
        self.max_btn.setAccessibleName(tip)
        for b in (self.min_btn, self.max_btn, self.close_btn):
            b.update()

    def refresh_icon(self) -> None:
        self.icon.setPixmap(make_icon(True).pixmap(18, 18))
