"""Profiles, the working copy, and per-app rules: all headless against FakeBackend."""

import json
import time

import pytest

from rgbapp.engine import Engine, EngineHost, FakeBackend
from rgbapp.engine.rules import Rule, RulesWatcher, pick_rule
from rgbapp.model.profiles import ProfileManager
from rgbapp.model.store import MemoryStore, SettingsStore


def make(tmp_path=None, start=True):
    store = SettingsStore(tmp_path / "profiles.json") if tmp_path else MemoryStore()
    host = EngineHost(Engine(FakeBackend()), store=store, save_delay=0.05)
    mgr = ProfileManager(host, store)
    if start:
        host.start()
        assert host.wait_running(3.0)
    return host, mgr, store


def effect_of(host, key="keyboard"):
    return host.layers(key)[0].effect


def test_a_fresh_install_has_one_clean_default_profile():
    host, mgr, _ = make()
    try:
        assert [p.name for p in mgr.profiles] == ["Default"]
        assert mgr.active_id == mgr.default_id
        assert not mgr.dirty  # the groups the engine fills in don't count as edits
    finally:
        host.stop()


def test_editing_marks_dirty_and_save_or_revert_clears_it():
    host, mgr, _ = make()
    try:
        host.set_effect("keyboard", "static")
        assert mgr.dirty
        mgr.revert()
        assert effect_of(host) == "rainbow_wave" and not mgr.dirty
        host.set_effect("keyboard", "static")
        mgr.save_active()
        assert not mgr.dirty
        host.set_effect("keyboard", "breathing")
        host.set_effect("keyboard", "static")  # back to what is saved: clean again
        assert not mgr.dirty
    finally:
        host.stop()


def test_create_switch_and_every_profile_keeps_its_own_lighting():
    host, mgr, _ = make()
    try:
        default_id = mgr.active_id
        host.set_effect("keyboard", "static")
        host.set_brightness("memory", 0.3)
        gaming = mgr.create("Gaming")  # copies the current lighting and activates it
        assert mgr.active_id == gaming and not mgr.dirty
        host.set_effect("keyboard", "breathing")
        mgr.save_active()
        mgr.activate(default_id)  # Default never saved the static edit
        assert effect_of(host) == "rainbow_wave"
        assert host.settings("memory").brightness == 1.0
        mgr.activate(gaming)
        assert effect_of(host) == "breathing" and host.settings("memory").brightness == pytest.approx(0.3)
    finally:
        host.stop()


def test_switching_reaches_the_engine_not_just_the_stored_settings():
    host, mgr, _ = make()
    try:
        green = mgr.create("Green")
        host.set_effect("keyboard", "static")
        host.set_param("keyboard", "color", "#00ff00")
        mgr.save_active()

        def keyboard_is_green():
            frame = host.latest_frames().get("keyboard")
            return frame is not None and (frame[:, 1] > 200).all() and (frame[:, 0] < 10).all()

        deadline = time.monotonic() + 3
        while not keyboard_is_green() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert keyboard_is_green()
        mgr.activate(mgr.default_id)  # rainbow: not one flat green
        time.sleep(0.2)
        assert not keyboard_is_green()
        mgr.activate(green)
        deadline = time.monotonic() + 3
        while not keyboard_is_green() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert keyboard_is_green()
    finally:
        host.stop()


def test_names_are_unique_and_delete_keeps_things_consistent():
    host, mgr, _ = make(start=False)
    a = mgr.create("Work")
    b = mgr.create("Work")
    assert mgr.name_of(a) == "Work" and mgr.name_of(b) == "Work (2)"
    with pytest.raises(ValueError):
        mgr.rename(b, "work")
    with pytest.raises(ValueError):
        mgr.rename(b, "   ")
    mgr.set_default(b)
    mgr.set_rules([Rule("game.exe", b), Rule("x.exe", a)])
    mgr.delete(b)  # active and default: both move on, its rule goes away
    assert mgr.active_id == mgr.default_id != b
    assert [r.match for r in mgr.rules] == ["x.exe"]
    mgr.delete(a)
    with pytest.raises(ValueError):
        mgr.delete(mgr.active_id)  # the last one stays


def test_duplicate_copies_the_saved_state_not_the_unsaved_edits():
    host, mgr, _ = make(start=False)
    host.set_effect("keyboard", "static")  # unsaved
    copy_id = mgr.duplicate(mgr.active_id)
    assert mgr.name_of(copy_id) == "Default copy"
    mgr.activate(copy_id)
    assert effect_of(host) == "rainbow_wave"


def test_export_import_round_trip(tmp_path):
    host, mgr, _ = make(start=False)
    host.set_effect("keyboard", "static")
    host.set_brightness("keyboard", 0.5)
    mgr.save_active()
    path = tmp_path / "p.json"
    mgr.export_profile(mgr.active_id, path)
    assert json.loads(path.read_text())["profile"]["name"] == "Default"
    new = mgr.import_profile(path)
    assert mgr.name_of(new) == "Default (2)"  # never collides with an existing name
    mgr.activate(new)
    assert effect_of(host) == "static" and host.settings("keyboard").brightness == pytest.approx(0.5)


def test_import_rejects_files_that_are_not_profiles(tmp_path):
    _, mgr, _ = make(start=False)
    for text in ("not json", "[]", '{"profile": {"name": "x"}}'):
        p = tmp_path / "bad.json"
        p.write_text(text)
        with pytest.raises(ValueError):
            mgr.import_profile(p)
    with pytest.raises(ValueError):
        mgr.import_profile(tmp_path / "missing.json")
    assert len(mgr.profiles) == 1


def test_unsaved_edits_and_the_active_profile_survive_a_restart(tmp_path):
    host, mgr, store = make(tmp_path)
    other = mgr.create("Other")
    host.set_effect("keyboard", "static")  # unsaved edit on "Other"
    host.stop()  # flushes the working copy

    host2, mgr2, _ = make(tmp_path, start=False)
    assert mgr2.active_id == other and effect_of(host2) == "static"
    assert mgr2.dirty  # still unsaved
    mgr2.revert()
    assert effect_of(host2) == "rainbow_wave" and not mgr2.dirty


def test_an_old_file_without_a_working_copy_still_loads(tmp_path):
    path = tmp_path / "profiles.json"
    layer = {"effect": "static", "params": {}}
    path.write_text(json.dumps({"version": 1, "default_profile_id": "a", "profiles": [
        {"id": "a", "name": "Default", "devices": {"keyboard": {"layers": [layer]}}}]}))
    host, mgr, _ = make(tmp_path, start=False)
    assert effect_of(host) == "static" and not mgr.dirty


# -- rules


class Procs:
    def __init__(self, running=(), foreground=None):
        self.names, self.fg = set(running), foreground

    def running(self):
        return set(self.names)

    def foreground(self):
        return self.fg


def watcher(rules, procs, default="D", valid=("D", "G", "W")):
    calls = []
    w = RulesWatcher(lambda: (tuple(rules), default, frozenset(valid)), calls.append, procs)
    return w, calls


def test_rule_matching_is_case_insensitive_and_first_rule_wins():
    rules = [Rule("Game.EXE", "G"), Rule("game", "W"), Rule("other.exe", "W")]
    assert pick_rule(rules, {"G", "W"}, {"game.exe"}, None).profile_id == "G"
    assert pick_rule(rules[1:], {"G", "W"}, {"game.exe"}, None).profile_id == "W"  # ".exe" optional
    assert pick_rule(rules, {"G", "W"}, {"notepad.exe"}, None) is None
    assert pick_rule([Rule("a.exe", "gone")], {"G"}, {"a.exe"}, None) is None  # deleted profile


def test_focus_rules_need_the_app_in_front():
    r = Rule("game.exe", "G", focused=True)
    assert not r.applies({"game.exe"}, "explorer.exe")
    assert r.applies({"game.exe"}, "game.exe")


def test_launching_an_app_switches_and_closing_it_returns_to_default():
    procs = Procs()
    w, calls = watcher([Rule("game.exe", "G")], procs)
    w.poll_once()
    assert calls == []  # nothing running at startup: leave the restored profile alone
    procs.names = {"game.exe"}
    w.poll_once()
    w.poll_once()  # steady: no repeat switches
    assert calls == ["G"]
    procs.names = set()
    w.poll_once()
    assert calls == ["G", "D"]


def test_an_app_already_running_at_startup_switches_immediately():
    w, calls = watcher([Rule("game.exe", "G")], Procs({"game.exe"}))
    w.poll_once()
    assert calls == ["G"]


def test_priority_by_list_order_and_changing_matches():
    procs = Procs({"a.exe", "b.exe"})
    w, calls = watcher([Rule("a.exe", "G"), Rule("b.exe", "W")], procs)
    w.poll_once()
    procs.names = {"b.exe"}  # the top rule's app closes: the next match takes over
    w.poll_once()
    assert calls == ["G", "W"]


def test_a_manual_choice_sticks_while_nothing_changes():
    w, calls = watcher([Rule("game.exe", "G")], Procs())
    for _ in range(3):
        w.poll_once()
    assert calls == []


def test_the_watcher_is_idle_without_rules():
    class Boom(Procs):
        def running(self):
            raise AssertionError("should not scan processes with no rules")

    w, calls = watcher([], Boom())
    w.poll_once()
    assert calls == []


def test_rules_round_trip_through_the_store(tmp_path):
    host, mgr, _ = make(tmp_path, start=False)
    a = mgr.create("A")
    mgr.set_rules([Rule("x.exe", a, focused=True), Rule("", a), Rule("y.exe", "nope")])
    assert mgr.rules == [Rule("x.exe", a, True)]  # blank and dangling rules are dropped
    assert mgr.rules_snapshot()[1] == mgr.default_id
