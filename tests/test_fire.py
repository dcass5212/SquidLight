from __future__ import annotations

import numpy as np

from rgbapp.engine.effects import create_effect, get_effect_class
from rgbapp.engine.effects.base import Ctx
from rgbapp.engine.inputs.keys import KeyEvent, KeySnapshot


def _grid():
    xs, ys = np.meshgrid(np.linspace(0, 1, 20), np.linspace(0, 1, 6))
    pos = np.stack([xs.ravel(), ys.ravel()], 1)
    names = tuple(f"Key: {i}" for i in range(len(pos)))
    return pos, names


def _run(e, p, pos, names, frames, inputs=None):
    out = None
    for i in range(frames):
        out = e.render(Ctx(t=i / 30, dt=1 / 30, led_positions=pos, led_names=names, inputs=inputs or {}), p)
    return out


def test_fire_needs_keys_only_when_reacting():
    cls = get_effect_class("fire")
    assert cls.required_inputs(cls.resolve_params({})) == set()
    assert cls.required_inputs(cls.resolve_params({"react": True})) == {"keys"}


def test_fire_burns_from_bottom():
    pos, names = _grid()
    e = create_effect("fire")
    out = _run(e, e.resolve_params({}), pos, names, 120)
    assert out.shape == (len(pos), 3) and out.dtype == np.float32
    assert out[-20:].mean() > out[:20].mean()


def test_fire_ignores_keys_when_react_off():
    pos, names = _grid()
    snap = KeySnapshot(events=(KeyEvent(1, names[10], 0.0),), active=True)
    a, b = create_effect("fire"), create_effect("fire")
    pa, pb = a.resolve_params({"intensity": 0.1}), b.resolve_params({"intensity": 0.1})
    _run(a, pa, pos, names, 1, {"keys": snap})
    _run(b, pb, pos, names, 1)
    assert a._seen == 0  # the event was never consumed


def test_fire_flares_at_pressed_key():
    pos, names = _grid()
    e = create_effect("fire")
    p = e.resolve_params({"react": True, "intensity": 0.1})
    _run(e, p, pos, names, 60)  # settle to a dim fire
    target = 2 * 20 + 10  # a key near the middle of the keyboard
    quiet = _run(e, p, pos, names, 1)[target].sum()
    snap = KeySnapshot(events=(KeyEvent(1, names[target], 0.0),), pressed=frozenset({names[target]}), active=True)
    lit = _run(e, p, pos, names, 1, {"keys": snap})[target].sum()
    assert lit > quiet + 0.5


def test_fire_surfaces_key_error():
    pos, names = _grid()
    e = create_effect("fire")
    _run(e, e.resolve_params({"react": True}), pos, names, 1, {"keys": KeySnapshot(error="no hook")})
    assert e.notice == "no hook"


def test_fire_two_rows_burn_like_keyboard_bottom_rows():
    xs, ys = np.meshgrid(np.linspace(0, 1, 10), np.array([0.0, 1.0]))  # two RAM sticks: one row each
    pos = np.stack([xs.ravel(), ys.ravel()], 1)
    e = create_effect("fire")
    out = _run(e, e.resolve_params({}), pos, (), 120)
    assert out[:10].sum() > 0.5  # the top stick is lit, not left at the flame tips


def test_fire_peaks_narrow_toward_the_top():
    xs, ys = np.meshgrid(np.linspace(0, 1, 88), np.linspace(0, 1, 24))
    pos = np.stack([xs.ravel(), ys.ravel()], 1)
    e = create_effect("fire")
    p = e.resolve_params({"height": 1.0})
    out = _run(e, p, pos, (), 400)
    lit = (out.sum(1) > 0.15).reshape(24, 88).sum(1)  # lit LEDs per row, top first
    assert lit[:4].mean() < lit[8:12].mean() < lit[-4:].mean()


def test_spectrum_lights_both_ram_rows():
    from rgbapp.engine.inputs.audio import N_BANDS
    xs, ys = np.meshgrid(np.linspace(0, 1, 10), np.array([0.0, 1.0]))
    pos = np.stack([xs.ravel(), ys.ravel()], 1)
    class A: bands = [0.5] * N_BANDS; error = None
    e = create_effect("audio_spectrum")
    out = e.render(Ctx(t=0, dt=1 / 30, led_positions=pos, inputs={"audio": A()}), e.resolve_params({}))
    assert out[:10].sum() > 0.5 and out[10:].sum() > 0.5
