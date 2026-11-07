"""Dashboard: master brightness on top, then one card per device group showing its live
output with effect, color, brightness and on/off. Clicking a card's preview opens the device."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..engine.devices import DeviceGroup
from .theme import T, health_color, health_text
from .keyboard_canvas import make_preview
from .widgets import DeviceControls, PercentSlider, StatusDot, Toggle, group_label


class DeviceCard(QFrame):
    open_requested = Signal(str)

    def __init__(self, bridge, group: DeviceGroup, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.bridge = bridge
        self.key = group.key

        col = QVBoxLayout(self)
        col.setContentsMargins(T.unit * 2, T.unit * 2, T.unit * 2, T.unit * 2)
        col.setSpacing(T.unit * 1.5)

        # title, model and on/off
        head = QHBoxLayout()
        head.setSpacing(T.unit)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.title = QLabel(group_label(group))
        self.title.setObjectName("CardTitle")
        model = QLabel(group.name)
        model.setObjectName("CardModel")
        names.addWidget(self.title)
        names.addWidget(model)
        head.addLayout(names, 1)
        self.toggle = Toggle()
        self.toggle.setAccessibleName(f"{group_label(group)} lighting on")
        self.toggle.setToolTip("Turn this device's lighting on or off")
        head.addWidget(self.toggle, 0, Qt.AlignTop)
        col.addLayout(head)

        # live preview
        self.preview = make_preview(group, clickable=True)
        self.preview.setMinimumHeight(132)
        self.preview.setToolTip(f"Open {group_label(group)}")
        self.preview.activated.connect(lambda: self.open_requested.emit(self.key))
        col.addWidget(self.preview, 1)

        # status
        status = QHBoxLayout()
        status.setSpacing(6)
        self.dot = StatusDot()
        self.status = QLabel("")
        self.status.setObjectName("Caption")
        leds = QLabel(f"{group.led_count} LEDs")
        leds.setObjectName("Caption")
        status.addWidget(self.dot)
        status.addWidget(self.status)
        status.addStretch(1)
        status.addWidget(leds)
        col.addLayout(status)

        self.controls = DeviceControls(bridge, self.key)
        col.addWidget(self.controls)

        self.toggle.toggled.connect(lambda on: bridge.set_enabled(self.key, on))
        bridge.settings_changed.connect(self._sync_toggle)
        self._sync_toggle(self.key)
        self.set_health(bridge.health(self.key))

    def _sync_toggle(self, key: str) -> None:
        if key != self.key:
            return
        self.toggle.blockSignals(True)
        self.toggle.setChecked(self.bridge.settings(self.key).enabled)
        self.toggle.blockSignals(False)

    def set_health(self, health: str) -> None:
        self.dot.set_color(health_color(health))
        self.status.setText(health_text(health))
        self.preview.set_dimmed(health == "paused")


class _EmptyState(QWidget):
    restart_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, T.unit * 10, 0, 0)
        col.setSpacing(T.unit)
        self.title = QLabel()
        self.title.setObjectName("EmptyTitle")
        self.body = QLabel()
        self.body.setObjectName("EmptyBody")
        self.body.setWordWrap(True)
        self.body.setFixedWidth(520)  # a fixed measure keeps wrapped lines from being clipped
        for w in (self.title, self.body):
            w.setAlignment(Qt.AlignHCenter)
            col.addWidget(w, 0, Qt.AlignHCenter)
        actions = QHBoxLayout()
        actions.setSpacing(T.unit)
        actions.setAlignment(Qt.AlignHCenter)
        self.restart_btn = QPushButton("Restart OpenRGB")
        self.restart_btn.setObjectName("AccentButton")
        self.restart_btn.clicked.connect(self.restart_requested)
        self.settings_btn = QPushButton("Settings and log")
        self.settings_btn.clicked.connect(self.settings_requested)
        actions.addWidget(self.restart_btn)
        actions.addWidget(self.settings_btn)
        col.addSpacing(T.unit)
        col.addLayout(actions)
        col.addStretch(1)

    def show_for(self, state: str, text: str) -> None:
        waiting = state in ("starting", "stopping")
        self.restart_btn.setVisible(not waiting)
        self.settings_btn.setVisible(not waiting)
        if state == "failed":
            self.title.setText("Can't reach OpenRGB")
            self.body.setText(
                f"{text}\n\nClose iCUE and Armoury Crate, then restart OpenRGB. "
                "The log in Settings shows what went wrong."
            )
        elif waiting:
            self.title.setText("Starting OpenRGB")
            self.body.setText(
                f"{text}\n\nYour devices appear here as soon as it connects. This can take a few seconds."
            )
        else:
            self.title.setText("No devices found")
            self.body.setText(
                "OpenRGB is running but reported nothing it can control. Close iCUE, Armoury Crate and "
                "Windows Dynamic Lighting, make sure OpenRGB runs as administrator, then restart it."
            )


class DashboardPage(QWidget):
    open_device = Signal(str)
    open_settings = Signal()

    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.cards: dict[str, DeviceCard] = {}
        self._columns = 0

        col = QVBoxLayout(self)
        col.setContentsMargins(T.unit * 3, T.unit * 3, T.unit * 3, 0)
        col.setSpacing(T.unit * 2)

        master = QFrame()
        master.setObjectName("MasterBar")
        row = QHBoxLayout(master)
        row.setContentsMargins(T.unit * 2, T.unit * 1.5, T.unit * 2, T.unit * 1.5)
        row.setSpacing(T.unit * 2)
        label = QLabel("Master brightness")
        label.setObjectName("MasterLabel")
        row.addWidget(label)
        self.master = PercentSlider("Master brightness")
        self.master.set_value(bridge.host.master_brightness)
        self.master.value_changed.connect(bridge.set_master_brightness)
        row.addWidget(self.master, 1)
        col.addWidget(master)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        self.grid = QGridLayout(holder)
        self.grid.setContentsMargins(0, 0, 0, T.unit * 3)
        self.grid.setSpacing(T.unit * 2)
        self.grid.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(holder)
        col.addWidget(self.scroll, 1)

        self.empty = _EmptyState()
        self.empty.restart_requested.connect(bridge.restart_openrgb)
        self.empty.settings_requested.connect(self.open_settings)
        col.addWidget(self.empty, 1)

        bridge.groups_changed.connect(self.set_groups)
        bridge.frames.connect(self._on_frames)
        bridge.health_changed.connect(self._on_health)
        bridge.status_changed.connect(self._on_status)
        bridge.master_changed.connect(self.master.set_value)
        self.set_groups(bridge.groups)
        self._on_status(bridge.status)

    # -- bridge events

    def set_groups(self, groups: list[DeviceGroup]) -> None:
        for card in self.cards.values():
            card.setParent(None)
            card.deleteLater()
        self.cards = {}
        for g in groups:
            card = DeviceCard(self.bridge, g)
            card.open_requested.connect(self.open_device)
            self.cards[g.key] = card
        self._columns = 0
        self._reflow()
        self._on_status(self.bridge.status)

    def _on_frames(self, frames: dict) -> None:
        for key, frame in frames.items():
            card = self.cards.get(key)
            if card is not None:
                card.preview.set_frame(frame)

    def _on_health(self, health: dict) -> None:
        for key, h in health.items():
            if key in self.cards:
                self.cards[key].set_health(h)

    def _on_status(self, status) -> None:
        has_cards = bool(self.cards)
        self.scroll.setVisible(has_cards)
        self.empty.setVisible(not has_cards)
        if not has_cards:
            self.empty.show_for(status.state, status.text)

    # -- layout

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._reflow()

    def _reflow(self) -> None:
        # measure the page, not the scroll viewport: it is stale while the empty state shows
        margins = self.layout().contentsMargins()
        width = self.width() - margins.left() - margins.right()
        columns = 1 if width < 720 else 2 if width < 1320 else 3
        if columns == self._columns:
            return
        self._columns = columns
        for card in self.cards.values():
            self.grid.removeWidget(card)
        for i, card in enumerate(self.cards.values()):
            self.grid.addWidget(card, i // columns, i % columns)
        for c in range(3):
            self.grid.setColumnStretch(c, 1 if c < columns else 0)
