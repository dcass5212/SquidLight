"""Right panel of a device page: the layer stack and the selected layer's properties.

The list shows the top layer first (it is composited last). Each row has a visibility
eye, the effect name, blend mode, opacity, a drag handle and delete. Keyboard: Up/Down
select, Space toggles visibility, Delete removes, Ctrl+Up/Down moves the layer.
Every edit goes straight to the engine through the bridge, so the hardware previews it.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QKeyEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..engine.compositor import BLEND_MODES, LayerSpec
from ..engine.devices import DeviceGroup
from ..engine.effects import get_effect_class, listed_effects
from .effect_library import EffectLibrary
from .param_editors import make_editor
from .theme import T, qcolor
from .widgets import PercentSlider, guard_wheel, keyboard_focused, track_focus_reason

PANEL_WIDTH = 340
LIST_MAX_HEIGHT = 280  # about four rows; more scroll


def effect_name(effect_id: str) -> str:
    try:
        return get_effect_class(effect_id).name
    except KeyError:
        return effect_id


# --------------------------------------------------------------------------- icons


class IconButton(QAbstractButton):
    """Small painted icon button: "eye" (checkable: checked = visible), "delete", "handle"."""

    def __init__(self, kind: str, accessible_name: str, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setAccessibleName(accessible_name)
        self.setToolTip(accessible_name)
        self.setFixedSize(28, 28)
        self.setAttribute(Qt.WA_Hover)
        if kind == "handle":
            self.setCursor(Qt.SizeVerCursor)
            self.setFocusPolicy(Qt.NoFocus)
        else:
            self.setCursor(Qt.PointingHandCursor)
            self.setFocusPolicy(Qt.TabFocus)
        if kind == "eye":
            self.setCheckable(True)

    def sizeHint(self) -> QSize:
        return QSize(28, 28)

    def focusInEvent(self, e) -> None:
        track_focus_reason(self, e)
        super().focusInEvent(e)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        if self.underMouse() and self.kind != "handle":
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(T.hover))
            p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 4, 4)
        if keyboard_focused(self):
            p.setPen(QPen(qcolor(T.accent), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 4, 4)
        c = r.center()
        muted = self.kind == "eye" and not self.isChecked()
        color = qcolor(T.muted if (muted or not self.underMouse()) else T.text)
        if self.kind == "delete" and self.underMouse():
            color = qcolor(T.error)
        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if self.kind == "eye":
            path = QPainterPath()
            path.moveTo(c.x() - 7, c.y())
            path.quadTo(c.x(), c.y() - 7, c.x() + 7, c.y())
            path.quadTo(c.x(), c.y() + 7, c.x() - 7, c.y())
            p.drawPath(path)
            if self.isChecked():
                p.setBrush(color)
            p.drawEllipse(c, 2.2, 2.2)
            if not self.isChecked():
                p.drawLine(QPointF(c.x() - 6, c.y() + 6), QPointF(c.x() + 6, c.y() - 6))
        elif self.kind == "delete":
            p.drawLine(QPointF(c.x() - 4.5, c.y() - 4.5), QPointF(c.x() + 4.5, c.y() + 4.5))
            p.drawLine(QPointF(c.x() + 4.5, c.y() - 4.5), QPointF(c.x() - 4.5, c.y() + 4.5))
        elif self.kind == "handle":
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            for dx in (-2.5, 2.5):
                for dy in (-5, 0, 5):
                    p.drawEllipse(QPointF(c.x() + dx, c.y() + dy), 1.3, 1.3)


# --------------------------------------------------------------------------- rows


class LayerRow(QFrame):
    clicked = Signal(str)  # layer id

    def __init__(self, spec: LayerSpec, parent=None) -> None:
        super().__init__(parent)
        self.layer_id = spec.id
        self.setObjectName("LayerRow")
        self.setProperty("selected", False)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setCursor(Qt.PointingHandCursor)

        self.eye = IconButton("eye", "Visible")
        self.name = QLabel()
        self.name.setObjectName("LayerName")
        self.handle = IconButton("handle", "Drag to reorder")
        self.delete = IconButton("delete", "Delete layer")
        self.blend = QComboBox()
        self.blend.setAccessibleName("Blend mode")
        self.blend.setToolTip("How this layer mixes with the layers below it")
        self.blend.setFocusPolicy(Qt.StrongFocus)
        for mode in BLEND_MODES:
            self.blend.addItem(mode.capitalize(), mode)
        self.blend.setMinimumWidth(104)
        self.opacity = PercentSlider("Layer opacity")
        self.opacity.setToolTip("Layer opacity")
        guard_wheel(self.blend, self.opacity.slider)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)
        top.addWidget(self.eye)
        top.addWidget(self.name, 1)
        top.addWidget(self.handle)
        top.addWidget(self.delete)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(32, 0, 4, 0)
        bottom.setSpacing(T.unit)
        bottom.addWidget(self.blend)
        bottom.addWidget(self.opacity, 1)
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 4, 4, 8)
        col.setSpacing(2)
        col.addLayout(top)
        col.addLayout(bottom)
        self.set_spec(spec)

    def set_spec(self, spec: LayerSpec) -> None:
        self.name.setText(effect_name(spec.effect))
        self.setAccessibleName(f"Layer {effect_name(spec.effect)}")
        for w in (self.eye, self.blend):
            w.blockSignals(True)
        self.eye.setChecked(spec.visible)
        self.eye.setToolTip("Hide layer" if spec.visible else "Show layer")
        self.blend.setCurrentIndex(max(self.blend.findData(spec.blend), 0))
        for w in (self.eye, self.blend):
            w.blockSignals(False)
        self.opacity.set_value(spec.opacity)
        self.name.setEnabled(spec.visible)

    def set_selected(self, selected: bool) -> None:
        if self.property("selected") != selected:
            self.setProperty("selected", selected)
            self.style().unpolish(self)
            self.style().polish(self)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.layer_id)
        super().mousePressEvent(e)


class LayerList(QWidget):
    """Holds the rows; handles keyboard navigation and drag-to-reorder."""

    key_action = Signal(str)  # "up" | "down" | "toggle" | "delete" | "move_up" | "move_down"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Layers")
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(4)
        self.col.addStretch(1)

    def focusInEvent(self, e) -> None:
        track_focus_reason(self, e)
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self.update()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        ctrl = bool(e.modifiers() & Qt.ControlModifier)
        actions = {
            Qt.Key_Up: "move_up" if ctrl else "up",
            Qt.Key_Down: "move_down" if ctrl else "down",
            Qt.Key_Space: "toggle",
            Qt.Key_Delete: "delete",
        }
        action = actions.get(e.key())
        if action:
            self.key_action.emit(action)
            e.accept()
            return
        super().keyPressEvent(e)

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if keyboard_focused(self):
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(qcolor(T.accent), 1.5))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), T.radius, T.radius)


# --------------------------------------------------------------------------- properties


class LayerProperties(QWidget):
    """Editors for one layer: its effect, the effect's params (generated), and its mask."""

    effect_changed = Signal(str)
    param_changed = Signal(str, object)
    mask_changed = Signal(object)

    def __init__(self, group: DeviceGroup, parent=None) -> None:
        super().__init__(parent)
        self.group = group
        self.layer_id: Optional[str] = None
        self.effect_id: Optional[str] = None
        self.editors: dict[str, QWidget] = {}
        self.col = QVBoxLayout(self)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(T.unit * 1.5)

        self.effect = QComboBox()
        self.effect.setAccessibleName("Layer effect")
        for cls in listed_effects():
            self.effect.addItem(cls.name, cls.id)
        self.effect.currentIndexChanged.connect(lambda _i: self.effect_changed.emit(self.effect.currentData()))
        self.mask = QComboBox()
        self.mask.setAccessibleName("Applies to")
        self.mask.setToolTip("Which LEDs this layer lights")
        self.mask.currentIndexChanged.connect(self._mask_picked)
        guard_wheel(self.effect, self.mask)
        self._params_box = QVBoxLayout()
        self._params_box.setSpacing(T.unit * 1.5)

        self.notice = QLabel()
        self.notice.setObjectName("Caption")
        self.notice.setWordWrap(True)
        self.notice.setAccessibleName("Layer notice")
        self.notice.hide()

        self.col.addLayout(self._field("Effect", self.effect))
        self.col.addWidget(self.notice)
        self.col.addLayout(self._params_box)
        self.col.addLayout(self._field("Applies to", self.mask))
        self.col.addStretch(1)

    @staticmethod
    def _field(label: str, widget: QWidget) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(6)
        lab = QLabel(label)
        lab.setObjectName("FieldLabel")
        lab.setWordWrap(True)  # long labels must not widen the panel
        box.addWidget(lab)
        box.addWidget(widget)
        return box

    def show_layer(self, spec: LayerSpec) -> None:
        """Rebuild the editors only when the layer or its effect changed; otherwise
        just sync values (so a slider being dragged is not torn down under the mouse)."""
        if spec.id != self.layer_id or spec.effect != self.effect_id:
            self.layer_id, self.effect_id = spec.id, spec.effect
            self._build_params(spec.effect)
        self.effect.blockSignals(True)
        cls = get_effect_class(spec.effect)
        if not cls.listed and self.effect.findData(spec.effect) < 0:  # e.g. the Paint layer
            self.effect.addItem(cls.name, cls.id)
        for i in reversed(range(self.effect.count())):  # drop an unlisted entry once it is not in use
            eid = self.effect.itemData(i)
            if eid != spec.effect and not get_effect_class(eid).listed:
                self.effect.removeItem(i)
        self.effect.setCurrentIndex(max(self.effect.findData(spec.effect), 0))
        self.effect.blockSignals(False)
        for p in cls.params:
            editor = self.editors.get(p.id)
            if editor is not None:
                editor.set_value(spec.params.get(p.id, p.default))
        self._sync_mask(spec.mask)

    def set_notice(self, text: str) -> None:
        self.notice.setText(text)
        self.notice.setVisible(bool(text))

    def _build_params(self, effect_id: str) -> None:
        while self._params_box.count():
            item = self._params_box.takeAt(0)
            layout = item.layout()
            if layout is not None:
                while layout.count():
                    w = layout.takeAt(0).widget()
                    if w is not None:
                        w.setParent(None)
                        w.deleteLater()
        self.editors = {}
        for p in get_effect_class(effect_id).params:
            if p.hidden:
                continue
            editor = make_editor(p)
            guard_wheel(*(w for w in editor.findChildren(QWidget) if isinstance(w, (QComboBox, QSlider))))
            editor.value_changed.connect(lambda v, pid=p.id: self.param_changed.emit(pid, v))
            self.editors[p.id] = editor
            self._params_box.addLayout(self._field(p.label, editor))

    def _sync_mask(self, mask) -> None:
        self.mask.blockSignals(True)
        self.mask.clear()
        self.mask.addItem("All LEDs", "all")
        for name in self.group.mask_choices():
            self.mask.addItem(name, name)
        if mask == "all":
            self.mask.setCurrentIndex(0)
        else:
            names = [mask] if isinstance(mask, str) else list(mask)
            idx = self.mask.findData(names[0]) if len(names) == 1 and isinstance(names[0], str) else -1
            if idx < 0:  # a hand-picked set of LEDs (paint/select, M4)
                count = int(self.group.resolve_mask(mask).sum())
                self.mask.addItem(f"Custom ({count} LEDs)", None)
                idx = self.mask.count() - 1
            self.mask.setCurrentIndex(idx)
        self.mask.blockSignals(False)

    def _mask_picked(self, _i: int) -> None:
        data = self.mask.currentData()
        if data is None:
            return
        self.mask_changed.emit("all" if data == "all" else [data])


# --------------------------------------------------------------------------- panel


class LayersPanel(QFrame):
    selection_changed = Signal(object)  # LayerSpec of the selected layer, or None

    def __init__(self, bridge, group: DeviceGroup, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.group = group
        self.key = group.key
        self.setObjectName("RightPanel")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFixedWidth(PANEL_WIDTH)
        self.rows: dict[str, LayerRow] = {}
        self.order: list[str] = []  # layer ids, top first (display order)
        self.selected: Optional[str] = None
        self._structure: tuple = ()
        self._drag: Optional[str] = None

        col = QVBoxLayout(self)
        col.setContentsMargins(T.unit * 2, T.unit * 2, T.unit * 2, T.unit * 2)
        col.setSpacing(T.unit * 1.5)

        head = QHBoxLayout()
        title = QLabel("Layers")
        title.setObjectName("PanelTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.add_button = QPushButton("+ Add effect")
        self.add_button.setObjectName("AccentButton")
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.clicked.connect(self.open_library)
        head.addWidget(self.add_button)
        col.addLayout(head)

        self.list = LayerList()
        self.list.key_action.connect(self._key_action)
        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QFrame.NoFrame)
        self.list_scroll.setWidget(self.list)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        col.addWidget(self.list_scroll)
        self.empty = QLabel("No layers. Add an effect to light this device.")
        self.empty.setObjectName("Caption")
        self.empty.setWordWrap(True)
        col.addWidget(self.empty)

        rule = QFrame()
        rule.setObjectName("Rule")
        rule.setFixedHeight(1)
        col.addWidget(rule)

        self.props_title = QLabel("Properties")
        self.props_title.setObjectName("PanelTitle")
        col.addWidget(self.props_title)
        self.props = LayerProperties(group)
        self.props.effect_changed.connect(lambda eid: self._edit(self.bridge.set_layer_effect, eid))
        self.props.param_changed.connect(lambda pid, v: self._edit(self.bridge.set_layer_param, pid, v))
        self.props.mask_changed.connect(lambda m: self._edit_props(mask=m))
        self.props_scroll = QScrollArea()
        self.props_scroll.setWidgetResizable(True)
        self.props_scroll.setFrameShape(QFrame.NoFrame)
        self.props_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.props_scroll.setWidget(self.props)
        self.props.setMaximumWidth(PANEL_WIDTH - T.unit * 4 - 12)  # room for the scrollbar
        col.addWidget(self.props_scroll, 1)

        bridge.settings_changed.connect(self._on_settings_changed)
        bridge.notices_changed.connect(lambda _n: self._show_notice())
        self.refresh()

    # -- reading the stack

    def _on_settings_changed(self, key: str) -> None:
        if key == self.key:
            self.refresh()

    def refresh(self) -> None:
        specs = self.bridge.layers(self.key)
        by_id = {s.id: s for s in specs}
        order = [s.id for s in reversed(specs)]
        structure = tuple(order)
        if structure != self._structure:
            self._rebuild_rows(order, by_id)
            self._structure = structure
        for lid, row in self.rows.items():
            row.set_spec(by_id[lid])
        if self.selected not in by_id:
            self.selected = order[0] if order else None
        self._show_selection(by_id)

    def _rebuild_rows(self, order: list[str], by_id: dict) -> None:
        for lid in list(self.rows):
            if lid not in by_id:
                row = self.rows.pop(lid)
                row.setParent(None)
                row.deleteLater()
        for lid in order:
            if lid not in self.rows:
                self.rows[lid] = self._make_row(by_id[lid])
        for i, lid in enumerate(order):  # re-insert in display order (above the stretch)
            self.list.col.removeWidget(self.rows[lid])
            self.list.col.insertWidget(i, self.rows[lid])
        self.order = order
        self.empty.setVisible(not order)
        self.list_scroll.setVisible(bool(order))
        self._fit_list()

    def _fit_list(self) -> None:
        """Show every row up to LIST_MAX_HEIGHT, then scroll."""
        self.list.adjustSize()
        rows = [self.rows[lid] for lid in self.order]
        needed = sum(r.sizeHint().height() for r in rows) + self.list.col.spacing() * max(len(rows) - 1, 0)
        self.list_scroll.setFixedHeight(min(needed + 4, LIST_MAX_HEIGHT))

    def _make_row(self, spec: LayerSpec) -> LayerRow:
        row = LayerRow(spec)
        lid = spec.id
        row.clicked.connect(self.select)
        row.eye.toggled.connect(lambda on: self._edit_props(layer_id=lid, visible=on))
        row.blend.currentIndexChanged.connect(lambda _i: self._edit_props(layer_id=lid, blend=row.blend.currentData()))
        row.opacity.value_changed.connect(lambda v: self._edit_props(layer_id=lid, opacity=v))
        row.delete.clicked.connect(lambda: self.remove(lid))
        row.handle.pressed.connect(lambda: self._drag_start(lid))
        row.handle.installEventFilter(self)
        return row

    def _show_notice(self) -> None:
        self.props.set_notice(self.bridge.notice(self.key, self.selected) if self.selected else "")

    def _show_selection(self, by_id: dict) -> None:
        for lid, row in self.rows.items():
            row.set_selected(lid == self.selected)
        spec = by_id.get(self.selected) if self.selected else None
        self.props.setVisible(spec is not None)
        self.props_title.setText(f"{effect_name(spec.effect)} properties" if spec else "Properties")
        if spec is not None:
            self.props.show_layer(spec)
        self._show_notice()
        self.selection_changed.emit(spec)

    # -- actions

    def select(self, layer_id: str) -> None:
        if layer_id != self.selected and layer_id in self.rows:
            self.selected = layer_id
            self._show_selection({s.id: s for s in self.bridge.layers(self.key)})

    def selected_spec(self) -> Optional[LayerSpec]:
        return next((s for s in self.bridge.layers(self.key) if s.id == self.selected), None)

    def add(self, effect_id: str) -> str:
        layer_id = self.bridge.add_layer(self.key, effect_id)
        self.select(layer_id)
        return layer_id

    def remove(self, layer_id: str) -> None:
        if layer_id not in self.order:
            return
        i = self.order.index(layer_id)
        rest = [lid for lid in self.order if lid != layer_id]
        if self.selected == layer_id:  # select the neighbour below, else above
            self.selected = rest[min(i, len(rest) - 1)] if rest else None
        self.bridge.remove_layer(self.key, layer_id)

    def move(self, layer_id: str, display_index: int) -> None:
        n = len(self.order)
        display_index = max(0, min(display_index, n - 1))
        self.bridge.move_layer(self.key, layer_id, n - 1 - display_index)

    def open_library(self) -> None:
        dlg = EffectLibrary(self.window())
        if dlg.exec() and dlg.chosen:
            self.add(dlg.chosen)
            self.list.setFocus(Qt.OtherFocusReason)

    def _edit(self, setter, *args) -> None:
        if self.selected:
            setter(self.key, self.selected, *args)

    def _edit_props(self, layer_id: Optional[str] = None, **props) -> None:
        lid = layer_id or self.selected
        if lid:
            self.bridge.set_layer_props(self.key, lid, **props)

    def _key_action(self, action: str) -> None:
        if not self.order:
            return
        i = self.order.index(self.selected) if self.selected in self.order else 0
        lid = self.order[i]
        if action == "up":
            self.select(self.order[max(i - 1, 0)])
        elif action == "down":
            self.select(self.order[min(i + 1, len(self.order) - 1)])
        elif action == "toggle":
            row = self.rows[lid]
            self._edit_props(layer_id=lid, visible=not row.eye.isChecked())
        elif action == "delete":
            self.remove(lid)
        elif action == "move_up":
            self.move(lid, i - 1)
        elif action == "move_down":
            self.move(lid, i + 1)
        self._ensure_visible()

    def _ensure_visible(self) -> None:
        row = self.rows.get(self.selected or "")
        if row is not None:
            self.list_scroll.ensureWidgetVisible(row)

    # -- drag to reorder (live in the list, committed on release)

    def _drag_start(self, layer_id: str) -> None:
        self._drag = layer_id
        self.select(layer_id)

    def eventFilter(self, obj, e) -> bool:
        if self._drag is None:
            return False
        t = e.type()
        if t == e.Type.MouseMove:
            self._drag_to(self.list.mapFromGlobal(e.globalPosition().toPoint()).y())
            return True
        if t == e.Type.MouseButtonRelease:
            lid, self._drag = self._drag, None
            target = [self.list.col.indexOf(self.rows[x]) for x in self.order]
            display = sorted(self.order, key=lambda x: target[self.order.index(x)])
            new_index = display.index(lid)
            if new_index != self.order.index(lid):
                self.move(lid, new_index)
            return False
        return False

    def _drag_to(self, y: int) -> None:
        row = self.rows[self._drag]
        others = [self.rows[x] for x in self.order if x != self._drag]
        others.sort(key=lambda r: self.list.col.indexOf(r))
        index = sum(1 for r in others if r.geometry().center().y() < y)
        if self.list.col.indexOf(row) != index:
            self.list.col.removeWidget(row)
            self.list.col.insertWidget(index, row)
