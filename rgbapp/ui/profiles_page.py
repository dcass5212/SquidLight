"""Profiles page: the profile list with its actions, and the auto-switch rules editor.

Also home of ``confirm_switch`` (shared with the header and tray): switching profiles throws
away unsaved edits, so it asks first.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..engine.rules import Rule, running_apps
from .theme import T

PAGE_MAX_WIDTH = 860
LIST_ROW_PX = 38  # one profile row, for sizing the list to its content


def confirm_switch(bridge, parent=None) -> bool:
    """True when it is fine to replace the live lighting: nothing unsaved, or the user chose
    to save or discard it. False means they cancelled."""
    if not bridge.dirty:
        return True
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle("Unsaved changes")
    box.setText(f"Save your changes to “{bridge.profiles.active_name}” first?")
    box.setInformativeText("Switching profile otherwise discards them.")
    save = box.addButton("Save", QMessageBox.AcceptRole)
    discard = box.addButton("Discard", QMessageBox.DestructiveRole)
    box.addButton(QMessageBox.Cancel)
    box.setDefaultButton(save)
    box.exec()
    clicked = box.clickedButton()
    if clicked is save:
        bridge.save_profile()
        return True
    return clicked is discard


def switch_profile(bridge, profile_id: str, parent=None) -> bool:
    """User-initiated switch: confirm, then activate. Returns whether it switched."""
    if profile_id == bridge.profiles.active_id or not confirm_switch(bridge, parent):
        return False
    bridge.activate_profile(profile_id)
    return True


class ProcessPicker(QDialog):
    """Pick a running program (or type a process name)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose a program")
        self.setMinimumSize(360, 440)
        col = QVBoxLayout(self)
        hint = QLabel("Pick a running program, or type its process name (for example game.exe).")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        col.addWidget(hint)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Search or type a name")
        self.filter.setAccessibleName("Program name")
        col.addWidget(self.filter)
        self.list = QListWidget()
        self.list.setAccessibleName("Running programs")
        col.addWidget(self.list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)
        self._apps = running_apps()
        self.filter.textChanged.connect(self._refill)
        self.list.currentTextChanged.connect(self._picked)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        self._refill("")

    def _refill(self, text: str) -> None:
        needle = text.strip().casefold()
        self.list.clear()
        for name in self._apps:
            if needle in name:
                self.list.addItem(name)

    def _picked(self, name: str) -> None:
        if name:
            self.filter.blockSignals(True)
            self.filter.setText(name)
            self.filter.blockSignals(False)

    def chosen(self) -> str:
        return self.filter.text().strip()


class RuleRow(QFrame):
    changed = Signal()
    remove_requested = Signal(object)
    move_requested = Signal(object, int)

    def __init__(self, rule: Rule, profiles, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("RuleRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, T.unit, 0, T.unit)
        row.setSpacing(T.unit)
        self.match = QLineEdit(rule.match)
        self.match.setAccessibleName("Program")
        self.match.setPlaceholderText("program.exe")
        self.match.editingFinished.connect(self.changed)
        pick = QPushButton("Pick…")
        pick.setToolTip("Choose from running programs")
        pick.clicked.connect(self._pick)
        arrow = QLabel("→")
        arrow.setObjectName("Muted")
        self.profile = QComboBox()
        self.profile.setAccessibleName("Profile for this program")
        for p in profiles:
            self.profile.addItem(p.name, p.id)
        index = self.profile.findData(rule.profile_id)
        self.profile.setCurrentIndex(max(index, 0))
        self.profile.currentIndexChanged.connect(lambda _: self.changed.emit())
        self.focused = QCheckBox("Only when in front")
        self.focused.setChecked(rule.focused)
        self.focused.setToolTip("Off: applies while the program is running. On: only while its window is focused.")
        self.focused.toggled.connect(lambda _: self.changed.emit())
        up, down, remove = QPushButton("↑"), QPushButton("↓"), QPushButton("✕")
        for b, tip in ((up, "Higher priority"), (down, "Lower priority"), (remove, "Remove rule")):
            b.setObjectName("SmallButton")
            b.setFixedSize(28, 28)
            b.setToolTip(tip)
            b.setAccessibleName(tip)
        up.clicked.connect(lambda: self.move_requested.emit(self, -1))
        down.clicked.connect(lambda: self.move_requested.emit(self, 1))
        remove.clicked.connect(lambda: self.remove_requested.emit(self))
        row.addWidget(self.match, 2)
        row.addWidget(pick)
        row.addWidget(arrow)
        row.addWidget(self.profile, 2)
        row.addWidget(self.focused)
        row.addWidget(up)
        row.addWidget(down)
        row.addWidget(remove)

    def _pick(self) -> None:
        dlg = ProcessPicker(self)
        if dlg.exec() and dlg.chosen():
            self.match.setText(dlg.chosen())
            self.changed.emit()

    def rule(self) -> Rule:
        return Rule(self.match.text().strip(), self.profile.currentData() or "", self.focused.isChecked())


class ProfilesPage(QWidget):
    def __init__(self, bridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        holder = QWidget()
        scroll.setWidget(holder)
        hrow = QHBoxLayout(holder)
        hrow.setContentsMargins(T.unit * 4, T.unit * 3, T.unit * 3, T.unit * 5)
        page = QWidget()
        page.setMaximumWidth(PAGE_MAX_WIDTH)
        hrow.addWidget(page, 1)
        hrow.addStretch(0)  # left-aligned like Settings once the window is wider than the page
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(T.unit)

        # -- profiles
        title = QLabel("Your profiles")
        title.setObjectName("SectionTitle")
        col.addWidget(title)
        self.hint = QLabel("")
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)
        col.addWidget(self.hint)
        col.addSpacing(T.unit)

        box = QFrame()
        box.setObjectName("Group")
        pcol = QVBoxLayout(box)
        pcol.setContentsMargins(T.unit * 2, T.unit * 2, T.unit * 2, T.unit * 2)
        pcol.setSpacing(T.unit * 1.5)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(T.unit)
        self.btn = {}
        for key, text, slot, tip in (
            ("activate", "Use this profile", self._activate, "Switch your lighting to the selected profile"),
            ("new", "New from current", self._new, "Save what's lit now as a new profile"),
            ("duplicate", "Duplicate", self._duplicate, "Copy the selected profile"),
            ("rename", "Rename…", self._rename, "Rename the selected profile"),
            ("default", "Make default", self._make_default, "Use the selected profile whenever no rule matches"),
            ("import", "Import…", self._import, "Add a profile from a .json file"),
            ("export", "Export…", self._export, "Save the selected profile to a .json file"),
            ("delete", "Delete", self._delete, "Delete the selected profile"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            self.btn[key] = b
        self.btn["activate"].setObjectName("AccentButton")
        for key in ("import", "export"):
            self.btn[key].setObjectName("GhostButton")
        self.btn["delete"].setObjectName("DangerButton")
        for key in ("activate", "new", "duplicate", "rename", "default"):
            toolbar.addWidget(self.btn[key])
        toolbar.addStretch(1)
        pcol.addLayout(toolbar)

        self.list = QListWidget()
        self.list.setAccessibleName("Profiles")
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.itemDoubleClicked.connect(lambda _: self._activate())
        self.list.currentItemChanged.connect(lambda *_: self._update_buttons())
        pcol.addWidget(self.list)
        footer = QHBoxLayout()
        footer.setSpacing(4)
        tip = QLabel("Double-click a profile to switch to it.")
        tip.setObjectName("Caption")
        footer.addWidget(tip, 1)
        for key in ("import", "export", "delete"):
            footer.addWidget(self.btn[key])
        pcol.addLayout(footer)
        col.addWidget(box)
        col.addSpacing(T.unit * 4)

        # -- rules
        rtitle = QLabel("Auto-switch rules")
        rtitle.setObjectName("SectionTitle")
        rhelp = QLabel(
            "When a program is running, SquidLight switches to its profile, and goes back to the "
            "default profile when it closes. The first matching rule wins."
        )
        rhelp.setObjectName("Muted")
        rhelp.setWordWrap(True)
        col.addWidget(rtitle)
        col.addWidget(rhelp)
        col.addSpacing(T.unit)
        rbox = QFrame()
        rbox.setObjectName("Group")
        rcol = QVBoxLayout(rbox)
        rcol.setContentsMargins(T.unit * 2, T.unit, T.unit * 2, T.unit * 2)
        rcol.setSpacing(T.unit)
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(0)
        rcol.addLayout(self.rows_layout)
        self.no_rules = QLabel("No rules yet. Add one to switch profiles when a game or app starts.")
        self.no_rules.setObjectName("Muted")
        self.no_rules.setWordWrap(True)
        self.no_rules.setContentsMargins(0, T.unit, 0, 0)
        rcol.addWidget(self.no_rules)
        add = QHBoxLayout()
        self.add_rule_btn = QPushButton("+ Add rule")
        self.add_rule_btn.setCursor(Qt.PointingHandCursor)
        self.add_rule_btn.clicked.connect(self._add_rule)
        add.addWidget(self.add_rule_btn)
        add.addStretch(1)
        rcol.addSpacing(T.unit / 2)
        rcol.addLayout(add)
        col.addWidget(rbox)
        col.addStretch(1)

        self.rows: list[RuleRow] = []
        self._building = False
        bridge.profiles_changed.connect(self.refresh)
        bridge.dirty_changed.connect(lambda _: self._refresh_list())
        self.refresh()

    # -- view

    def refresh(self) -> None:
        self._refresh_list()
        self._rebuild_rules()

    def _refresh_list(self) -> None:
        mgr = self.bridge.profiles
        keep = self.selected_id() or mgr.active_id
        self.list.blockSignals(True)
        self.list.clear()
        for p in mgr.profiles:
            tags = []
            if p.id == mgr.active_id:
                tags.append("active, unsaved changes" if self.bridge.dirty else "active")
            if p.is_default:
                tags.append("default")
            item = QListWidgetItem(p.name + (f"    ({', '.join(tags)})" if tags else ""))
            item.setData(Qt.UserRole, p.id)
            if p.id == mgr.active_id:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            self.list.addItem(item)
            if p.id == keep:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        # as tall as its rows (between 3 and 7 of them), not a big empty box
        row_h = self.list.sizeHintForRow(0) if self.list.count() else LIST_ROW_PX
        self.list.setFixedHeight(min(max(self.list.count(), 2), 7) * max(row_h, 1) + 10)
        self.hint.setText(
            "Everything you change is live. Save keeps it in the active profile; switching "
            "profiles discards unsaved changes."
        )
        self._update_buttons()

    def _select(self, profile_id: str) -> None:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == profile_id:
                self.list.setCurrentRow(i)
                return

    def selected_id(self) -> str:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else ""

    def _update_buttons(self) -> None:
        mgr = self.bridge.profiles
        sel = self.selected_id()
        have = bool(sel)
        self.btn["activate"].setEnabled(have and sel != mgr.active_id)
        for k in ("duplicate", "rename", "export"):
            self.btn[k].setEnabled(have)
        self.btn["default"].setEnabled(have and sel != mgr.default_id)
        self.btn["delete"].setEnabled(have and len(mgr.profiles) > 1)

    # -- profile actions

    def _activate(self) -> None:
        pid = self.selected_id()
        if pid:
            switch_profile(self.bridge, pid, self)

    def _ask_name(self, title: str, label: str, text: str = "") -> str:
        name, ok = QInputDialog.getText(self, title, label, text=text)
        return name.strip() if ok else ""

    def _guard(self, fn, *args):
        try:
            return fn(*args)
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Profiles", str(exc).strip("'\""))
            return None

    def _new(self) -> None:
        name = self._ask_name("New profile", "Name for a profile that starts from your current lighting:")
        if name:  # the current edits move into the new profile, so nothing is discarded
            pid = self._guard(self.bridge.create_profile, name)
            if pid:
                self._select(pid)

    def _duplicate(self) -> None:
        pid = self.selected_id()
        if pid:
            self._guard(self.bridge.duplicate_profile, pid)

    def _rename(self) -> None:
        pid = self.selected_id()
        if not pid:
            return
        name = self._ask_name("Rename profile", "New name:", self.bridge.profiles.name_of(pid))
        if name:
            self._guard(self.bridge.rename_profile, pid, name)

    def _make_default(self) -> None:
        pid = self.selected_id()
        if pid:
            self.bridge.set_default_profile(pid)

    def _delete(self) -> None:
        pid = self.selected_id()
        if not pid:
            return
        name = self.bridge.profiles.name_of(pid)
        if QMessageBox.question(self, "Delete profile", f"Delete “{name}”? This can't be undone.") == QMessageBox.Yes:
            self._guard(self.bridge.delete_profile, pid)

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import profile", "", "SquidLight profile (*.json)")
        if path:
            self._guard(self.bridge.import_profile, path)

    def _export(self) -> None:
        pid = self.selected_id()
        if not pid:
            return
        suggested = self.bridge.profiles.name_of(pid).replace("/", "-").replace("\\", "-") + ".json"
        path, _ = QFileDialog.getSaveFileName(self, "Export profile", suggested, "SquidLight profile (*.json)")
        if path:
            try:
                self.bridge.export_profile(pid, path)
            except OSError as exc:
                QMessageBox.warning(self, "Export profile", f"Could not write the file: {exc}")

    # -- rules

    def _rebuild_rules(self) -> None:
        self._building = True
        for row in self.rows:
            self.rows_layout.removeWidget(row)
            row.deleteLater()
        self.rows = []
        profiles = self.bridge.profiles.profiles
        for rule in self.bridge.profiles.rules:
            self._append_row(RuleRow(rule, profiles))
        self.no_rules.setVisible(not self.rows)
        self._building = False

    def _append_row(self, row: RuleRow) -> None:
        row.changed.connect(self._save_rules)
        row.remove_requested.connect(self._remove_row)
        row.move_requested.connect(self._move_row)
        self.rows.append(row)
        self.rows_layout.addWidget(row)
        self.no_rules.setVisible(False)

    def _add_rule(self) -> None:
        dlg = ProcessPicker(self)
        if not (dlg.exec() and dlg.chosen()):
            return
        mgr = self.bridge.profiles
        rules = mgr.rules + [Rule(dlg.chosen(), mgr.active_id)]
        self.bridge.set_rules(rules)

    def _save_rules(self) -> None:
        if not self._building:
            self.bridge.set_rules([r.rule() for r in self.rows])

    def _remove_row(self, row: RuleRow) -> None:
        self.rows.remove(row)
        self.bridge.set_rules([r.rule() for r in self.rows])

    def _move_row(self, row: RuleRow, delta: int) -> None:
        i = self.rows.index(row)
        j = i + delta
        if 0 <= j < len(self.rows):
            self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
            self.bridge.set_rules([r.rule() for r in self.rows])
