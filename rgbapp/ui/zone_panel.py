"""Zones of a non-keyboard device group: pick a zone, set its ARGB LED count, give it a
color or an effect of its own, and sync the whole group to the keyboard (docs/02).

A zone's color or effect is an ordinary layer whose mask is that zone, so it shows up in
the layers panel and stacks like any other layer. Nothing here touches a device: counts go
to the bridge, which hands them to the engine's worker thread.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..engine.devices import DeviceGroup, ZoneRef
from ..engine.effects import listed_effects
from .theme import T
from .widgets import ColorSwatch, Toggle, guard_wheel


class _ZoneRow(QFrame):
    """One zone: name and size, an LED-count editor when it can be resized, color and effect."""

    selected = Signal(str)  # zone mask name

    def __init__(self, bridge, key: str, ref: ZoneRef, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ZoneRow")
        self.setProperty("selected", False)
        self.setAttribute(Qt.WA_StyledBackground)
        self.bridge, self.key, self.ref = bridge, key, ref

        # A resizable header gets two lines (name, then its controls) so it fits narrow windows
        col = QVBoxLayout(self)
        col.setContentsMargins(T.unit / 2, T.unit / 2, T.unit, T.unit / 2)
        col.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(T.unit)
        col.addLayout(top)
        if ref.info.resizable:
            row = QHBoxLayout()
            row.setContentsMargins(T.unit, 0, 0, T.unit / 2)
            row.setSpacing(T.unit)
            col.addLayout(row)
        else:
            row = top

        self.pick = QPushButton(ref.label)
        self.pick.setObjectName("ZonePick")
        self.pick.setCheckable(True)
        self.pick.setCursor(Qt.PointingHandCursor)
        self.pick.setToolTip("Select this zone: it is outlined in the preview")
        self.pick.setAccessibleName(f"Select {ref.label}")
        self.pick.clicked.connect(lambda _c=False: self.selected.emit(ref.name))
        top.addWidget(self.pick)

        self.count = QLabel()
        self.count.setObjectName("Caption")
        top.addWidget(self.count)
        top.addStretch(1)

        self.size_edit: Optional[QSpinBox] = None
        self.apply_btn: Optional[QPushButton] = None
        if ref.info.resizable:
            self.size_edit = QSpinBox()
            self.size_edit.setRange(ref.info.min_size, ref.info.max_size)
            self.size_edit.setAccessibleName(f"{ref.label} LED count")
            self.size_edit.setToolTip(
                f"How many LEDs are on this header ({ref.info.min_size} to {ref.info.max_size}). "
                "OpenRGB cannot detect this for addressable headers."
            )
            self.size_edit.setValue(ref.info.size)
            self.size_edit.setFixedWidth(72)
            self.size_edit.setAlignment(Qt.AlignRight)
            guard_wheel(self.size_edit)
            self.size_edit.valueChanged.connect(self._size_typed)
            self.size_edit.lineEdit().returnPressed.connect(self._apply_size)
            size_label = QLabel("LED count")
            size_label.setObjectName("FieldLabel")
            size_label.setBuddy(self.size_edit)
            row.addWidget(size_label)
            row.addWidget(self.size_edit)
            self.apply_btn = QPushButton("Set")
            self.apply_btn.setAccessibleName(f"Apply {ref.label} LED count")
            self.apply_btn.setToolTip("Apply the LED count")
            self.apply_btn.setEnabled(False)
            self.apply_btn.clicked.connect(self._apply_size)
            row.addWidget(self.apply_btn)
            row.addStretch(1)

        self.swatch = ColorSwatch(f"{ref.label} color")
        self.swatch.setToolTip(f"Light {ref.label} one color")
        self.swatch.color_changed.connect(self._color_changed)
        row.addWidget(self.swatch)

        self.effect_btn = QPushButton("Add effect")
        self.effect_btn.setObjectName("MenuButton")
        self.effect_btn.setCursor(Qt.PointingHandCursor)
        self.effect_btn.setAccessibleName(f"{ref.label} effect")
        self.effect_btn.setToolTip("Add an effect layer that applies to this zone only")
        menu = QMenu(self.effect_btn)
        for cls in listed_effects():
            menu.addAction(cls.name, lambda cid=cls.id: self._add_effect(cid))
        self.effect_btn.setMenu(menu)
        row.addWidget(self.effect_btn)

        self.set_ref(ref)

    # -- state

    def set_ref(self, ref: ZoneRef) -> None:
        self.ref = ref
        n = ref.info.size
        self.count.setText("No LEDs set" if n == 0 and ref.info.resizable else f"{n} LED{'s' if n != 1 else ''}")
        empty = n == 0
        self.swatch.setEnabled(not empty)
        self.effect_btn.setEnabled(not empty)
        tip = "Set the LED count first" if empty else ""
        if empty:
            self.swatch.setToolTip(tip)
            self.effect_btn.setToolTip(tip)

    def set_selected(self, on: bool) -> None:
        self.pick.setChecked(on)
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_actions_enabled(self, on: bool) -> None:
        for w in (self.swatch, self.effect_btn):
            w.setEnabled(on and self.ref.info.size > 0)

    def refresh_color(self, layers) -> None:
        layer = self._color_layer(layers)
        if layer is not None:
            self.swatch.blockSignals(True)
            self.swatch.set_color(str(layer.params.get("color", "#ffffff")))
            self.swatch.blockSignals(False)

    # -- size

    def _size_typed(self, value: int) -> None:
        self.apply_btn.setEnabled(value != self.ref.info.size)

    def _apply_size(self) -> None:
        if self.size_edit is None or self.size_edit.value() == self.ref.info.size:
            return
        self.apply_btn.setEnabled(False)
        self.bridge.set_zone_size(self.ref.device_id, self.ref.device_zone, self.size_edit.value())

    # -- color / effect layers

    def _color_layer(self, layers):
        """The zone's own plain-color layer, if it has one (the newest, if several)."""
        found = [l for l in layers if l.effect == "static" and list(_names(l.mask)) == [self.ref.name]]
        return found[-1] if found else None

    def _color_changed(self, hex_: str) -> None:
        layer = self._color_layer(self.bridge.layers(self.key))
        if layer is None:
            layer_id = self.bridge.add_layer(self.key, "static", {"color": hex_})
            self.bridge.set_layer_props(self.key, layer_id, mask=[self.ref.name])
        else:
            self.bridge.set_layer_param(self.key, layer.id, "color", hex_)

    def _add_effect(self, effect_id: str) -> None:
        layer_id = self.bridge.add_layer(self.key, effect_id)
        self.bridge.set_layer_props(self.key, layer_id, mask=[self.ref.name])
        self.layer_added.emit(layer_id)

    layer_added = Signal(str)


def _names(mask):
    return [mask] if isinstance(mask, str) else list(mask)


class ZonePanel(QFrame):
    """Under the preview of Memory and Mainboard (and any other zoned device)."""

    zone_selected = Signal(object)  # ZoneRef, or None when cleared
    layer_added = Signal(str)
    sync_changed = Signal(bool)

    def __init__(self, bridge, group: DeviceGroup, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.bridge, self.group, self.key = bridge, group, group.key
        self.rows: dict[str, _ZoneRow] = {}
        self._selected: Optional[str] = None

        col = QVBoxLayout(self)
        col.setContentsMargins(T.unit * 2, T.unit * 2, T.unit * 2, T.unit * 2)
        col.setSpacing(T.unit / 2)

        title = QLabel("Zones")
        title.setObjectName("CardTitle")
        col.addWidget(title)

        self.hint = QLabel()
        self.hint.setObjectName("Caption")
        self.hint.setWordWrap(True)
        col.addWidget(self.hint)
        col.addSpacing(T.unit)

        self.empty_note = QLabel(
            "OpenRGB can't tell how many LEDs addressable headers drive. Enter the total for each "
            "header that has fans or strips plugged in, then press Set."
        )
        self.empty_note.setObjectName("EmptyBody")
        self.empty_note.setWordWrap(True)
        self.empty_note.setVisible(group.led_count == 0)
        col.addWidget(self.empty_note)

        for ref in group.zone_refs:
            row = _ZoneRow(bridge, self.key, ref)
            row.selected.connect(self._row_selected)
            row.layer_added.connect(self.layer_added)
            self.rows[ref.name] = row
            col.addWidget(row)

        self.sync_row = QWidget()
        sync_col = QVBoxLayout(self.sync_row)
        sync_col.setContentsMargins(0, T.unit, 0, 0)
        sync_col.setSpacing(T.unit * 1.5)
        rule = QFrame()
        rule.setObjectName("Rule")
        rule.setFixedHeight(1)
        sync_col.addWidget(rule)
        sync = QHBoxLayout()
        sync.setContentsMargins(T.unit, 0, T.unit, 0)
        sync.setSpacing(T.unit * 2)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        label = QLabel("Sync with keyboard")
        label.setObjectName("SettingName")
        self.sync_note = QLabel()
        self.sync_note.setObjectName("Caption")
        self.sync_note.setWordWrap(True)
        texts.addWidget(label)
        texts.addWidget(self.sync_note)
        self.sync = Toggle()
        self.sync.setAccessibleName("Sync with keyboard")
        self.sync.setToolTip("Mirror the keyboard's top effect onto this device")
        sync.addLayout(texts, 1)
        sync.addWidget(self.sync, 0, Qt.AlignVCenter)
        sync_col.addLayout(sync)
        col.addWidget(self.sync_row)
        self.sync_row.setVisible(self.key != "keyboard")
        self.sync.toggled.connect(self._sync_toggled)

        bridge.settings_changed.connect(self._on_settings_changed)
        self.refresh()

    # -- selection

    def _row_selected(self, name: str) -> None:
        self.select(None if name == self._selected else name)

    def select(self, name: Optional[str]) -> None:
        self._selected = name if name in self.rows else None
        for n, row in self.rows.items():
            row.set_selected(n == self._selected)
        self.zone_selected.emit(self.selected_ref())

    def selected_ref(self) -> Optional[ZoneRef]:
        return self.rows[self._selected].ref if self._selected else None

    # -- state

    def _on_settings_changed(self, key: str) -> None:
        if key == self.key:
            self.refresh()

    def refresh(self) -> None:
        settings = self.bridge.settings(self.key)
        layers = self.bridge.layers(self.key)
        synced = settings.sync_keyboard
        self.sync.blockSignals(True)
        self.sync.setChecked(synced)
        self.sync.blockSignals(False)
        self.sync_note.setText(
            "Following the keyboard's top effect. Turn off to edit this device's own layers."
            if synced
            else "Mirror the keyboard's top effect here instead of this device's own layers."
        )
        for row in self.rows.values():
            row.set_actions_enabled(not synced)
            row.refresh_color(layers)
        self.hint.setText(
            "Pick a color or add an effect for one zone; each becomes a layer you can edit on the right."
            if not synced
            else ""
        )
        self.hint.setVisible(not synced)

    def _sync_toggled(self, on: bool) -> None:
        self.bridge.set_sync_keyboard(self.key, on)
        self.sync_changed.emit(on)
