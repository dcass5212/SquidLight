import numpy as np
import pytest

from rgbapp.engine.backend import FakeBackend
from rgbapp.engine.compositor import Compositor, LayerSpec, to_uint8
from rgbapp.engine.devices import build_groups
from rgbapp.engine.effects import EFFECTS, Ctx, create_effect, get_effect_class


@pytest.fixture
def groups():
    return {g.key: g for g in build_groups(FakeBackend().devices())}


def ctx_for(group, t=0.0, dt=1 / 30):
    return Ctx(t=t, dt=dt, led_positions=group.positions)


def render(effect_id, group, t=0.0, **raw):
    cls = get_effect_class(effect_id)
    return create_effect(effect_id).render(ctx_for(group, t), cls.resolve_params(raw))


# ---------------------------------------------------------------- effects


@pytest.mark.parametrize("effect_id", sorted(EFFECTS))
@pytest.mark.parametrize("group_key", ["keyboard", "memory"])
@pytest.mark.parametrize("t", [0.0, 0.37, 12.5, 1e5])
def test_every_effect_returns_float32_n3_in_range(effect_id, group_key, t, groups):
    group = groups[group_key]
    out = render(effect_id, group, t)
    assert out.shape == (group.led_count, 3)
    assert out.dtype == np.float32
    assert np.isfinite(out).all()
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_static_is_the_color_everywhere(groups):
    out = render("static", groups["keyboard"], color="#ff8000")
    np.testing.assert_allclose(out, np.tile([1.0, 128 / 255, 0.0], (115, 1)), atol=1e-6)


def test_breathing_is_dim_at_start_and_bright_at_half_period(groups):
    g = groups["memory"]
    start = render("breathing", g, t=0.0, color="#ffffff", speed=0.5, min_level=0.0)
    peak = render("breathing", g, t=1.0, color="#ffffff", speed=0.5, min_level=0.0)  # period = 2 s
    assert start.max() == pytest.approx(0.0, abs=1e-6)
    assert peak.min() == pytest.approx(1.0, abs=1e-6)


def test_breathing_respects_min_level(groups):
    out = render("breathing", groups["memory"], t=0.0, color="#ffffff", min_level=0.3)
    assert out.min() == pytest.approx(0.3, abs=1e-6)


def test_rainbow_varies_across_the_device_and_moves_over_time(groups):
    g = groups["keyboard"]
    a, b = render("rainbow_wave", g, t=0.0), render("rainbow_wave", g, t=0.5)
    assert len({tuple(np.round(c, 2)) for c in a}) > 10  # many different hues at once
    assert not np.allclose(a, b)  # animates


def test_rainbow_is_periodic_in_time(groups):
    g = groups["keyboard"]
    a = render("rainbow_wave", g, t=0.0, speed=0.5)
    b = render("rainbow_wave", g, t=2.0, speed=0.5)  # exactly one full cycle later
    np.testing.assert_allclose(a, b, atol=1e-4)


def test_rainbow_reverse_speed_reverses_direction(groups):
    g = groups["keyboard"]
    fwd = render("rainbow_wave", g, t=0.3, speed=0.5)
    back = render("rainbow_wave", g, t=-0.3, speed=-0.5)  # same phase offset, opposite sign
    np.testing.assert_allclose(fwd, back, atol=1e-4)


def test_params_are_clamped_and_bad_values_fall_back(groups):
    p = get_effect_class("rainbow_wave").resolve_params({"speed": 99, "angle": "oops", "nope": 1})
    assert p["speed"] == 3.0
    assert p["angle"] == 0.0  # default
    assert "nope" not in p
    assert get_effect_class("static").resolve_params({"color": "red"})["color"] == "#ff0000"


def test_color_cycle_is_uniform_and_changes_hue_over_time(groups):
    g = groups["keyboard"]
    a = render("color_cycle", g, t=0.0, speed=0.25)
    b = render("color_cycle", g, t=1.0, speed=0.25)  # a quarter turn later
    assert np.allclose(a, a[0]) and np.allclose(b, b[0])  # whole device, one color
    np.testing.assert_allclose(a[0], [1, 0, 0], atol=1e-6)  # hue 0 = red
    assert not np.allclose(a[0], b[0])
    c = render("color_cycle", g, t=4.0, speed=0.25)  # one full cycle
    np.testing.assert_allclose(a, c, atol=1e-5)


def test_gradient_wave_shows_the_gradient_colors_and_moves(groups):
    g = groups["keyboard"]
    grad = [(0.0, "#ff0000"), (1.0, "#0000ff")]
    out = render("gradient_wave", g, t=0.0, gradient=grad, mirror=False, speed=0.5)
    assert np.allclose(out[:, 1], 0.0)  # only red..blue mixes, no green
    assert out[:, 0].max() > 0.9 and out[:, 2].max() > 0.9  # both ends appear
    assert not np.allclose(out, render("gradient_wave", g, t=0.5, gradient=grad, mirror=False, speed=0.5))


def test_gradient_wave_mirror_has_no_seam():
    strip = np.stack([np.linspace(0, 1, 60), np.full(60, 0.5)], axis=1).astype(np.float32)
    cls = get_effect_class("gradient_wave")
    grad = [(0.0, "#ff0000"), (1.0, "#0000ff")]

    def biggest_step(mirror):
        out = cls().render(Ctx(0.3, 0.0, strip), cls.resolve_params({"gradient": grad, "mirror": mirror, "wavelength": 0.7}))
        return np.abs(np.diff(out[:, 0])).max()

    assert biggest_step(False) > 0.9  # wraps from blue straight back to red
    assert biggest_step(True) < 0.1  # there and back: smooth everywhere


def test_sample_gradient_holds_end_colors_and_interpolates():
    from rgbapp.engine.effects.base import sample_gradient

    out = sample_gradient([(0.25, "#000000"), (0.75, "#ffffff")], np.array([0.0, 0.5, 1.0]))
    np.testing.assert_allclose(out, [[0, 0, 0], [0.5, 0.5, 0.5], [1, 1, 1]], atol=1e-6)


def test_starlight_twinkles_and_fades(groups):
    from rgbapp.engine.effects.starlight import Starlight

    g = groups["keyboard"]
    fx = Starlight(seed=1)
    params = Starlight.resolve_params({"density": 0.5, "duration": 0.5, "color": "#ffffff", "background": "#000000"})
    frames = [fx.render(Ctx(t=i / 30, dt=1 / 30, led_positions=g.positions), params) for i in range(90)]
    lit = [(f.max(axis=1) > 0.05).mean() for f in frames[30:]]
    assert 0.15 < np.mean(lit) < 0.6  # about `density` of the keys are twinkling
    assert not np.allclose(frames[40], frames[41])
    # with no time passing nothing changes, and stars always fade back out
    still = Ctx(t=3.0, dt=0.0, led_positions=g.positions)
    np.testing.assert_array_equal(fx.render(still, params), fx.render(still, params))
    quiet = Starlight.resolve_params({"density": 0.01, "duration": 0.5})
    for i in range(30):
        last = fx.render(Ctx(t=3 + i / 30, dt=1 / 30, led_positions=g.positions), quiet)
    assert (last.max(axis=1) > 0.05).mean() < 0.1


def test_starlight_background_and_random_colors(groups):
    from rgbapp.engine.effects.starlight import Starlight

    g = groups["memory"]
    fx = Starlight(seed=2)
    p = Starlight.resolve_params({"background": "#0000ff", "density": 0.01})
    first = fx.render(Ctx(0.0, 0.0, g.positions), p)
    np.testing.assert_allclose(first, np.tile([0, 0, 1], (20, 1)), atol=1e-6)
    p = Starlight.resolve_params({"random_colors": True, "density": 1.0, "duration": 2.0})
    for i in range(60):
        out = fx.render(Ctx(i / 30, 1 / 30, g.positions), p)
    lit = out[out.max(axis=1) > 0.5]
    assert len({tuple(np.round(c, 1)) for c in lit}) > 3  # several different star colors


# ---------------------------------------------------------------- compositor


def comp_with(group, *layers):
    c = Compositor(group)
    c.set_layers(list(layers))
    return c


def test_empty_stack_is_black(groups):
    g = groups["keyboard"]
    assert not comp_with(g).render(ctx_for(g)).any()


def test_normal_opacity_blends_toward_the_layer(groups):
    g = groups["memory"]
    base = LayerSpec("static", {"color": "#ff0000"})
    top = LayerSpec("static", {"color": "#0000ff"}, opacity=0.25)
    out = comp_with(g, base, top).render(ctx_for(g))
    np.testing.assert_allclose(out[0], [0.75, 0.0, 0.25], atol=1e-6)


def test_blend_modes(groups):
    g = groups["memory"]
    base = LayerSpec("static", {"color": "#808000"})  # (128, 128, 0)
    half = 128 / 255
    cases = {
        "add": [min(half + 1.0, 1.0), min(half + 0.0, 1.0), 0.0],  # top = #ff0000
        "multiply": [half * 1.0, half * 0.0, 0.0],
        "screen": [1 - (1 - half) * 0.0, 1 - (1 - half) * 1.0, 0.0],
    }
    for mode, expected in cases.items():
        out = comp_with(g, base, LayerSpec("static", {"color": "#ff0000"}, blend=mode)).render(ctx_for(g))
        np.testing.assert_allclose(out[0], expected, atol=1e-6, err_msg=mode)


def test_invisible_and_zero_opacity_layers_are_skipped(groups):
    g = groups["memory"]
    out = comp_with(
        g,
        LayerSpec("static", {"color": "#00ff00"}),
        LayerSpec("static", {"color": "#ff0000"}, visible=False),
        LayerSpec("static", {"color": "#0000ff"}, opacity=0.0),
    ).render(ctx_for(g))
    np.testing.assert_allclose(out[0], [0, 1, 0], atol=1e-6)


def test_mask_by_led_index_and_by_zone_name(groups):
    g = groups["keyboard"]
    base = LayerSpec("static", {"color": "#000000"})
    by_index = comp_with(g, base, LayerSpec("static", {"color": "#ffffff"}, mask=[0, 5])).render(ctx_for(g))
    assert by_index[[0, 5]].min() == 1.0 and by_index.sum() == 6.0
    by_zone = comp_with(g, LayerSpec("static", {"color": "#ffffff"}, mask=["Keyboard"])).render(ctx_for(g))
    assert by_zone.min() == 1.0
    mem = groups["memory"]
    second_stick = comp_with(mem, LayerSpec("static", {"color": "#ffffff"}, mask=["Corsair DRAM (2)"])).render(ctx_for(mem))
    assert second_stick[:10].sum() == 0 and second_stick[10:].min() == 1.0


def test_layer_keeps_effect_state_across_edits_by_id(groups):
    g = groups["memory"]
    layer = LayerSpec("breathing")
    c = comp_with(g, layer)
    first = c._layers[0].effect
    c.set_layers([LayerSpec("breathing", {"speed": 1.0}, id=layer.id)])
    assert c._layers[0].effect is first
    c.set_layers([LayerSpec("static", id=layer.id)])  # different effect: new instance
    assert c._layers[0].effect is not first


def test_broken_effect_and_unknown_effect_do_not_break_the_stack(groups, caplog):
    g = groups["memory"]

    class Boom(create_effect("static").__class__):
        id = "boom"

        def render(self, ctx, params):
            raise RuntimeError("boom")

    EFFECTS["boom"] = Boom
    try:
        c = comp_with(
            g,
            LayerSpec("static", {"color": "#00ff00"}),
            LayerSpec("boom"),
            LayerSpec("does_not_exist"),
        )
        for _ in range(3):
            out = c.render(ctx_for(g))
        np.testing.assert_allclose(out[0], [0, 1, 0], atol=1e-6)
        failures = [r for r in caplog.records if "effect boom failed" in r.getMessage()]
        assert len(failures) == 1  # logged once, not once per frame
    finally:
        del EFFECTS["boom"]


def test_to_uint8_rounds_scales_and_clips():
    f = np.array([[0.0, 0.5, 1.0], [2.0, -1.0, 0.2]], dtype=np.float32)
    np.testing.assert_array_equal(to_uint8(f), [[0, 128, 255], [255, 0, 51]])
    np.testing.assert_array_equal(to_uint8(f, 0.5), [[0, 64, 128], [128, 0, 26]])
    assert to_uint8(f).dtype == np.uint8
