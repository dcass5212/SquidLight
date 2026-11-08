"""Lights off on lock/idle/schedule, and the fades that go with it."""

from datetime import time

import numpy as np

from rgbapp.engine import FakeBackend, LayerSpec, LoopConfig, RenderLoop
from rgbapp.engine.away import AwayPolicy, in_window, parse_clock
from rgbapp.engine.devices import build_groups
from rgbapp.model.app_settings import AppSettings


def test_window_same_day_and_through_midnight():
    assert in_window(time(12), time(9), time(17))
    assert not in_window(time(17), time(9), time(17))
    assert in_window(time(23, 30), time(23), time(7))
    assert in_window(time(3), time(23), time(7))
    assert not in_window(time(12), time(23), time(7))
    assert not in_window(time(12), time(8), time(8))


def test_policy_reasons():
    p = AwayPolicy(off_on_lock=True, idle_enabled=True, idle_minutes=5, schedule_enabled=True)
    assert p.reasons(time(12), 0, False) == []
    assert p.reasons(time(12), 0, True) == ["locked"]
    assert p.reasons(time(12), 299, False) == []
    assert p.reasons(time(12), 300, False) == ["idle"]
    assert p.reasons(time(23, 30), 0, True) == ["locked", "schedule"]
    assert AwayPolicy(off_on_lock=False).reasons(time(12), 0, True) == []


def test_bad_clock_text_and_settings_normalize():
    assert parse_clock("25:00") == time(0, 0)
    assert parse_clock("nope", time(7)) == time(7)
    s = AppSettings(schedule_off="9:5", schedule_on="bad", idle_minutes=0, fade_ms=99999).normalized()
    assert (s.schedule_off, s.schedule_on) == ("09:05", "07:00")
    assert (s.idle_minutes, s.fade_ms) == (1, 3000)


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def make():
    clock = Clock()
    backend = FakeBackend(clock=clock)
    backend.connect()
    loop = RenderLoop(backend, build_groups(backend.devices()), LoopConfig(fps=60.0), clock=clock)
    for g in loop.groups:
        loop.set_layers(g.key, [LayerSpec(effect="static", params={"color": "#ff0000"})])
    return loop, clock


def run(loop, clock, seconds):
    for _ in range(int(seconds * 60)):
        loop.step(clock.now)
        clock.now += 1 / 60


def level(loop):
    return int(loop.latest_frames()["keyboard"][:, 0].max())


def test_away_fades_to_black_and_back():
    loop, clock = make()
    loop.set_fade(0.5)
    run(loop, clock, 0.2)
    assert level(loop) == 255
    loop.set_away(True)
    run(loop, clock, 0.25)
    assert 0 < level(loop) < 255
    run(loop, clock, 0.5)
    assert level(loop) == 0
    loop.set_away(False)
    run(loop, clock, 0.6)
    assert level(loop) == 255


def test_zero_fade_is_instant():
    loop, clock = make()
    loop.set_fade(0.0)
    run(loop, clock, 0.1)
    loop.set_away(True)
    run(loop, clock, 0.05)
    assert level(loop) == 0


def test_crossfade_blends_old_into_new():
    loop, clock = make()
    loop.set_fade(0.5)
    run(loop, clock, 0.2)
    loop.begin_crossfade()
    for g in loop.groups:
        loop.set_layers(g.key, [LayerSpec(effect="static", params={"color": "#0000ff"})])
    run(loop, clock, 0.25)
    frame = loop.latest_frames()["keyboard"]
    assert 0 < frame[:, 0].max() < 255 and 0 < frame[:, 2].max() < 255
    run(loop, clock, 0.5)
    frame = loop.latest_frames()["keyboard"]
    assert frame[:, 0].max() == 0 and frame[:, 2].max() == 255
