"""M4 engine/model side: the paint document, the per-key effect and its compositing,
and saving/loading the setup so a painted layout survives a restart."""

import json

import pytest

from rgbapp.engine import Engine, EngineHost, LayerSpec
from rgbapp.engine.backend import FakeBackend
from rgbapp.engine.compositor import Compositor
from rgbapp.engine.devices import build_groups
from rgbapp.engine.effects import Ctx, get_effect_class, listed_effects
from rgbapp.engine.host import GroupSettings
from rgbapp.model.paint import PaintDocument
from rgbapp.model.store import SettingsStore


def keyboard_group():
    backend = FakeBackend()
    backend.connect()
    return next(g for g in build_groups(backend.devices()) if g.key == "keyboard")


def ctx_for(group):
    return Ctx(t=0.0, dt=1 / 30, led_positions=group.positions)


# -- PaintDocument


def test_apply_clear_undo_redo():
    doc = PaintDocument()
    assert doc.apply([1, 2, 3], "#FF0000")
    assert doc.to_params() == {"1": "#ff0000", "2": "#ff0000", "3": "#ff0000"}
    assert doc.apply([2], "#00ff00")
    assert doc.clear([1])
    assert doc.to_params() == {"2": "#00ff00", "3": "#ff0000"}
    assert doc.undo() and doc.color_of(1) == "#ff0000"
    assert doc.undo() and doc.color_of(2) == "#ff0000"
    assert doc.redo() and doc.color_of(2) == "#00ff00"
    assert doc.apply([9], "#0000ff")  # a new edit drops the redo branch
    assert not doc.can_redo


def test_noop_edits_are_not_history():
    doc = PaintDocument({"4": "#123456"})
    assert not doc.apply([4], "#123456")
    assert not doc.clear([5])
    assert not doc.can_undo


def test_clear_all_is_one_undoable_step():
    doc = PaintDocument()
    doc.apply(range(10), "#ffffff")
    assert doc.clear_all() and doc.is_empty
    assert doc.undo() and len(doc.colors) == 10


def test_edits_with_one_merge_key_are_a_single_undo_step():
    doc = PaintDocument()
    for c in ("#110000", "#220000", "#330000"):  # dragging inside a color picker
        doc.apply([1, 2], c, merge="drag")
    assert doc.color_of(1) == "#330000"
    assert doc.undo() and doc.is_empty and not doc.can_undo


def test_end_gesture_and_undo_start_a_new_merge_step():
    doc = PaintDocument()
    doc.apply([1], "#111111", merge="m")
    doc.end_gesture()
    doc.apply([1], "#222222", merge="m")
    assert doc.undo() and doc.color_of(1) == "#111111"
    doc.apply([1], "#333333", merge="m")  # must not fold into the earlier, older step
    assert doc.undo() and doc.color_of(1) == "#111111"


def test_undo_history_is_bounded():
    doc = PaintDocument()
    for i in range(300):
        doc.apply([0], "#%06x" % (i + 1))
    steps = 0
    while doc.undo():
        steps += 1
    assert steps == 200


# -- the effect and layering


def test_param_coercion_cleans_a_painted_map():
    p = get_effect_class("static_keys").params[0]
    assert p.hidden
    assert p.coerce({3: "#ABCDEF", "4": "#000000"}) == {"3": "#abcdef", "4": "#000000"}
    assert p.coerce("garbage") == {}


def test_static_keys_is_not_offered_in_the_library():
    assert "static_keys" not in [c.id for c in listed_effects()]


def test_painted_keys_light_and_the_rest_are_transparent():
    group = keyboard_group()
    cls = get_effect_class("static_keys")
    params = cls.resolve_params({"colors": {"0": "#ff0000", "5": "#0000ff"}})
    eff = cls()
    frame = eff.render(ctx_for(group), params)
    assert frame.shape == (group.led_count, 3)
    assert frame[0].tolist() == [1, 0, 0] and frame[5].tolist() == [0, 0, 1]
    assert eff.coverage(ctx_for(group), params).nonzero()[0].tolist() == [0, 5]


def test_paint_over_an_effect_shows_the_effect_through_unpainted_keys():
    group = keyboard_group()
    comp = Compositor(group)
    comp.set_layers([
        LayerSpec("static", {"color": "#00ff00"}),
        LayerSpec("static_keys", {"colors": {"7": "#ff0000"}}),
    ])
    out = comp.render(ctx_for(group))
    assert out[7].tolist() == [1, 0, 0]
    assert out[8].tolist() == [0, 1, 0] and out[0].tolist() == [0, 1, 0]


def test_effect_on_top_of_paint_layers_with_its_blend_mode():
    group = keyboard_group()
    comp = Compositor(group)
    comp.set_layers([
        LayerSpec("static_keys", {"colors": {"1": "#ff0000", "2": "#ff0000"}}),
        LayerSpec("static", {"color": "#0000ff"}, mask=[2, 3], blend="add"),
    ])
    out = comp.render(ctx_for(group))
    assert out[1].tolist() == [1, 0, 0]  # painted, outside the effect's mask
    assert out[2].tolist() == [1, 0, 1]  # painted + effect added on top
    assert out[3].tolist() == [0, 0, 1]  # effect only (the paint does not cover it)
    assert out[4].tolist() == [0, 0, 0]


def test_paint_layer_opacity_and_visibility_apply():
    group = keyboard_group()
    comp = Compositor(group)
    spec = LayerSpec("static_keys", {"colors": {"0": "#ffffff"}}, opacity=0.5)
    comp.set_layers([spec])
    assert comp.render(ctx_for(group))[0].tolist() == pytest.approx([0.5, 0.5, 0.5])
    spec.visible = False
    comp.set_layers([spec])
    assert not comp.render(ctx_for(group)).any()


# -- saving and loading


def test_store_round_trip_keeps_layers_params_and_masks(tmp_path):
    store = SettingsStore(tmp_path / "profiles.json")
    spec_paint = LayerSpec("static_keys", {"colors": {"3": "#112233"}}, opacity=0.8, visible=False)
    spec_fx = LayerSpec("rainbow_wave", {"speed": 1.5}, mask=["Number pad"], blend="add")
    store.save(0.6, {"keyboard": GroupSettings(layers=[spec_fx, spec_paint], brightness=0.4, enabled=False)})
    master, groups = store.load()
    assert master == pytest.approx(0.6)
    kb = groups["keyboard"]
    assert (kb.brightness, kb.enabled) == (0.4, False)
    assert [s.id for s in kb.layers] == [spec_fx.id, spec_paint.id]
    assert kb.layers[0].mask == ["Number pad"] and kb.layers[0].blend == "add"
    assert kb.layers[1].params == {"colors": {"3": "#112233"}}
    assert kb.layers[1].opacity == 0.8 and kb.layers[1].visible is False


def test_store_uses_the_documented_profile_format_and_keeps_other_profiles(tmp_path):
    path = tmp_path / "profiles.json"
    other = {"id": "x", "name": "Gaming", "devices": {}}
    rules = [{"match": "a.exe", "profile_id": "x"}]
    path.write_text(json.dumps({"version": 1, "profiles": [other], "rules": rules, "default_profile_id": "x"}))
    SettingsStore(path).save(1.0, {"keyboard": GroupSettings(layers=[LayerSpec("static")])})
    doc = json.loads(path.read_text())
    assert doc["version"] == 1 and doc["rules"] == rules
    assert doc["profiles"][0]["id"] == "x" and "keyboard" in doc["profiles"][0]["devices"]


def test_store_survives_missing_and_broken_files(tmp_path):
    path = tmp_path / "profiles.json"
    bak = path.with_name("profiles.json.bak")
    store = SettingsStore(path)
    assert store.load() == (1.0, {})
    path.write_text("{ not json")
    assert store.load() == (1.0, {})
    store.save(1.0, {"keyboard": GroupSettings(layers=[LayerSpec("static")])})
    assert "keyboard" in store.load()[1]
    store.save(0.5, {"keyboard": GroupSettings(layers=[LayerSpec("breathing")])})
    assert bak.exists()
    path.write_text("{ broken")  # the .bak still holds a good copy
    assert store.load()[1]["keyboard"].layers[0].effect == "static"
    store.save(0.5, {})
    assert json.loads(bak.read_text())  # a broken file never replaces the backup


def test_store_skips_a_bad_layer_but_keeps_the_rest(tmp_path):
    path = tmp_path / "profiles.json"
    layers = [{"nope": 1}, {"effect": "static", "blend": "weird", "opacity": 7}]
    path.write_text(json.dumps({"version": 1, "default_profile_id": "a",
                                "profiles": [{"id": "a", "devices": {"keyboard": {"layers": layers}}}]}))
    loaded = SettingsStore(path).load()[1]["keyboard"].layers
    assert len(loaded) == 1 and loaded[0].blend == "normal" and loaded[0].opacity == 1.0


def test_painted_layout_survives_a_restart(tmp_path):
    store = SettingsStore(tmp_path / "profiles.json")

    host = EngineHost(Engine(FakeBackend()), store=store, save_delay=0.05)
    host.start()
    assert host.wait_running(3.0)
    host.set_layer_props("keyboard", host.layers("keyboard")[0].id, opacity=0.9)
    paint_id = host.add_layer("keyboard", "static_keys", {"colors": {"10": "#ff00aa", "11": "#00ffaa"}})
    host.set_brightness("keyboard", 0.5)
    host.stop()  # flushes the pending save

    again = EngineHost(Engine(FakeBackend()), store=store)
    layers = again.layers("keyboard")
    assert [s.effect for s in layers] == ["rainbow_wave", "static_keys"]
    assert layers[0].opacity == pytest.approx(0.9)
    assert layers[1].id == paint_id and layers[1].params["colors"] == {"10": "#ff00aa", "11": "#00ffaa"}
    assert again.settings("keyboard").brightness == pytest.approx(0.5)

    again.start()
    assert again.wait_running(3.0)
    assert [s.effect for s in again.layers("keyboard")] == ["rainbow_wave", "static_keys"]
    again.stop()


def test_saves_are_debounced_and_optional(tmp_path):
    class CountingStore(SettingsStore):
        saves = 0

        def save(self, master, groups):
            type(self).saves += 1
            super().save(master, groups)

    store = CountingStore(tmp_path / "p.json")
    host = EngineHost(Engine(FakeBackend()), store=store, save_delay=60)
    for i in range(20):
        host.set_param("keyboard", "speed", 0.1 * i)
    assert CountingStore.saves == 0  # still waiting out the delay
    host.stop()
    assert CountingStore.saves == 1
    assert store.load()[1]["keyboard"].layers[0].params["speed"] == pytest.approx(1.9)

    no_store = EngineHost(Engine(FakeBackend()))
    no_store.set_param("keyboard", "speed", 1.0)  # nothing to save to; must not raise
    no_store.stop()
