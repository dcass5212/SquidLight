"""The keyboard drawn from its layout file: each key a rounded keycap lit with the
engine's live output for that LED. Hovering a key names it.

Used large on the device page and small on the dashboard card. Any group with a
physical layout gets this; others get the grid ``LedPreview``.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QKeyEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ..engine.devices import DeviceGroup
from .theme import T, qcolor
from .widgets import LedPreview, keyboard_focused, track_focus_reason

LIT_THRESHOLD = 24  # below this a key reads as unlit and shows the keycap color
GLOW_RES = 2  # glow image pixels per key unit (low on purpose: upscaling blurs it)
BODY_PAD = 0.4  # key units of keyboard case around the keys


def make_preview(group: DeviceGroup, clickable: bool = False, parent=None) -> QWidget:
    """The right live preview for a device group."""
    if group.layout is not None:
        return KeyboardCanvas(group, clickable=clickable, parent=parent)
    return LedPreview(group, clickable=clickable, parent=parent)


class KeyboardCanvas(QWidget):
    activated = Signal()  # clicked (when clickable)
    selection_changed = Signal(object)  # frozenset of LED indices (paint mode)
    color_picked = Signal(str)  # '#rrggbb' under the eyedropper (paint mode)

    MAX_PITCH = 60.0  # px per key unit

    def __init__(self, group: DeviceGroup, clickable: bool = False, parent=None) -> None:
        super().__init__(parent)
        if group.layout is None:
            raise ValueError(f"{group.key} has no physical layout")
        self._group = group
        self._lay = group.layout
        self._frame: Optional[np.ndarray] = None
        self._dimmed = False
        self._clickable = clickable
        self._outline: set[int] = set()  # LED indices to outline (the selected layer's mask)
        self._geom_key = None
        # paint mode: pick keys with the mouse (click, shift/ctrl-click, drag a box, a row)
        self._paint = False
        self._tool = "select"  # "select" | "row" | "eyedropper"
        self._selected: set[int] = set()
        self._press: Optional[tuple[QPoint, bool]] = None  # where the mouse went down, additive?
        self._band: Optional[QRectF] = None
        self._rects: list[QRectF] = []
        self._pitch = 0.0

        # keycaps: a cap is one LED, or several LEDs drawn as one key (the spacebar)
        caps: dict[str, list[int]] = {}
        self._caps: list[list[int]] = []
        for i, cap in enumerate(self._lay.caps):
            if cap:
                caps.setdefault(cap, []).append(i)
            else:
                self._caps.append([i])
        self._caps += list(caps.values())
        self._cap_of: dict[int, list[int]] = {i: idx for idx in self._caps for i in idx}

        # bounds in key units, and each LED's pixel box in the glow image
        r = self._lay.rects
        self._ux0, self._uy0 = float(r[:, 0].min()), float(r[:, 1].min())
        self._uw = float((r[:, 0] + r[:, 2]).max()) - self._ux0
        self._uh = float((r[:, 1] + r[:, 3]).max()) - self._uy0
        self._glow_w = int(math.ceil(self._uw * GLOW_RES)) + 2 * GLOW_RES
        self._glow_h = int(math.ceil(self._uh * GLOW_RES)) + 2 * GLOW_RES
        px = (r - [self._ux0 - 1, self._uy0 - 1, 0, 0]) * GLOW_RES
        self._glow_boxes = [
            (int(round(y)), int(round(y + max(h, 1))), int(round(x)), int(round(x + max(w, 1))))
            for x, y, w, h in px.tolist()
        ]

        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName(f"Live preview of {group.name}")
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
            self.setFocusPolicy(Qt.StrongFocus)

    # -- data

    @property
    def group(self) -> DeviceGroup:
        return self._group

    def set_frame(self, frame: np.ndarray) -> None:
        self._frame = frame
        self.update()

    def set_dimmed(self, dimmed: bool) -> None:
        if dimmed != self._dimmed:
            self._dimmed = dimmed
            self.update()

    def set_outline(self, indices: Optional[np.ndarray]) -> None:
        """Outline these LEDs (e.g. the selected layer's mask); None clears it."""
        self._outline = set() if indices is None else {int(i) for i in indices}
        self.update()

    # -- paint mode: selection

    @property
    def paint_mode(self) -> bool:
        return self._paint

    def set_paint_mode(self, on: bool) -> None:
        if on == self._paint:
            return
        self._paint = on
        self._press = self._band = None
        self.setFocusPolicy(Qt.StrongFocus if (on or self._clickable) else Qt.NoFocus)
        self.setCursor(Qt.CrossCursor if on else (Qt.PointingHandCursor if self._clickable else Qt.ArrowCursor))
        if not on:
            self.set_selection(())
        self.update()

    @property
    def tool(self) -> str:
        return self._tool

    def set_tool(self, tool: str) -> None:
        if tool not in ("select", "row", "eyedropper"):
            raise ValueError(f"unknown paint tool {tool!r}")
        self._tool = tool
        self.setCursor(Qt.CrossCursor if self._paint else self.cursor())

    @property
    def selection(self) -> frozenset:
        return frozenset(self._selected)

    def set_selection(self, indices) -> None:
        """Select these LEDs (a keycap made of several LEDs is always selected whole)."""
        new: set[int] = set()
        for i in indices:
            new.update(self._cap_of.get(int(i), [int(i)]))
        if new != self._selected:
            self._selected = new
            self.update()
            self.selection_changed.emit(frozenset(new))

    def select_all(self) -> None:
        self.set_selection(range(self._group.led_count))

    def row_of(self, index: int) -> list[int]:
        """The LEDs on the same keyboard row as ``index`` (tall keys count for their top row)."""
        r = self._lay.rects
        cy = r[index, 1] + min(r[index, 3], 1.0) / 2
        return np.flatnonzero((r[:, 1] + 0.05 < cy) & (cy < r[:, 1] + r[:, 3] - 0.05)).tolist()

    def _paint_click(self, led: Optional[int], mods) -> None:
        """A click (no drag) in paint mode."""
        if led is None:
            if not mods & (Qt.ShiftModifier | Qt.ControlModifier):
                self.set_selection(())
            return
        if self._tool == "eyedropper" and self._frame is not None and len(self._frame) == self._group.led_count:
            self.color_picked.emit("#%02x%02x%02x" % tuple(int(c) for c in self._frame[led]))
            return
        group = self.row_of(led) if (self._tool == "row" or mods & Qt.AltModifier) else self._cap_of.get(led, [led])
        if mods & Qt.ControlModifier:  # toggle
            on = not set(group) <= self._selected
            self.set_selection((self._selected | set(group)) if on else (self._selected - set(group)))
        elif mods & Qt.ShiftModifier:  # add
            self.set_selection(self._selected | set(group))
        else:
            self.set_selection(group)

    def _band_select(self, additive: bool) -> None:
        probe = self._band.adjusted(-1, -1, 1, 1)  # a dead-straight drag is a zero-area rect
        hit = {i for i, rect in enumerate(self._rects) if probe.intersects(rect)}
        self.set_selection((self._selected | hit) if additive else hit)

    # -- geometry queries

    def led_rect(self, index: int) -> QRectF:
        """Where LED ``index`` is drawn, in widget coordinates."""
        self._geometry()
        return self._rects[index]

    def led_at(self, pos) -> Optional[int]:
        self._geometry()
        pt = QPointF(pos)
        for i, rect in enumerate(self._rects):
            if rect.contains(pt):
                return i
        return None

    # -- input

    def focusInEvent(self, e) -> None:
        track_focus_reason(self, e)
        super().focusInEvent(e)

    def mousePressEvent(self, e) -> None:
        if self._paint and e.button() == Qt.LeftButton:
            self.setFocus(Qt.MouseFocusReason)
            self._press = (e.position().toPoint(), bool(e.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)))
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._paint and self._press is not None and self._tool == "select":
            start, additive = self._press
            pos = e.position().toPoint()
            if self._band is not None or (pos - start).manhattanLength() > 6:
                self._band = QRectF(QPointF(start), QPointF(pos)).normalized()
                self._band_select(additive)
                self.update()
            return
        super().mouseMoveEvent(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if self._paint:
            if e.key() == Qt.Key_A and e.modifiers() & Qt.ControlModifier:
                self.select_all()
                return
            if e.key() == Qt.Key_Escape:
                self.set_selection(())
                return
        self._key_press(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._paint and e.button() == Qt.LeftButton and self._press is not None:
            was_drag = self._band is not None
            self._press, self._band = None, None
            if not was_drag:
                self._paint_click(self.led_at(e.position()), e.modifiers())
            self.update()
            return
        if self._clickable and e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.activated.emit()
        super().mouseReleaseEvent(e)

    def _key_press(self, e: QKeyEvent) -> None:
        if self._clickable and not self._paint and e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activated.emit()
            return
        super().keyPressEvent(e)

    def event(self, e) -> bool:
        if e.type() == QEvent.ToolTip:
            i = self.led_at(e.pos())
            if i is None:
                QToolTip.hideText()
                if self._clickable:
                    QToolTip.showText(e.globalPos(), self.toolTip(), self)
            else:
                name = self._group.led_names[i]
                QToolTip.showText(e.globalPos(), f"{name}  (LED {i})", self)
            return True
        return super().event(e)

    # -- geometry

    def _geometry(self) -> None:
        key = (self.width(), self.height())
        if key == self._geom_key:
            return
        self._geom_key = key
        pad = 12.0
        uw, uh = self._uw + 2 * BODY_PAD, self._uh + 2 * BODY_PAD
        pitch = min((self.width() - 2 * pad) / uw, (self.height() - 2 * pad) / uh, self.MAX_PITCH)
        pitch = max(pitch, 1.0)
        self._pitch = pitch
        ox = (self.width() - uw * pitch) / 2 + (BODY_PAD - self._ux0) * pitch
        oy = (self.height() - uh * pitch) / 2 + (BODY_PAD - self._uy0) * pitch
        gap = max(1.0, pitch * 0.07)
        self._rects = [
            QRectF(ox + x * pitch + gap / 2, oy + y * pitch + gap / 2, w * pitch - gap, h * pitch - gap)
            for x, y, w, h in self._lay.rects.tolist()
        ]
        self._body = QRectF(
            ox + (self._ux0 - BODY_PAD) * pitch, oy + (self._uy0 - BODY_PAD) * pitch, uw * pitch, uh * pitch
        )
        self._glow_rect = QRectF(
            ox + (self._ux0 - 1) * pitch,
            oy + (self._uy0 - 1) * pitch,
            self._glow_w / GLOW_RES * pitch,
            self._glow_h / GLOW_RES * pitch,
        )
        self._radius = max(1.5, pitch * 0.12)
        self._cap_rects = [self._union(idx) for idx in self._caps]
        # legends only when keys are big enough to read
        self._show_labels = pitch >= 26
        self._font = QFont(self.font())
        self._font.setPixelSize(int(min(max(pitch * 0.24, 8), 13)))
        self._metrics = QFontMetricsF(self._font)

    def _union(self, idx: list[int]) -> QRectF:
        r = self._rects[idx[0]]
        for i in idx[1:]:
            r = r.united(self._rects[i])
        return r

    # -- drawing

    def _glow_image(self, frame: np.ndarray) -> QImage:
        img = np.zeros((self._glow_h, self._glow_w, 3), dtype=np.uint8)
        for (y0, y1, x0, x1), color in zip(self._glow_boxes, frame):
            img[y0:y1, x0:x1] = color
        h, w, _ = img.shape
        return QImage(img.data, w, h, 3 * w, QImage.Format_RGB888).copy()

    def paintEvent(self, _event) -> None:
        self._geometry()
        # no frame: the keyboard sits on whatever surface holds it and lights it
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)

        n = self._group.led_count
        frame = self._frame
        if frame is None or len(frame) != n:
            frame = np.zeros((n, 3), dtype=np.uint8)

        p.save()
        p.setClipRect(self.rect().adjusted(1, 1, -1, -1))
        level = 0.3 if self._dimmed else 1.0

        # the keyboard case
        p.setOpacity(level)
        p.setBrush(qcolor(T.kb_body))
        p.setPen(QPen(qcolor(T.track if self._pitch >= 26 else T.border), 1))
        p.drawRoundedRect(self._body, self._pitch * 0.25, self._pitch * 0.25)

        # light spilling onto the case around the keys
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.setOpacity(0.35 * level)
        p.drawImage(self._glow_rect, self._glow_image(frame))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setOpacity(level)

        colors = frame.tolist()
        lit = (frame.max(axis=1) >= LIT_THRESHOLD).tolist()
        off = qcolor(T.led_off)
        edge = QPen(QColor(0, 0, 0, 110), 1)
        p.setFont(self._font)
        for idx, cap in zip(self._caps, self._cap_rects):
            if len(idx) == 1:
                i = idx[0]
                fill = QColor(*colors[i]) if lit[i] else off
                p.setPen(edge)
                p.setBrush(fill)
                p.drawRoundedRect(cap, self._radius, self._radius)
            else:  # one keycap, several LEDs: fill each segment inside the cap's outline
                path = QPainterPath()
                path.addRoundedRect(cap, self._radius, self._radius)
                p.save()
                p.setClipPath(path, Qt.IntersectClip)
                p.setPen(Qt.NoPen)
                for i in idx:  # segments overlap by a pixel so no seam shows between them
                    p.setBrush(QColor(*colors[i]) if lit[i] else off)
                    p.drawRect(self._rects[i].adjusted(-1, 0, 1, 0))
                p.restore()
                p.setPen(edge)
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
                fill = QColor(*colors[idx[len(idx) // 2]]) if lit[idx[len(idx) // 2]] else off
            if self._show_labels:
                label = next((self._lay.labels[i] for i in idx if self._lay.labels[i]), "")
                if label and self._metrics.horizontalAdvance(label) <= cap.width() - 4:
                    p.setPen(_legend_color(fill))
                    p.drawText(cap, Qt.AlignCenter, label)

        if self._outline:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(qcolor(T.accent), 2))
            for idx, cap in zip(self._caps, self._cap_rects):
                if not self._outline.isdisjoint(idx):
                    p.drawRoundedRect(cap.adjusted(-1.5, -1.5, 1.5, 1.5), self._radius + 1, self._radius + 1)
        if self._paint and self._selected:
            p.setBrush(QColor(255, 255, 255, 40))
            p.setPen(QPen(qcolor(T.text), 2))
            for idx, cap in zip(self._caps, self._cap_rects):
                if not self._selected.isdisjoint(idx):
                    p.drawRoundedRect(cap.adjusted(-1.5, -1.5, 1.5, 1.5), self._radius + 1, self._radius + 1)
        if self._band is not None:
            accent = qcolor(T.accent)
            p.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 40))
            p.setPen(QPen(accent, 1))
            p.drawRect(self._band)
        p.restore()

        if (self._clickable or self._paint) and keyboard_focused(self):
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(qcolor(T.accent), 2))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), T.radius, T.radius)


def _legend_color(fill: QColor) -> QColor:
    """Dark legends on bright keys, light legends on dark ones."""
    lum = 0.2126 * fill.redF() + 0.7152 * fill.greenF() + 0.0722 * fill.blueF()
    return QColor(0, 0, 0, 190) if lum > 0.5 else QColor(255, 255, 255, 160)
