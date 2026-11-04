"""Paint mode's right panel: tools, the brush color, fill/clear and undo/redo.

Painting edits a "Static per-key" layer in the group's stack (created on the first
paint), so it is non-destructive and shows up in the layers panel like any other layer.
The panel keeps a ``PaintDocument`` for undo and writes every change to the layer
through the bridge, so the keyboard shows it immediately. If the layer is changed or
removed elsewhere, the document reloads from the stack.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..engine.devices import DeviceGroup
from ..model.paint import PaintDocument
from .keyboard_canvas import KeyboardCanvas
from .theme import T, qcolor
from .widgets import ColorSwatch, keyboard_focused, track_focus_reason

PAINT_EFFECT = "static_keys"
RECENT_MAX = 8
DEFAULT_BRUSH = "#ff2d55"


class ColorChip(QAbstractButton):
    """A small color button for the recent-colors row."""

    def __init__(self, color: str, parent=None) -> None:
        super().__init__(parent)
        self.color = color
        self.setFixedSize(26, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(f"Use {color.upper()}")
        self.setToolTip(color.upper())

    def sizeHint(self) -> QSize:
        return QSize(26, 26)

    def focusInEvent(self, e) -> None:
        track_focus_reason(self, e)
        super().focusInEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        focused = keyboard_focused(self)
        p.setPen(QPen(qcolor(T.accent if focused else T.border), 1.5 if focused else 1))
        p.setBrush(qcolor(self.color))
        p.drawRoundedRect(r, 4, 4)


class PaintPanel(QFrame):
    PANEL_WIDTH = 340
    changed = Signal()  # the document changed (tests, status)

    def __init__(self, bridge, group: DeviceGroup, canvas: KeyboardCanvas, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.group = group
        self.key = group.key
        self.canvas = canvas
        self.doc = PaintDocument()
        self.color = DEFAULT_BRUSH
        self.recent: list[str] = []
        self._pending_recent: Optional[str] = None
        self.setObjectName("RightPanel")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFixedWidth(self.PANEL_WIDTH)

        col = QVBoxLayout(self)
        col.setContentsMargins(T.unit * 2, T.unit * 2, T.unit * 2, T.unit * 2)
        col.setSpacing(T.unit * 1.5)

        title = QLabel("Paint")
        title.setObjectName("PanelTitle")
        col.addWidget(title)
        self.hint = QLabel()
        self.hint.setObjectName("Caption")
        self.hint.setWordWrap(True)
        col.addWidget(self.hint)

        # -- selection
        col.addWidget(self._label("Select keys"))
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.tool_group = QButtonGroup(self)
        self.tool_buttons: dict[str, QPushButton] = {}
        for tool, text, tip in (
            ("select", "Keys", "Click a key, Shift-click to add, Ctrl-click to toggle, drag a box"),
            ("row", "Row", "Click a key to select its whole row (or Alt-click in Keys)"),
            ("eyedropper", "Eyedropper", "Click a key to copy the color it shows"),
        ):
            b = QPushButton(text)
            b.setObjectName("PanelButton")
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            self.tool_group.addButton(b)
            tools.addWidget(b)
            self.tool_buttons[tool] = b
            b.clicked.connect(lambda _c=False, t=tool: self.set_tool(t))
        col.addLayout(tools)
        sel_row = QHBoxLayout()
        sel_row.setSpacing(6)
        self.select_all_button = QPushButton("Select all")
        self.select_none_button = QPushButton("Select none")
        for b in (self.select_all_button, self.select_none_button):
            b.setObjectName("PanelButton")
            b.setCursor(Qt.PointingHandCursor)
            sel_row.addWidget(b)
        self.select_all_button.clicked.connect(canvas.select_all)
        self.select_none_button.clicked.connect(lambda: canvas.set_selection(()))
        col.addLayout(sel_row)
        self.count = QLabel()
        self.count.setObjectName("Caption")
        col.addWidget(self.count)

        # -- color
        col.addWidget(self._label("Color"))
        color_row = QHBoxLayout()
        color_row.setSpacing(T.unit * 1.5)
        self.swatch = ColorSwatch("Paint color")
        self.swatch.set_color(self.color)
        self.swatch.color_changed.connect(self._swatch_changed)
        self.hex = QLabel(self.color.upper())
        self.hex.setObjectName("ValueLabel")
        color_row.addWidget(self.swatch)
        color_row.addWidget(self.hex)
        color_row.addStretch(1)
        col.addLayout(color_row)
        self.recent_row = QHBoxLayout()
        self.recent_row.setSpacing(6)
        self.recent_row.addStretch(1)
        col.addLayout(self.recent_row)

        # -- fill / clear
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.fill_button = QPushButton()
        self.fill_button.setObjectName("AccentButton")
        self.clear_button = QPushButton()
        self.clear_button.setObjectName("PanelButton")
        for b in (self.fill_button, self.clear_button):
            b.setCursor(Qt.PointingHandCursor)
            actions.addWidget(b)
        self.fill_button.clicked.connect(self.fill)
        self.clear_button.clicked.connect(self.clear)
        col.addLayout(actions)

        history = QHBoxLayout()
        history.setSpacing(6)
        self.undo_button = QPushButton("Undo")
        self.redo_button = QPushButton("Redo")
        self.undo_button.setToolTip("Undo (Ctrl+Z)")
        self.redo_button.setToolTip("Redo (Ctrl+Y)")
        for b in (self.undo_button, self.redo_button):
            b.setObjectName("PanelButton")
            b.setCursor(Qt.PointingHandCursor)
            history.addWidget(b)
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        col.addLayout(history)
        col.addStretch(1)

        # Active while this panel is shown (a hidden page's shortcuts are off).
        for seq, slot in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo)):
            QShortcut(QKeySequence(seq), self, activated=slot, context=Qt.WindowShortcut)

        canvas.selection_changed.connect(self._selection_changed)
        canvas.color_picked.connect(self._picked)
        bridge.settings_changed.connect(self._on_settings_changed)
        self.set_tool("select")
        self.sync()
        self._refresh()

    @staticmethod
    def _label(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("FieldLabel")
        return lab

    # -- the paint layer

    def paint_layer(self):
        return next((s for s in self.bridge.layers(self.key) if s.effect == PAINT_EFFECT), None)

    def sync(self) -> None:
        """Make the document match the stack (it may have been edited in the layers panel)."""
        layer = self.paint_layer()
        stored = dict(layer.params.get("colors") or {}) if layer else {}
        if {str(k): str(v).lower() for k, v in stored.items()} != self.doc.to_params():
            self.doc.load(stored)
            self._refresh()

    def _on_settings_changed(self, key: str) -> None:
        if key == self.key:
            self.sync()

    def _push(self) -> None:
        params = self.doc.to_params()
        layer = self.paint_layer()
        if layer is None:
            if params:
                self.bridge.add_layer(self.key, PAINT_EFFECT, {"colors": params})
        else:
            self.bridge.set_layer_param(self.key, layer.id, "colors", params)
        self._refresh()
        self.changed.emit()

    # -- tools

    def set_tool(self, tool: str) -> None:
        self.tool_buttons[tool].setChecked(True)
        self.canvas.set_tool(tool)

    def _selection_changed(self, _sel) -> None:
        self.doc.end_gesture()
        self._commit_recent()
        self._refresh()

    def _picked(self, color: str) -> None:
        self._set_color(color)
        self._pending_recent = color
        self._commit_recent()
        self.set_tool("select")  # one-shot

    # -- color

    def _set_color(self, color: str) -> None:
        self.color = color.lower()
        self.swatch.set_color(self.color)
        self.hex.setText(self.color.upper())

    def _swatch_changed(self, color: str) -> None:
        """Live while the picker is open: the selected keys follow the color."""
        self._set_color(color)
        self._pending_recent = self.color
        sel = self.canvas.selection
        if sel and self.doc.apply(sel, self.color, merge=("color", sel)):
            self._push()

    def _commit_recent(self) -> None:
        color, self._pending_recent = self._pending_recent, None
        if color is None:
            return
        self.recent = ([color] + [c for c in self.recent if c != color])[:RECENT_MAX]
        while self.recent_row.count() > 1:
            w = self.recent_row.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for i, c in enumerate(self.recent):
            chip = ColorChip(c)
            chip.clicked.connect(lambda _c=False, c=c: self._use_recent(c))
            self.recent_row.insertWidget(i, chip)

    def _use_recent(self, color: str) -> None:
        self._set_color(color)
        sel = self.canvas.selection
        if sel and self.doc.apply(sel, self.color):
            self._push()

    # -- actions

    def _targets(self):
        """The selection, or every key when nothing is selected."""
        return self.canvas.selection or range(self.group.led_count)

    def fill(self) -> None:
        self._pending_recent = self.color
        self._commit_recent()
        if self.doc.apply(self._targets(), self.color):
            self._push()

    def clear(self) -> None:
        sel = self.canvas.selection
        changed = self.doc.clear(sel) if sel else self.doc.clear_all()
        if changed:
            self._push()

    def undo(self) -> None:
        if self.doc.undo():
            self._push()

    def redo(self) -> None:
        if self.doc.redo():
            self._push()

    # -- view

    def _refresh(self) -> None:
        n = len(self.canvas.selection)
        self.count.setText(f"{n} key{'s' if n != 1 else ''} selected" if n else "No keys selected")
        self.fill_button.setText("Fill selected" if n else "Fill all")
        self.clear_button.setText("Clear selected" if n else "Clear all")
        self.clear_button.setEnabled(not self.doc.is_empty)
        self.undo_button.setEnabled(self.doc.can_undo)
        self.redo_button.setEnabled(self.doc.can_redo)
        self.hint.setText(
            "Pick keys, then pick a color: the selected keys follow it. Unpainted keys show the layers below."
            if self.doc.is_empty
            else 'Painted keys live in the "Static per-key" layer. Switch to Effects to reorder or fade it.'
        )
