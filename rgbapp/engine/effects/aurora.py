from __future__ import annotations

import math

import numpy as np

from .base import Ctx, Effect, Param, parse_color, sample_gradient

_TAU = 2.0 * math.pi


class Aurora(Effect):
    """Slow curtains of drifting color, like the northern lights."""

    id = "aurora"
    name = "Aurora"
    params = [
        Param("gradient", "Colors", "gradient", ((0.0, "#00ff87"), (0.5, "#00b4a6"), (1.0, "#8a3dff"))),
        Param("background", "Background", "color", "#00060a"),
        Param("speed", "Speed", "float", 1.0, min=0.0, max=5.0),
        Param("scale", "Curtain size", "float", 1.0, min=0.3, max=4.0),
        Param("contrast", "Contrast", "float", 1.6, min=0.5, max=4.0),
    ]

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        n = ctx.n
        if n == 0:
            return np.zeros((0, 3), dtype=np.float32)
        x, y = ctx.led_positions[:, 0], ctx.led_positions[:, 1]
        t = ctx.t * params["speed"] * 0.06  # at speed 1 a full drift takes about 17 s
        s = params["scale"]

        # Domain warp: bends the curtains so they ripple instead of sliding in straight lines.
        warp = 0.22 * np.sin(_TAU * (1.3 * y + 0.8 * t) + 1.3) + 0.12 * np.sin(_TAU * (2.3 * x - 1.1 * t))
        u = x * s + warp
        c1 = np.sin(_TAU * (1.0 * u + t))
        c2 = np.sin(_TAU * (1.7 * u - 1.3 * t) + 2.0 + 1.5 * np.sin(_TAU * (0.7 * y + 0.5 * t)))
        band = 0.5 + 0.25 * c1 + 0.25 * c2  # 0..1, bright where the curtains line up
        # Curtains hang from above: brighter toward the top, with a slow vertical shimmer.
        hang = 0.65 + 0.35 * (1.0 - y) * (0.8 + 0.2 * np.sin(_TAU * (1.5 * x + 2.0 * t)))
        level = np.clip(band, 0.0, 1.0) ** params["contrast"] * hang

        # Color drifts on its own slower field, so brightness and hue don't move together.
        hue = 0.5 + 0.5 * np.sin(_TAU * (0.45 * x * s + 0.35 * y + 0.7 * t) + 0.6 * c2)
        color = sample_gradient(params["gradient"], hue)
        bg = parse_color(params["background"])
        return (bg + (color - bg) * np.clip(level, 0.0, 1.0)[:, None]).astype(np.float32)
