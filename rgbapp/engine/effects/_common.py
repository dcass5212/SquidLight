"""Small helpers shared by the input-driven effects."""

from __future__ import annotations

import numpy as np

from .base import Ctx


def linear(ctx: Ctx) -> bool:
    """True for a strip (all LEDs on one line: RAM, ARGB headers): there is no 'up' to use."""
    p = ctx.led_positions
    return len(p) == 0 or float(np.ptp(p[:, 1])) < 1e-6


def key_origins(ctx: Ctx) -> dict[str, np.ndarray]:
    """LED name -> (x, y) in aspect-corrected units (height = 1), averaged over LEDs sharing
    a name (the spacebar). Only key LEDs are listed."""
    out: dict[str, list] = {}
    for i, name in enumerate(ctx.led_names):
        if name.startswith("Key: "):
            out.setdefault(name, []).append(ctx.led_positions[i])
    return {k: np.mean(v, axis=0) * np.array([ctx.aspect, 1.0]) for k, v in out.items()}


def hashed_origin(name: str, ctx: Ctx) -> np.ndarray:
    """A stable place for a key on a device that has no keys (RAM, case): spread by name."""
    h = sum((i + 1) * ord(c) for i, c in enumerate(name))
    return np.array([(h * 37 % 101) / 100.0 * ctx.aspect, (h * 53 % 97) / 96.0])


def lerp(bg: np.ndarray, fg: np.ndarray, level: np.ndarray) -> np.ndarray:
    """bg -> fg by a per-LED level (n,); fg may be one color or (n, 3)."""
    return (bg + (fg - bg) * np.asarray(level)[:, None]).astype(np.float32)


_KEY_ROWS = 6  # a keyboard's rows: the yardstick for how tall a few-row device should look


def bottom_rows(ctx: Ctx, strip_y: float | None = None) -> np.ndarray:
    """Positions with y squeezed toward the bottom for a device with only a few rows of LEDs
    (RAM sticks, ARGB headers). Normalization stretches such a device over the full height, so
    "up" effects would leave its top row at the very top. Instead the rows are packed like the
    bottom rows of a keyboard. A single row (a strip) is moved to ``strip_y`` if given."""
    pos = ctx.led_positions
    if len(pos) == 0 or any(n.startswith("Key: ") for n in ctx.led_names):
        return pos  # a keyboard already has its real layout
    rows = len(np.unique(np.round(pos[:, 1], 3)))
    if rows >= _KEY_ROWS:
        return pos
    out = pos.copy()
    if rows < 2:
        if strip_y is not None:
            out[:, 1] = strip_y
        return out
    out[:, 1] = 1.0 - (1.0 - pos[:, 1]) * (rows - 1) / (_KEY_ROWS - 1)
    return out
