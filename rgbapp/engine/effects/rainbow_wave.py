from __future__ import annotations

import math

import numpy as np

from .base import Ctx, Effect, Param, hsv_to_rgb


class RainbowWave(Effect):
    id = "rainbow_wave"
    name = "Rainbow Wave"
    params = [
        Param("speed", "Speed (cycles/sec)", "float", 0.14, min=-3.0, max=3.0),
        Param("wavelength", "Wavelength (device widths)", "float", 1.0, min=0.1, max=8.0),
        Param("angle", "Direction (degrees)", "float", 0.0, min=0.0, max=360.0),
        Param("saturation", "Saturation", "float", 1.0, min=0.0, max=1.0),
        Param("brightness", "Brightness", "float", 1.0, min=0.0, max=1.0),
    ]

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        a = math.radians(params["angle"])
        centered = ctx.led_positions - 0.5
        along = centered[:, 0] * math.cos(a) + centered[:, 1] * math.sin(a)
        hue = along / params["wavelength"] - ctx.t * params["speed"]
        return hsv_to_rgb(hue, params["saturation"], params["brightness"])
