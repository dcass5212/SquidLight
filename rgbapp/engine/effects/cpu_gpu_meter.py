from __future__ import annotations

import math

import numpy as np

from ._common import lerp
from .base import Ctx, Effect, Param, parse_color, sample_gradient

_SOURCES = ["CPU load", "RAM use", "GPU load"]
_KEYS = {"CPU load": "cpu_load", "RAM use": "ram_load", "GPU load": "gpu_load"}
_NEEDS = {"CPU load": "cpu", "RAM use": "cpu", "GPU load": "sensors"}


class CpuGpuMeter(Effect):
    """A bar that fills with CPU, RAM or GPU load."""

    id = "cpu_gpu_meter"
    name = "CPU/GPU Meter"
    params = [
        Param("source", "Show", "enum", "CPU load", choices=_SOURCES),
        Param("direction", "Fills toward", "enum", "Right", choices=["Right", "Left", "Up", "Down"]),
        Param("gradient", "Colors (idle to maxed)", "gradient", [(0.0, "#00e060"), (0.6, "#ffd000"), (1.0, "#ff2020")]),
        Param("background", "Empty color", "color", "#080808"),
        Param("smoothing", "Smoothing (s)", "float", 0.4, min=0.0, max=3.0),
    ]

    def __init__(self) -> None:
        self._shown = 0.0

    @classmethod
    def required_inputs(cls, params: dict) -> set[str]:
        return {_NEEDS.get(params.get("source", "CPU load"), "cpu")}

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        stats = ctx.inputs.get("stats")
        src = params["source"]
        value = stats.get(_KEYS[src]) if stats is not None else None
        self.notice = stats.errors.get(_NEEDS[src]) if stats is not None else None
        target = 0.0 if value is None else float(value)
        k = 1.0 if params["smoothing"] <= 0 else 1.0 - math.exp(-max(ctx.dt, 0.0) / params["smoothing"])
        self._shown += (target - self._shown) * k
        v = min(max(self._shown, 0.0), 1.0)

        p = ctx.led_positions
        axis = {"Right": p[:, 0], "Left": 1.0 - p[:, 0], "Up": 1.0 - p[:, 1], "Down": p[:, 1]}[params["direction"]]
        edge = max(1.0 / max(ctx.n, 1), 0.04)  # soft edge so the end LED shows partial loads
        lit = np.clip((v * (1.0 + edge) - axis) / edge, 0.0, 1.0) if v > 0.005 else np.zeros(ctx.n)
        color = sample_gradient(params["gradient"], np.full(ctx.n, v))  # the whole bar takes the load's color
        return lerp(parse_color(params["background"]), color, lit)
