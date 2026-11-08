import numpy as np
import pytest

from rgbapp.engine.effects import EFFECTS, create_effect
from rgbapp.engine.effects.base import Ctx


def strip(n=20):
    x = np.linspace(0, 1, n)
    return Ctx(t=0.0, dt=1 / 60, led_positions=np.stack([x, np.full(n, 0.5)], axis=1))


def grid(w=22, h=6):
    xs, ys = np.meshgrid(np.linspace(0, 1, w), np.linspace(0, 1, h))
    return Ctx(t=0.0, dt=1 / 60, led_positions=np.stack([xs.ravel(), ys.ravel()], axis=1), aspect=w / h)


def run(effect_id, ctx, t, **over):
    e = create_effect(effect_id)
    params = {p.id: p.default for p in e.params} | over
    ctx.t = t
    return e.render(ctx, params)


@pytest.mark.parametrize("eid", ["comet", "aurora"])
@pytest.mark.parametrize("make", [strip, grid])
def test_shape_range_and_registered(eid, make):
    assert eid in EFFECTS and EFFECTS[eid].listed
    for t in (0.0, 1.3, 50.0):
        f = run(eid, make(), t)
        assert f.shape == (len(make().led_positions), 3) and f.dtype == np.float32
        assert f.min() >= 0.0 and f.max() <= 1.0 + 1e-6


def test_comet_head_moves_forward_and_tail_trails_behind():
    ctx = strip()
    f = run("comet", ctx, 1.0, speed=0.25, background="#000000")  # head about 1/4 of the way (plus entry offset)
    lum = f.sum(axis=1)
    peak = int(lum.argmax())
    assert 0 < peak < 19
    assert lum[peak - 3] > lum[peak + 3]  # lit behind the head, dark ahead of it
    later = run("comet", strip(), 1.6, speed=0.25).sum(axis=1)
    assert int(later.argmax()) > peak


def test_comet_reverse_speed_runs_the_other_way():
    a = run("comet", strip(), 1.0, speed=0.25).sum(axis=1)
    b = run("comet", strip(), 1.0, speed=-0.25).sum(axis=1)
    assert int(b.argmax()) > int(a.argmax()) or int(b.argmax()) != int(a.argmax())
    pk = int(b.argmax())
    assert b[min(pk + 3, 19)] > b[max(pk - 3, 0)]  # tail is now on the right


def test_comet_bounce_turns_around():
    out = run("comet", strip(), 0.5, speed=0.5, mode="bounce").sum(axis=1)  # cycle 0.25: head at 0.25, going right
    back = run("comet", strip(), 2.5, speed=0.5, mode="bounce").sum(axis=1)  # cycle 1.25: head at 0.75, going left
    assert int(back.argmax()) > int(out.argmax())
    pk = int(back.argmax())
    assert back[min(pk + 3, 19)] > back[max(pk - 3, 0)]  # tail now trails to the right


def test_comet_is_dark_when_off_screen_and_handles_empty():
    empty = Ctx(t=0.0, dt=0.0, led_positions=np.zeros((0, 2)))
    assert create_effect("comet").render(empty, {p.id: p.default for p in create_effect("comet").params}).shape == (0, 3)


def test_aurora_drifts_and_varies_across_leds():
    a, b = run("aurora", grid(), 0.0), run("aurora", grid(), 5.0)
    assert not np.allclose(a, b)
    assert float(a.std(axis=0).max()) > 0.02
    still = run("aurora", grid(), 7.0, speed=0.0), run("aurora", grid(), 0.0, speed=0.0)
    assert np.allclose(*still)
