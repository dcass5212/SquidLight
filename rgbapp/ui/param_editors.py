"""Property editors generated from an effect's ``Param`` list.

Every editor has ``value_changed(object)`` (emitted live while the user drags or picks,
so the hardware previews immediately) and ``set_value(value)`` (silent).
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from ..engine.effects import Param
from ..engine.effects.base import sample_gradient
from .theme import T, qcolor
from .widgets import ColorSwatch, Toggle

SLIDER_STEPS = 1000
MAX_STOPS = 10


class _Editor(QWidget):
    value_changed = Signal(object)

    def set_value(self, value: Any) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class NumberEditor(_Editor):
    """A slider over the param's range with the value beside it (float or int)."""

    def __init__(self, param: Param, parent=None) -> None:
        super().__init__(parent)
        self.param = param
        self.is_int = param.type == "int"
        self.lo = float(param.min if param.min is not None else 0.0)
        self.hi = float(param.max if param.max is not None else (100.0 if self.is_int else 1.0))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setAccessibleName(param.label)
        if self.is_int:
            self.slider.setRange(int(self.lo), int(self.hi))
        else:
            self.slider.setRange(0, SLIDER_STEPS)
            self.slider.setPageStep(SLIDER_STEPS // 10)
        self.label = QLabel()
        self.label.setObjectName("ValueLabel")
        self.label.setMinimumWidth(44)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(T.unit)
        row.addWidget(self.slider, 1)
        row.addWidget(self.label)
        self.slider.valueChanged.connect(self._changed)
        self.set_value(param.default)

    def _to_value(self, pos: int):
        if self.is_int:
            return int(pos)
        return self.lo + (self.hi - self.lo) * pos / SLIDER_STEPS

    def _format(self, v) -> str:
        if self.is_int:
            return str(int(v))
        span = self.hi - self.lo
        digits = 0 if span >= 100 else 1 if span >= 10 else 2
        text = f"{v:.{digits}f}"
        return text + "°" if "degree" in self.param.label.lower() else text

    def _changed(self, pos: int) -> None:
        v = self._to_value(pos)
        self.label.setText(self._format(v))
        self.value_changed.emit(round(v, 4) if not self.is_int else v)

    def set_value(self, value) -> None:
        v = float(np.clip(float(value), self.lo, self.hi))
        pos = int(round(v)) if self.is_int else int(round((v - self.lo) / ((self.hi - self.lo) or 1) * SLIDER_STEPS))
        self.slider.blockSignals(True)
        self.slider.setValue(pos)
        self.slider.blockSignals(False)
        self.label.setText(self._format(int(round(v)) if self.is_int else v))


class BoolEditor(_Editor):
    def __init__(self, param: Param, parent=None) -> None:
        super().__init__(parent)
        self.toggle = Toggle()
        self.toggle.setAccessibleName(param.label)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.toggle)
        row.addStretch(1)
        self.toggle.toggled.connect(self.value_changed.emit)
        self.set_value(param.default)

    def set_value(self, value) -> None:
        self.toggle.blockSignals(True)
        self.toggle.setChecked(bool(value))
        self.toggle.blockSignals(False)


class EnumEditor(_Editor):
    def __init__(self, param: Param, parent=None) -> None:
        super().__init__(parent)
        self.combo = QComboBox()
        self.combo.setAccessibleName(param.label)
        for choice in param.choices or ():
            self.combo.addItem(str(choice).replace("_", " ").capitalize(), choice)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.combo, 1)
        self.combo.currentIndexChanged.connect(lambda _i: self.value_changed.emit(self.combo.currentData()))
        self.set_value(param.default)

    def set_value(self, value) -> None:
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(max(self.combo.findData(value), 0))
        self.combo.blockSignals(False)


class ColorEditor(_Editor):
    def __init__(self, param: Param, parent=None) -> None:
        super().__init__(parent)
        self.swatch = ColorSwatch(param.label)
        self.hex = QLabel()
        self.hex.setObjectName("ValueLabel")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(T.unit * 1.5)
        row.addWidget(self.swatch)
        row.addWidget(self.hex)
        row.addStretch(1)
        self.swatch.color_changed.connect(self._changed)
        self.set_value(param.default)

    def _changed(self, hex_: str) -> None:
        self.hex.setText(hex_.upper())
        self.value_changed.emit(hex_)

    def set_value(self, value) -> None:
        self.swatch.set_color(str(value))
        self.hex.setText(str(value).upper())


class GradientBar(QWidget):
    """The gradient with one draggable handle per stop underneath it.

    Click a handle to select it, drag it sideways to change the point on the gradient it stands
    for, double-click it to edit its color, double-click empty space to add a stop there.
    The owner keeps ``stops`` sorted and ``selected`` current; the bar only reports gestures.
    """

    selected_changed = Signal(int)
    position_dragged = Signal(float)  # new position of the selected stop, 0..1
    edit_requested = Signal(int)
    add_requested = Signal(float)
    delete_requested = Signal()

    MARGIN = 9
    STRIP_H = 22
    HANDLE_H = 14

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.stops: list[tuple[float, str]] = []
        self.selected = 0
        self._dragging = False
        self.setFixedHeight(self.STRIP_H + self.HANDLE_H + 4)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Gradient stops")
        self.setToolTip("Drag a handle to move it along the gradient. Double-click to edit its color.")

    def _x(self, pos: float) -> float:
        return self.MARGIN + pos * (self.width() - 2 * self.MARGIN)

    def _pos_at(self, x: float) -> float:
        span = max(self.width() - 2 * self.MARGIN, 1)
        return min(max((x - self.MARGIN) / span, 0.0), 1.0)

    def _hit(self, x: float) -> int | None:
        best, best_d = None, 10.0
        for i, (pos, _c) in enumerate(self.stops):
            d = abs(self._x(pos) - x) - (0.5 if i == self.selected else 0.0)
            if d <= best_d:
                best, best_d = i, d
        return best

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        strip = QRectF(self.MARGIN, 0.5, self.width() - 2 * self.MARGIN, self.STRIP_H - 1)
        grad = QLinearGradient(strip.left(), 0, strip.right(), 0)
        for pos, color in self.stops:
            grad.setColorAt(min(max(pos, 0.0), 1.0), qcolor(color))
        p.setPen(QPen(qcolor(T.border), 1))
        p.setBrush(grad)
        p.drawRoundedRect(strip, 4, 4)
        top = self.STRIP_H + 1
        for i, (pos, color) in enumerate(self.stops):
            x = self._x(pos)
            sel = i == self.selected
            half = 6.5 if sel else 5.5
            tri = QPolygonF([QPointF(x, top), QPointF(x - half, top + self.HANDLE_H), QPointF(x + half, top + self.HANDLE_H)])
            p.setPen(QPen(qcolor(T.accent if sel else T.border), 2 if sel else 1))
            p.setBrush(qcolor(color))
            p.drawPolygon(tri)
            if sel:
                p.setPen(QPen(qcolor(T.accent), 2))
                p.drawLine(QPointF(x, 1), QPointF(x, top))
        if self.hasFocus():
            p.setPen(QPen(qcolor(T.accent), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            return
        self.setFocus()
        i = self._hit(e.position().x())
        if i is not None:
            if i != self.selected:
                self.selected_changed.emit(i)
            self._dragging = True

    def mouseMoveEvent(self, e) -> None:
        if self._dragging:
            self.position_dragged.emit(self._pos_at(e.position().x()))

    def mouseReleaseEvent(self, _e) -> None:
        self._dragging = False

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            return
        self._dragging = False
        i = self._hit(e.position().x())
        if i is None:
            self.add_requested.emit(self._pos_at(e.position().x()))
        else:
            if i != self.selected:
                self.selected_changed.emit(i)
            self.edit_requested.emit(i)

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_requested.emit()
        elif e.key() in (Qt.Key_Left, Qt.Key_Right) and self.stops:
            step = 0.01 if e.key() == Qt.Key_Right else -0.01
            self.position_dragged.emit(min(max(self.stops[self.selected][0] + step, 0.0), 1.0))
        else:
            super().keyPressEvent(e)


class _StopChip(ColorSwatch):
    """A stop's color box. The first click selects it; clicking the selected box edits its color."""

    chosen = Signal()

    def __init__(self, name: str, parent=None) -> None:
        super().__init__(name, parent)
        self.is_selected = False

    def _pick(self) -> None:
        if self.is_selected:
            super()._pick()
        else:
            self.chosen.emit()

    def edit_color(self) -> None:
        ColorSwatch._pick(self)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.is_selected:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(qcolor(T.accent), 2.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1.25, 1.25, -1.25, -1.25), T.radius, T.radius)


def _hex(rgb) -> str:
    r, g, b = (int(round(float(np.clip(v, 0.0, 1.0)) * 255)) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


class GradientEditor(_Editor):
    """Edit a gradient's stops.

    Each stop is a color box (below the bar) and a handle on the bar. Click a box or handle to
    select it, then: click the selected box (or double-click its handle) to change its color,
    drag its handle or use the Position slider to move it along the gradient (this controls
    shading), the arrows to move it earlier or later in the color order, and - to delete it.
    + adds a stop in the widest gap; double-clicking empty space on the bar adds one there.
    """

    def __init__(self, param: Param, parent=None) -> None:
        super().__init__(parent)
        self.param = param
        self.stops: list[tuple[float, str]] = []
        self.selected = 0
        self.bar = GradientBar()
        self.swatch_row = QHBoxLayout()
        self.swatch_row.setSpacing(6)
        self.swatches: list[_StopChip] = []

        def small(text: str, name: str, tip: str) -> QPushButton:
            b = QPushButton(text)
            b.setObjectName("SmallButton")
            b.setAccessibleName(name)
            b.setToolTip(tip)
            b.setFixedSize(28, 28)
            b.setCursor(Qt.PointingHandCursor)
            return b

        self.earlier_btn = small("◀", "Move color earlier", "Move the selected color earlier in the order")
        self.later_btn = small("▶", "Move color later", "Move the selected color later in the order")
        self.remove_btn = small("−", "Delete selected color", "Delete the selected color (Delete key)")
        self.add_btn = small("+", "Add color stop", "Add a color")
        self.earlier_btn.clicked.connect(lambda: self._shift(-1))
        self.later_btn.clicked.connect(lambda: self._shift(1))
        self.remove_btn.clicked.connect(self._remove)
        self.add_btn.clicked.connect(lambda: self._add(None))

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addStretch(1)
        for b in (self.earlier_btn, self.later_btn, self.remove_btn, self.add_btn):
            controls.addWidget(b)

        self.pos_slider = QSlider(Qt.Horizontal)
        self.pos_slider.setRange(0, SLIDER_STEPS)
        self.pos_slider.setPageStep(SLIDER_STEPS // 20)
        self.pos_slider.setAccessibleName("Selected color position")
        self.pos_slider.setToolTip("Where the selected color sits on the gradient")
        self.pos_label = QLabel()
        self.pos_label.setObjectName("ValueLabel")
        self.pos_label.setMinimumWidth(44)
        self.pos_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        pos_row = QHBoxLayout()
        pos_row.setContentsMargins(0, 0, 0, 0)
        pos_row.setSpacing(T.unit)
        pos_row.addWidget(QLabel("Position"))
        pos_row.addWidget(self.pos_slider, 1)
        pos_row.addWidget(self.pos_label)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(T.unit)
        col.addWidget(self.bar)
        self.swatch_row.addStretch(1)
        col.addLayout(self.swatch_row)
        col.addLayout(controls)
        col.addLayout(pos_row)

        self.bar.selected_changed.connect(self.select)
        self.bar.position_dragged.connect(self._move_selected)
        self.bar.edit_requested.connect(lambda _i: self.swatches[self.selected].edit_color())
        self.bar.add_requested.connect(self._add)
        self.bar.delete_requested.connect(self._remove)
        self.pos_slider.valueChanged.connect(lambda v: self._move_selected(v / SLIDER_STEPS))
        self.set_value(param.default)

    # ---- model

    def colors(self) -> list[str]:
        return [c for _, c in self.stops]

    def _apply(self, stops: list[tuple[float, str]], selected: int, emit: bool) -> None:
        """Show ``stops`` (any order; sorted here, stable) and select the one at ``selected``."""
        order = sorted(range(len(stops)), key=lambda i: stops[i][0])
        self.stops = [(min(max(float(stops[i][0]), 0.0), 1.0), stops[i][1]) for i in order]
        self.selected = order.index(selected) if selected in order else 0
        n = len(self.stops)
        if len(self.swatches) != n:
            for sw in self.swatches:
                sw.setParent(None)
                sw.deleteLater()
            self.swatches = []
            for i in range(n):
                sw = _StopChip(f"Color {i + 1}")
                sw.setFixedSize(28, 28)
                sw.color_changed.connect(lambda hex_, sw=sw: self._stop_color(self.swatches.index(sw), hex_))
                sw.chosen.connect(lambda sw=sw: self.select(self.swatches.index(sw)))
                self.swatch_row.insertWidget(i, sw)
                self.swatches.append(sw)
        for i, (sw, (_p, c)) in enumerate(zip(self.swatches, self.stops)):
            sw.set_color(c)
            sw.is_selected = i == self.selected
            sw.update()
        self.bar.stops = self.stops
        self.bar.selected = self.selected
        self.bar.update()
        pos = self.stops[self.selected][0] if self.stops else 0.0
        self.pos_slider.blockSignals(True)
        self.pos_slider.setValue(int(round(pos * SLIDER_STEPS)))
        self.pos_slider.blockSignals(False)
        self.pos_label.setText(f"{round(pos * 100)}%")
        self.add_btn.setEnabled(n < MAX_STOPS)
        self.remove_btn.setEnabled(n > 1)
        self.earlier_btn.setEnabled(self.selected > 0)
        self.later_btn.setEnabled(self.selected < n - 1)
        if emit:
            self.value_changed.emit([[round(p, 4), c] for p, c in self.stops])

    # ---- gestures

    def select(self, i: int) -> None:
        if 0 <= i < len(self.stops) and i != self.selected:
            self._apply(self.stops, i, emit=False)

    def _stop_color(self, i: int, hex_: str) -> None:
        stops = list(self.stops)
        stops[i] = (stops[i][0], hex_)
        self._apply(stops, i, emit=True)

    def _move_selected(self, pos: float) -> None:
        if not self.stops:
            return
        pos = round(min(max(pos, 0.0), 1.0), 3)
        if pos == self.stops[self.selected][0]:
            return
        stops = list(self.stops)
        stops[self.selected] = (pos, stops[self.selected][1])
        self._apply(stops, self.selected, emit=True)

    def _shift(self, d: int) -> None:
        """Swap the selected color with its neighbour; the positions stay where they are."""
        j = self.selected + d
        if not 0 <= j < len(self.stops):
            return
        colors = self.colors()
        colors[self.selected], colors[j] = colors[j], colors[self.selected]
        self._apply([(p, c) for (p, _), c in zip(self.stops, colors)], j, emit=True)

    def _add(self, pos: float | None) -> None:
        if len(self.stops) >= MAX_STOPS:
            return
        if pos is None:  # middle of the widest gap (or the far end for a lone stop)
            if len(self.stops) == 1:
                pos = 1.0 if self.stops[0][0] < 0.5 else 0.0
            else:
                gaps = [(self.stops[i + 1][0] - self.stops[i][0], i) for i in range(len(self.stops) - 1)]
                width, i = max(gaps)
                pos = self.stops[i][0] + width / 2
        color = _hex(sample_gradient(self.stops, pos)) if self.stops else "#ffffff"
        stops = self.stops + [(round(pos, 3), color)]
        self._apply(stops, len(stops) - 1, emit=True)

    def _remove(self) -> None:
        if len(self.stops) > 1:
            stops = [s for i, s in enumerate(self.stops) if i != self.selected]
            self._apply(stops, min(self.selected, len(stops) - 1), emit=True)

    def set_value(self, value) -> None:
        try:
            stops = sorted((float(p), str(c)) for p, c in value)
        except (TypeError, ValueError):
            stops = [(float(p), str(c)) for p, c in self.param.default]
        keep = min(self.selected, len(stops) - 1) if stops else 0
        self._apply(stops or [(0.0, "#ffffff")], max(keep, 0), emit=False)


_EDITORS = {
    "float": NumberEditor,
    "int": NumberEditor,
    "bool": BoolEditor,
    "enum": EnumEditor,
    "color": ColorEditor,
    "gradient": GradientEditor,
}


def make_editor(param: Param, parent=None) -> _Editor:
    return _EDITORS[param.type](param, parent)
