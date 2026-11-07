from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ._common import hashed_origin, lerp
from .base import Ctx, Effect, Param, hsv_to_rgb, parse_color


class ReactiveTyping(Effect):
    """Keys flash when pressed and fade out. On devices without keys, a press flashes the
    stretch of the device its key maps to."""

    id = "reactive_typing"
    name = "Reactive Typing"
    needs = {"keys"}
    params = [
        Param("color", "Key color", "color", "#00c8ff"),
        Param("random_colors", "Random color per press", "bool", False),
        Param("background", "Background", "color", "#000000"),
        Param("fade", "Fade time (s)", "float", 0.8, min=0.1, max=4.0),
        Param("hold", "Stay lit while held", "bool", True),
    ]

    def __init__(self) -> None:
        self._level: Optional[np.ndarray] = None
        self._hue: Optional[np.ndarray] = None
        self._seen = 0
        self._rng = np.random.default_rng()

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        n = ctx.n
        if self._level is None or len(self._level) != n:
            self._level, self._hue = np.zeros(n), np.zeros(n)
        keys = ctx.inputs.get("keys")
        self.notice = keys.error if keys is not None and keys.error else None
        dt = min(max(ctx.dt, 0.0), 0.25)
        self._level *= math.exp(-dt * 4.6 / params["fade"])  # about 1% left after `fade` seconds

        by_name: dict[str, list[int]] = {}
        for i, name in enumerate(ctx.led_names):
            by_name.setdefault(name, []).append(i)
        has_keys = any(k.startswith("Key: ") for k in by_name)

        def idx_for(name: str) -> list[int]:
            if name in by_name:
                return by_name[name]
            if has_keys or n == 0:
                return []  # a keyboard without this key
            c = int(hashed_origin(name, ctx)[0] / max(ctx.aspect, 1e-6) * (n - 1))
            return list(range(max(0, c - 1), min(n, c + 2)))

        if keys is not None:
            for ev in keys.events:
                if ev.seq <= self._seen:
                    continue
                self._seen = ev.seq
                idx = idx_for(ev.name)
                if idx:
                    self._level[idx] = 1.0
                    self._hue[idx] = self._rng.random()
            if params["hold"]:
                for name in keys.pressed:
                    idx = idx_for(name)
                    if idx:
                        self._level[idx] = 1.0

        if params["random_colors"]:
            fg = hsv_to_rgb(self._hue, 1.0, 1.0)
        else:
            fg = np.broadcast_to(parse_color(params["color"]), (n, 3))
        return lerp(parse_color(params["background"]), fg, self._level)
