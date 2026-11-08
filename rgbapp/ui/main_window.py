"""Main window: a title bar (page title, status chip, save/revert, caption buttons) over sidebar | page stack.

Closing the window hides it to the tray; lighting keeps running. Quit is in the tray menu.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .dashboard import DashboardPage
from .device_page import DevicePage
from .settings_page import SettingsPage
from .profiles_page import ProfilesPage, switch_profile
from .sidebar import DASHBOARD, PROFILES, SETTINGS, Sidebar, device_page_id
from .theme import T
from .tray import make_icon
from .widgets import group_label

from .titlebar import StatusChip, TitleBar

RESIZE_BORDER = 6
WM_NCHITTEST = 0x0084
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17


class MainWindow(QMainWindow):
    hidden_to_tray = Signal()
    quit_requested = Signal()

    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.allow_close = False  # set by the app when quitting for real
        self.tray_available = False
        self.setWindowTitle("SquidLight")
        self.setWindowIcon(make_icon(True))
        self.setWindowFlag(Qt.FramelessWindowHint, True)  # we draw our own title bar
        self.setMinimumSize(1100, 700)
        self.resize(1240, 780)  # the size it restores to; the window opens maximized

        outer = QWidget()
        outer.setObjectName("Content")
        outer_col = QVBoxLayout(outer)
        outer_col.setContentsMargins(0, 0, 0, 0)
        outer_col.setSpacing(0)
        self.titlebar = TitleBar(bridge)
        # the page title, status chip and Save / Revert live in the title bar
        self.title, self.subtitle, self.chip = self.titlebar.title, self.titlebar.subtitle, self.titlebar.chip
        self.unsaved, self.save_btn, self.revert_btn = (
            self.titlebar.unsaved, self.titlebar.save_btn, self.titlebar.revert_btn
        )
        self.titlebar.minimize_clicked.connect(self.showMinimized)
        self.titlebar.maximize_clicked.connect(self.toggle_maximized)
        self.titlebar.close_clicked.connect(self.close)
        outer_col.addWidget(self.titlebar)
        root = QWidget()
        outer_col.addWidget(root, 1)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(outer)

        self.sidebar = Sidebar()
        layout.addWidget(self.sidebar)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        layout.addLayout(content, 1)

        self.stack = QStackedWidget()
        content.addWidget(self.stack, 1)
        self.pages: dict[str, QWidget] = {}
        self.dashboard = DashboardPage(bridge)
        self.dashboard.open_device.connect(lambda key: self.show_page(device_page_id(key)))
        self.dashboard.open_settings.connect(lambda: self.show_page(SETTINGS))
        self._add_page(DASHBOARD, self.dashboard)
        self._add_page(PROFILES, ProfilesPage(bridge))
        self._add_page(SETTINGS, SettingsPage(bridge))
        self._titles = {DASHBOARD: "Dashboard", PROFILES: "Profiles", SETTINGS: "Settings"}
        self._subtitles: dict[str, str] = {}

        self.sidebar.navigate.connect(self.show_page)
        self.sidebar.profile_chosen.connect(lambda pid: switch_profile(self.bridge, pid, self))
        bridge.groups_changed.connect(self._on_groups)
        bridge.health_changed.connect(self.sidebar.set_health)
        bridge.status_changed.connect(self.chip.set_status)
        self.chip.set_status(bridge.status)
        self._on_groups(bridge.groups)
        bridge.profiles_changed.connect(self._refresh_profiles)
        bridge.dirty_changed.connect(self._refresh_dirty)
        self._refresh_profiles()

        QShortcut(QKeySequence("Ctrl+W"), self, activated=self.close)

    # -- profiles

    def _refresh_profiles(self) -> None:
        self._refresh_dirty(self.bridge.dirty)

    def _refresh_dirty(self, dirty: bool) -> None:
        for w in (self.unsaved, self.save_btn, self.revert_btn):
            w.setVisible(dirty)
        mgr = self.bridge.profiles
        self.sidebar.set_profiles(mgr.profiles, mgr.active_id, dirty)

    # -- pages

    def _add_page(self, page_id: str, widget: QWidget) -> None:
        self.pages[page_id] = widget
        self.stack.addWidget(widget)

    def _on_groups(self, groups) -> None:
        current = self.current_page()
        for pid in [p for p in self.pages if p.startswith("device:")]:
            w = self.pages.pop(pid)
            self.stack.removeWidget(w)
            w.deleteLater()
            self._titles.pop(pid, None)
            self._subtitles.pop(pid, None)
        for g in groups:
            pid = device_page_id(g.key)
            self._add_page(pid, DevicePage(self.bridge, g))
            self._titles[pid] = group_label(g)
            self._subtitles[pid] = f"{g.name}, {g.led_count} LEDs"
        self.sidebar.set_groups(groups)
        self.sidebar.set_health({g.key: self.bridge.health(g.key) for g in groups})
        self.show_page(current if current in self.pages else DASHBOARD)

    def current_page(self) -> str:
        w = self.stack.currentWidget()
        return next((pid for pid, page in self.pages.items() if page is w), DASHBOARD)

    def show_page(self, page_id: str) -> None:
        page = self.pages.get(page_id)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self.title.setText(self._titles.get(page_id, ""))
        sub = self._subtitles.get(page_id, "")
        self.subtitle.setText(sub)
        self.subtitle.setVisible(bool(sub))
        self.sidebar.select(page_id)

    # -- window

    def toggle_maximized(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def show_initial(self) -> None:
        self._place_normal()  # so restoring from maximized lands somewhere sensible
        self._placed = True
        self.showMaximized()

    def bring_to_front(self) -> None:
        if not self._placed:  # started in the tray: show_initial never ran
            self._place_normal()
            self._placed = True
        if self.isMinimized() or not self.isVisible():
            self.showMaximized() if self._was_maximized else self.showNormal()
        self.raise_()
        self.activateWindow()

    _was_maximized = True
    _placed = False

    def _place_normal(self) -> None:
        """Centre the restored-size window on the screen it is on, clamped to the usable area."""
        screen = (self.screen() if self.isVisible() else None) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        w = min(max(self.width(), self.minimumWidth()), area.width())
        h = min(max(self.height(), self.minimumHeight()), area.height())
        w, h = min(w, 1240), min(h, 780)
        self.setGeometry(area.x() + (area.width() - w) // 2, area.y() + (area.height() - h) // 2, w, h)

    def _ensure_on_screen(self) -> None:
        if self.isMaximized() or self.isMinimized() or self.isFullScreen():
            return
        screen = self.screen() or QGuiApplication.primaryScreen()
        if not screen.availableGeometry().contains(self.frameGeometry().center()) or not (
            screen.availableGeometry().intersected(self.frameGeometry()).width() > self.width() * 0.8
        ):
            self._place_normal()

    def nativeEvent(self, event_type, message):
        """Frameless window: resize from the edges. Dragging is done by TitleBar (startSystemMove)."""
        if sys.platform == "win32" and bytes(event_type) == b"windows_generic_MSG" and not self.isMaximized():
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_NCHITTEST:
                rect = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(msg.hWnd, ctypes.byref(rect))  # physical pixels, like lParam
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                b = max(4, round(RESIZE_BORDER * self.devicePixelRatioF()))
                left, right = x - rect.left < b, rect.right - x <= b
                top, bottom = y - rect.top < b, rect.bottom - y <= b
                # the top-right corner belongs to the caption buttons
                buttons_w = round(3 * self.titlebar.min_btn.width() * self.devicePixelRatioF())
                if top and rect.right - x < buttons_w:
                    top = right = False
                hit = (
                    HTTOPLEFT if top and left else HTTOPRIGHT if top and right
                    else HTBOTTOMLEFT if bottom and left else HTBOTTOMRIGHT if bottom and right
                    else HTLEFT if left else HTRIGHT if right else HTTOP if top else HTBOTTOM if bottom
                    else 0
                )
                if hit:
                    return True, hit
        return super().nativeEvent(event_type, message)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.bridge.set_preview_active(True)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self.bridge.set_preview_active(False)

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == e.Type.WindowStateChange:
            self.titlebar.set_maximized(self.isMaximized())
            QTimer.singleShot(0, self._ensure_on_screen)
            if not self.isMinimized():
                self._was_maximized = self.isMaximized()
            self.bridge.set_preview_active(not self.isMinimized() and self.isVisible())

    def closeEvent(self, e: QCloseEvent) -> None:
        if self.allow_close:
            e.accept()
            return
        e.ignore()
        if self.tray_available:
            self.hide()
            self.hidden_to_tray.emit()
        else:  # nowhere to hide: closing means quitting (devices restored first)
            self.quit_requested.emit()
