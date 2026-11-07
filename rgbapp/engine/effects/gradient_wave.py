from __future__ import annotations

import math

import numpy as np

from .base import Ctx, Effect, Param, sample_gradient


class GradientWave(Effect):
    """A user gradient scrolling across the device."""

    id = "gradient_wave"
    name = "Gradient Wave"
    params = [
        Param("gradient", "Colors", "gradient", ((0.0, "#ff0066"), (0.5, "#7a00ff"), (1.0, "#00c8ff"))),
        Param("speed", "Speed (waves/sec)", "float", 0.11, min=-3.0, max=3.0),
        Param("wavelength", "Wave size (device widths)", "float", 1.0, min=0.1, max=8.0),
        Param("angle", "Direction (degrees)", "float", 0.0, min=0.0, max=360.0),
        # mirrored, the gradient runs there and back so the wave has no hard seam
        Param("mirror", "Mirror (no seam)", "bool", True),
    ]

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        a = math.radians(params["angle"])
        centered = ctx.led_positions - 0.5
        along = centered[:, 0] * math.cos(a) + centered[:, 1] * math.sin(a)
        phase = np.mod(along / params["wavelength"] - ctx.t * params["speed"], 1.0)
        if params["mirror"]:
            phase = 1.0 - np.abs(2.0 * phase - 1.0)
        return sample_gradient(params["gradient"], phase)
