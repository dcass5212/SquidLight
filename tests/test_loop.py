import time

import numpy as np
import pytest

from rgbapp.engine import Engine, LayerSpec, LoopConfig, RenderLoop
from rgbapp.engine.backend import ConnectionState, FakeBackend
from rgbapp.engine.devices import build_groups


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(fps=30.0, **backend_kw):
    clock = Clock()
    backend = FakeBackend(clock=clock, **backend_kw)
    backend.connect()
    loop = RenderLoop(backend, build_groups(backend.devices()), LoopConfig(fps=fps), clock=clock)
    return loop, backend, clock


def run_frames(loop, clock, seconds, fps=30.0):
    for _ in range(int(round(seconds * fps))):
        loop.step(clock.now)
        clock.now += 1.0 / fps


def test_static_layer_is_written_once_then_skipped_as_unchanged():
    loop, backend, clock = make()
    loop.set_layers("keyboard", [LayerSpec("static", {"color": "#00ff00"})])
    run_frames(loop, clock, 2.0)
    kb = backend.writes_for("fake-keyboard")
    assert len(kb) == 1
    assert (kb[0][1] == [0, 255, 0]).all()
    assert loop.stats.skipped_unchanged >= 50


def test_write_never_exceeds_the_cap_per_device():
    loop, backend, clock = make(fps=60.0)  # render faster than any device cap
    for key in ("keyboard", "memory"):
        loop.set_layers(key, [LayerSpec("rainbow_wave")])
    run_frames(loop, clock, 3.0, fps=60.0)
    for dev_id, cap in (("fake-keyboard", 30.0), ("fake-dram-0", 15.0), ("fake-dram-1", 15.0)):
        times = [w[2] for w in backend.writes_for(dev_id)]
        gaps = np.diff(times)
        assert len(times) <= cap * 3.0 + 1, dev_id
        assert gaps.min() >= 1.0 / cap - 0.002 - 1e-9, dev_id  # never closer than the cap
        assert len(times) >= cap * 3.0 * 0.9, dev_id  # and not starved either


def test_write_hz_override_is_honoured():
    clock = Clock()
    backend = FakeBackend(clock=clock)
    backend.connect()
    cfg = LoopConfig(fps=60.0, write_hz={"keyboard": 60.0})
    loop = RenderLoop(backend, build_groups(backend.devices()), cfg, clock=clock)
    loop.set_layers("keyboard", [LayerSpec("rainbow_wave")])
    run_frames(loop, clock, 2.0, fps=60.0)
    assert len(backend.writes_for("fake-keyboard")) >= 110


def test_brightness_master_and_group_scale_the_output():
    loop, backend, clock = make()
    loop.set_layers("memory", [LayerSpec("static", {"color": "#ffffff"})])
    loop.set_brightness("memory", 0.5)
    loop.set_master_brightness(0.5)
    run_frames(loop, clock, 0.2)
    assert (backend.writes_for("fake-dram-0")[-1][1] == 64).all()  # 255 * 0.25 = 63.75 -> 64


def test_frames_are_split_per_ram_stick_with_the_right_length():
    loop, backend, clock = make()
    loop.set_layers("memory", [LayerSpec("rainbow_wave")])
    run_frames(loop, clock, 0.5)
    ram = [w for w in backend.writes if w[0].startswith("fake-dram")]
    assert {w[0] for w in ram} == {"fake-dram-0", "fake-dram-1"}
    assert all(w[1].shape == (10, 3) and w[1].dtype == np.uint8 for w in ram)


def test_a_failing_device_sits_out_then_recovers_and_the_loop_survives():
    loop, backend, clock = make()
    loop.set_layers("keyboard", [LayerSpec("rainbow_wave")])
    backend.fail_writes = True
    run_frames(loop, clock, 1.0)
    # 4 devices (keyboard, 2 sticks, board) fail once each, then sit out for 3 s
    # instead of failing again on every one of the 30 frames.
    assert loop.stats.write_errors == 4
    assert not backend.writes
    backend.fail_writes = False
    run_frames(loop, clock, 4.0)  # past the 3 s sit-out
    assert backend.writes_for("fake-keyboard")


def test_disconnect_then_reconnect_resends_the_current_frame():
    loop, backend, clock = make()
    loop.set_layers("keyboard", [LayerSpec("static", {"color": "#0000ff"})])
    run_frames(loop, clock, 0.5)
    assert len(backend.writes_for("fake-keyboard")) == 1

    backend.set_connected(False)
    backend.can_reconnect = False
    run_frames(loop, clock, 1.0)
    assert len(backend.writes_for("fake-keyboard")) == 1  # nothing written while down
    assert loop.stats.reconnects == 0

    backend.can_reconnect = True
    run_frames(loop, clock, 0.5)
    assert loop.stats.reconnects == 1
    kb = backend.writes_for("fake-keyboard")
    assert len(kb) == 2 and (kb[1][1] == [0, 0, 255]).all()  # same static frame, sent again


def test_a_broken_layer_does_not_stop_other_groups():
    loop, backend, clock = make()
    loop.set_layers("keyboard", [LayerSpec("nonexistent")])
    loop.set_layers("memory", [LayerSpec("static", {"color": "#ffffff"})])
    run_frames(loop, clock, 0.3)
    assert backend.writes_for("fake-dram-0")


def test_threaded_loop_runs_paced_and_stops_cleanly():
    backend = FakeBackend()
    backend.connect()
    loop = RenderLoop(backend, build_groups(backend.devices()), LoopConfig(fps=30.0))
    loop.set_layers("keyboard", [LayerSpec("rainbow_wave")])
    loop.start()
    time.sleep(1.0)
    loop.stop()
    assert not loop._thread.is_alive()
    frames = loop.stats.frames
    assert 20 <= frames <= 36  # ~30 fps; generous bounds so CI jitter cannot fail it
    assert loop.stats.avg_frame_ms < 10.0
    time.sleep(0.2)
    assert loop.stats.frames == frames  # really stopped


def test_engine_lifecycle_closes_the_backend():
    backend = FakeBackend()
    engine = Engine(backend)
    with engine:
        assert engine.connection_state is ConnectionState.CONNECTED
        assert {g.key for g in engine.groups} == {"keyboard", "memory", "mainboard"}
        engine.set_layers("keyboard", [LayerSpec("rainbow_wave")])
        time.sleep(0.3)
    assert backend.closed
    assert backend.writes_for("fake-keyboard")
    with pytest.raises(RuntimeError):
        engine.set_layers("keyboard", [])


def test_engine_start_failure_cleans_up():
    backend = FakeBackend()
    backend.can_reconnect = False
    engine = Engine(backend)
    with pytest.raises(Exception):
        engine.start()
    assert backend.closed or backend.state is ConnectionState.DISCONNECTED
