from __future__ import annotations

import numpy as np

from ._common import bottom_rows, linear
from .base import Ctx, Effect, Param, parse_color

_MAX_DROPS = 64
_MAX_SPLASHES = 48
_X_SIGMA = 0.09  # how wide a streak is, in device heights (about half a key)
_SPLASH_LIFE = 0.7  # seconds a splash ring lasts
_SPLASH_SPEED = 0.55  # heights per second the ring spreads
_SPLASH_WIDTH = 0.1


class Raindrop(Effect):
    """Drops fall from the top in streaks and splash into rings where they land.

    A strip (RAM, ARGB headers) has no 'down', so there each drop is a ring on a pond: it
    lands at a random spot and spreads out along the strip.
    """

    id = "raindrop"
    name = "Raindrop"
    params = [
        Param("color", "Drop color", "color", "#4aa8ff"),
        Param("background", "Background", "color", "#000510"),
        Param("density", "Rainfall", "float", 1.0, min=0.1, max=4.0),
        Param("speed", "Fall speed", "float", 0.7, min=0.1, max=3.0),
        Param("trail", "Streak length", "float", 0.45, min=0.1, max=1.0),
        Param("splash", "Splash on landing", "bool", True),
    ]

    def __init__(self) -> None:
        self._drops: list[list[float]] = []  # x, y, speed
        self._splashes: list[list[float]] = []  # x, age
        self._rng = np.random.default_rng()

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        n = ctx.n
        bg = parse_color(params["background"])
        out = np.broadcast_to(bg, (n, 3)).astype(np.float32).copy()
        if n == 0:
            return out
        dt = min(max(ctx.dt, 0.0), 0.25)
        pond = linear(ctx)
        speed = params["speed"]
        width = ctx.aspect

        # Advance what is already falling or spreading.
        for d in self._drops:
            d[1] += d[2] * speed * dt
        landed = [d for d in self._drops if d[1] >= 1.0]
        self._drops = [d for d in self._drops if d[1] < 1.0]
        for s in self._splashes:
            s[1] += dt
        self._splashes = [s for s in self._splashes if s[1] < _SPLASH_LIFE]
        if params["splash"] or pond:
            for d in landed:
                self._splashes.append([d[0], 0.0])

        # New drops arrive at random (Poisson), more of them on a wider device.
        for _ in range(int(self._rng.poisson(params["density"] * 3.0 * max(width, 0.3) * dt))):
            x = float(self._rng.random()) * width
            if pond:
                self._splashes.append([x, 0.0])
            else:
                self._drops.append([x, -0.05, 0.8 + 0.5 * float(self._rng.random())])
        self._drops = self._drops[-_MAX_DROPS:]
        self._splashes = self._splashes[-_MAX_SPLASHES:]

        pos = (bottom_rows(ctx, strip_y=1.0) if not pond else ctx.led_positions) * np.array([width, 1.0])
        fg = parse_color(params["color"])
        glow = np.zeros(n, dtype=np.float32)

        tail = params["trail"]
        for x, y, _ in self._drops:
            dx = (pos[:, 0] - x) / _X_SIGMA
            behind = y - pos[:, 1]  # >0 above the drop's head: the streak
            streak = np.where(behind >= 0.0, np.exp(-behind / (tail / 3.0)), np.exp(-((behind / 0.05) ** 2)))
            glow += (np.exp(-dx * dx) * streak).astype(np.float32)

        for x, age in self._splashes:
            if pond:
                d = np.abs(pos[:, 0] - x)
            else:
                d = np.hypot(pos[:, 0] - x, pos[:, 1] - 1.0)
            ring = np.exp(-(((d - age * _SPLASH_SPEED * speed) / _SPLASH_WIDTH) ** 2))
            glow += (ring * (1.0 - age / _SPLASH_LIFE) * 0.8).astype(np.float32)

        out += (fg - bg) * np.clip(glow, 0.0, 1.0)[:, None]
        return np.clip(out, 0.0, 1.0).astype(np.float32)
