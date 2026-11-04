from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .base import Ctx, Effect, Param, hsv_to_rgb, parse_color

_MAX_DT = 0.25  # after a stall, don't spawn a burst of stars


class Starlight(Effect):
    """Random LEDs twinkle on and fade out over a background color."""

    id = "starlight"
    name = "Starlight"
    params = [
        Param("color", "Star color", "color", "#ffffff"),
        Param("random_colors", "Random star colors", "bool", False),
        Param("background", "Background", "color", "#000000"),
        Param("density", "Density", "float", 0.15, min=0.01, max=1.0),
        Param("duration", "Twinkle time (s)", "float", 1.2, min=0.1, max=5.0),
    ]

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = np.random.default_rng(seed)
        self._age: Optional[np.ndarray] = None  # seconds since each star lit; NaN = dark
        self._hue: Optional[np.ndarray] = None

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        n = ctx.n
        if self._age is None or len(self._age) != n:
            self._age = np.full(n, np.nan)
            self._hue = np.zeros(n)
        dur = params["duration"]
        dt = min(max(ctx.dt, 0.0), _MAX_DT)

        age = self._age + dt
        age[age >= dur] = np.nan  # finished stars go dark
        # Each dark LED lights with rate density/duration, so on average ``density`` of
        # the device is twinkling at any moment.
        dark = np.isnan(age)
        p = 1.0 - math.exp(-params["density"] / dur * dt)
        born = dark & (self._rng.random(n) < p)
        age[born] = 0.0
        self._hue[born] = self._rng.random(int(born.sum()))
        self._age = age

        level = np.where(np.isnan(age), 0.0, np.sin(np.pi * np.nan_to_num(age) / dur))[:, None]
        if params["random_colors"]:
            star = hsv_to_rgb(self._hue, 1.0, 1.0)
        else:
            star = np.broadcast_to(parse_color(params["color"]), (n, 3))
        bg = parse_color(params["background"])
        return (bg + (star - bg) * level).astype(np.float32)
