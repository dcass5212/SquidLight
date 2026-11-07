from __future__ import annotations

import numpy as np

from rgbapp.engine.effects import create_effect
from rgbapp.engine.effects.base import Ctx


def _run(e, p, pos, frames, aspect=1.0):
    out = None
    for i in range(frames):
        out = e.render(Ctx(t=i / 30, dt=1 / 30, led_positions=pos, aspect=aspect), p)
    return out


def _grid():
    xs, ys = np.meshgrid(np.linspace(0, 1, 20), np.linspace(0, 1, 6))
    return np.stack([xs.ravel(), ys.ravel()], 1)


def test_raindrop_shape_and_range():
    pos = _grid()
    e = create_effect("raindrop")
    out = _run(e, e.resolve_params({}), pos, 120, aspect=3.5)
    assert out.shape == (len(pos), 3) and out.dtype == np.float32
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_raindrop_lights_more_than_background():
    pos = _grid()
    e = create_effect("raindrop")
    p = e.resolve_params({"background": "#000000", "density": 4.0})
    total = sum(_run(e, p, pos, 1, 3.5).sum() for _ in range(90))
    assert total > 1.0


def test_raindrop_strip_rains_as_pond_ripples():
    pos = np.stack([np.linspace(0, 1, 16), np.zeros(16)], 1)  # one row: no 'down'
    e = create_effect("raindrop")
    p = e.resolve_params({"background": "#000000", "density": 4.0})
    total = sum(_run(e, p, pos, 1, 8.0).sum() for _ in range(90))
    assert total > 0.5


def test_raindrop_empty_device():
    e = create_effect("raindrop")
    assert _run(e, e.resolve_params({}), np.zeros((0, 2)), 3).shape == (0, 3)
