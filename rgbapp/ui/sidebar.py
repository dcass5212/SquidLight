"""Left navigation: Dashboard, one entry per detected device group (with a status dot),
and Profiles / Settings pinned to the bottom."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QLabel, QVBoxLayout, QWidget

from .theme import T, health_color, health_text, qcolor
from .widgets import group_label, keyboard_focused, track_focus_reason

DASHBOARD = "dashboard"
PROFILES = "profiles"
SETTINGS = "settings"


def device_page_id(key: str) -> str:
    return f"device:{key}"


class SidebarButton(QAbstractButton):
    def __init__(self, text: str, page_id: str, parent=None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.page_id = page_id
        self.dot_color: Optional[str] = None
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setAccessibleName(text)

    def sizeHint(self) -> QSize:
        return QSize(192, 38)

    def focusInEvent(self, e) -> None:
        track_focus_reason(self, e)
        super().focusInEvent(e)

    def set_dot(self, color: Optional[str], tooltip: str = "") -> None:
        self.dot_color = color
        self.setToolTip(tooltip)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(T.unit, 1, -T.unit, -1)
        if self.isChecked() or self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(T.raised if self.isChecked() else T.hover))
            p.drawRoundedRect(r, T.radius, T.radius)
        if self.isChecked():
            p.setBrush(qcolor(T.accent))
            p.drawRoundedRect(QRectF(r.left(), r.top() + 9, 3, r.height() - 18), 1.5, 1.5)
        if keyboard_focused(self):
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(qcolor(T.accent), 1.5))
            p.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), T.radius, T.radius)

        p.setPen(qcolor(T.text if self.isChecked() else T.muted if not self.underMouse() else T.text))
        font = self.font()
        font.setWeight(font.Weight.DemiBold if self.isChecked() else font.Weight.Normal)
        p.setFont(font)
        p.drawText(r.adjusted(T.unit * 2, 0, -T.unit * 3, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text())

        if self.dot_color:
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(self.dot_color))
            p.drawEllipse(QRectF(r.right() - T.unit * 2 - 4, r.center().y() - 4, 8, 8))


class Sidebar(QWidget):
    navigate = Signal(str)  # page id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFixedWidth(208)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, SidebarButton] = {}
        self._device_buttons: list[SidebarButton] = []

        col = QVBoxLayout(self)
        col.setContentsMargins(0, T.unit * 2, 0, T.unit * 2)
        col.setSpacing(2)

        wordmark = QLabel("SquidLight")
        wordmark.setObjectName("Wordmark")
        wordmark.setContentsMargins(T.unit, 0, 0, 0)
        col.addWidget(wordmark)
        col.addSpacing(T.unit * 3)

        col.addWidget(self._add(SidebarButton("Dashboard", DASHBOARD)))
        col.addSpacing(T.unit * 2)
        self._devices_label = QLabel("Devices")
        self._devices_label.setObjectName("SidebarSection")
        col.addWidget(self._devices_label)
        col.addSpacing(4)
        self._device_col = QVBoxLayout()
        self._device_col.setSpacing(2)
        col.addLayout(self._device_col)
        self._no_devices = QLabel("None found yet")
        self._no_devices.setObjectName("SidebarSection")
        self._device_col.addWidget(self._no_devices)

        col.addStretch(1)
        col.addWidget(self._add(SidebarButton("Profiles", PROFILES)))
        col.addWidget(self._add(SidebarButton("Settings", SETTINGS)))

        self._buttons[DASHBOARD].setChecked(True)

    def _add(self, btn: SidebarButton) -> SidebarButton:
        self._group.addButton(btn)
        self._buttons[btn.page_id] = btn
        btn.clicked.connect(lambda: self.navigate.emit(btn.page_id))
        return btn

    def set_groups(self, groups) -> None:
        for btn in self._device_buttons:
            self._group.removeButton(btn)
            self._buttons.pop(btn.page_id, None)
            btn.deleteLater()
        self._device_buttons = []
        for g in groups:
            btn = self._add(SidebarButton(group_label(g), device_page_id(g.key)))
            btn.setToolTip(g.name)
            self._device_col.addWidget(btn)
            self._device_buttons.append(btn)
        self._no_devices.setVisible(not groups)

    def set_health(self, health: dict[str, str]) -> None:
        for key, h in health.items():
            btn = self._buttons.get(device_page_id(key))
            if btn is not None:
                btn.set_dot(health_color(h), health_text(h))

    def select(self, page_id: str) -> None:
        btn = self._buttons.get(page_id)
        if btn is not None:
            btn.setChecked(True)

    def button(self, page_id: str) -> Optional[SidebarButton]:
        return self._buttons.get(page_id)
