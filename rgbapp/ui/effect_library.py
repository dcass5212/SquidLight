"""Effect library: a grid of effect cards, each with a small animated preview.

Previews run the real effect classes on a tiny synthetic grid in the GUI thread. That
is pure numpy (no device I/O) and only while the dialog is open.
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QDialog, QGridLayout, QLabel, QVBoxLayout

from ..engine.compositor import to_uint8
from ..engine.effects import listed_effects, Ctx
from .theme import T, qcolor
from .widgets import keyboard_focused, track_focus_reason

PREVIEW_COLS, PREVIEW_ROWS = 16, 5
PREVIEW_INTERVAL_MS = 40
_DESCRIPTIONS = {
    "static": "One solid color.",
    "breathing": "A color that fades in and out.",
    "color_cycle": "Everything fades through the spectrum together.",
    "rainbow_wave": "A rainbow sweeping across the device.",
    "gradient_wave": "Your own colors sweeping across the device.",
    "starlight": "Random keys twinkle and fade.",
    "reactive_typing": "Keys flash when you press them, then fade. Reacts to typing in any app.",
    "ripple": "A ring spreads out from each key you press.",
    "audio_spectrum": "Equalizer bars that follow whatever your PC is playing.",
    "audio_pulse": "The whole device throbs with the bass of your audio.",
    "cpu_gpu_meter": "A bar that fills with CPU, RAM or GPU load.",
    "temperature_gradient": "A color that shifts from cool to hot with a temperature. Needs LibreHardwareMonitor.",
}


def _grid_positions() -> np.ndarray:
    xs, ys = np.meshgrid(np.linspace(0, 1, PREVIEW_COLS), np.linspace(0, 1, PREVIEW_ROWS))
    return np.stack([xs.ravel(), ys.ravel()], axis=1).astype(np.float32)


class EffectCard(QAbstractButton):
    def __init__(self, effect_cls, parent=None) -> None:
        super().__init__(parent)
        self.effect_id = effect_cls.id
        self.setText(effect_cls.name)
        self.description = _DESCRIPTIONS.get(effect_cls.id, "")
        self.setToolTip(self.description)
        self.setAccessibleName(effect_cls.name)
        self.setAccessibleDescription(self.description)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)
        self._effect = effect_cls()
        self._params = effect_cls.resolve_params({})
        self._colors = np.zeros((PREVIEW_COLS * PREVIEW_ROWS, 3), dtype=np.uint8)

    def sizeHint(self) -> QSize:
        return QSize(196, 132)

    def focusInEvent(self, e) -> None:
        track_focus_reason(self, e)
        super().focusInEvent(e)

    def tick(self, ctx: Ctx) -> None:
        try:
            self._colors = to_uint8(self._effect.render(ctx, self._params))
        except Exception:  # a broken effect just shows dark in the library
            self._colors[:] = 0
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        hover = self.underMouse()
        p.setPen(QPen(qcolor(T.accent if keyboard_focused(self) else T.track if hover else T.border), 1.5 if keyboard_focused(self) else 1))
        p.setBrush(qcolor(T.hover if hover else T.raised))
        p.drawRoundedRect(r, T.radius + 2, T.radius + 2)

        well = QRectF(r.left() + 10, r.top() + 10, r.width() - 20, r.height() - 48)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(T.well))
        p.drawRoundedRect(well, T.radius, T.radius)
        pitch = min((well.width() - 12) / PREVIEW_COLS, (well.height() - 12) / PREVIEW_ROWS)
        ox = well.center().x() - pitch * PREVIEW_COLS / 2
        oy = well.center().y() - pitch * PREVIEW_ROWS / 2
        size = pitch * 0.74
        for i, (cr, cg, cb) in enumerate(self._colors.tolist()):
            row, col = divmod(i, PREVIEW_COLS)
            p.setBrush(QColor(cr, cg, cb) if max(cr, cg, cb) >= 24 else qcolor(T.led_off))
            p.drawRoundedRect(QRectF(ox + col * pitch, oy + row * pitch, size, size), 2, 2)

        p.setPen(qcolor(T.text))
        font = self.font()
        font.setWeight(font.Weight.DemiBold)
        p.setFont(font)
        p.drawText(QRectF(r.left() + 12, r.bottom() - 34, r.width() - 24, 28), Qt.AlignVCenter | Qt.AlignLeft, self.text())


class EffectLibrary(QDialog):
    """Pick an effect. ``exec()`` returns Accepted with ``chosen`` set."""

    def __init__(self, parent=None, title: str = "Add effect") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("EffectLibrary")
        self.chosen: Optional[str] = None
        col = QVBoxLayout(self)
        col.setContentsMargins(T.unit * 3, T.unit * 3, T.unit * 3, T.unit * 3)
        col.setSpacing(T.unit * 2)
        heading = QLabel("Effect library")
        heading.setObjectName("EmptyTitle")
        hint = QLabel("New layers go on top of the stack. Hover a card for a description.")
        hint.setObjectName("Caption")
        col.addWidget(heading)
        col.addWidget(hint)
        grid = QGridLayout()
        grid.setSpacing(T.unit * 1.5)
        self.cards: dict[str, EffectCard] = {}
        for i, cls in enumerate(listed_effects()):
            card = EffectCard(cls)
            card.clicked.connect(lambda _=False, eid=cls.id: self._choose(eid))
            grid.addWidget(card, i // 3, i % 3)
            self.cards[cls.id] = card
        col.addLayout(grid)

        self._positions = _grid_positions()
        self._t0 = time.monotonic()
        self._last = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(PREVIEW_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self._tick()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._timer.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._timer.stop()

    def _tick(self) -> None:
        t = time.monotonic() - self._t0
        ctx = Ctx(t=t, dt=max(t - self._last, 0.0), led_positions=self._positions)
        self._last = t
        for card in self.cards.values():
            card.tick(ctx)

    def _choose(self, effect_id: str) -> None:
        self.chosen = effect_id
        self.accept()
