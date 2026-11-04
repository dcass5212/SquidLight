"""EngineHost and the loop controls it relies on (on/off, pause, preview frames, health)."""

import time

import numpy as np

from rgbapp.engine import Engine, EngineHost, LayerSpec, LoopConfig, RenderLoop
from rgbapp.engine.backend import BackendError, FakeBackend
from rgbapp.engine.devices import build_groups


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_loop():
    clock = Clock()
    backend = FakeBackend(clock=clock)
    backend.connect()
    loop = RenderLoop(backend, build_groups(backend.devices()), LoopConfig(), clock=clock)
    return loop, backend, clock


def frames(loop, clock, n):
    for _ in range(n):
        loop.step(clock.now)
        clock.now += 1 / 30


# -- loop controls


def test_disabled_group_writes_black_and_keeps_its_brightness():
    loop, backend, clock = make_loop()
    loop.set_layers("keyboard", [LayerSpec("static", {"color": "#ffffff"})])
    loop.set_brightness("keyboard", 0.7)
    loop.set_enabled("keyboard", False)
    frames(loop, clock, 3)
    assert (backend.writes_for("fake-keyboard")[-1][1] == 0).all()
    loop.set_enabled("keyboard", True)
    frames(loop, clock, 3)
    assert (backend.writes_for("fake-keyboard")[-1][1] == 179).all()  # 255 * 0.7 = 178.5, rounds up


def test_latest_frames_match_what_was_sent():
    loop, backend, clock = make_loop()
    loop.set_layers("memory", [LayerSpec("static", {"color": "#102030"})])
    frames(loop, clock, 2)
    out = loop.latest_frames()
    assert set(out) == {"keyboard", "memory", "mainboard"}
    assert out["memory"].shape == (20, 3) and out["memory"].dtype == np.uint8
    assert (out["memory"] == [0x10, 0x20, 0x30]).all()


def test_pause_restores_devices_then_resume_retakes_and_resends():
    loop, backend, clock = make_loop()
    loop.set_layers("keyboard", [LayerSpec("static", {"color": "#00ff00"})])
    frames(loop, clock, 3)
    assert len(backend.writes_for("fake-keyboard")) == 1

    loop.pause()
    frames(loop, clock, 30)
    assert backend.closed and loop.paused
    assert loop.group_health("keyboard") == "paused"
    assert len(backend.writes_for("fake-keyboard")) == 1  # no writes, no auto-reconnect
    assert "keyboard" in loop.latest_frames()  # the preview keeps rendering

    loop.resume()
    frames(loop, clock, 3)
    assert not loop.paused and not backend.closed
    kb = backend.writes_for("fake-keyboard")
    assert len(kb) == 2 and (kb[1][1] == [0, 255, 0]).all()


def test_resume_while_server_is_down_keeps_retrying():
    loop, backend, clock = make_loop()
    loop.set_layers("keyboard", [LayerSpec("static", {"color": "#0000ff"})])
    loop.pause()
    frames(loop, clock, 2)
    backend.can_reconnect = False
    loop.resume()
    frames(loop, clock, 5)
    assert loop.group_health("keyboard") == "disconnected"
    backend.can_reconnect = True
    frames(loop, clock, 3)
    assert loop.group_health("keyboard") == "ok"
    assert backend.writes_for("fake-keyboard")


def test_group_health_reports_a_failing_device():
    loop, backend, clock = make_loop()
    loop.set_layers("keyboard", [LayerSpec("rainbow_wave")])
    backend.fail_writes = True
    frames(loop, clock, 2)
    assert loop.group_health("keyboard") == "error"
    backend.fail_writes = False
    clock.now += 5
    frames(loop, clock, 2)
    assert loop.group_health("keyboard") == "ok"


# -- host


def wait_for(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def test_host_applies_settings_made_before_the_engine_was_up():
    backend = FakeBackend()
    host = EngineHost(Engine(backend))
    host.set_effect("memory", "static")
    host.set_param("memory", "color", "#ff0000")
    host.set_brightness("memory", 0.5)
    host.set_enabled("keyboard", False)
    host.set_master_brightness(1.0)
    host.start()
    try:
        assert host.wait_running(3.0)
        assert host.status().state == "connected"
        assert host.generation == 1
        assert wait_for(lambda: backend.writes_for("fake-dram-0"))
        # the very first frame on the hardware is already the user's effect: no black flash
        assert (backend.writes_for("fake-dram-0")[0][1] == [128, 0, 0]).all()
        assert wait_for(lambda: backend.writes_for("fake-keyboard"))
        assert (backend.writes_for("fake-keyboard")[-1][1] == 0).all()
        # untouched groups get the default effect
        assert host.settings("mainboard").effect == "rainbow_wave"
    finally:
        host.stop()
    assert backend.closed
    assert host.status().state == "stopping"


def test_host_changes_reach_the_hardware_live():
    backend = FakeBackend()
    host = EngineHost(Engine(backend))
    host.start()
    try:
        assert host.wait_running(3.0)
        host.set_effect("keyboard", "static")
        host.set_param("keyboard", "color", "#00ff00")
        assert wait_for(lambda: backend.writes_for("fake-keyboard") and (backend.writes_for("fake-keyboard")[-1][1] == [0, 255, 0]).all())
        host.set_brightness("keyboard", 0.0)
        assert wait_for(lambda: (backend.writes_for("fake-keyboard")[-1][1] == 0).all())
    finally:
        host.stop()


def test_host_remembers_params_per_effect():
    host = EngineHost(Engine(FakeBackend()))
    host.set_effect("keyboard", "static")
    host.set_param("keyboard", "color", "#123456")
    host.set_effect("keyboard", "breathing")
    host.set_effect("keyboard", "static")
    assert host.settings("keyboard").current_params() == {"color": "#123456"}


def test_host_layer_stack_edits_reach_the_hardware_live():
    backend = FakeBackend()
    host = EngineHost(Engine(backend))
    host.set_effect("memory", "static")
    host.set_param("memory", "color", "#ff0000")  # base: red
    host.start()
    try:
        assert host.wait_running(3.0)
        last = lambda: backend.writes_for("fake-dram-0")[-1][1] if backend.writes_for("fake-dram-0") else None
        green = host.add_layer("memory", "static", {"color": "#00ff00"})  # middle
        blue = host.add_layer("memory", "static", {"color": "#0000ff"})  # top
        host.set_layer_props("memory", blue, opacity=0.5)
        assert wait_for(lambda: last() is not None and (last() == [0, 128, 128]).all())  # half blue over green
        host.set_layer_props("memory", blue, blend="add", opacity=1.0)
        assert wait_for(lambda: (last() == [0, 255, 255]).all())
        host.set_layer_props("memory", green, visible=False)
        assert wait_for(lambda: (last() == [255, 0, 255]).all())
        host.move_layer("memory", blue, 0)  # blue to the bottom: red covers it
        assert [s.id for s in host.layers("memory")][0] == blue
        assert wait_for(lambda: (last() == [255, 0, 0]).all())
        host.set_layer_props("memory", host.layers("memory")[1].id, mask=["Corsair DRAM (2)"])  # red only on stick 2
        assert wait_for(lambda: (last() == [0, 0, 255]).all())
        host.remove_layer("memory", green)
        assert len(host.layers("memory")) == 2
        host.set_layer_param("memory", blue, "color", "#ffffff")
        assert wait_for(lambda: (last() == 255).all())
    finally:
        host.stop()


def test_host_layer_effect_switch_remembers_params_and_validates():
    import pytest

    host = EngineHost(Engine(FakeBackend()))
    lid = host.add_layer("keyboard", "breathing", {"color": "#abcdef"})
    host.set_layer_effect("keyboard", lid, "starlight")
    assert host.layers("keyboard")[-1].params == {}
    host.set_layer_effect("keyboard", lid, "breathing")
    assert host.layers("keyboard")[-1].params == {"color": "#abcdef"}
    with pytest.raises(ValueError):
        host.set_layer_props("keyboard", lid, blend="dodge")
    with pytest.raises(KeyError):
        host.set_layer_effect("keyboard", lid, "nope")
    with pytest.raises(KeyError):
        host.remove_layer("keyboard", "missing")
    # the copy handed out is not the host's own
    host.layers("keyboard")[-1].params["color"] = "#000000"
    assert host.layers("keyboard")[-1].params["color"] == "#abcdef"


def test_layers_keep_animation_state_across_edits():
    backend = FakeBackend()
    host = EngineHost(Engine(backend))
    host.start()
    try:
        assert host.wait_running(3.0)
        lid = host.add_layer("keyboard", "starlight")
        comp = host.engine.loop._runtimes["keyboard"].compositor
        effect = next(r.effect for r in comp._layers if r.spec.id == lid)
        host.set_layer_param("keyboard", lid, "density", 0.9)
        host.set_layer_props("keyboard", lid, opacity=0.4)
        assert next(r.effect for r in comp._layers if r.spec.id == lid) is effect
    finally:
        host.stop()


def test_host_retries_until_openrgb_answers():
    backend = FakeBackend()
    backend.can_reconnect = False
    host = EngineHost(Engine(backend), retry_first=0.05)
    host.start()
    try:
        assert wait_for(lambda: host.status().state == "failed")
        assert "Retrying" in host.status().text
        assert host.groups == []
        backend.can_reconnect = True
        assert host.wait_running(3.0)
        assert host.status().state == "connected"
    finally:
        host.stop()


def test_host_pause_shows_in_status_and_reaches_the_loop():
    backend = FakeBackend()
    host = EngineHost(Engine(backend))
    host.start()
    try:
        assert host.wait_running(3.0)
        host.set_paused(True)
        assert host.status().state == "paused"
        assert wait_for(lambda: backend.closed)
        host.set_paused(False)
        assert wait_for(lambda: not backend.closed)
        assert wait_for(lambda: host.status().state == "connected")
    finally:
        host.stop()


def test_stop_async_runs_off_the_calling_thread_and_calls_back():
    backend = FakeBackend()
    host = EngineHost(Engine(backend))
    host.start()
    assert host.wait_running(3.0)
    done = []
    t = host.stop_async(lambda: done.append(True))
    t.join(5.0)
    assert done == [True] and backend.closed


def test_stop_during_a_failing_start_returns_promptly():
    backend = FakeBackend()
    backend.can_reconnect = False
    host = EngineHost(Engine(backend), retry_first=10.0)
    host.start()
    assert wait_for(lambda: host.status().state == "failed")
    started = time.monotonic()
    host.stop()
    assert time.monotonic() - started < 2.0
