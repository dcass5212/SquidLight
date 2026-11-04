"""DeviceGroup: the unit the engine renders to.

A group is one frame buffer ``(n_leds, 3)``. Usually it is one physical device (the
keyboard); the RAM sticks are merged into one "memory" group so an effect can flow
across all of them. A group knows how to split a frame back into per-device writes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence, Union

import numpy as np

from .backend import DeviceInfo, ZoneInfo
from .layout import DeviceLayout, match_layout

log = logging.getLogger(__name__)

# OpenRGB device type -> group key. Unlisted types use their own type name.
_GROUP_KEYS = {"keyboard": "keyboard", "dram": "memory", "motherboard": "mainboard"}
# Types whose devices are merged into one group; everything else gets one group each.
_MERGED = {"dram"}

@dataclass(frozen=True)
class ZoneRef:
    """One zone of a group, as the zone UI and masks see it."""

    name: str  # unique within the group: what a layer mask uses
    label: str  # for people: "Stick 2", "Addressable RGB Header 1"
    device_id: str
    device_zone: str  # the zone's own name on its device (what the backend resizes by)
    info: ZoneInfo
    indices: np.ndarray  # LED indices within the group


Mask = Union[str, Sequence[int], Sequence[str]]  # "all" | LED indices | zone names


@dataclass(frozen=True)
class Member:
    device: DeviceInfo
    offset: int  # first LED of this device within the group buffer

    @property
    def stop(self) -> int:
        return self.offset + self.device.led_count


class DeviceGroup:
    def __init__(self, key: str, name: str, devices: Sequence[DeviceInfo]) -> None:
        self.key = key
        self.name = name
        members, offset = [], 0
        for dev in devices:
            members.append(Member(dev, offset))
            offset += dev.led_count
        self.members: tuple[Member, ...] = tuple(members)
        self.led_count = offset
        self.led_names: tuple[str, ...] = tuple(n for d in devices for n in d.led_names)
        self.zone_refs: list[ZoneRef] = []
        self.zones: dict[str, np.ndarray] = self._zone_indices()
        # A physical key layout, when one fits (single-device groups only: the keyboard).
        self.layout: Optional[DeviceLayout] = match_layout(self.led_names) if len(self.members) == 1 else None
        # Named LED sets from the layout ("Number pad", ...), usable in masks like zones.
        self.regions: dict[str, np.ndarray] = dict(self.layout.regions) if self.layout else {}
        self.positions: np.ndarray = _normalize(self.layout.centers()) if self.layout else self._positions()

    # -- frame <-> devices

    def split(self, frame: np.ndarray):
        """Yield ``(device_id, colors)`` for each member from a group frame."""
        for m in self.members:
            yield m.device.id, frame[m.offset : m.stop]

    @property
    def device_types(self) -> set[str]:
        return {m.device.type for m in self.members}

    # -- masks

    def resolve_mask(self, mask: Mask) -> np.ndarray:
        """Turn a layer mask into a boolean array of length ``led_count``."""
        if isinstance(mask, str):
            if mask == "all":
                return np.ones(self.led_count, dtype=bool)
            mask = [mask]  # a single zone name
        out = np.zeros(self.led_count, dtype=bool)
        for item in mask:
            if isinstance(item, str):
                idx = self.zones.get(item)
                if idx is None:
                    idx = self.regions.get(item)
                if idx is None:
                    log.warning("mask: unknown zone or region %r on %s", item, self.key)
                    continue
                out[idx] = True
            else:
                if 0 <= int(item) < self.led_count:
                    out[int(item)] = True
                else:
                    log.warning("mask: LED index %s out of range on %s", item, self.key)
        return out

    # -- internals

    def _zone_indices(self) -> dict[str, np.ndarray]:
        zones: dict[str, np.ndarray] = {}
        for member_no, m in enumerate(self.members, start=1):
            for z in m.device.zones:
                name, n = z.name, 2
                while name in zones:  # two RAM sticks both have a zone called "Corsair DRAM"
                    name = f"{z.name} ({n})"
                    n += 1
                idx = np.arange(m.offset + z.start, m.offset + z.start + z.size)
                zones[name] = idx
                # a merged group of identical devices (RAM sticks) reads best as "Stick 2"
                label = f"Stick {member_no}" if m.device.type == "dram" and len(self.members) > 1 else z.name
                self.zone_refs.append(ZoneRef(name, label, m.device.id, z.name, z, idx))
        return zones

    def mask_choices(self) -> list[str]:
        """Names a layer mask can use besides "all": layout regions, then zones that are
        a real subset of the group (a zone covering every LED adds nothing)."""
        out = list(self.regions)
        out += [z for z, idx in self.zones.items() if 0 < len(idx) < self.led_count]
        return out

    def _positions(self) -> np.ndarray:
        """Normalized (x, y) per LED in 0..1, from zones (linear or matrix)."""
        pos = np.zeros((self.led_count, 2), dtype=np.float64)
        slot = 0  # each zone gets its own band in y
        for m in self.members:
            covered = np.zeros(m.device.led_count, dtype=bool)
            for z in m.device.zones:
                local = _zone_layout(z, m.device.led_count)
                idx = m.offset + local.indices
                pos[idx, 0] = local.xy[:, 0]
                pos[idx, 1] = local.xy[:, 1] + slot * 1.5
                covered[local.indices] = True
                slot += 1
            # LEDs that belong to no zone are laid out in a line of their own
            stray = np.flatnonzero(~covered)
            if stray.size:
                pos[m.offset + stray, 0] = _spread(stray.size)
                pos[m.offset + stray, 1] = slot * 1.5
                slot += 1
        return _normalize(pos)


@dataclass
class _Layout:
    indices: np.ndarray  # LED indices within the device
    xy: np.ndarray  # (len(indices), 2), x and y each in 0..1


def _spread(n: int) -> np.ndarray:
    return np.array([0.5]) if n == 1 else np.linspace(0.0, 1.0, n)


def _zone_layout(z: ZoneInfo, device_leds: int) -> _Layout:
    """Local coordinates for one zone (each axis 0..1)."""
    if z.matrix_map:
        h, w = len(z.matrix_map), len(z.matrix_map[0])
        mapped, xy = [], []
        for r, row in enumerate(z.matrix_map):
            for c, led in enumerate(row):
                if led is not None and 0 <= led < device_leds:
                    mapped.append(led)
                    xy.append(((c + 0.5) / w, (r + 0.5) / h))
        # zone LEDs missing from the map go on a strip just above the matrix
        zone_leds = set(range(z.start, z.start + z.size))
        missing = sorted(zone_leds - set(mapped))
        if missing:
            xs = _spread(len(missing)) * (1 - 1 / w) + 0.5 / w
            mapped += missing
            xy += [(x, -0.5 / h) for x in xs]
        return _Layout(np.array(mapped, dtype=int), np.array(xy, dtype=float).reshape(-1, 2))
    n = z.size
    idx = np.arange(z.start, z.start + n)
    xy = np.stack([_spread(n), np.full(n, 0.5)], axis=1) if n else np.zeros((0, 2))
    return _Layout(idx, xy)


def _normalize(pos: np.ndarray) -> np.ndarray:
    if len(pos) == 0:
        return pos.astype(np.float32)
    lo, hi = pos.min(axis=0), pos.max(axis=0)
    span = hi - lo
    out = np.where(span > 1e-9, (pos - lo) / np.where(span > 1e-9, span, 1.0), 0.5)
    return out.astype(np.float32)


def build_groups(devices: Sequence[DeviceInfo]) -> list[DeviceGroup]:
    """Group writable devices. A device with no LEDs is skipped, unless it has a zone
    whose LED count the user can set (the board's ARGB headers report 0 until they do):
    it gets an empty group so its page exists and the count can be entered."""
    buckets: dict[str, list[DeviceInfo]] = {}
    for dev in devices:
        if not dev.writable:
            log.warning("skipping %s: not controllable (no Direct mode)", dev.name)
            continue
        if dev.led_count == 0 and not any(z.resizable for z in dev.zones):
            log.info("skipping %s: it reports 0 LEDs", dev.name)
            continue
        base = _GROUP_KEYS.get(dev.type, dev.type)
        key = base
        if dev.type not in _MERGED:  # repeats of a non-merged type get a numeric suffix
            n = 1
            while key in buckets:
                n += 1
                key = f"{base}{n}"
        buckets.setdefault(key, []).append(dev)
    groups = []
    for key, devs in buckets.items():
        name = devs[0].name if len(devs) == 1 else f"{devs[0].name} x{len(devs)}"
        groups.append(DeviceGroup(key, name, devs))
    return groups
