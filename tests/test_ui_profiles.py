"""Profiles UI: header Save/Revert, switching with the unsaved-changes prompt, the profiles
page, rules, and the tray submenu. Runs against FakeBackend (profiles live in memory)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QInputDialog, QMessageBox  # noqa: E402

from rgbapp.app import App  # noqa: E402
from rgbapp.engine import Engine, EngineHost, FakeBackend  # noqa: E402
from rgbapp.engine.rules import Rule  # noqa: E402
from rgbapp.ui import profiles_page  # noqa: E402


@pytest.fixture
def app(qtbot, qapp):
    host = EngineHost(Engine(FakeBackend()))
    a = App(qapp, host)
    a.tray_available = a.window.tray_available = True
    qtbot.addWidget(a.window)
    a.start()
    assert host.wait_running(3.0)
    a.bridge.poll_status()
    yield a
    a.bridge.stop()
    a.window.allow_close = True
    host.stop()


def edit(app, effect="static"):
    app.bridge.set_effect("keyboard", effect)
    app.bridge.poll_status()


def answer(monkeypatch, label):
    """Click the named button of the Save / Discard / Cancel prompt without showing it."""

    def fake_exec(self):
        self._clicked = next(b for b in self.buttons() if b.text().replace("&", "") == label)

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self._clicked)


def test_header_shows_unsaved_state_with_save_and_revert(app):
    w = app.window
    assert w.profile.currentText() == "Default"
    assert not w.save_btn.isVisibleTo(w) and not w.unsaved.isVisibleTo(w)
    edit(app)
    assert w.unsaved.isVisibleTo(w) and w.save_btn.isVisibleTo(w) and w.revert_btn.isVisibleTo(w)
    w.save_btn.click()
    assert not w.unsaved.isVisibleTo(w)
    edit(app, "breathing")
    assert w.unsaved.isVisibleTo(w)
    w.revert_btn.click()
    assert app.bridge.settings("keyboard").effect == "static"  # back to what was saved
    assert not w.unsaved.isVisibleTo(w)


def test_switching_with_unsaved_changes_asks_first(app, monkeypatch):
    other = app.bridge.create_profile("Other")
    default = app.bridge.profiles.default_id
    edit(app)  # unsaved on "Other"
    w = app.window

    answer(monkeypatch, "Cancel")
    assert not profiles_page.switch_profile(app.bridge, default, w)
    assert app.bridge.profiles.active_id == other and app.bridge.settings("keyboard").effect == "static"

    answer(monkeypatch, "Discard")
    assert profiles_page.switch_profile(app.bridge, default, w)
    assert app.bridge.profiles.active_id == default and w.profile.currentData() == default
    assert app.bridge.settings("keyboard").effect == "rainbow_wave"

    app.bridge.activate_profile(other)
    assert app.bridge.settings("keyboard").effect == "rainbow_wave"  # the discarded edit is gone
    edit(app)
    answer(monkeypatch, "Save")
    assert profiles_page.switch_profile(app.bridge, default, w)
    app.bridge.activate_profile(other)
    assert app.bridge.settings("keyboard").effect == "static"  # saved on the way out


def test_switching_rebuilds_the_dashboard(app):
    other = app.bridge.create_profile("Other")
    edit(app)
    app.bridge.save_profile()
    app.bridge.activate_profile(app.bridge.profiles.default_id)
    assert app.window.dashboard.cards["keyboard"].controls.effect.currentData() == "rainbow_wave"
    app.bridge.activate_profile(other)
    assert app.window.dashboard.cards["keyboard"].controls.effect.currentData() == "static"


def test_the_rules_thread_switches_through_the_gui_thread(app, qtbot):
    gaming = app.bridge.create_profile("Gaming")
    app.bridge.activate_profile(app.bridge.profiles.default_id)
    app.bridge._rule_profile.emit(gaming)  # what the watcher thread does
    qtbot.waitUntil(lambda: app.bridge.profiles.active_id == gaming, timeout=2000)
    assert app.window.profile.currentData() == gaming
    app.bridge._rule_profile.emit("gone")  # a deleted profile is ignored
    assert app.bridge.profiles.active_id == gaming


def test_profiles_page_actions(app, monkeypatch):
    page = app.window.pages["profiles"]
    assert page.list.count() == 1 and "default" in page.list.item(0).text()
    assert not page.btn["delete"].isEnabled()  # the last profile stays

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Work", True)))
    page.btn["new"].click()
    assert [p.name for p in app.bridge.profiles.profiles] == ["Default", "Work"]
    assert page.list.count() == 2 and "active" in page.list.currentItem().text()

    page.list.setCurrentRow(0)
    assert page.btn["activate"].isEnabled() and not page.btn["default"].isEnabled()
    page.btn["duplicate"].click()
    assert page.list.count() == 3

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("work", True)))
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    page.list.setCurrentRow(2)
    page.btn["rename"].click()  # clashes with "Work" ignoring case
    assert warned

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    page.list.setCurrentRow(2)
    page.btn["delete"].click()
    assert page.list.count() == 2


def test_rules_editor_adds_reorders_and_removes(app, monkeypatch):
    page = app.window.pages["profiles"]
    bridge = app.bridge
    gaming = bridge.create_profile("Gaming")

    class FakePicker:
        name = "valorant.exe"

        def __init__(self, *a):
            pass

        def exec(self):
            return 1

        def chosen(self):
            return FakePicker.name

    monkeypatch.setattr(profiles_page, "ProcessPicker", FakePicker)
    page.add_rule_btn.click()
    FakePicker.name = "obs64.exe"
    page.add_rule_btn.click()
    assert [r.match for r in bridge.profiles.rules] == ["valorant.exe", "obs64.exe"]
    assert len(page.rows) == 2 and not page.no_rules.isVisibleTo(page)

    page.rows[0].profile.setCurrentIndex(page.rows[0].profile.findData(bridge.profiles.default_id))
    assert bridge.profiles.rules[0].profile_id == bridge.profiles.default_id

    page.rows[1].focused.setChecked(True)
    page.rows[1].move_requested.emit(page.rows[1], -1)
    assert [r.match for r in bridge.profiles.rules] == ["obs64.exe", "valorant.exe"]
    assert bridge.profiles.rules[0].focused

    page.rows[0].remove_requested.emit(page.rows[0])
    assert bridge.profiles.rules == [Rule("valorant.exe", bridge.profiles.default_id)]
    bridge.set_rules(bridge.profiles.rules + [Rule("x.exe", gaming)])
    bridge.delete_profile(gaming)  # rules for a deleted profile disappear with it
    assert gaming not in {r.profile_id for r in bridge.profiles.rules}


def test_tray_profile_submenu_follows_and_switches(app, monkeypatch):
    tray = app.tray
    other = app.bridge.create_profile("Other")
    assert [a.text() for a in tray.profile_menu.actions()] == ["Default", "Other"]
    assert tray.profile_actions[other].isChecked()
    edit(app)
    answer(monkeypatch, "Cancel")
    tray.profile_actions[app.bridge.profiles.default_id].trigger()
    assert app.bridge.profiles.active_id == other and tray.profile_actions[other].isChecked()
    app.bridge.save_profile()
    tray.profile_actions[app.bridge.profiles.default_id].trigger()  # clean: no prompt
    assert app.bridge.profiles.active_id == app.bridge.profiles.default_id
