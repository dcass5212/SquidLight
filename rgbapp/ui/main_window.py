"""Main window: sidebar | header (page title, status chip, profile) over the page stack.

Closing the window hides it to the tray; lighting keeps running. Quit is in the tray menu.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .dashboard import DashboardPage
from .device_page import DevicePage
from .settings_page import SettingsPage
from .profiles_page import ProfilesPage, switch_profile
from .sidebar import DASHBOARD, PROFILES, SETTINGS, Sidebar, device_page_id
from .theme import T, status_color
from .tray import make_icon
from .widgets import StatusDot, group_label

CHIP_MAX_TEXT_PX = 380


class StatusChip(QFrame):
    """OpenRGB connection state, always visible in the header."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusChip")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 12, 5)
        row.setSpacing(T.unit)
        self.dot = StatusDot()
        self.label = QLabel()
        row.addWidget(self.dot)
        row.addWidget(self.label)
        self.full_text = ""

    def set_status(self, status) -> None:
        self.full_text = status.text
        self.dot.set_color(status_color(status.state))
        metrics = self.label.fontMetrics()
        self.label.setText(metrics.elidedText(status.text, Qt.ElideRight, CHIP_MAX_TEXT_PX))
        self.setToolTip(status.text)
        self.setAccessibleName(f"OpenRGB status: {status.text}")


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
        self.setMinimumSize(1100, 700)
        self.resize(1240, 780)

        root = QWidget()
        root.setObjectName("Content")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(root)

        self.sidebar = Sidebar()
        layout.addWidget(self.sidebar)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        layout.addLayout(content, 1)

        header = QFrame()
        header.setObjectName("Header")
        hrow = QHBoxLayout(header)
        hrow.setContentsMargins(T.unit * 3, T.unit * 2, T.unit * 3, T.unit * 2)
        hrow.setSpacing(T.unit * 1.5)
        self.title = QLabel("Dashboard")
        self.title.setObjectName("PageTitle")
        hrow.addWidget(self.title)
        hrow.addStretch(1)
        self.chip = StatusChip()
        hrow.addWidget(self.chip)
        self.unsaved = QLabel("● Unsaved changes")
        self.unsaved.setObjectName("Caption")
        self.unsaved.setStyleSheet(f"color: {T.warn};")
        self.unsaved.setAccessibleName("Unsaved changes to this profile")
        self.save_btn = QPushButton("Save")
        self.save_btn.setToolTip("Keep these changes in the active profile")
        self.save_btn.clicked.connect(bridge.save_profile)
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.setToolTip("Throw away changes and go back to the saved profile")
        self.revert_btn.clicked.connect(bridge.revert_profile)
        for w in (self.unsaved, self.save_btn, self.revert_btn):
            hrow.addWidget(w)
        self.profile = QComboBox()
        self.profile.setAccessibleName("Profile")
        self.profile.setMinimumWidth(160)
        self.profile.setToolTip("Active profile")
        self.profile.activated.connect(self._profile_picked)
        hrow.addWidget(self.profile)
        content.addWidget(header)

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

        self.sidebar.navigate.connect(self.show_page)
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
        mgr = self.bridge.profiles
        self.profile.blockSignals(True)
        self.profile.clear()
        for p in mgr.profiles:
            self.profile.addItem(p.name, p.id)
        self.profile.setCurrentIndex(max(self.profile.findData(mgr.active_id), 0))
        self.profile.blockSignals(False)
        self._refresh_dirty(self.bridge.dirty)

    def _refresh_dirty(self, dirty: bool) -> None:
        for w in (self.unsaved, self.save_btn, self.revert_btn):
            w.setVisible(dirty)

    def _profile_picked(self, index: int) -> None:
        if not switch_profile(self.bridge, self.profile.itemData(index), self):
            self._refresh_profiles()  # cancelled: show the active one again

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
        for g in groups:
            pid = device_page_id(g.key)
            self._add_page(pid, DevicePage(self.bridge, g))
            self._titles[pid] = group_label(g)
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
        self.sidebar.select(page_id)

    # -- window

    def bring_to_front(self) -> None:
        self.showNormal() if self.isMinimized() else self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.bridge.set_preview_active(True)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self.bridge.set_preview_active(False)

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == e.Type.WindowStateChange:
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
