from __future__ import annotations

import math

import numpy as np

from .base import Ctx, Effect, Param, parse_color, sample_gradient


class Comet(Effect):
    """A bright head with a fading tail sweeping across the device."""

    id = "comet"
    name = "Comet"
    params = [
        Param("head", "Head color", "color", "#ffffff"),
        Param("tail", "Tail color", "color", "#1e5cff"),
        Param("background", "Background", "color", "#000000"),
        Param("speed", "Speed (sweeps/sec)", "float", 0.4, min=-3.0, max=3.0),
        Param("tail_length", "Tail length (device widths)", "float", 0.35, min=0.05, max=1.5),
        Param("head_size", "Head size (device widths)", "float", 0.04, min=0.0, max=0.3),
        Param("angle", "Direction (degrees)", "float", 0.0, min=0.0, max=360.0),
        # loop: runs off one side and starts again; bounce: turns around at each end
        Param("mode", "Mode", "enum", "loop", choices=("loop", "bounce")),
    ]

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        n = ctx.n
        bg = parse_color(params["background"])
        if n == 0:
            return np.zeros((0, 3), dtype=np.float32)

        # Where each LED sits along the travel direction, 0..1 across the device's own extent.
        a = math.radians(params["angle"])
        pos = ctx.led_positions
        along = (pos[:, 0] - 0.5) * math.cos(a) + (pos[:, 1] - 0.5) * math.sin(a)
        lo, hi = float(along.min()), float(along.max())
        u = (along - lo) / (hi - lo) if hi - lo > 1e-6 else np.full(n, 0.5)

        tail_len = params["tail_length"]
        r = params["head_size"]
        cycles = ctx.t * abs(params["speed"])
        forward = params["speed"] >= 0
        if params["mode"] == "bounce":
            f = cycles % 2.0
            moving_up = f <= 1.0
            head = f if moving_up else 2.0 - f
            sign = 1.0 if moving_up else -1.0
            if not forward:
                head, sign = 1.0 - head, -sign
        else:
            # Enter fully off one end, leave fully off the other, so the tail never pops.
            span = 1.0 + tail_len + 2.0 * r
            head = (cycles % 1.0) * span - r
            sign = 1.0
            if not forward:
                head, sign = 1.0 - head, -1.0

        behind = (head - u) * sign  # > 0: LED is in the tail; < 0: ahead of the head
        k = np.clip(behind / tail_len, 0.0, 1.0)
        level = np.where(behind >= 0.0, (1.0 - k) ** 1.6, 0.0)
        if r > 0.0:  # a short glow in front of the head
            level = np.maximum(level, np.clip(1.0 + behind / r, 0.0, 1.0) * (behind < 0.0))
        color = sample_gradient([(0.0, params["head"]), (1.0, params["tail"])], np.sqrt(k))
        return (bg + (color - bg) * level[:, None]).astype(np.float32)
