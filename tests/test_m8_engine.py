"""M8 engine pieces: write-rate cap, resume reapply, input options, restart, settings file."""

import json

import numpy as np

from rgbapp.engine import Engine, EngineHost, FakeBackend, LayerSpec, LoopConfig, RenderLoop
from rgbapp.engine.devices import build_groups
from rgbapp.engine.inputs import InputHub
from rgbapp.engine.loop import STALL_REASSERT_S
from rgbapp.model.app_settings import AppSettings, AppSettingsStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(fps=60.0, cap=None):
    clock = Clock()
    backend = FakeBackend(clock=clock)
    backend.connect()
    loop = RenderLoop(backend, build_groups(backend.devices()), LoopConfig(fps=fps, max_write_hz=cap), clock=clock)
    return loop, backend, clock


def run_frames(loop, clock, seconds, fps=60.0):
    for _ in range(int(round(seconds * fps))):
        loop.step(clock.now)
        clock.now += 1.0 / fps


def writes_per_second(backend, dev, seconds):
    return len(backend.writes_for(dev)) / seconds


# ---------------------------------------------------------------- write-rate cap


def test_cap_sets_the_keyboard_rate_but_never_raises_smbus_devices():
    loop, backend, clock = make(cap=60)
    for key in ("keyboard", "memory"):
        loop.set_layers(key, [LayerSpec("rainbow_wave")])
    run_frames(loop, clock, 2.0)
    assert writes_per_second(backend, "fake-keyboard", 2.0) > 50  # keyboard at the 60 cap
    assert writes_per_second(backend, "fake-dram-0", 2.0) <= 15.5  # RAM stays at its 15 default


def test_a_low_cap_slows_everything_down_and_applies_live():
    loop, backend, clock = make(cap=None)
    for key in ("keyboard", "memory"):
        loop.set_layers(key, [LayerSpec("rainbow_wave")])
    loop.set_write_cap(15)
    run_frames(loop, clock, 2.0)
    for dev in ("fake-keyboard", "fake-dram-0"):
        times = [w[2] for w in backend.writes_for(dev)]
        assert np.diff(times).min() >= 1 / 15 - 0.003, dev


def test_no_cap_keeps_the_type_defaults():
    cfg = LoopConfig()
    backend = FakeBackend()
    groups = {g.key: g for g in build_groups(backend.devices())}
    assert cfg.write_hz_for(groups["keyboard"]) == 30
    assert cfg.write_hz_for(groups["memory"]) == 15


# ---------------------------------------------------------------- resume reapply


def test_request_resend_rewrites_unchanged_frames_and_reasserts_direct_mode():
    loop, backend, clock = make()
    loop.set_layers("keyboard", [LayerSpec("static", {"color": "#ff0000"})])
    run_frames(loop, clock, 1.0)
    assert len(backend.writes_for("fake-keyboard")) == 1  # static: written once
    loop.request_resend(reassert=True)
    run_frames(loop, clock, 1.0)
    assert len(backend.writes_for("fake-keyboard")) == 2
    assert backend.reasserts == 1


def test_a_long_stall_counts_as_resume_and_reapplies():
    clock = Clock()
    backend = FakeBackend(clock=clock)
    backend.connect()
    frames = []

    def sleep(s):
        clock.now += s
        frames.append(clock.now)
        if len(frames) == 3:
            clock.now += STALL_REASSERT_S + 5  # the PC slept
        if len(frames) >= 8:
            loop._stop.set()

    loop = RenderLoop(backend, build_groups(backend.devices()), LoopConfig(fps=30), clock=clock, sleep=sleep)
    loop.set_layers("keyboard", [LayerSpec("static", {"color": "#00ff00"})])
    loop.run()
    assert backend.reasserts == 1
    assert len(backend.writes_for("fake-keyboard")) == 2  # first frame, then the full resend


def test_engine_and_host_reapply_reach_the_loop():
    host = EngineHost(Engine(FakeBackend()))
    host.start()
    assert host.wait_running(3.0)
    try:
        host.reapply()
        assert host.engine.loop._resend == "reassert"
    finally:
        host.stop()


# ---------------------------------------------------------------- input options


class Worker:
    def __init__(self, name, log):
        self.name, self.log = name, log

    def start(self):
        self.log.append(("start", self.name))

    def stop(self):
        self.log.append(("stop", self.name))

    def snapshot(self, now):
        return None


def test_changing_the_audio_device_rebuilds_a_running_audio_worker_on_the_next_frame():
    log = []
    hub = InputHub({"audio": lambda: Worker("audio", log), "keys": lambda: Worker("keys", log)})
    hub.set_needs({"audio", "keys"})
    hub.configure(audio_device="Speakers (USB)")
    assert hub.audio_device == "Speakers (USB)"
    assert log.count(("stop", "audio")) == 0  # nothing happens off the render thread
    hub.set_needs({"audio", "keys"})
    assert log.count(("stop", "audio")) == 1 and log.count(("start", "audio")) == 2
    assert log.count(("start", "keys")) == 1  # untouched
    hub.configure(audio_device="Speakers (USB)")  # same value: no rebuild
    hub.set_needs({"audio", "keys"})
    assert log.count(("start", "audio")) == 2


def test_engine_keeps_input_options_for_the_next_start():
    engine = Engine(FakeBackend())
    engine.set_input_options("Headphones", "http://pc:8085/data.json")
    hub = InputHub(audio_device=engine.audio_device, lhm_url=engine.lhm_url)
    assert hub.audio_device == "Headphones" and hub.lhm_url.startswith("http://pc")


# ---------------------------------------------------------------- restart


def test_restart_stops_restores_reconfigures_and_comes_back():
    backend = FakeBackend()
    host = EngineHost(Engine(backend))
    host.start()
    assert host.wait_running(3.0)
    gen = host.generation
    seen = []
    t = host.restart(reconfigure=lambda engine: seen.append(backend.closed))
    t.join(5.0)
    assert seen == [True]  # devices were restored before reconfiguring
    assert host.wait_running(3.0)
    assert host.generation >= gen + 2  # once going down, once coming back
    assert backend.connect_calls == 2
    host.stop()


def test_quit_during_restart_wins():
    host = EngineHost(Engine(FakeBackend()))
    host.start()
    assert host.wait_running(3.0)
    host.stop()
    host.restart().join(5.0)
    assert host.status().state == "stopping"


# ---------------------------------------------------------------- settings.json


def test_settings_round_trip_and_keep_unknown_keys(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"future_option": 7, "accent": "#112233"}), encoding="utf-8")
    store = AppSettingsStore(path)
    s = store.load()
    assert s.accent == "#112233" and s.openrgb_port == 6742
    store.save(AppSettings(accent="#AABBCC", write_cap_hz=60, audio_device="Speakers"))
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["future_option"] == 7 and doc["accent"] == "#aabbcc" and doc["version"] == 1
    assert store.load().write_cap_hz == 60 and (tmp_path / "settings.json.bak").exists()


def test_bad_values_fall_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"openrgb_port": "nope", "write_cap_hz": 999, "accent": "green", "lhm_url": " "}),
        encoding="utf-8",
    )
    s = AppSettingsStore(path).load()
    d = AppSettings()
    assert (s.openrgb_port, s.write_cap_hz, s.accent, s.lhm_url) == (
        d.openrgb_port, d.write_cap_hz, d.accent, d.lhm_url
    )


def test_a_broken_file_loads_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert AppSettingsStore(path).load() == AppSettings()
