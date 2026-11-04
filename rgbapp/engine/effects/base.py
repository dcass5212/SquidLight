"""Effect plugin contract: Param, Ctx, Effect, and small color helpers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional, Sequence

import numpy as np

log = logging.getLogger(__name__)

PARAM_TYPES = ("color", "gradient", "float", "int", "enum", "bool", "ledmap")
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


@dataclass(frozen=True)
class Param:
    """One user-editable setting. The UI builds its property editor from these."""

    id: str
    label: str
    type: str  # one of PARAM_TYPES
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    choices: Optional[Sequence[str]] = None
    hidden: bool = False  # stored with the layer but given no generated editor (e.g. painted keys)

    def __post_init__(self) -> None:
        if self.type not in PARAM_TYPES:
            raise ValueError(f"unknown param type {self.type!r}")

    def coerce(self, value: Any) -> Any:
        """Validate/clamp ``value``; a bad value falls back to the default."""
        try:
            if self.type == "float":
                return _clamp(float(value), self.min, self.max)
            if self.type == "int":
                return int(_clamp(int(round(float(value))), self.min, self.max))
            if self.type == "bool":
                return bool(value)
            if self.type == "enum":
                if value in (self.choices or ()):
                    return value
                raise ValueError(value)
            if self.type == "color":
                parse_color(value)  # validates
                return _normalize_hex(value)
            if self.type == "ledmap":  # {led index: '#rrggbb'}, as painted by the user
                return {str(int(k)): _normalize_hex(v) for k, v in dict(value).items() if int(k) >= 0}
            if self.type == "gradient":
                stops = [(min(max(float(p), 0.0), 1.0), _normalize_hex(c)) for p, c in value]
                parse_color(stops[0][1])
                return sorted(stops)
        except (TypeError, ValueError, IndexError):
            log.warning("bad value %r for param %r; using default", value, self.id)
        return self.default


def _clamp(v: float, lo: Optional[float], hi: Optional[float]) -> float:
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _normalize_hex(value: Any) -> str:
    m = _HEX.match(str(value).strip())
    if not m:
        raise ValueError(value)
    return "#" + m.group(1).lower()


def parse_color(value: Any) -> np.ndarray:
    """'#rrggbb' -> float32 array [r, g, b] in 0..1."""
    m = _HEX.match(str(value).strip())
    if not m:
        raise ValueError(f"not a #rrggbb color: {value!r}")
    h = m.group(1)
    return np.array([int(h[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.float32) / 255.0


def sample_gradient(stops: Sequence, u: np.ndarray) -> np.ndarray:
    """Colors of a gradient (``[(pos, '#rrggbb'), ...]``, pos 0..1) at ``u`` -> float32 (..., 3).

    Before the first stop and after the last, the end colors hold.
    """
    stops = sorted((float(p), c) for p, c in stops)
    pos = np.array([p for p, _ in stops], dtype=np.float64)
    cols = np.stack([parse_color(c) for _, c in stops]).astype(np.float64)
    u = np.asarray(u, dtype=np.float64)
    out = np.stack([np.interp(u, pos, cols[:, ch]) for ch in range(3)], axis=-1)
    return out.astype(np.float32)


def hsv_to_rgb(h: np.ndarray, s: float | np.ndarray, v: float | np.ndarray) -> np.ndarray:
    """Vectorized HSV (all 0..1; ``h`` wraps) -> float32 array (..., 3)."""
    h = np.asarray(h, dtype=np.float64) % 1.0
    s = np.broadcast_to(np.asarray(s, dtype=np.float64), h.shape)
    v = np.broadcast_to(np.asarray(v, dtype=np.float64), h.shape)
    i = np.floor(h * 6.0)
    f = h * 6.0 - i
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    i = i.astype(int) % 6
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=-1).astype(np.float32)


@dataclass
class Ctx:
    """Everything an effect may look at for one frame."""

    t: float  # seconds since the loop started
    dt: float  # seconds since the previous frame
    led_positions: np.ndarray  # (n, 2) float, normalized x, y in 0..1
    inputs: dict = field(default_factory=dict)  # audio / keys / stats snapshots (M7)

    @property
    def n(self) -> int:
        return len(self.led_positions)


class Effect:
    """Base class. Subclasses set the class attributes and implement ``render``.

    An instance is created per layer, so it may keep small state between frames.
    ``render`` must return a float32 array of shape ``(ctx.n, 3)`` in 0..1.
    """

    id: ClassVar[str]
    name: ClassVar[str]
    params: ClassVar[list[Param]] = []
    needs: ClassVar[set[str]] = set()  # {"audio"}, {"keys"}, {"cpu"}: inputs to start
    listed: ClassVar[bool] = True  # False: made by a tool (Paint), not offered in the effect library

    @classmethod
    def resolve_params(cls, raw: Optional[dict] = None) -> dict:
        """Merge ``raw`` over the defaults, coercing and clamping every value."""
        raw = raw or {}
        out = {p.id: p.coerce(raw[p.id]) if p.id in raw else p.default for p in cls.params}
        unknown = set(raw) - set(out)
        if unknown:
            log.debug("%s: ignoring unknown params %s", cls.id, sorted(unknown))
        return out

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        raise NotImplementedError

    def coverage(self, ctx: Ctx, params: dict) -> Optional[np.ndarray]:
        """Which LEDs this effect draws on, as a bool ``(n,)`` array; ``None`` means all.
        Uncovered LEDs let the layers below show through."""
        return None
