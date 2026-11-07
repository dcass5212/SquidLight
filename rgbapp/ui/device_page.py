"""One device group: the live canvas, brightness/on-off below it, and the layers panel
on the right (docs/02). The keyboard gets the per-key canvas drawn from its layout."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..engine.devices import DeviceGroup
from .keyboard_canvas import KeyboardCanvas, make_preview
from .layers_panel import LayersPanel
from .paint_panel import PaintPanel
from .theme import T
from .widgets import LedPreview
from .zone_panel import ZonePanel
from .widgets import DeviceControls


class DevicePage(QWidget):
    def __init__(self, bridge, group: DeviceGroup, parent=None) -> None:
        super().__init__(parent)
        self.key = group.key
        self.group = group
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        # the canvas column scrolls when the zones list and the canvas don't both fit
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        scroll.setWidget(holder)
        body = QVBoxLayout(holder)
        body.setContentsMargins(T.unit * 4, T.unit * 3, T.unit * 4, T.unit * 3)
        body.setSpacing(T.unit * 2)
        self.preview = make_preview(group)
        self.preview.setMinimumHeight(220)
        self.paint: PaintPanel | None = None
        self.mode_buttons: dict[str, QPushButton] = {}
        keyboard = isinstance(self.preview, KeyboardCanvas)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(T.unit * 2)
        if keyboard:
            toolbar.addWidget(self._mode_bar())
        toolbar.addStretch(1)
        self.hint = QLabel("Hover a key to see its name" if keyboard else "Click an LED to select its zone")
        self.hint.setObjectName("Caption")
        toolbar.addWidget(self.hint)
        body.addLayout(toolbar)
        body.addWidget(self.preview, 1)
        # Memory, Mainboard and the like: zones to pick, size and color (M5)
        self.zones: ZonePanel | None = None
        if isinstance(self.preview, LedPreview) and group.zone_refs:
            self.preview.selectable = True
            self.preview.empty_text = "Set an LED count for a header below to light it"
            self.zones = ZonePanel(bridge, group)
            body.addWidget(self.zones)
        else:
            self.hint.setVisible(keyboard)
        left.addWidget(scroll, 1)

        bar = QFrame()
        bar.setObjectName("BottomBar")
        bar_row = QHBoxLayout(bar)
        bar_row.setContentsMargins(T.unit * 4, T.unit * 2, T.unit * 4, T.unit * 2)
        self.controls = DeviceControls(bridge, group.key, inline=True)
        bar_row.addWidget(self.controls)
        left.addWidget(bar)
        row.addLayout(left, 1)

        self.layers = LayersPanel(bridge, group)
        self.layers.selection_changed.connect(self._show_mask)
        self.right = QStackedWidget()
        self.right.setFixedWidth(self.layers.width())
        self.right.addWidget(self.layers)
        if isinstance(self.preview, KeyboardCanvas):
            self.paint = PaintPanel(bridge, group, self.preview)
            self.right.addWidget(self.paint)
        row.addWidget(self.right)

        if self.zones is not None:
            self.zones.zone_selected.connect(self._zone_selected)
            self.zones.layer_added.connect(self.layers.select)
            self.zones.sync_changed.connect(self._sync_changed)
            self.preview.led_pressed.connect(self._led_pressed)
            self._sync_changed(bridge.settings(group.key).sync_keyboard)
        bridge.frames.connect(self._on_frames)
        bridge.health_changed.connect(lambda h: self.preview.set_dimmed(h.get(self.key) == "paused"))
        self._show_mask(self.layers.selected_spec())

    # -- Effects / Paint mode (keyboard only)

    def _mode_bar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Segmented")
        frame.setAttribute(Qt.WA_StyledBackground)
        bar = QHBoxLayout(frame)
        bar.setContentsMargins(3, 3, 3, 3)
        bar.setSpacing(2)
        buttons = QButtonGroup(self)
        for mode, text, tip in (
            ("effects", "Effects", "Stack effects as layers"),
            ("paint", "Paint", "Color individual keys"),
        ):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            buttons.addButton(b)
            bar.addWidget(b)
            self.mode_buttons[mode] = b
            b.clicked.connect(lambda _c=False, m=mode: self.set_mode(m))
        self.mode_buttons["effects"].setChecked(True)
        return frame

    @property
    def mode(self) -> str:
        return "paint" if self.right.currentWidget() is self.paint and self.paint is not None else "effects"

    def set_mode(self, mode: str) -> None:
        if self.paint is None or mode == self.mode:
            return
        self.mode_buttons[mode].setChecked(True)
        painting = mode == "paint"
        self.right.setCurrentWidget(self.paint if painting else self.layers)
        self.preview.set_paint_mode(painting)
        self.hint.setText(
            "Drag to select a box, Shift-click to add" if painting else "Hover a key to see its name"
        )
        if painting:
            self.paint.sync()
        else:
            self.layers.refresh()

    def _on_frames(self, frames: dict) -> None:
        frame = frames.get(self.key)
        if frame is not None:
            self.preview.set_frame(frame)

    # -- zones (non-keyboard devices)

    def _zone_selected(self, ref) -> None:
        self.preview.set_outline(None if ref is None else ref.indices)

    def _led_pressed(self, led: int) -> None:
        for ref in self.group.zone_refs:
            if led in set(ref.indices.tolist()):
                self.zones.select(None if self.zones.selected_ref() is ref else ref.name)
                return

    def _sync_changed(self, synced: bool) -> None:
        # a synced group renders the keyboard's effect, so its own layers would do nothing
        self.layers.setEnabled(not synced)

    def _show_mask(self, spec) -> None:
        """Outline the keys the selected layer applies to (nothing when it is all of them)."""
        if not isinstance(self.preview, KeyboardCanvas):
            if self.zones is not None and self.zones.selected_ref() is None:
                self.preview.set_outline(
                    None if spec is None or spec.mask == "all" else self.group.resolve_mask(spec.mask).nonzero()[0]
                )
            return
        if spec is None or spec.mask == "all":
            self.preview.set_outline(None)
        else:
            self.preview.set_outline(self.group.resolve_mask(spec.mask).nonzero()[0])
