"""Property editors generated from an effect's ``Param`` list.

Every editor has ``value_changed(object)`` (emitted live while the user drags or picks,
so the hardware previews immediately) and ``set_value(value)`` (silent).
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from ..engine.effects import Param
from .theme import T, qcolor
from .widgets import ColorSwatch, Toggle

SLIDER_STEPS = 1000
MAX_STOPS = 6


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
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.stops: list[tuple[float, str]] = []
        self.setFixedHeight(18)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        grad = QLinearGradient(r.left(), 0, r.right(), 0)
        for pos, color in self.stops:
            grad.setColorAt(pos, qcolor(color))
        p.setPen(QPen(qcolor(T.border), 1))
        p.setBrush(grad)
        p.drawRoundedRect(r, 4, 4)


class GradientEditor(_Editor):
    """A preview bar and one swatch per stop. Stops stay evenly spaced; + and - add or
    remove the last stop (1 to 6 stops)."""

    def __init__(self, param: Param, parent=None) -> None:
        super().__init__(parent)
        self.param = param
        self.stops: list[tuple[float, str]] = []
        self.bar = GradientBar()
        self.swatch_row = QHBoxLayout()
        self.swatch_row.setSpacing(6)
        self.swatches: list[ColorSwatch] = []
        self.add_btn = QPushButton("+")
        self.add_btn.setObjectName("SmallButton")
        self.add_btn.setAccessibleName("Add color stop")
        self.add_btn.setToolTip("Add a color")
        self.remove_btn = QPushButton("−")
        self.remove_btn.setObjectName("SmallButton")
        self.remove_btn.setAccessibleName("Remove last color stop")
        self.remove_btn.setToolTip("Remove the last color")
        for b in (self.add_btn, self.remove_btn):
            b.setFixedSize(28, 28)
            b.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self._add)
        self.remove_btn.clicked.connect(self._remove)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addLayout(self.swatch_row)
        controls.addStretch(1)
        controls.addWidget(self.remove_btn)
        controls.addWidget(self.add_btn)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(T.unit)
        col.addWidget(self.bar)
        col.addLayout(controls)
        self.set_value(param.default)

    def colors(self) -> list[str]:
        return [c for _, c in self.stops]

    def _set_colors(self, colors: list[str], emit: bool) -> None:
        n = len(colors)
        self.stops = [((i / (n - 1)) if n > 1 else 0.0, c) for i, c in enumerate(colors)]
        if len(self.swatches) != n:
            for s in self.swatches:
                s.setParent(None)
                s.deleteLater()
            self.swatches = []
            for i in range(n):
                sw = ColorSwatch(f"Color {i + 1}")
                sw.setFixedSize(28, 28)
                sw.color_changed.connect(lambda hex_, i=i: self._stop_changed(i, hex_))
                self.swatch_row.addWidget(sw)
                self.swatches.append(sw)
        for sw, c in zip(self.swatches, colors):
            sw.set_color(c)
        self.add_btn.setEnabled(n < MAX_STOPS)
        self.remove_btn.setEnabled(n > 1)
        self.bar.stops = self.stops
        self.bar.update()
        if emit:
            self.value_changed.emit([[round(p, 4), c] for p, c in self.stops])

    def _stop_changed(self, i: int, hex_: str) -> None:
        colors = self.colors()
        colors[i] = hex_
        self._set_colors(colors, emit=True)

    def _add(self) -> None:
        colors = self.colors()
        if len(colors) < MAX_STOPS:
            self._set_colors(colors + [colors[-1] if colors else "#ffffff"], emit=True)

    def _remove(self) -> None:
        colors = self.colors()
        if len(colors) > 1:
            self._set_colors(colors[:-1], emit=True)

    def set_value(self, value) -> None:
        try:
            colors = [str(c) for _, c in sorted((float(p), c) for p, c in value)]
        except (TypeError, ValueError):
            colors = [str(c) for _, c in self.param.default]
        self._set_colors(colors or ["#ffffff"], emit=False)


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
