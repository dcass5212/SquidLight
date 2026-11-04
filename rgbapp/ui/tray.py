"""Tray icon and menu: Open, Profile, Master brightness, Pause lighting, Quit."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .theme import T, qcolor

BRIGHTNESS_STEPS = (25, 50, 75, 100)


def make_icon(lit: bool = True) -> QIcon:
    """A 3x3 LED grid; the middle row glows in the accent while lighting runs."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(T.raised))
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        cell = size / 3.6
        dot = cell * 0.72
        start = (size - 3 * cell) / 2
        for r in range(3):
            for c in range(3):
                on = lit and r == 1
                p.setBrush(qcolor(T.accent if on else "#4a4a4a"))
                x = start + c * cell + (cell - dot) / 2
                y = start + r * cell + (cell - dot) / 2
                p.drawRoundedRect(QRectF(x, y, dot, dot), dot * 0.3, dot * 0.3)
        p.end()
        icon.addPixmap(pm)
    return icon


class Tray(QObject):
    open_requested = Signal()
    quit_requested = Signal()

    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._icon_lit = make_icon(True)
        self._icon_paused = make_icon(False)
        self.icon = QSystemTrayIcon(self._icon_lit, self)
        self.icon.setToolTip("SquidLight")

        menu = QMenu()
        self.menu = menu
        self.open_action = menu.addAction("Open SquidLight")
        self.open_action.triggered.connect(self.open_requested)
        menu.addSeparator()

        self.profile_menu = menu.addMenu("Profile")
        self._profile_group = QActionGroup(self)
        self._profile_group.setExclusive(True)
        self.profile_actions: dict[str, QAction] = {}

        bright = menu.addMenu("Master brightness")
        self._bright_group = QActionGroup(self)
        self._bright_group.setExclusive(True)
        self.brightness_actions: dict[int, QAction] = {}
        for pct in BRIGHTNESS_STEPS:
            act = bright.addAction(f"{pct}%")
            act.setCheckable(True)
            act.triggered.connect(lambda _=False, v=pct: bridge.set_master_brightness(v / 100))
            self._bright_group.addAction(act)
            self.brightness_actions[pct] = act

        self.pause_action = menu.addAction("Pause lighting")
        self.pause_action.setCheckable(True)
        self.pause_action.setToolTip("Hands the devices back to their own lighting until you resume")
        self.pause_action.toggled.connect(bridge.set_paused)
        menu.addSeparator()
        self.quit_action = menu.addAction("Quit")
        self.quit_action.triggered.connect(self.quit_requested)

        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._activated)
        bridge.master_changed.connect(self._sync_brightness)
        bridge.paused_changed.connect(self._sync_paused)
        bridge.status_changed.connect(lambda s: self.icon.setToolTip(f"SquidLight\n{s.text}"))
        self._sync_brightness(bridge.host.master_brightness)
        bridge.profiles_changed.connect(self._rebuild_profiles)
        self._rebuild_profiles()

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def notify(self, title: str, text: str) -> None:
        self.icon.showMessage(title, text, self._icon_lit, 6000)

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_requested.emit()

    def _rebuild_profiles(self) -> None:
        mgr = self.bridge.profiles
        for act in self.profile_actions.values():
            self._profile_group.removeAction(act)
        self.profile_menu.clear()
        self.profile_actions = {}
        for p in mgr.profiles:
            act = self.profile_menu.addAction(p.name)
            act.setCheckable(True)
            act.setChecked(p.id == mgr.active_id)
            act.triggered.connect(lambda _=False, pid=p.id: self._choose_profile(pid))
            self._profile_group.addAction(act)
            self.profile_actions[p.id] = act

    def _choose_profile(self, profile_id: str) -> None:
        # Unsaved edits would be lost by switching: the same Save / Discard / Cancel question
        # as in the window, even when the window is hidden.
        from .profiles_page import switch_profile

        if not switch_profile(self.bridge, profile_id):
            self._rebuild_profiles()

    def _sync_brightness(self, value: float) -> None:
        pct = int(round(value * 100))
        for step, act in self.brightness_actions.items():
            act.setChecked(step == pct)
        if pct not in self.brightness_actions:  # a slider value between the steps
            checked = self._bright_group.checkedAction()
            if checked is not None:
                self._bright_group.setExclusive(False)
                checked.setChecked(False)
                self._bright_group.setExclusive(True)

    def _sync_paused(self, paused: bool) -> None:
        self.pause_action.blockSignals(True)
        self.pause_action.setChecked(paused)
        self.pause_action.blockSignals(False)
        self.icon.setIcon(self._icon_paused if paused else self._icon_lit)
