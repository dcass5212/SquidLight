from __future__ import annotations

import math

import numpy as np

from .base import Ctx, Effect, Param, parse_color


class Breathing(Effect):
    id = "breathing"
    name = "Breathing"
    params = [
        Param("color", "Color", "color", "#00aaff"),
        Param("speed", "Speed (breaths/sec)", "float", 0.18, min=0.02, max=2.0),
        Param("min_level", "Dimmest level", "float", 0.0, min=0.0, max=1.0),
    ]

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        # Raised cosine: starts dim at t=0, peaks half a period later, no sharp edges.
        wave = 0.5 - 0.5 * math.cos(2.0 * math.pi * params["speed"] * ctx.t)
        level = params["min_level"] + (1.0 - params["min_level"]) * wave
        return np.tile(parse_color(params["color"]) * np.float32(level), (ctx.n, 1))
