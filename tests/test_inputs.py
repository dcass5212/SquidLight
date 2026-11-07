"""M7: input workers (pure parts), the hub, and the input-driven effects. No hardware, no sound card."""

import numpy as np
import pytest

from rgbapp.engine import LayerSpec, LoopConfig, RenderLoop
from rgbapp.engine.backend import FakeBackend
from rgbapp.engine.devices import build_groups
from rgbapp.engine.effects import Ctx, create_effect, get_effect_class
from rgbapp.engine.inputs import InputHub, KeyTracker, StatsSnapshot
from rgbapp.engine.inputs.audio import AudioAnalyzer, AudioSnapshot, N_BANDS
from rgbapp.engine.inputs.keys import KeySnapshot, led_name_for
from rgbapp.engine.inputs.stats import StatsPoller, StatsUnavailable, parse_lhm

SR, BLOCK = 48000, 1024


def sine(freq, amp=0.5, n=BLOCK, phase=0):
    t = (np.arange(n) + phase) / SR
    return amp * np.sin(2 * np.pi * freq * t)


# ---------------------------------------------------------------- audio analysis


def test_silence_is_all_zero():
    a = AudioAnalyzer()
    snap = a.process(np.zeros(BLOCK))
    assert snap.active and snap.level == 0 and snap.bass == 0
    assert not snap.bands.any()


def test_a_tone_lights_its_own_band_and_no_far_one():
    a = AudioAnalyzer()
    for i in range(10):
        snap = a.process(sine(2000, phase=i * BLOCK))
    peak = int(np.argmax(snap.bands))
    edges = np.geomspace(40, 16000, N_BANDS + 1)
    assert edges[peak] * 0.7 < 2000 < edges[peak + 1] * 1.4
    assert snap.bands[peak] > 0.95
    assert snap.bands[0] < 0.2 and snap.bands[-1] < 0.5


def test_bass_tone_drives_bass_and_treble_does_not():
    low, high = AudioAnalyzer(), AudioAnalyzer()
    for i in range(10):
        s_low = low.process(sine(60, phase=i * BLOCK))
        s_high = high.process(sine(5000, phase=i * BLOCK))
    assert s_low.bass > 0.9
    assert s_high.bass < 0.2


def test_normalization_follows_the_loudness_and_values_stay_in_range():
    a = AudioAnalyzer()
    for i in range(10):
        quiet = a.process(sine(1000, amp=0.01, phase=i * BLOCK))
    for i in range(10):
        loud = a.process(sine(1000, amp=0.8, phase=i * BLOCK))
    assert quiet.bands.max() > 0.9  # auto-gain: a quiet song still moves the bars
    for s in (quiet, loud):
        assert 0.0 <= s.bands.min() and s.bands.max() <= 1.0 and 0.0 <= s.level <= 1.0


def test_bands_fall_off_smoothly_after_the_sound_stops():
    a = AudioAnalyzer()
    for i in range(5):
        a.process(sine(1000, phase=i * BLOCK))
    after = [a.process(np.zeros(BLOCK)).bands.max() for _ in range(40)]
    assert after[0] > 0.5 and after[-1] < 0.01
    assert all(x >= y for x, y in zip(after, after[1:]))


# ---------------------------------------------------------------- keys


def test_key_tracker_events_held_keys_and_expiry():
    k = KeyTracker()
    k.press("Key: A", 10.0)
    k.press("Key: A", 10.1)  # auto-repeat is not a new press
    k.press("Key: B", 10.2)
    snap = k.snapshot(10.5)
    assert snap.pressed == {"Key: A", "Key: B"}
    assert [e.name for e in snap.events] == ["Key: A", "Key: B"]
    assert snap.events[0].age == pytest.approx(0.5)
    assert snap.events[0].seq < snap.events[1].seq
    k.release("Key: A")
    assert k.snapshot(10.6).pressed == {"Key: B"}
    k.press("Key: A", 11.0)  # pressing again after a release is a new press
    assert len(k.snapshot(11.1).events) == 3
    assert k.snapshot(20.0).events == ()  # old presses are forgotten


def test_virtual_keys_map_to_k70_led_names():
    assert led_name_for(0x41) == "Key: A"
    assert led_name_for(0x31) == "Key: 1"
    assert led_name_for(0x70) == "Key: F1"
    assert led_name_for(0x20) == "Key: Space"
    assert led_name_for(0x0D) == "Key: Enter"
    assert led_name_for(0x0D, extended=True) == "Key: Number Pad Enter"
    assert led_name_for(0x65) == "Key: Number Pad 5"
    assert led_name_for(0xA0) == "Key: Left Shift"
    assert led_name_for(0xDC) == "Key: \\ (ANSI)"
    assert led_name_for(0xFF) is None


def test_every_mapped_name_exists_in_the_k70_layout():
    import json
    from rgbapp.engine.layout import LAYOUT_DIR

    names = {e["name"] for e in json.loads((LAYOUT_DIR / "k70_pro_ansi.json").read_text(encoding="utf-8"))["leds"]}
    from rgbapp.engine.inputs import keys

    missing = {led_name_for(vk) for vk in list(keys._NAMES) + [0x5C]} - names - {None}
    assert not missing, missing


# ---------------------------------------------------------------- stats


LHM_TREE = {
    "Text": "Sensor", "Children": [{"Text": "PC", "Children": [
        {"Text": "Ryzen", "ImageURL": "images_icon/cpu.png", "Children": [
            {"Text": "Temperatures", "Children": [
                {"Text": "Core #1", "Value": "70,0 °C", "Children": []},
                {"Text": "Core (Tctl/Tdie)", "Value": "61.5 °C", "Children": []}]},
            {"Text": "Load", "Children": [{"Text": "CPU Total", "Value": "12.0 %", "Children": []}]}]},
        {"Text": "NVIDIA GeForce", "ImageURL": "images_icon/nvidia.png", "Children": [
            {"Text": "Temperatures", "Children": [{"Text": "GPU Core", "Value": "55.0 °C", "Children": []}]},
            {"Text": "Load", "Children": [
                {"Text": "GPU Memory", "Value": "5.0 %", "Children": []},
                {"Text": "GPU Core", "Value": "40.0 %", "Children": []}]}]}]}]}


def test_parse_libre_hardware_monitor_tree():
    got = parse_lhm(LHM_TREE)
    assert got["cpu_temp"] == 61.5  # the package/Tctl sensor wins over the hotter core
    assert got["gpu_temp"] == 55.0
    assert got["cpu_load"] == pytest.approx(0.12)
    assert got["gpu_load"] == pytest.approx(0.40)


class Prov:
    need, interval = "sensors", 1.0

    def __init__(self, result):
        self.result = result

    def read(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_poller_keeps_values_and_reports_an_unavailable_source():
    ok = StatsPoller(Prov({"cpu_temp": 50.0}))
    ok.poll_once()
    assert ok.read() == ({"cpu_temp": 50.0}, "")
    bad = StatsPoller(Prov(StatsUnavailable("no source")))
    bad.poll_once()
    assert bad.read() == ({}, "no source")
    crash = StatsPoller(Prov(RuntimeError("boom")))
    crash.poll_once()  # must not raise
    assert crash.read()[1]


# ---------------------------------------------------------------- the hub


class FakeWorker:
    def __init__(self, name, log):
        self.name, self.log, self.value = name, log, f"snap-{name}"

    def start(self):
        self.log.append(("start", self.name))

    def stop(self):
        self.log.append(("stop", self.name))

    def snapshot(self, now):
        return self.value


def fake_hub(log):
    return InputHub({n: (lambda n=n: FakeWorker(n, log)) for n in ("audio", "keys")})


def test_hub_starts_and_stops_workers_to_match_needs():
    log = []
    hub = fake_hub(log)
    hub.set_needs({"audio"})
    hub.set_needs({"audio"})  # no change: no restart
    assert log == [("start", "audio")]
    hub.set_needs({"audio", "keys"})
    hub.set_needs({"keys"})
    assert log == [("start", "audio"), ("start", "keys"), ("stop", "audio")]
    assert hub.snapshot(0.0) == {"keys": "snap-keys"}
    hub.shutdown()
    assert hub.running == set() and log[-1] == ("stop", "keys")


def test_hub_survives_a_worker_that_will_not_start():
    class Broken:
        def start(self):
            raise OSError("no audio device")

    hub = InputHub({"audio": Broken})
    hub.set_needs({"audio"})
    assert hub.running == set() and hub.snapshot(0) == {}


# ---------------------------------------------------------------- the loop wiring


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def make_loop(log):
    clock = Clock()
    backend = FakeBackend(clock=clock)
    backend.connect()
    loop = RenderLoop(backend, build_groups(backend.devices()), LoopConfig(), clock=clock, inputs=fake_hub(log))
    return loop, clock


def test_inputs_run_only_while_a_visible_layer_needs_them():
    log = []
    loop, clock = make_loop(log)
    loop.set_layers("keyboard", [LayerSpec("rainbow_wave")])
    loop.step(clock.now)
    assert log == []  # nothing needs input: no worker, no cost

    layer = LayerSpec("reactive_typing")
    loop.set_layers("keyboard", [LayerSpec("rainbow_wave"), layer])
    loop.step(clock.now)
    assert log == [("start", "keys")]

    layer.visible = False
    loop.set_layers("keyboard", [LayerSpec("rainbow_wave"), layer])
    loop.step(clock.now)
    assert log[-1] == ("stop", "keys")

    layer.visible = True
    loop.set_layers("keyboard", [layer])
    loop.step(clock.now)
    loop.set_enabled("keyboard", False)  # the device is switched off: no need to listen either
    loop.step(clock.now)
    assert log[-1] == ("stop", "keys")
    loop.set_enabled("keyboard", True)
    loop.step(clock.now)
    loop.pause()
    loop.step(clock.now)
    assert log[-1] == ("stop", "keys") and loop.inputs.running == set()
    loop.stop()


def test_meter_asks_only_for_the_input_its_source_needs():
    cls = get_effect_class("cpu_gpu_meter")
    assert cls.required_inputs(cls.resolve_params({"source": "CPU load"})) == {"cpu"}
    assert cls.required_inputs(cls.resolve_params({"source": "GPU load"})) == {"sensors"}


def test_a_layer_notice_reaches_the_loop():
    log = []
    loop, clock = make_loop(log)
    loop.set_layers("keyboard", [LayerSpec("temperature_gradient", id="t1")])
    loop.step(clock.now)
    assert loop.notices()["keyboard"]["t1"]  # no sensor source: the user is told


# ---------------------------------------------------------------- effects


K70 = ["Profile", "Light"] + [f"Key: {c}" for c in "QWERTYUIOP"] + ["Key: Space", "Key: Space", "Key: Space"]


def key_ctx(inputs=None, t=0.0, dt=1 / 30):
    n = len(K70)
    x = np.concatenate([[0.0, 0.05], np.linspace(0.1, 0.9, 10), [0.4, 0.5, 0.6]])
    y = np.concatenate([[0.0, 0.0], np.zeros(10) + 0.4, np.ones(3)])
    return Ctx(t=t, dt=dt, led_positions=np.stack([x, y], axis=1).astype(np.float32), inputs=inputs or {},
               led_names=tuple(K70), aspect=3.0)


def keys_input(names=(), pressed=(), seq0=1, age=0.0):
    ev = tuple(__import__("rgbapp.engine.inputs.keys", fromlist=["KeyEvent"]).KeyEvent(seq0 + i, n, age) for i, n in enumerate(names))
    return {"keys": KeySnapshot(frozenset(pressed), ev, True)}


def run(effect_id, ctx_fn, frames, **raw):
    eff = create_effect(effect_id)
    params = get_effect_class(effect_id).resolve_params(raw)
    return eff, [eff.render(ctx_fn(i), params) for i in range(frames)]


def test_reactive_typing_flashes_the_pressed_key_and_fades():
    eff = create_effect("reactive_typing")
    p = get_effect_class("reactive_typing").resolve_params({"color": "#ffffff", "hold": False, "fade": 0.5})
    idle = eff.render(key_ctx(), p)
    assert idle.max() == 0
    hit = eff.render(key_ctx(keys_input(["Key: E"])), p)
    e = K70.index("Key: E")
    assert (hit[e] == 1).all() and hit.sum() == pytest.approx(3.0)  # only that key
    # the same event, seen again next frame, is not a second press
    later = [eff.render(key_ctx(keys_input(["Key: E"], age=0.1)), p) for _ in range(15)]
    assert later[0][e][0] < 1.0 and later[-1][e][0] < 0.05
    assert all(a[e][0] >= b[e][0] for a, b in zip(later, later[1:]))


def test_reactive_typing_holds_while_pressed_and_lights_all_spacebar_leds():
    eff = create_effect("reactive_typing")
    p = get_effect_class("reactive_typing").resolve_params({"color": "#ffffff", "hold": True, "fade": 0.3})
    for _ in range(30):
        out = eff.render(key_ctx(keys_input(pressed=["Key: Space"])), p)
    space = [i for i, n in enumerate(K70) if n == "Key: Space"]
    assert len(space) == 3 and (out[space] == 1).all() and out.sum() == pytest.approx(9.0)


def test_reactive_typing_on_a_device_without_keys_still_responds():
    n = 8
    ctx = Ctx(0.0, 1 / 30, np.stack([np.linspace(0, 1, n), np.full(n, 0.5)], 1).astype(np.float32),
              keys_input(["Key: A"]), tuple(f"LED {i}" for i in range(n)), 1.0)
    out = create_effect("reactive_typing").render(ctx, get_effect_class("reactive_typing").resolve_params({}))
    assert out.max() > 0.5


def test_ripple_expands_from_the_key_and_dies_out():
    eff = create_effect("ripple")
    p = get_effect_class("ripple").resolve_params({"color": "#ffffff", "speed": 1.0, "width": 0.2, "lifetime": 1.0})
    first = eff.render(key_ctx(keys_input(["Key: Q"])), p)
    q = K70.index("Key: Q")
    assert first[q].min() > 0.9  # starts at the pressed key
    dist = []
    for i in range(1, 25):
        out = eff.render(key_ctx(keys_input(["Key: Q"], age=0.0), dt=1 / 30), p)
        lit = np.flatnonzero(out[:, 0] > 0.5)
        dist.append(max((np.linalg.norm((key_ctx().led_positions[j] - key_ctx().led_positions[q]) * [3.0, 1.0]) for j in lit), default=0))
    assert dist[10] > dist[0]  # the ring moved outward
    for _ in range(40):
        out = eff.render(key_ctx(dt=1 / 30), p)
    assert out.max() == 0  # gone after its lifetime


def test_audio_spectrum_draws_bars_from_the_bottom_on_a_keyboard():
    bands = np.zeros(N_BANDS, dtype=np.float32)
    bands[:] = np.linspace(1.0, 0.0, N_BANDS)  # tall on the left, short on the right
    ctx = key_ctx({"audio": AudioSnapshot(bands, 0.5, 0.5, True)})
    out = create_effect("audio_spectrum").render(ctx, get_effect_class("audio_spectrum").resolve_params({"background": "#000000"}))
    bottom = [i for i, y in enumerate(ctx.led_positions[:, 1]) if y == 1.0]  # space row
    top = [i for i, y in enumerate(ctx.led_positions[:, 1]) if y == 0.4 and ctx.led_positions[i, 0] > 0.7]
    assert out[bottom].max() > 0.5  # the bottom row is lit under the tall bars
    assert out[top].max() < 0.05  # a short bar doesn't reach the upper row on the right


def test_audio_spectrum_on_a_strip_uses_brightness_and_silence_is_dark():
    n = 12
    pos = np.stack([np.linspace(0, 1, n), np.full(n, 0.5)], 1).astype(np.float32)
    bands = np.zeros(N_BANDS, dtype=np.float32)
    bands[0] = 1.0
    eff = create_effect("audio_spectrum")
    p = get_effect_class("audio_spectrum").resolve_params({})
    out = eff.render(Ctx(0, 1 / 30, pos, {"audio": AudioSnapshot(bands, 0, 0, True)}), p)
    assert out[0].max() > out[-1].max() and out[-1].max() < 0.1
    assert eff.render(Ctx(0, 1 / 30, pos, {"audio": AudioSnapshot(active=True)}), p).max() == 0
    capture_failed = AudioSnapshot(error="Can't capture audio: no device")
    eff.render(Ctx(0, 1 / 30, pos, {"audio": capture_failed}), p)
    assert "capture" in eff.notice


def test_audio_pulse_follows_the_bass_and_is_uniform():
    eff = create_effect("audio_pulse")
    p = get_effect_class("audio_pulse").resolve_params({"background": "#000000"})
    quiet = eff.render(key_ctx({"audio": AudioSnapshot(bass=0.0, active=True)}), p)
    loud = eff.render(key_ctx({"audio": AudioSnapshot(bass=1.0, active=True)}), p)
    assert quiet.max() == 0
    assert loud.min() > 0.9 and (loud == loud[0]).all()


def test_meter_fills_with_the_load_and_notices_a_missing_source():
    n = 10
    pos = np.stack([np.linspace(0, 1, n), np.full(n, 0.5)], 1).astype(np.float32)

    def lit(load, source="CPU load", errors=None):
        eff = create_effect("cpu_gpu_meter")
        p = get_effect_class("cpu_gpu_meter").resolve_params({"source": source, "smoothing": 0.0, "background": "#000000"})
        vals = {} if load is None else {"cpu_load": load, "gpu_load": load}
        out = eff.render(Ctx(0, 1 / 30, pos, {"stats": StatsSnapshot(vals, errors or {})}), p)
        return int((out.max(axis=1) > 0.5).sum()), eff

    assert lit(0.0)[0] == 0
    assert lit(0.5)[0] == 5
    assert lit(1.0)[0] == 10
    count, eff = lit(None, "GPU load", {"sensors": "No sensor source"})
    assert count == 0 and eff.notice == "No sensor source"


def test_meter_color_runs_from_green_to_red():
    pos = np.stack([np.linspace(0, 1, 10), np.full(10, 0.5)], 1).astype(np.float32)
    eff = create_effect("cpu_gpu_meter")
    p = get_effect_class("cpu_gpu_meter").resolve_params({"smoothing": 0.0})
    low = eff.render(Ctx(0, 1 / 30, pos, {"stats": StatsSnapshot({"cpu_load": 0.1})}), p)[0]
    high = eff.render(Ctx(0, 1 / 30, pos, {"stats": StatsSnapshot({"cpu_load": 1.0})}), p)[0]
    assert low[1] > low[0] and high[0] > high[1]


def test_temperature_gradient_cool_to_hot_and_no_source_notice():
    pos = np.zeros((6, 2), dtype=np.float32)
    p = get_effect_class("temperature_gradient").resolve_params({"smoothing": 0.0, "low": 30, "high": 90})

    def color(temp, errors=None):
        eff = create_effect("temperature_gradient")
        vals = {} if temp is None else {"cpu_temp": temp}
        return eff.render(Ctx(0, 1 / 30, pos, {"stats": StatsSnapshot(vals, errors or {})}), p), eff

    cold, _ = color(25)
    hot, _ = color(95)
    mid, _ = color(60)
    assert cold[0][2] > cold[0][0] and hot[0][0] > hot[0][2]  # blue then red
    assert not np.allclose(mid, cold) and not np.allclose(mid, hot)
    dim, eff = color(None, {"sensors": "start LibreHardwareMonitor"})
    assert dim.max() < 0.1 and eff.notice == "start LibreHardwareMonitor"


def test_input_effects_render_with_no_inputs_at_all():
    for eid in ("reactive_typing", "ripple", "audio_spectrum", "audio_pulse", "cpu_gpu_meter", "temperature_gradient"):
        out = create_effect(eid).render(key_ctx(), get_effect_class(eid).resolve_params({}))
        assert out.shape == (len(K70), 3) and out.dtype == np.float32
