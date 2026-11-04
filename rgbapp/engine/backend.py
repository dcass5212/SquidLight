"""Backend abstraction: the only place that knows how to talk to hardware.

``Backend`` is a small protocol. ``OpenRGBBackend`` implements it over the OpenRGB
SDK server; ``FakeBackend`` implements it in memory so the engine and its tests need
no hardware. Everything above this module works with ``DeviceInfo`` and numpy arrays.
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable

import numpy as np

log = logging.getLogger(__name__)


class BackendError(Exception):
    """A device or connection operation failed. The render loop catches this."""


class ConnectionState(enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"


@dataclass(frozen=True)
class ZoneInfo:
    name: str
    start: int  # index of the zone's first LED within the device's LED list
    size: int
    kind: str = "linear"  # "linear" | "matrix" | "single"
    # matrix_map[row][col] = LED index within the device, or None for an empty cell
    matrix_map: Optional[tuple[tuple[Optional[int], ...], ...]] = None


@dataclass(frozen=True)
class DeviceInfo:
    id: str  # stable across reconnects (not the OpenRGB list index)
    name: str
    type: str  # lower-case: "keyboard", "dram", "motherboard", ...
    led_names: tuple[str, ...]
    zones: tuple[ZoneInfo, ...]
    writable: bool = True  # False if there is no Direct mode

    @property
    def led_count(self) -> int:
        return len(self.led_names)


@runtime_checkable
class Backend(Protocol):
    state: ConnectionState

    def connect(self) -> None:
        """Connect and put every writable device in Direct mode. Raises BackendError."""

    def devices(self) -> list[DeviceInfo]: ...

    def write(self, device_id: str, colors: np.ndarray) -> None:
        """Send one frame, ``colors`` shape (n, 3) uint8. Raises BackendError."""

    def maintain(self) -> bool:
        """Called regularly by the loop. Reconnects if needed (with backoff).

        Returns True when the connection was just re-established, which tells the
        caller to resend the current frame.
        """

    def close(self) -> None:
        """Restore each device's original mode and colors, then disconnect."""


# --------------------------------------------------------------------------- reconnect


class Backoff:
    """Exponential backoff: 1 s, 2 s, 4 s, 8 s, then 15 s forever."""

    def __init__(self, first: float = 1.0, cap: float = 15.0) -> None:
        self.first = first
        self.cap = cap
        self._current = first

    def next_delay(self) -> float:
        delay = self._current
        self._current = min(self._current * 2, self.cap)
        return delay

    def reset(self) -> None:
        self._current = self.first


# --------------------------------------------------------------------------- fake


def _k70_names() -> list[str]:
    """The K70 RGB PRO's LED names as OpenRGB reports them (the layout file lists them
    in device order; see docs/device-dump.txt)."""
    from .layout import load_layouts

    for layout in load_layouts():
        if layout.id == "k70_pro_ansi":
            return [e.name for e in layout.entries]
    return []


class FakeBackend:
    """In-memory backend that mirrors this machine's devices (see docs/04).

    Records every write in ``writes`` as ``(device_id, colors_copy, timestamp)``.
    Failure injection for tests: ``fail_writes`` (BackendError on write) and
    ``set_connected(False)`` (simulates the server going away).
    """

    def __init__(
        self,
        *,
        keyboard_leds: int = 115,
        ram_sticks: int = 2,
        ram_leds: int = 10,
        board_zone_sizes: tuple[int, ...] = (0, 12, 0),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.state = ConnectionState.DISCONNECTED
        self.writes: list[tuple[str, np.ndarray, float]] = []
        self.closed = False
        self.connect_calls = 0
        self.fail_writes = False
        self.can_reconnect = True
        self._wanted = False  # like OpenRGBBackend: reconnect only between connect() and close()
        self._clock = clock
        self._devices = self._build(keyboard_leds, ram_sticks, ram_leds, board_zone_sizes)

    @staticmethod
    def _build(kb: int, sticks: int, ram_leds: int, board: tuple[int, ...]) -> list[DeviceInfo]:
        devs: list[DeviceInfo] = []
        for i in range(sticks):
            devs.append(
                DeviceInfo(
                    id=f"fake-dram-{i}",
                    name="Corsair Vengeance RGB DDR5",
                    type="dram",
                    led_names=tuple(f"Corsair DRAM LED {n}" for n in range(ram_leds)),
                    zones=(ZoneInfo("Corsair DRAM", 0, ram_leds),),
                )
            )
        zones, start = [], 0
        for i, size in enumerate(board):
            zones.append(ZoneInfo(f"Addressable RGB Header {i + 1}", start, size))
            start += size
        devs.append(
            DeviceInfo(
                id="fake-board",
                name="ASUS B650EM MAX GAMING WIFI",
                type="motherboard",
                led_names=tuple(f"Header LED {n}" for n in range(start)),
                zones=tuple(zones),
            )
        )
        # 7 rows x 21 columns matrix like the real K70 Pro; leftover LEDs are unmapped.
        # At the real size (115) the LED names are the real ones, in the real order.
        names = _k70_names() if kb == 115 else []
        if len(names) != kb:
            names = ["Profile", "Light", "Lock", "Logo"] + [f"Key: {n}" for n in range(kb - 4)]
        rows, cols = 7, 21
        cells: list[list[Optional[int]]] = [[None] * cols for _ in range(rows)]
        for idx in range(4, min(kb, rows * cols)):
            cells[idx // cols][idx % cols] = idx
        devs.append(
            DeviceInfo(
                id="fake-keyboard",
                name="Corsair K70 RGB PRO",
                type="keyboard",
                led_names=tuple(names),
                zones=(
                    ZoneInfo(
                        "Keyboard", 0, kb, "matrix", tuple(tuple(r) for r in cells)
                    ),
                ),
            )
        )
        return devs

    # -- Backend protocol

    def connect(self) -> None:
        self.connect_calls += 1
        self._wanted = True
        if not self.can_reconnect:
            raise BackendError("fake: server unavailable")
        self.state = ConnectionState.CONNECTED
        self.closed = False

    def devices(self) -> list[DeviceInfo]:
        return list(self._devices)

    def write(self, device_id: str, colors: np.ndarray) -> None:
        if self.state is not ConnectionState.CONNECTED:
            raise BackendError("fake: not connected")
        if self.fail_writes:
            raise BackendError("fake: write failed")
        dev = self._by_id(device_id)
        if colors.shape != (dev.led_count, 3) or colors.dtype != np.uint8:
            raise BackendError(
                f"fake: bad frame {colors.shape}/{colors.dtype} for {dev.led_count} LEDs"
            )
        self.writes.append((device_id, colors.copy(), self._clock()))

    def maintain(self) -> bool:
        if not self._wanted or self.state is ConnectionState.CONNECTED:
            return False
        try:
            self.connect()
        except BackendError:
            return False
        return True

    def close(self) -> None:
        self.closed = True
        self._wanted = False
        self.state = ConnectionState.DISCONNECTED

    # -- test helpers

    def set_connected(self, connected: bool) -> None:
        self.state = ConnectionState.CONNECTED if connected else ConnectionState.DISCONNECTED

    def writes_for(self, device_id: str) -> list[tuple[str, np.ndarray, float]]:
        return [w for w in self.writes if w[0] == device_id]

    def _by_id(self, device_id: str) -> DeviceInfo:
        for d in self._devices:
            if d.id == device_id:
                return d
        raise BackendError(f"fake: unknown device {device_id}")


# --------------------------------------------------------------------------- OpenRGB


@dataclass
class _DeviceState:
    """What we remember about each device so we can restore it."""

    device: object  # openrgb.orgb.Device
    info: DeviceInfo
    prev_mode: int
    prev_colors: list  # list[RGBColor]
    prev_per_led: bool


class OpenRGBBackend:
    """Talks to a running OpenRGB SDK server through ``openrgb-python``.

    Not thread-safe: use it from the engine thread only (the client is synchronous).
    ``client_factory`` is injectable so reconnect logic can be tested without a server.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6742,
        client_name: str = "rgbapp",
        client_factory: Optional[Callable[..., object]] = None,
        clock: Callable[[], float] = time.monotonic,
        settle_seconds: float = 0.0,
    ) -> None:
        self.host = host
        self.port = port
        self.client_name = client_name
        # OpenRGB opens its port before hardware detection finishes (RAM first, USB later),
        # so a fresh connection can see a partial device list. When > 0, wait up to this
        # long for the device count to stop changing before reading the list.
        self.settle_seconds = settle_seconds
        self.state = ConnectionState.DISCONNECTED
        self._client_factory = client_factory
        self._clock = clock
        self._client = None
        self._states: dict[str, _DeviceState] = {}
        self._infos: list[DeviceInfo] = []
        self._backoff = Backoff()
        self._next_attempt = 0.0
        self._wanted = False  # True between connect() and close(): reconnect only then
        self._ever_connected = False

    # -- connection

    def _make_client(self):
        if self._client_factory is not None:
            return self._client_factory(
                address=self.host, port=self.port, name=self.client_name
            )
        from openrgb import OpenRGBClient  # imported lazily so tests don't need it

        return OpenRGBClient(address=self.host, port=self.port, name=self.client_name)

    def connect(self) -> None:
        self._wanted = True
        try:
            self._open()
        except BackendError:
            self._schedule_retry()
            raise

    def _open(self) -> None:
        try:
            client = self._make_client()
            self._settle(client)
            states = self._enter_direct(client)
        except BackendError:
            raise
        except Exception as exc:  # socket errors, protocol errors, parsing errors
            raise BackendError(f"cannot connect to OpenRGB at {self.host}:{self.port}: {exc}") from exc
        self._client = client
        self._states = states
        self._infos = [s.info for s in states.values()]
        self.state = ConnectionState.CONNECTED
        self._ever_connected = True
        self._backoff.reset()
        log.info("connected to OpenRGB, %d device(s)", len(states))

    def _settle(self, client, quiet: float = 2.0, poll: float = 0.5) -> None:
        """Wait until OpenRGB's device count has been unchanged for ``quiet`` seconds."""
        if self.settle_seconds <= 0:
            return
        start = self._clock()
        last, stable_since = len(client.devices), start
        while True:
            now = self._clock()
            if now - stable_since >= quiet or now - start >= self.settle_seconds:
                break
            time.sleep(poll)
            client.update()
            count = len(client.devices)
            if count != last:
                log.info("OpenRGB device count changed %d -> %d while settling", last, count)
                last, stable_since = count, self._clock()

    def _enter_direct(self, client) -> dict[str, _DeviceState]:
        from openrgb.utils import ModeColors

        states: dict[str, _DeviceState] = {}
        for index, dev in enumerate(client.devices):
            info = _describe(dev, index, set(states))
            prev_mode = int(dev.active_mode)
            prev_colors = list(dev.colors)
            prev_per_led = dev.modes[prev_mode].color_mode == ModeColors.PER_LED
            if info.writable:
                try:
                    dev.set_mode("Direct")
                except Exception as exc:
                    log.warning("could not put %s in Direct mode: %s", info.name, exc)
                    info = _replace_writable(info, False)
            else:
                log.warning("%s has no Direct mode; it will not be controlled", info.name)
            states[info.id] = _DeviceState(dev, info, prev_mode, prev_colors, prev_per_led)
        return states

    def _schedule_retry(self) -> None:
        self._next_attempt = self._clock() + self._backoff.next_delay()

    def _mark_disconnected(self, why: str) -> None:
        if self.state is ConnectionState.CONNECTED:
            log.warning("lost connection to OpenRGB: %s", why)
        self.state = ConnectionState.DISCONNECTED
        self._schedule_retry()
        client, self._client = self._client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass

    def maintain(self) -> bool:
        if not self._wanted or self.state is ConnectionState.CONNECTED:
            return False
        if self._clock() < self._next_attempt:
            return False
        try:
            self._open()
        except BackendError as exc:
            self._schedule_retry()
            log.debug("reconnect failed: %s", exc)
            return False
        return True

    # -- io

    def devices(self) -> list[DeviceInfo]:
        return list(self._infos)

    def write(self, device_id: str, colors: np.ndarray) -> None:
        if self.state is not ConnectionState.CONNECTED:
            raise BackendError("not connected")
        st = self._states.get(device_id)
        if st is None:
            raise BackendError(f"unknown device {device_id}")
        if not st.info.writable:
            return
        if colors.shape != (st.info.led_count, 3):
            raise BackendError(
                f"frame shape {colors.shape} does not match {st.info.led_count} LEDs"
            )
        from openrgb.utils import RGBColor

        try:
            st.device.set_colors([RGBColor(int(r), int(g), int(b)) for r, g, b in colors], fast=True)
        except Exception as exc:
            self._mark_disconnected(str(exc))
            raise BackendError(f"write to {st.info.name} failed: {exc}") from exc

    def close(self) -> None:
        """Restore every device we touched, then disconnect."""
        self._wanted = False
        if self.state is ConnectionState.CONNECTED:
            for st in self._states.values():
                self._restore(st)
        client, self._client = self._client, None
        self.state = ConnectionState.DISCONNECTED
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass

    def _restore(self, st: _DeviceState) -> None:
        if not st.info.writable:
            return
        try:
            st.device.set_mode(st.prev_mode)
            if st.prev_per_led and st.prev_colors:
                st.device.set_colors(st.prev_colors, fast=True)
        except Exception as exc:
            log.warning("could not restore %s: %s", st.info.name, exc)


def _replace_writable(info: DeviceInfo, writable: bool) -> DeviceInfo:
    return DeviceInfo(info.id, info.name, info.type, info.led_names, info.zones, writable)


def _describe(dev, index: int, taken: set[str]) -> DeviceInfo:
    """Build a DeviceInfo from an openrgb-python Device.

    The id is derived from the type and OpenRGB's device location (unique per device,
    e.g. the two RAM sticks differ by SMBus address) so it survives list reordering.
    """
    dtype = getattr(dev.type, "name", str(dev.type)).lower()
    location = getattr(getattr(dev, "metadata", None), "location", "") or ""
    base = f"{dtype}:{location}" if location else f"{dtype}:{dev.name}:{index}"
    dev_id, n = base, 1
    while dev_id in taken:  # identical location strings; keep ids unique anyway
        n += 1
        dev_id = f"{base}#{n}"

    zones = []
    start = 0
    for z in dev.zones:
        size = len(z.leds)
        ztype = getattr(z.type, "name", str(z.type)).lower()
        matrix = None
        raw = getattr(z, "matrix_map", None)
        if ztype == "matrix" and raw:
            matrix = tuple(
                tuple(None if (v is None or v >= 0xFFFF_FFF0) else start + int(v) for v in row)
                for row in raw
            )
        zones.append(ZoneInfo(z.name, start, size, ztype, matrix))
        start += size

    has_direct = any(m.name.lower() == "direct" for m in dev.modes)
    return DeviceInfo(
        id=dev_id,
        name=dev.name,
        type=dtype,
        led_names=tuple(led.name for led in dev.leds),
        zones=tuple(zones),
        writable=has_direct,
    )
