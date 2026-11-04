"""OpenRGBBackend against a fake ``openrgb-python`` client (no server, no hardware)."""

from types import SimpleNamespace as NS

import numpy as np
import pytest
from openrgb.utils import DeviceType, ModeColors, ZoneType

from rgbapp.engine.backend import Backoff, BackendError, ConnectionState, OpenRGBBackend


class FakeDevice:
    def __init__(self, name, dtype, location, n_leds, zone_type=ZoneType.LINEAR, modes=("Direct", "Static"),
                 active=0, matrix=None):
        self.name = name
        self.type = dtype
        self.metadata = NS(location=location)
        self.leds = [NS(name=f"LED {i}") for i in range(n_leds)]
        self.zones = [NS(name="Zone", type=zone_type, leds=self.leds, matrix_map=matrix)]
        self.modes = [
            NS(name=m, color_mode=ModeColors.PER_LED if m == "Direct" else ModeColors.MODE_SPECIFIC)
            for m in modes
        ]
        self.active_mode = active
        self.colors = [f"orig{i}" for i in range(n_leds)]
        self.calls = []
        self.fail = False

    def set_mode(self, mode, **kw):
        idx = mode if isinstance(mode, int) else next(i for i, m in enumerate(self.modes) if m.name == mode)
        self.active_mode = idx
        self.calls.append(("mode", self.modes[idx].name))

    def set_colors(self, colors, fast=False):
        if self.fail:
            raise ConnectionResetError("boom")
        self.calls.append(("colors", len(colors), fast, colors))


class FakeClient:
    server_up = True
    instances = []

    def __init__(self, address, port, name):
        if not FakeClient.server_up:
            raise ConnectionRefusedError("no server")
        self.devices = [
            FakeDevice("Stick", DeviceType.DRAM, "I2C 0x19", 10, active=0, modes=("Static", "Direct")),
            FakeDevice("Stick", DeviceType.DRAM, "I2C 0x1B", 10),
            FakeDevice("KB", DeviceType.KEYBOARD, "HID kb", 6, ZoneType.MATRIX,
                       matrix=[[0, 1, 2], [3, 4, 0xFFFFFFFF]]),
            FakeDevice("Fan", DeviceType.COOLER, "HID fan", 4, modes=("Static",)),  # no Direct mode
        ]
        self.disconnected = False
        FakeClient.instances.append(self)

    def disconnect(self):
        self.disconnected = True


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def backend():
    FakeClient.server_up = True
    FakeClient.instances = []
    clock = Clock()
    b = OpenRGBBackend(client_factory=FakeClient, clock=clock)
    b.clock = clock
    return b


def frame(n, v=7):
    return np.full((n, 3), v, dtype=np.uint8)


def test_connect_describes_devices_and_enters_direct_mode(backend):
    backend.connect()
    infos = {d.name + d.id: d for d in backend.devices()}
    assert backend.state is ConnectionState.CONNECTED
    ids = [d.id for d in backend.devices()]
    assert len(set(ids)) == 4 and all(i for i in ids)
    kb = next(d for d in backend.devices() if d.type == "keyboard")
    assert kb.led_count == 6 and kb.zones[0].kind == "matrix"
    assert kb.zones[0].matrix_map == ((0, 1, 2), (3, 4, None))  # 0xFFFFFFFF -> empty cell
    fan = next(d for d in backend.devices() if d.type == "cooler")
    assert not fan.writable
    client = FakeClient.instances[0]
    assert client.devices[0].active_mode == 1 and ("mode", "Direct") in client.devices[0].calls
    assert client.devices[3].calls == []  # untouched: no Direct mode


def test_device_ids_are_stable_across_reconnects(backend):
    backend.connect()
    first = [d.id for d in backend.devices()]
    backend._mark_disconnected("test")
    backend.clock.now += 20
    assert backend.maintain() is True
    assert [d.id for d in backend.devices()] == first


def test_write_sends_fast_rgb_colors(backend):
    backend.connect()
    kb = next(d for d in backend.devices() if d.type == "keyboard")
    backend.write(kb.id, frame(6, 9))
    call = FakeClient.instances[0].devices[2].calls[-1]
    assert call[0] == "colors" and call[1] == 6 and call[2] is True
    assert (call[3][0].red, call[3][0].green, call[3][0].blue) == (9, 9, 9)


def test_write_validates_shape_and_unknown_device(backend):
    backend.connect()
    kb = next(d for d in backend.devices() if d.type == "keyboard")
    with pytest.raises(BackendError):
        backend.write(kb.id, frame(5))
    with pytest.raises(BackendError):
        backend.write("nope", frame(6))


def test_write_failure_marks_disconnected_and_raises(backend):
    backend.connect()
    kb = next(d for d in backend.devices() if d.type == "keyboard")
    FakeClient.instances[0].devices[2].fail = True
    with pytest.raises(BackendError):
        backend.write(kb.id, frame(6))
    assert backend.state is ConnectionState.DISCONNECTED
    assert FakeClient.instances[0].disconnected
    with pytest.raises(BackendError):
        backend.write(kb.id, frame(6))  # still down


def test_reconnect_uses_exponential_backoff_capped_at_15s(backend):
    backend.connect()
    backend._mark_disconnected("test")
    FakeClient.server_up = False
    attempt_times = [0.0]  # the moment the connection was lost
    original_open = backend._open

    def counting_open():
        attempt_times.append(backend.clock.now)
        return original_open()

    backend._open = counting_open
    for second in range(120):  # two minutes of simulated time, polled every second
        backend.clock.now = float(second)
        backend.maintain()
    gaps = list(np.diff(attempt_times))
    assert gaps[:5] == [1.0, 2.0, 4.0, 8.0, 15.0]
    assert set(gaps[4:]) == {15.0}  # and it never gives up


def test_reconnect_succeeds_when_server_returns(backend):
    backend.connect()
    backend._mark_disconnected("test")
    FakeClient.server_up = False
    backend.clock.now = 100
    assert backend.maintain() is False
    FakeClient.server_up = True
    backend.clock.now = 200
    assert backend.maintain() is True
    assert backend.state is ConnectionState.CONNECTED
    assert backend.maintain() is False  # already up


def test_initial_connect_failure_raises_backend_error(backend):
    FakeClient.server_up = False
    with pytest.raises(BackendError):
        backend.connect()
    assert backend.state is ConnectionState.DISCONNECTED


def test_close_restores_previous_modes_and_colors_and_stops_reconnecting(backend):
    backend.connect()
    backend.close()
    devs = FakeClient.instances[0].devices
    assert devs[0].active_mode == 0  # stick 0 was in "Static" before we took over
    assert devs[0].calls[-1][0] != "colors"  # a firmware mode needs no color restore
    assert devs[1].calls[-1][0] == "colors" and devs[1].calls[-1][3][0] == "orig0"  # Direct: colors restored
    assert devs[3].calls == []  # never touched, never restored
    assert FakeClient.instances[0].disconnected
    backend.clock.now += 100
    assert backend.maintain() is False  # closed on purpose: do not reconnect


def test_backoff_sequence_and_reset():
    b = Backoff()
    assert [b.next_delay() for _ in range(6)] == [1, 2, 4, 8, 15, 15]
    b.reset()
    assert b.next_delay() == 1


def test_settle_waits_for_late_detected_devices(monkeypatch):
    """OpenRGB opens its port before USB detection finishes; we must not read a partial list."""

    class LateClient(FakeClient):
        def __init__(self, address, port, name):
            super().__init__(address, port, name)
            self._all = self.devices
            self.devices = self._all[:2]  # only the RAM so far
            self.updates = 0

        def update(self):
            self.updates += 1
            if self.updates >= 2:
                self.devices = self._all

    clock = Clock()
    monkeypatch.setattr("rgbapp.engine.backend.time.sleep", lambda s: setattr(clock, "now", clock.now + s))
    b = OpenRGBBackend(client_factory=LateClient, clock=clock, settle_seconds=15.0)
    b.connect()
    assert len(b.devices()) == 4
    assert any(d.type == "keyboard" for d in b.devices())


def test_settle_gives_up_at_the_deadline(monkeypatch):
    class NeverSettles(FakeClient):
        def update(self):
            self.devices = self.devices + [self.devices[0]]  # count changes forever

    clock = Clock()
    monkeypatch.setattr("rgbapp.engine.backend.time.sleep", lambda s: setattr(clock, "now", clock.now + s))
    b = OpenRGBBackend(client_factory=NeverSettles, clock=clock, settle_seconds=3.0)
    b._settle(NeverSettles("h", 1, "n"))
    assert clock.now <= 3.5  # bounded, did not loop forever
