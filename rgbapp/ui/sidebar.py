"""Left navigation: Dashboard, one entry per detected device group (with a status dot), the
profile list under it (click one to switch), and Manage profiles / Settings pinned to the bottom."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .theme import T, health_color, health_text, qcolor
from .widgets import group_label, keyboard_focused, track_focus_reason

DASHBOARD = "dashboard"
PROFILES = "profiles"
SETTINGS = "settings"


def device_page_id(key: str) -> str:
    return f"device:{key}"


def profile_button_id(profile_id: str) -> str:
    return f"profile:{profile_id}"


class SidebarButton(QAbstractButton):
    def __init__(self, text: str, page_id: str, parent=None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.page_id = page_id
        self.dot_color: Optional[str] = None
        self.is_profile = False  # profile entries are actions, not pages: no selection bar
        self.active = False
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setAccessibleName(text)

    def sizeHint(self) -> QSize:
        return QSize(192, 36)

    def focusInEvent(self, e) -> None:
        track_focus_reason(self, e)
        super().focusInEvent(e)

    def set_dot(self, color: Optional[str], tooltip: str = "") -> None:
        self.dot_color = color
        self.setToolTip(tooltip)
        self.update()

    def set_active(self, active: bool, tooltip: str = "") -> None:
        self.active = active
        self.setToolTip(tooltip)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(T.unit, 1, -T.unit, -1)
        page_selected = self.isChecked() and not self.is_profile
        if page_selected or self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(T.raised if page_selected else T.hover))
            p.drawRoundedRect(r, T.radius, T.radius)
        if page_selected:
            p.setBrush(qcolor(T.accent))
            p.drawRoundedRect(QRectF(r.left(), r.top() + 10, 3, r.height() - 20), 1.5, 1.5)
        if keyboard_focused(self):
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(qcolor(T.accent), 1.5))
            p.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), T.radius, T.radius)

        lit = page_selected or self.active
        p.setPen(qcolor(T.text if lit or self.underMouse() else T.muted))
        font = self.font()
        font.setWeight(font.Weight.DemiBold if lit else font.Weight.Normal)
        p.setFont(font)
        p.drawText(r.adjusted(T.unit * 2, 0, -T.unit * 3, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text())

        if self.is_profile:  # the active profile gets a lit accent dot, the others stay empty
            if self.active:
                p.setPen(Qt.NoPen)
                p.setBrush(qcolor(T.accent))
                p.drawEllipse(QRectF(r.right() - T.unit * 2 - 3, r.center().y() - 3, 6, 6))
        elif self.dot_color:
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(self.dot_color))
            p.drawEllipse(QRectF(r.right() - T.unit * 2 - 3, r.center().y() - 3, 6, 6))


class _Mark(QWidget):
    """The wordmark's glyph: a small point of light in the accent color."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(22, 22)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QRectF(self.rect()).center()
        glow = QRadialGradient(c, 11)
        glow.setColorAt(0.0, qcolor(T.accent, 150))
        glow.setColorAt(1.0, qcolor(T.accent, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, 11, 11)
        p.setBrush(qcolor(T.accent))
        p.drawEllipse(c, 4, 4)


class Sidebar(QWidget):
    navigate = Signal(str)  # page id
    profile_chosen = Signal(str)  # profile id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFixedWidth(208)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, SidebarButton] = {}
        self._device_buttons: list[SidebarButton] = []
        self._profile_buttons: list[SidebarButton] = []

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, T.unit * 2)
        col.setSpacing(2)

        # same height as the page header, so the two hairlines meet in one line
        brand = QFrame()
        brand.setObjectName("Brand")
        brand.setFixedHeight(T.unit * 10)
        brow = QHBoxLayout(brand)
        brow.setContentsMargins(T.unit * 2, 0, T.unit * 2, 0)
        brow.setSpacing(T.unit)
        brow.addWidget(_Mark())
        wordmark = QLabel("SquidLight")
        wordmark.setObjectName("Wordmark")
        brow.addWidget(wordmark, 1)
        col.addWidget(brand)
        col.addSpacing(T.unit * 2)

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

        col.addSpacing(T.unit * 2)
        profiles_label = QLabel("Profiles")
        profiles_label.setObjectName("SidebarSection")
        col.addWidget(profiles_label)
        col.addSpacing(4)
        self._profile_col = QVBoxLayout()
        self._profile_col.setSpacing(2)
        col.addLayout(self._profile_col)

        col.addStretch(1)
        col.addWidget(self._add(SidebarButton("Manage profiles", PROFILES)))
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

    def set_profiles(self, profiles, active_id: str, dirty: bool = False) -> None:
        """One button per profile; clicking asks the window to switch to it."""
        profiles = list(profiles)
        signature = [(p.id, p.name) for p in profiles]
        # Same profiles as before (only the active one or the unsaved flag changed): update the
        # buttons in place, so hover/focus survive and no button is deleted mid-click.
        reuse = signature == getattr(self, "_profile_sig", None) and len(self._profile_buttons) == len(profiles)
        if not reuse:
            for btn in self._profile_buttons:
                self._profile_col.removeWidget(btn)
                btn.deleteLater()
            self._profile_buttons = []
            self._profile_sig = signature
        for i, prof in enumerate(profiles):
            if reuse:
                btn = self._profile_buttons[i]
            else:
                btn = SidebarButton(prof.name, profile_button_id(prof.id))
                btn.is_profile = True
                btn.setCheckable(False)
                btn.clicked.connect(lambda _=False, pid=prof.id: self.profile_chosen.emit(pid))
                self._profile_col.addWidget(btn)
                self._profile_buttons.append(btn)
            is_active = prof.id == active_id
            if is_active:
                tip = f"{prof.name} (active, unsaved changes)" if dirty else f"{prof.name} (active)"
            else:
                tip = f"Switch to {prof.name}"
            btn.set_active(is_active, tip)
            btn.setAccessibleName(f"Profile {prof.name}" + (", active" if is_active else ""))

    def profile_button(self, profile_id: str) -> Optional[SidebarButton]:
        return next((b for b in self._profile_buttons if b.page_id == profile_button_id(profile_id)), None)

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
