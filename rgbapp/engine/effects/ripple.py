from __future__ import annotations

import numpy as np

from ._common import hashed_origin, key_origins
from .base import Ctx, Effect, Param, hsv_to_rgb, parse_color

_MAX_RIPPLES = 24


class Ripple(Effect):
    """A ring expands from each pressed key."""

    id = "ripple"
    name = "Ripple"
    needs = {"keys"}
    params = [
        Param("color", "Ring color", "color", "#ff4bd8"),
        Param("random_colors", "Random color per press", "bool", False),
        Param("background", "Background", "color", "#000000"),
        Param("speed", "Speed", "float", 1.1, min=0.15, max=6.0),
        Param("width", "Ring width", "float", 0.35, min=0.1, max=1.5),
        Param("lifetime", "Lifetime (s)", "float", 1.8, min=0.3, max=4.0),
    ]

    def __init__(self) -> None:
        self._rings: list[dict] = []  # origin, age, hue
        self._seen = 0
        self._rng = np.random.default_rng()

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        n = ctx.n
        keys = ctx.inputs.get("keys")
        self.notice = keys.error if keys is not None and keys.error else None
        dt = min(max(ctx.dt, 0.0), 0.25)
        life = params["lifetime"]
        for r in self._rings:
            r["age"] += dt
        self._rings = [r for r in self._rings if r["age"] < life]

        if keys is not None:
            origins = key_origins(ctx)
            for ev in keys.events:
                if ev.seq <= self._seen:
                    continue
                self._seen = ev.seq
                o = origins.get(ev.name)
                if o is None:
                    if origins:
                        continue  # a keyboard without this key
                    o = hashed_origin(ev.name, ctx)
                self._rings.append({"o": o, "age": min(ev.age, life * 0.5), "hue": self._rng.random()})
            self._rings = self._rings[-_MAX_RIPPLES:]

        bg = parse_color(params["background"])
        out = np.broadcast_to(bg, (n, 3)).astype(np.float32).copy()
        if not self._rings or n == 0:
            return out
        pos = ctx.led_positions * np.array([ctx.aspect, 1.0])
        base = parse_color(params["color"])
        for r in self._rings:
            d = np.linalg.norm(pos - r["o"], axis=1)
            radius = r["age"] * params["speed"]
            ring = np.exp(-(((d - radius) / params["width"]) ** 2)) * (1.0 - r["age"] / life)
            col = hsv_to_rgb(np.array([r["hue"]]), 1.0, 1.0)[0] if params["random_colors"] else base
            out += (col - bg) * ring[:, None].astype(np.float32)
        return np.clip(out, 0.0, 1.0).astype(np.float32)
