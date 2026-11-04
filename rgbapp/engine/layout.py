"""Physical layouts: where each LED of a device sits, in key units.

Layout files live in ``rgbapp/layouts/*.json`` and list LEDs by their OpenRGB name.
Names are not unique (the K70's spacebar is three "Key: Space" LEDs), so an LED is
matched by ``(name, occurrence)``: the 2nd "Key: Space" on the device is the 2nd one
in the file. Device LEDs the file doesn't know go on a fallback strip below the keys
so nothing is silently dropped, and are logged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

log = logging.getLogger(__name__)

LAYOUT_DIR = Path(__file__).resolve().parent.parent / "layouts"
MIN_MATCH = 0.5  # a layout applies when it places at least this share of a device's LEDs
_FALLBACK_GAP = 0.5  # key units between the layout and the fallback strip


@dataclass(frozen=True)
class LayoutEntry:
    name: str
    label: str
    x: float
    y: float
    w: float
    h: float
    region: str = ""
    cap: str = ""  # entries sharing a cap are drawn as one keycap (the spacebar)


@dataclass(frozen=True)
class LayoutFile:
    id: str
    name: str
    regions: dict[str, str]  # region id -> display name
    entries: tuple[LayoutEntry, ...]


@dataclass(frozen=True)
class DeviceLayout:
    """A layout resolved against one device's LED list (index = device LED index)."""

    layout_id: str
    rects: np.ndarray  # (n, 4) float: x, y, w, h in key units
    labels: tuple[str, ...]
    caps: tuple[str, ...]  # "" when the LED is its own keycap
    regions: dict[str, np.ndarray]  # display name -> device LED indices
    unmatched: tuple[int, ...]  # device LED indices placed on the fallback strip

    @property
    def size(self) -> tuple[float, float]:
        if not len(self.rects):
            return (0.0, 0.0)
        return (float((self.rects[:, 0] + self.rects[:, 2]).max()), float((self.rects[:, 1] + self.rects[:, 3]).max()))

    def centers(self) -> np.ndarray:
        return self.rects[:, :2] + self.rects[:, 2:] / 2


def _read(path: Path) -> LayoutFile:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError(f"{path.name}: unsupported version {data.get('version')!r}")
    entries = tuple(
        LayoutEntry(
            name=str(e["name"]),
            label=str(e.get("label", e["name"].removeprefix("Key: "))),
            x=float(e["x"]),
            y=float(e["y"]),
            w=float(e.get("w", 1.0)),
            h=float(e.get("h", 1.0)),
            region=str(e.get("region", "")),
            cap=str(e.get("cap", "")),
        )
        for e in data["leds"]
    )
    return LayoutFile(str(data.get("id", path.stem)), str(data.get("name", path.stem)), dict(data.get("regions", {})), entries)


@lru_cache(maxsize=None)
def load_layouts(directory: Path = LAYOUT_DIR) -> tuple[LayoutFile, ...]:
    out = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            out.append(_read(path))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.error("layout %s ignored: %s", path.name, exc)
    return tuple(out)


def _occurrence_keys(names: Sequence[str]) -> list[tuple[str, int]]:
    seen: dict[str, int] = {}
    keys = []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        keys.append((n, seen[n]))
    return keys


def resolve(layout: LayoutFile, led_names: Sequence[str]) -> tuple[DeviceLayout, int]:
    """Place ``led_names`` with ``layout``. Returns the result and how many LEDs matched."""
    by_key = dict(zip(_occurrence_keys([e.name for e in layout.entries]), layout.entries))
    n = len(led_names)
    rects = np.zeros((n, 4), dtype=np.float64)
    labels, caps = [""] * n, [""] * n
    regions: dict[str, list[int]] = {}
    unmatched = []
    for i, key in enumerate(_occurrence_keys(led_names)):
        e = by_key.get(key)
        if e is None:
            unmatched.append(i)
            continue
        rects[i] = (e.x, e.y, e.w, e.h)
        labels[i] = e.label
        caps[i] = e.cap
        if e.region:
            regions.setdefault(layout.regions.get(e.region, e.region), []).append(i)
    matched = n - len(unmatched)
    if unmatched and matched:
        # a strip under the keyboard, one small square per unknown LED
        placed = np.ones(n, dtype=bool)
        placed[unmatched] = False
        bottom = float((rects[placed, 1] + rects[placed, 3]).max())
        for col, i in enumerate(unmatched):
            rects[i] = (col * 1.0, bottom + _FALLBACK_GAP, 0.9, 0.9)
            labels[i] = led_names[i].removeprefix("Key: ")
    return (
        DeviceLayout(
            layout.id,
            rects,
            tuple(labels),
            tuple(caps),
            {k: np.array(v, dtype=int) for k, v in regions.items()},
            tuple(unmatched),
        ),
        matched,
    )


def match_layout(led_names: Sequence[str], layouts: Optional[Sequence[LayoutFile]] = None) -> Optional[DeviceLayout]:
    """The best-fitting layout for a device, or None if none places enough of its LEDs."""
    if not led_names:
        return None
    best: Optional[tuple[DeviceLayout, int]] = None
    for layout in load_layouts() if layouts is None else layouts:
        result = resolve(layout, led_names)
        if best is None or result[1] > best[1]:
            best = result
    if best is None or best[1] < MIN_MATCH * len(led_names):
        return None
    dev_layout = best[0]
    if dev_layout.unmatched:
        log.warning(
            "layout %s: %d LED(s) not in the layout, shown on a strip below it: %s",
            dev_layout.layout_id,
            len(dev_layout.unmatched),
            ", ".join(repr(led_names[i]) for i in dev_layout.unmatched),
        )
    return dev_layout
