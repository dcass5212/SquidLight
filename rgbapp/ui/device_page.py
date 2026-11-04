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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..engine.devices import DeviceGroup
from .keyboard_canvas import KeyboardCanvas, make_preview
from .layers_panel import LayersPanel
from .paint_panel import PaintPanel
from .theme import T
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
        body = QVBoxLayout()
        body.setContentsMargins(T.unit * 3, T.unit * 3, T.unit * 3, T.unit * 3)
        body.setSpacing(T.unit * 1.5)
        self.preview = make_preview(group)
        self.preview.setMinimumHeight(260)
        self.paint: PaintPanel | None = None
        self.mode_buttons: dict[str, QPushButton] = {}
        if isinstance(self.preview, KeyboardCanvas):
            body.addLayout(self._mode_bar())
        body.addWidget(self.preview, 1)
        hint = " Hover a key to see its name." if isinstance(self.preview, KeyboardCanvas) else ""
        note = QLabel(f"{group.name}, {group.led_count} LEDs.{hint}")
        note.setObjectName("Caption")
        body.addWidget(note)
        left.addLayout(body, 1)

        bar = QFrame()
        bar.setObjectName("BottomBar")
        bar_row = QHBoxLayout(bar)
        bar_row.setContentsMargins(T.unit * 3, T.unit * 2, T.unit * 3, T.unit * 2)
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

        bridge.frames.connect(self._on_frames)
        bridge.health_changed.connect(lambda h: self.preview.set_dimmed(h.get(self.key) == "paused"))
        self._show_mask(self.layers.selected_spec())

    # -- Effects / Paint mode (keyboard only)

    def _mode_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(0)
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
        bar.addStretch(1)
        return bar

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
        if painting:
            self.paint.sync()
        else:
            self.layers.refresh()

    def _on_frames(self, frames: dict) -> None:
        frame = frames.get(self.key)
        if frame is not None:
            self.preview.set_frame(frame)

    def _show_mask(self, spec) -> None:
        """Outline the keys the selected layer applies to (nothing when it is all of them)."""
        if not isinstance(self.preview, KeyboardCanvas):
            return
        if spec is None or spec.mask == "all":
            self.preview.set_outline(None)
        else:
            self.preview.set_outline(self.group.resolve_mask(spec.mask).nonzero()[0])
