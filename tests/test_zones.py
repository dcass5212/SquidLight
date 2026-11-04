"""M5: ARGB zone LED counts (resize, persistence, applied on connect) and "sync with keyboard"."""

import time
from types import SimpleNamespace as NS

import numpy as np
import pytest
from openrgb.utils import DeviceType, ModeColors, PacketType, ZoneType

from rgbapp.engine import Engine, EngineHost
from rgbapp.engine.backend import BackendError, FakeBackend, OpenRGBBackend
from rgbapp.engine.core import zone_key
from rgbapp.engine.devices import build_groups
from rgbapp.model.store import SettingsStore

BOARD = "fake-board"
HEADER1 = "Addressable RGB Header 1"


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def board_group(backend):
    return {g.key: g for g in build_groups(backend.devices())}["mainboard"]


# -- FakeBackend / groups


def test_unsized_board_still_gets_an_empty_group_so_its_page_can_exist():
    backend = FakeBackend(board_zone_sizes=(0, 0, 0))
    g = board_group(backend)
    assert g.led_count == 0
    assert [z.label for z in g.zone_refs] == [f"Addressable RGB Header {i}" for i in (1, 2, 3)]
    assert all(z.info.resizable for z in g.zone_refs)


def test_fake_resize_moves_later_zones_and_renumbers_leds():
    backend = FakeBackend(board_zone_sizes=(0, 12, 0))
    backend.connect()
    backend.resize_zone(BOARD, HEADER1, 30)
    g = board_group(backend)
    assert g.led_count == 42
    ref = {z.device_zone: z for z in g.zone_refs}
    assert list(ref[HEADER1].indices) == list(range(30))
    assert list(ref["Addressable RGB Header 2"].indices) == list(range(30, 42))


def test_fake_resize_rejects_bad_requests():
    backend = FakeBackend()
    with pytest.raises(BackendError):  # not connected
        backend.resize_zone(BOARD, HEADER1, 5)
    backend.connect()
    with pytest.raises(BackendError):
        backend.resize_zone(BOARD, HEADER1, 10_000)
    with pytest.raises(BackendError):
        backend.resize_zone(BOARD, "No such zone", 5)
    with pytest.raises(BackendError):  # a RAM stick's zone is fixed
        backend.resize_zone("fake-dram-0", "Corsair DRAM", 5)


def test_memory_zones_are_labeled_as_sticks_with_unique_mask_names():
    mem = {g.key: g for g in build_groups(FakeBackend().devices())}["memory"]
    assert [z.label for z in mem.zone_refs] == ["Stick 1", "Stick 2"]
    assert [z.name for z in mem.zone_refs] == ["Corsair DRAM", "Corsair DRAM (2)"]
    assert not any(z.info.resizable for z in mem.zone_refs)


# -- engine


def test_engine_applies_saved_counts_on_start_and_renders_them():
    backend = FakeBackend(board_zone_sizes=(0, 0, 0))
    engine = Engine(backend)
    engine.zone_sizes[zone_key(BOARD, HEADER1)] = 24
    engine.start()
    try:
        assert backend.resizes == [(BOARD, HEADER1, 24)]
        assert {g.key: g for g in engine.groups}["mainboard"].led_count == 24
        assert wait_for(lambda: backend.writes_for(BOARD))
        assert backend.writes_for(BOARD)[-1][1].shape == (24, 3)
    finally:
        engine.stop()


def test_engine_ignores_a_saved_count_the_device_cannot_take():
    backend = FakeBackend(board_zone_sizes=(0, 0, 0))
    engine = Engine(backend)
    engine.zone_sizes[zone_key(BOARD, HEADER1)] = 99999  # clamped into range, not fatal
    engine.start()
    try:
        assert {g.key: g for g in engine.groups}["mainboard"].led_count == 120
    finally:
        engine.stop()


def test_engine_resize_while_running_rebuilds_groups_and_keeps_rendering():
    backend = FakeBackend(board_zone_sizes=(0, 0, 0))
    engine = Engine(backend)
    engine.start()
    try:
        seen = []
        engine.resize_zone(BOARD, HEADER1, 16, configure=lambda e: seen.append([g.key for g in e.groups]))
        assert len(seen) == 1 and set(seen[0]) == {"keyboard", "memory", "mainboard"}
        assert wait_for(lambda: backend.writes_for(BOARD))
        assert backend.writes_for(BOARD)[-1][1].shape == (16, 3)
        assert engine.running and engine.loop is not None
    finally:
        engine.stop()


def test_failed_runtime_resize_restarts_the_loop_and_reports():
    backend = FakeBackend(board_zone_sizes=(0, 12, 0))
    engine = Engine(backend)
    engine.start()
    try:
        with pytest.raises(BackendError):
            engine.resize_zone(BOARD, HEADER1, 10_000)
        frames = engine.loop.stats.frames
        assert wait_for(lambda: engine.loop.stats.frames > frames + 2)  # a fresh loop is rendering
    finally:
        engine.stop()


# -- host + store


def test_host_resize_runs_off_thread_bumps_generation_and_persists(tmp_path):
    store = SettingsStore(tmp_path / "profiles.json")
    backend = FakeBackend(board_zone_sizes=(0, 0, 0))
    host = EngineHost(Engine(backend), store=store, save_delay=0.05)
    host.start()
    try:
        assert host.wait_running(3.0)
        gen = host.generation
        host.set_zone_size(BOARD, HEADER1, 20)
        assert wait_for(lambda: host.generation > gen)
        assert {g.key: g for g in host.groups}["mainboard"].led_count == 20
        assert host.zone_size(BOARD, HEADER1) == 20
    finally:
        host.stop()
    assert store.load_zone_sizes() == {zone_key(BOARD, HEADER1): 20}


def test_counts_set_before_the_engine_is_up_apply_when_it_starts(tmp_path):
    store = SettingsStore(tmp_path / "profiles.json")
    store.save_zone_sizes({zone_key(BOARD, HEADER1): 18})
    host = EngineHost(Engine(FakeBackend(board_zone_sizes=(0, 0, 0))), store=store)
    host.start()
    try:
        assert host.wait_running(3.0)
        assert {g.key: g for g in host.groups}["mainboard"].led_count == 18
    finally:
        host.stop()


def test_zone_sizes_file_survives_garbage_and_keeps_a_backup(tmp_path):
    store = SettingsStore(tmp_path / "profiles.json")
    assert store.load_zone_sizes() == {}
    store.save_zone_sizes({"a|b": 3})
    store.save_zone_sizes({"a|b": 4})
    assert store.hardware_path.with_name("hardware.json.bak").exists()
    store.hardware_path.write_text("{ not json", encoding="utf-8")
    assert store.load_zone_sizes() == {"a|b": 3}  # falls back to the .bak
    store.hardware_path.write_text('{"zone_sizes": {"a|b": -2, "c|d": "x", "e|f": 7}}', encoding="utf-8")
    assert store.load_zone_sizes() == {"e|f": 7}


# -- sync with keyboard


def started_host(**kw):
    backend = FakeBackend()
    host = EngineHost(Engine(backend), **kw)
    host.start()
    assert host.wait_running(3.0)
    return host, backend


def last_colors(backend, device_id):
    writes = backend.writes_for(device_id)
    return writes[-1][1] if writes else np.zeros((1, 3), dtype=np.uint8) + 7


def test_synced_group_mirrors_the_keyboards_top_effect_and_follows_edits():
    host, backend = started_host()
    try:
        host.set_effect("keyboard", "static")
        host.set_param("keyboard", "color", "#ff0000")
        host.set_effect("memory", "static")
        host.set_param("memory", "color", "#0000ff")
        assert wait_for(lambda: (last_colors(backend, "fake-dram-0") == [0, 0, 255]).all())

        host.set_sync_keyboard("memory", True)
        assert wait_for(lambda: (last_colors(backend, "fake-dram-0") == [255, 0, 0]).all())

        host.set_param("keyboard", "color", "#00ff00")  # a keyboard edit reaches the synced group
        assert wait_for(lambda: (last_colors(backend, "fake-dram-0") == [0, 255, 0]).all())

        top = host.add_layer("keyboard", "static", {"color": "#ffffff"})  # a new top layer wins
        assert wait_for(lambda: (last_colors(backend, "fake-dram-1") == 255).all())
        host.set_layer_props("keyboard", top, visible=False)  # hidden layers don't count
        assert wait_for(lambda: (last_colors(backend, "fake-dram-1") == [0, 255, 0]).all())

        host.set_sync_keyboard("memory", False)  # back to the group's own layers
        assert wait_for(lambda: (last_colors(backend, "fake-dram-0") == [0, 0, 255]).all())
        assert host.settings("memory").layers[0].params["color"] == "#0000ff"  # never overwritten
    finally:
        host.stop()


def test_paint_layers_are_not_mirrored_and_keyboard_cannot_sync_to_itself():
    host, backend = started_host()
    try:
        host.set_effect("keyboard", "static_keys")
        host.set_effect("memory", "static")
        host.set_param("memory", "color", "#0000ff")
        host.set_sync_keyboard("memory", True)
        host.set_sync_keyboard("keyboard", True)
        assert not host.settings("keyboard").sync_keyboard
        time.sleep(0.2)  # a per-key picture has no effect to carry over: own layers stay
        assert (last_colors(backend, "fake-dram-0") == [0, 0, 255]).all()
    finally:
        host.stop()


def test_sync_flag_round_trips_through_the_store(tmp_path):
    store = SettingsStore(tmp_path / "profiles.json")
    host = EngineHost(Engine(FakeBackend()), store=store, save_delay=0.01)
    host.set_sync_keyboard("memory", True)
    host.start()
    assert host.wait_running(3.0)
    host.stop()
    _, groups = SettingsStore(tmp_path / "profiles.json").load()
    assert groups["memory"].sync_keyboard is True


def test_synced_group_survives_a_zone_resize():
    host, backend = started_host()
    try:
        host.set_effect("keyboard", "static")
        host.set_param("keyboard", "color", "#ff0000")
        host.set_sync_keyboard("mainboard", True)
        gen = host.generation
        host.set_zone_size(BOARD, HEADER1, 10)
        assert wait_for(lambda: host.generation > gen)
        assert wait_for(lambda: backend.writes_for(BOARD) and last_colors(backend, BOARD).shape == (22, 3))
        assert wait_for(lambda: (last_colors(backend, BOARD) == [255, 0, 0]).all())
    finally:
        host.stop()


# -- OpenRGBBackend: limits come from raw packets, resize goes through the zone


class _Comms:
    def __init__(self, owner):
        self.owner = owner
        self.callback = lambda *a: None

    def requestDeviceData(self, index):
        zones = [NS(name=z.name, leds_min=z.min, leds_max=z.max) for z in self.owner.devices[index].zones]
        self.callback(index, PacketType.REQUEST_CONTROLLER_DATA, NS(zones=zones))


class _ArgbDevice:
    def __init__(self):
        self.name = "Aura"
        self.type = DeviceType.MOTHERBOARD
        self.metadata = NS(location="HID aura")
        self.id = 0
        self.leds = []
        self.modes = [NS(name="Direct", color_mode=ModeColors.PER_LED)]
        self.active_mode = 0
        self.colors = []
        self.set_colors_calls = []
        self.zones = [self._zone("Header 1"), self._zone("Header 2")]

    def _zone(self, name):
        dev = self

        class Z(NS):
            def resize(self, size):
                dev.client.pending = (self.name, size)

        return Z(name=name, type=ZoneType.LINEAR, leds=[], min=0, max=60, matrix_map=None)

    def set_mode(self, mode, **kw):
        self.active_mode = 0

    def set_colors(self, colors, fast=False):
        self.set_colors_calls.append(len(colors))


class _ArgbClient:
    def __init__(self, address, port, name):
        self.devices = [_ArgbDevice()]
        self.devices[0].client = self
        self.comms = _Comms(self)
        self.pending = None
        self.update_calls = 0

    def update(self):
        self.update_calls += 1
        if self.pending:
            zone_name, size = self.pending
            dev = self.devices[0]
            sizes = {z.name: len(z.leds) for z in dev.zones}
            sizes[zone_name] = size
            dev.leds = [NS(name=f"LED {i}") for i in range(sum(sizes.values()))]
            start = 0
            for z in dev.zones:
                z.leds = dev.leds[start : start + sizes[z.name]]
                start += sizes[z.name]
            self.pending = None
        self.comms.requestDeviceData(0)

    def disconnect(self):
        pass


def test_openrgb_backend_reads_limits_and_resizes_a_zone():
    backend = OpenRGBBackend(client_factory=_ArgbClient)
    backend.connect()
    info = backend.devices()[0]
    assert [(z.min_size, z.max_size, z.resizable) for z in info.zones] == [(0, 60, True), (0, 60, True)]

    backend.resize_zone(info.id, "Header 1", 30)
    after = backend.devices()[0]
    assert after.id == info.id  # same id: the pages and saved settings still match
    assert [z.size for z in after.zones] == [30, 0]
    assert after.led_count == 30
    backend.write(info.id, np.zeros((30, 3), dtype=np.uint8))
    assert backend._states[info.id].device.set_colors_calls == [30]

    with pytest.raises(BackendError):
        backend.resize_zone(info.id, "Header 1", 61)
    with pytest.raises(BackendError):
        backend.resize_zone(info.id, "Header 9", 5)
