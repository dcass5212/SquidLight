"""Static per-key: the colors painted in Paint mode. Keys that were never painted are
left transparent, so the layers below show through them."""

from __future__ import annotations

from typing import Optional

import numpy as np

from .base import Ctx, Effect, Param, parse_color


class StaticKeys(Effect):
    id = "static_keys"
    name = "Static per-key"
    listed = False
    params = [Param("colors", "Painted keys", "ledmap", {}, hidden=True)]

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        out = np.zeros((ctx.n, 3), dtype=np.float32)
        for k, color in params["colors"].items():
            i = int(k)
            if i < ctx.n:
                out[i] = parse_color(color)
        return out

    def coverage(self, ctx: Ctx, params: dict) -> Optional[np.ndarray]:
        cov = np.zeros(ctx.n, dtype=bool)
        for k in params["colors"]:
            i = int(k)
            if i < ctx.n:
                cov[i] = True
        return cov
