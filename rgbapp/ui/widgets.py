"""Small shared widgets: LED preview, toggle switch, status dot, sliders, color swatch,
and the effect/brightness controls used by both the dashboard cards and device pages."""

from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QColorDialog,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QWidget,
)

from ..engine.devices import DeviceGroup
from ..engine.effects import listed_effects, get_effect_class
from .theme import T, qcolor

_KEYBOARD_REASONS = {Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason}


def track_focus_reason(widget: QWidget, event) -> None:
    """Call from focusInEvent: remember whether focus arrived from the keyboard."""
    widget._kbd_focus = event.reason() in _KEYBOARD_REASONS  # type: ignore[attr-defined]


def keyboard_focused(widget: QWidget) -> bool:
    """Focus rings on custom widgets show for keyboard focus only, not after a click."""
    return widget.hasFocus() and getattr(widget, "_kbd_focus", False)


class _WheelGuard(QObject):
    """Wheel events over an unfocused combo or slider scroll the panel instead of
    silently changing a value."""

    def eventFilter(self, obj, e) -> bool:
        if e.type() == QEvent.Wheel and not obj.hasFocus():
            e.ignore()
            return True
        return False


_WHEEL_GUARD = None


def guard_wheel(*widgets: QWidget) -> None:
    global _WHEEL_GUARD
    if _WHEEL_GUARD is None:
        _WHEEL_GUARD = _WheelGuard()
    for w in widgets:
        w.setFocusPolicy(Qt.StrongFocus)
        w.installEventFilter(_WHEEL_GUARD)


_GROUP_LABELS = {"keyboard": "Keyboard", "memory": "Memory", "mainboard": "Mainboard", "case": "Case"}


def group_label(group: DeviceGroup) -> str:
    """What the user calls a device group: "Keyboard", not "Corsair K70 RGB PRO"."""
    base = group.key.rstrip("0123456789")
    label = _GROUP_LABELS.get(base, base.replace("_", " ").capitalize())
    suffix = group.key[len(base):]
    return f"{label} {suffix}" if suffix else label


# --------------------------------------------------------------------------- LED preview


def _grid_count(values: np.ndarray) -> int:
    """How many evenly spaced grid lines a set of normalized coordinates sits on."""
    u = np.unique(np.round(values, 4))
    if len(u) < 2:
        return 1
    gap = float(np.median(np.diff(u)))  # median: a few stray LEDs off the grid don't skew it
    return int(min(max(round(1.0 / gap) + 1, 2), 200))


class _PreviewLayout:
    """Where each LED of a group sits on a regular grid, computed once per group."""

    def __init__(self, group: DeviceGroup) -> None:
        pos = group.positions.astype(np.float64)
        # RAM sticks read as vertical bars (docs/02); their zones are laid out as rows.
        self.vertical = "dram" in group.device_types
        if self.vertical:
            pos = pos[:, ::-1]
        self.nx = _grid_count(pos[:, 0]) if len(pos) else 1
        self.ny = _grid_count(pos[:, 1]) if len(pos) else 1
        self.gap_x = 3 if self.vertical else 1  # columns between sticks are left dark
        self.cols = np.rint(pos[:, 0] * (self.nx - 1)).astype(int) * self.gap_x if len(pos) else np.zeros(0, int)
        self.rows = np.rint(pos[:, 1] * (self.ny - 1)).astype(int) if len(pos) else np.zeros(0, int)
        self.width_cells = (self.nx - 1) * self.gap_x + 1
        self.height_cells = self.ny


class LedPreview(QWidget):
    """Live picture of what a device group is showing, drawn from the engine's output."""

    activated = Signal()
    led_pressed = Signal(int)  # LED index under a click (when ``selectable``)

    MAX_PITCH = 30.0

    def __init__(self, group: Optional[DeviceGroup] = None, clickable: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.framed = False  # draw a dark inset behind the LEDs (off: they light the surface)
        self._group: Optional[DeviceGroup] = None
        self._layout: Optional[_PreviewLayout] = None
        self._frame: Optional[np.ndarray] = None
        self._dimmed = False
        self._clickable = clickable
        self._geom_key = None
        self._rects: list[QRectF] = []
        self._glow_rect = QRectF()
        self._outline: set[int] = set()  # LEDs to ring (the selected zone)
        self.empty_text = "No LEDs"
        self.selectable = False
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
            self.setFocusPolicy(Qt.StrongFocus)
        if group is not None:
            self.set_group(group)

    def set_group(self, group: DeviceGroup) -> None:
        self._group = group
        self._layout = _PreviewLayout(group)
        self._frame = None
        self._geom_key = None
        self.setAccessibleName(f"Live preview of {group.name}")
        self.update()

    def set_frame(self, frame: np.ndarray) -> None:
        self._frame = frame
        self.update()

    def set_dimmed(self, dimmed: bool) -> None:
        if dimmed != self._dimmed:
            self._dimmed = dimmed
            self.update()

    def set_outline(self, indices) -> None:
        new = set() if indices is None else {int(i) for i in indices}
        if new != self._outline:
            self._outline = new
            self.update()

    def led_at(self, pos) -> Optional[int]:
        self._geometry()
        # nearest LED within one pitch: the dots are small, clicks should be forgiving
        best, best_d = None, 1e18
        for i, r in enumerate(self._rects):
            d = (r.center().x() - pos.x()) ** 2 + (r.center().y() - pos.y()) ** 2
            if d < best_d:
                best, best_d = i, d
        if best is None:
            return None
        limit = max(self._rects[best].width(), 8.0) * 1.5
        return best if best_d <= limit * limit else None

    # -- input

    def focusInEvent(self, e) -> None:
        track_focus_reason(self, e)
        super().focusInEvent(e)

    def mousePressEvent(self, e) -> None:
        if self.selectable and e.button() == Qt.LeftButton:
            led = self.led_at(e.position())
            if led is not None:
                self.led_pressed.emit(led)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._clickable and e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.activated.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if self._clickable and e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activated.emit()
            return
        super().keyPressEvent(e)

    # -- drawing

    def _geometry(self) -> None:
        key = (self.width(), self.height())
        if key == self._geom_key or self._layout is None:
            return
        self._geom_key = key
        lay = self._layout
        pad = 14.0
        avail_w, avail_h = max(self.width() - 2 * pad, 1), max(self.height() - 2 * pad, 1)
        pitch = min(avail_w / lay.width_cells, avail_h / lay.height_cells, self.MAX_PITCH)
        ox = (self.width() - lay.width_cells * pitch) / 2
        oy = (self.height() - lay.height_cells * pitch) / 2
        size = pitch * 0.68
        inset = (pitch - size) / 2
        self._rects = [
            QRectF(ox + c * pitch + inset, oy + r * pitch + inset, size, size)
            for c, r in zip(lay.cols.tolist(), lay.rows.tolist())
        ]
        self._radius = max(1.5, size * 0.22)
        # glow image has a one-cell dark border so the light fades out past the edge
        self._glow_rect = QRectF(ox - pitch, oy - pitch, (lay.width_cells + 2) * pitch, (lay.height_cells + 2) * pitch)

    def _glow_image(self, frame: np.ndarray) -> QImage:
        lay = self._layout
        img = np.zeros((lay.height_cells + 2, lay.width_cells + 2, 3), dtype=np.uint8)
        img[lay.rows + 1, lay.cols + 1] = frame[: len(lay.rows)]
        img = np.ascontiguousarray(img)
        h, w, _ = img.shape
        qimg = QImage(img.data, w, h, 3 * w, QImage.Format_RGB888)
        return qimg.copy()  # detach from the numpy buffer

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if self.framed:
            p.setBrush(qcolor(T.well))
            p.drawRoundedRect(QRectF(self.rect()), T.radius, T.radius)

        if self._group is None or not self._group.led_count:
            p.setPen(qcolor(T.muted))
            p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, self.empty_text)
            return

        self._geometry()
        frame = self._frame
        if frame is None or len(frame) != self._group.led_count:
            frame = np.zeros((self._group.led_count, 3), dtype=np.uint8)

        p.save()
        p.setClipRect(self.rect().adjusted(1, 1, -1, -1))
        level = 0.3 if self._dimmed else 1.0
        # 1) light bleeding into the inset: the frame as a tiny image, smoothly upscaled
        #    and added on top (black adds nothing, so there is no visible image edge)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.setOpacity(0.45 * level)
        p.drawImage(self._glow_rect, self._glow_image(frame))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setOpacity(level)
        # 2) each LED as a crisp rounded dot; unlit ones keep the device's shape visible
        off = qcolor(T.led_off)
        lit = frame.max(axis=1) >= 24
        for rect, (r, g, b), on in zip(self._rects, frame.tolist(), lit.tolist()):
            p.setBrush(QColor(r, g, b) if on else off)
            p.drawRoundedRect(rect, self._radius, self._radius)
        if self._outline:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(qcolor(T.accent), 1.5))
            for i in self._outline:
                if i < len(self._rects):
                    p.drawRoundedRect(self._rects[i].adjusted(-1.5, -1.5, 1.5, 1.5), self._radius, self._radius)
        p.restore()

        if self._clickable and keyboard_focused(self):
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(qcolor(T.accent), 2))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), T.radius, T.radius)


# --------------------------------------------------------------------------- toggle


class Toggle(QAbstractButton):
    """An on/off switch. Space toggles it when focused."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(46, 28)

    def focusInEvent(self, e) -> None:
        track_focus_reason(self, e)
        super().focusInEvent(e)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.45)
        track = QRectF(3, 4, 40, 20)
        if keyboard_focused(self):
            p.setPen(QPen(qcolor(T.accent), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(track.adjusted(-2.5, -2.5, 2.5, 2.5), 12.5, 12.5)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(T.accent if self.isChecked() else T.track))
        p.drawRoundedRect(track, 10, 10)
        knob_x = track.right() - 18 if self.isChecked() else track.left() + 2
        p.setBrush(qcolor(T.on_accent if self.isChecked() else T.text))
        p.drawEllipse(QRectF(knob_x, track.top() + 2, 16, 16))


# --------------------------------------------------------------------------- status dot


class StatusDot(QWidget):
    def __init__(self, color: str = T.muted, diameter: int = 8, parent=None) -> None:
        super().__init__(parent)
        self._color = color
        self._d = diameter
        self.setFixedSize(diameter + 2, diameter + 2)

    def set_color(self, color: str) -> None:
        if color != self._color:
            self._color = color
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(self._color))
        p.drawEllipse(QPointF(self.width() / 2, self.height() / 2), self._d / 2, self._d / 2)


# --------------------------------------------------------------------------- slider


class PercentSlider(QWidget):
    """0..100% slider with its value beside it. Emits 0..1."""

    value_changed = Signal(float)

    def __init__(self, accessible_name: str, parent=None) -> None:
        super().__init__(parent)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setPageStep(10)
        self.slider.setAccessibleName(accessible_name)
        self.label = QLabel("100%")
        self.label.setObjectName("ValueLabel")
        self.label.setMinimumWidth(40)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(T.unit)
        row.addWidget(self.slider, 1)
        row.addWidget(self.label)
        self.slider.valueChanged.connect(self._changed)

    def _changed(self, v: int) -> None:
        self.label.setText(f"{v}%")
        self.value_changed.emit(v / 100.0)

    def value(self) -> float:
        return self.slider.value() / 100.0

    def set_value(self, value: float) -> None:
        v = int(round(value * 100))
        self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self.label.setText(f"{v}%")


# --------------------------------------------------------------------------- color swatch


class ColorSwatch(QPushButton):
    """Shows a color; clicking opens a picker that previews live on the hardware."""

    color_changed = Signal(str)  # "#rrggbb", emitted live while picking

    def __init__(self, name: str = "Effect color", parent=None) -> None:
        super().__init__(parent)
        self._color = "#ffffff"
        self._name = name
        self.setFixedSize(T.control_h, T.control_h)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self.setAccessibleName(name)
        self.setToolTip(f"Change {name.lower()}")
        self.clicked.connect(self._pick)

    def color(self) -> str:
        return self._color

    def set_color(self, hex_: str) -> None:
        self._color = hex_
        self.update()

    def _pick(self) -> None:
        original = self._color
        dlg = QColorDialog(QColor(original), self.window())
        dlg.setOption(QColorDialog.DontUseNativeDialog)  # native one has no live preview
        dlg.setWindowTitle(self._name)
        dlg.currentColorChanged.connect(lambda c: self._emit(c.name()))
        if dlg.exec():
            self._emit(dlg.selectedColor().name())
        else:
            self._emit(original)

    def _emit(self, hex_: str) -> None:
        if hex_ != self._color:
            self.set_color(hex_)
            self.color_changed.emit(hex_)

    def focusInEvent(self, e) -> None:
        track_focus_reason(self, e)
        super().focusInEvent(e)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        focused = keyboard_focused(self)
        hover = self.underMouse() and self.isEnabled()
        if not self.isEnabled():
            p.setOpacity(0.4)
        p.setPen(QPen(qcolor(T.accent if focused else T.track if hover else T.border), 1.5 if focused else 1))
        p.setBrush(qcolor(T.raised))
        outer = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        p.drawRoundedRect(outer, T.radius, T.radius)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))  # keeps near-black colors visible
        p.setBrush(QColor(self._color))
        p.drawRoundedRect(outer.adjusted(4.5, 4.5, -4.5, -4.5), 3, 3)


# --------------------------------------------------------------------------- device controls


def _color_param(effect_id: str):
    return next((p for p in get_effect_class(effect_id).params if p.type == "color"), None)


class DeviceControls(QWidget):
    """Effect, color and brightness for one device group (plus on/off when ``inline``).

    The effect picker edits the base (bottom) layer; a note says when more layers sit
    on top. The device page bar (``inline``) leaves effects to its layers panel.
    Stays in sync with every other control for the same group through the bridge.
    """

    def __init__(self, bridge, key: str, inline: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.key = key

        self.effect = QComboBox()
        self.effect.setAccessibleName("Effect")
        for cls in listed_effects():
            self.effect.addItem(cls.name, cls.id)
        self.effect.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.swatch = ColorSwatch()
        self.brightness = PercentSlider("Brightness")
        self.toggle: Optional[Toggle] = None
        self.layers_note = QLabel()
        self.layers_note.setObjectName("Caption")
        self.layers_note.setToolTip("Open the device to edit every layer")

        effect_label = QLabel("Effect")
        effect_label.setObjectName("FieldLabel")
        bright_label = QLabel("Brightness")
        bright_label.setObjectName("FieldLabel")

        if inline:  # device page bottom bar: one row
            for w in (self.effect, self.swatch, self.layers_note):
                w.hide()
            row = QHBoxLayout(self)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(T.unit * 1.5)
            bright_label.setText("Device brightness")
            row.addWidget(bright_label)
            row.addWidget(self.brightness, 3)
            row.addSpacing(T.unit * 3)
            self.toggle = Toggle()
            self.toggle.setAccessibleName("Lighting on")
            self.toggle.setToolTip("Turn this device's lighting on or off")
            on_label = QLabel("Lighting")
            on_label.setObjectName("FieldLabel")
            row.addWidget(on_label)
            row.addWidget(self.toggle)
        else:  # dashboard card: two rows
            grid = QGridLayout(self)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(T.unit * 2)
            grid.setVerticalSpacing(T.unit * 1.5)
            grid.addWidget(effect_label, 0, 0)
            grid.addWidget(self.effect, 0, 1)
            grid.addWidget(self.swatch, 0, 2)
            grid.addWidget(self.layers_note, 1, 1, 1, 2)
            grid.addWidget(bright_label, 2, 0)
            grid.addWidget(self.brightness, 2, 1, 1, 2)
            grid.setColumnStretch(1, 1)
        self._inline = inline

        self.effect.currentIndexChanged.connect(self._effect_picked)
        self.swatch.color_changed.connect(self._color_picked)
        self.brightness.value_changed.connect(lambda v: self.bridge.set_brightness(self.key, v))
        if self.toggle is not None:
            self.toggle.toggled.connect(lambda on: self.bridge.set_enabled(self.key, on))
        bridge.settings_changed.connect(self._on_settings_changed)
        self.refresh()

    def _effect_picked(self, _index: int) -> None:
        self.bridge.set_effect(self.key, self.effect.currentData())

    def _color_picked(self, hex_: str) -> None:
        param = _color_param(self.effect.currentData())
        if param is not None:
            self.bridge.set_param(self.key, param.id, hex_)

    def _on_settings_changed(self, key: str) -> None:
        if key == self.key:
            self.refresh()

    def refresh(self) -> None:
        s = self.bridge.settings(self.key)
        for w in (self.effect, self.toggle):
            if w is not None:
                w.blockSignals(True)
        self.effect.setCurrentIndex(max(self.effect.findData(s.effect), 0))
        if not self._inline:
            param = _color_param(s.effect) if s.layers else None
            self.swatch.setVisible(param is not None)
            if param is not None:
                self.swatch.set_color(s.current_params().get(param.id, param.default))
            extra = len(s.layers) - 1
            if not s.layers:
                note = "No layers: pick an effect to light this device"
            else:
                note = f"+ {extra} more layer{'s' if extra != 1 else ''} on top" if extra > 0 else ""
            self.layers_note.setText(note)
            self.layers_note.setVisible(bool(note))
            if not s.layers:
                self.effect.setCurrentIndex(-1)
        self.brightness.set_value(s.brightness)
        self.brightness.setEnabled(s.enabled)
        if self.toggle is not None:
            self.toggle.setChecked(s.enabled)
        for w in (self.effect, self.toggle):
            if w is not None:
                w.blockSignals(False)
