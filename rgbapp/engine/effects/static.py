from __future__ import annotations

import numpy as np

from .base import Ctx, Effect, Param, parse_color


class Static(Effect):
    id = "static"
    name = "Static"
    params = [Param("color", "Color", "color", "#ff0000")]

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        return np.tile(parse_color(params["color"]), (ctx.n, 1))
