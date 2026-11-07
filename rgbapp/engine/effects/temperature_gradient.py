from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .base import Ctx, Effect, Param, sample_gradient

_SOURCES = ["CPU temperature", "GPU temperature"]
_KEYS = {"CPU temperature": "cpu_temp", "GPU temperature": "gpu_temp"}


class TemperatureGradient(Effect):
    """The whole device takes a color from a gradient according to a temperature."""

    id = "temperature_gradient"
    name = "Temperature Gradient"
    needs = {"sensors"}
    params = [
        Param("source", "Sensor", "enum", "CPU temperature", choices=_SOURCES),
        Param(
            "gradient", "Colors (cool to hot)", "gradient",
            [(0.0, "#0040ff"), (0.35, "#00e0a0"), (0.65, "#ffd000"), (1.0, "#ff1010")],
        ),
        Param("low", "Cool at (°C)", "float", 30.0, min=0.0, max=100.0),
        Param("high", "Hot at (°C)", "float", 85.0, min=10.0, max=120.0),
        Param("smoothing", "Smoothing (s)", "float", 1.0, min=0.0, max=5.0),
    ]

    def __init__(self) -> None:
        self._shown: Optional[float] = None

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        stats = ctx.inputs.get("stats")
        temp = stats.get(_KEYS[params["source"]]) if stats is not None else None
        if temp is None:
            err = stats.errors.get("sensors") if stats is not None else None
            self.notice = err or "The sensor source has no reading for this."
            return np.full((ctx.n, 3), 0.04, dtype=np.float32)  # dim, so it is clearly idle
        self.notice = None
        if self._shown is None:
            self._shown = temp
        k = 1.0 if params["smoothing"] <= 0 else 1.0 - math.exp(-max(ctx.dt, 0.0) / params["smoothing"])
        self._shown += (temp - self._shown) * k
        lo, hi = params["low"], max(params["high"], params["low"] + 1.0)
        u = min(max((self._shown - lo) / (hi - lo), 0.0), 1.0)
        col = sample_gradient(params["gradient"], np.array(u))
        return np.broadcast_to(col.astype(np.float32), (ctx.n, 3)).copy()
