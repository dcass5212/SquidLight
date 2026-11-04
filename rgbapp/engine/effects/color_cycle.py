from __future__ import annotations

import numpy as np

from .base import Ctx, Effect, Param, hsv_to_rgb


class ColorCycle(Effect):
    """The whole device fades through the spectrum together."""

    id = "color_cycle"
    name = "Color Cycle"
    params = [
        Param("speed", "Speed (cycles/sec)", "float", 0.1, min=0.01, max=2.0),
        Param("saturation", "Saturation", "float", 1.0, min=0.0, max=1.0),
        Param("brightness", "Brightness", "float", 1.0, min=0.0, max=1.0),
    ]

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        rgb = hsv_to_rgb(np.array(ctx.t * params["speed"]), params["saturation"], params["brightness"])
        return np.tile(rgb, (ctx.n, 1))
