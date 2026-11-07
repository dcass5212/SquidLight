from __future__ import annotations

import numpy as np

from ._common import bottom_rows, hashed_origin
from .base import Ctx, Effect, Param, sample_gradient

# Fine enough that one cell is a quarter of a key: ~4.5 cells per key across, ~4 rows per key row.
_GRID_W = 100
_GRID_H = 24
_NODE_GAP = 7  # cells between tongue control points, a bit over one key wide: fewer, chunkier tongues
_TEX_H, _TEX_W = 7, 24  # coarse texture grid that scrolls up through the flames
_STEP = 1.0 / 4.5  # simulation ticks per second at speed 1 (the old 0.15x setting); frames in between are interpolated
_NODE_X = np.arange(0, _GRID_W + _NODE_GAP, _NODE_GAP, dtype=np.float32)
_POINT_X = np.arange(2 * len(_NODE_X) - 1, dtype=np.float32) * (_NODE_GAP / 2.0)
_CELL_X = np.arange(_GRID_W, dtype=np.float32)
_UP = (((_GRID_H - 1) - np.arange(_GRID_H, dtype=np.float32)) / (_GRID_H - 1))[:, None]  # 0 bottom .. 1 top


def _upsample_matrix(n_out: int, n_in: int) -> np.ndarray:
    """Linear-interpolation weights (n_out, n_in) that stretch a coarse axis over a fine one."""
    pos = np.linspace(0.0, n_in - 1.0, n_out)
    lo = np.minimum(np.floor(pos).astype(int), n_in - 2)
    w = np.zeros((n_out, n_in), dtype=np.float32)
    w[np.arange(n_out), lo] = 1.0 - (pos - lo)
    w[np.arange(n_out), lo + 1] = pos - lo
    return w


_RY = _upsample_matrix(_GRID_H, _TEX_H)
_RX = _upsample_matrix(_GRID_W, _TEX_W)


def _bilinear(field: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """Sample ``field`` (H, W) at normalized LED positions (y = 0 is the top) with smooth blending."""
    h, w = field.shape
    p = np.clip(pos, 0.0, 1.0)
    fx, fy = p[:, 0] * (w - 1), p[:, 1] * (h - 1)
    x0, y0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    tx, ty = (fx - x0).astype(np.float32), (fy - y0).astype(np.float32)
    top = field[y0, x0] * (1 - tx) + field[y0, x1] * tx
    bot = field[y1, x0] * (1 - tx) + field[y1, x1] * tx
    return top * (1 - ty) + bot * ty

# Left = background and flame tips, middle = the coal-to-flame transition, right = the solid base.
_DEFAULT_GRADIENT = [
    (0.0, "#000000"),
    (0.15, "#5a0600"),
    (0.4, "#d03000"),
    (0.65, "#ff6a00"),
    (0.85, "#ffb200"),
    (1.0, "#ffdc70"),
]


class Fire(Effect):
    """Table-top flames: chunky tongues with a solid base, shaded by how far up each flame you are.

    The gradient is read by position in the flame, not by random heat: its right end is the base
    (solid along the bottom), the middle is the coal-to-flame transition, and the left end is the
    tips and the background. A slowly scrolling soft texture folds some lick and wobble into it.
    """

    id = "fire"
    name = "Fire"
    params = [
        Param("palette", "Flame colors", "gradient", _DEFAULT_GRADIENT),
        Param("intensity", "Intensity", "float", 0.85, min=0.1, max=1.0),
        Param("height", "Flame height", "float", 0.6, min=0.1, max=1.0),
        Param("speed", "Speed", "float", 1.0, min=0.0, max=3.0),
        Param("react", "React to typing", "bool", False),
        Param("flare", "Typing flare", "float", 1.0, min=0.2, max=2.0),
    ]

    @classmethod
    def required_inputs(cls, params: dict) -> set[str]:
        return {"keys"} if params.get("react") else set()

    def __init__(self) -> None:
        blank = np.zeros((_GRID_H, _GRID_W), dtype=np.float32)
        self._view = blank.copy()  # what is drawn: gradient position per cell
        self._view_prev = blank.copy()
        self._blaze = blank.copy()  # typing blazes, drawn over the flames
        self._coarse = np.random.default_rng().random((_TEX_H, _TEX_W)).astype(np.float32)
        self._tongue = np.zeros(len(_NODE_X), dtype=np.float32)
        self._acc = 0.0
        self._seen = 0
        self._rng = np.random.default_rng()

    def _key_cell(self, name: str, ctx: Ctx, cache: dict) -> tuple[int, int] | None:
        """Grid cell (row, col) under a key, or None for a key this device doesn't have."""
        if not cache:
            groups: dict[str, list] = {}
            for i, nm in enumerate(ctx.led_names):
                groups.setdefault(nm, []).append(ctx.led_positions[i])
            cache.update({k: np.mean(v, axis=0) for k, v in groups.items()})
            cache["__keys__"] = any(k.startswith("Key: ") for k in groups)
        p = cache.get(name)
        if p is None:
            if cache["__keys__"]:
                return None
            x = float(hashed_origin(name, ctx)[0]) / max(ctx.aspect, 1e-6)
            p = np.array([x, 0.5])
        p = np.clip(p, 0.0, 1.0)
        return int(round(p[1] * (_GRID_H - 1))), int(round(p[0] * (_GRID_W - 1)))

    def _ignite(self, cell: tuple[int, int], amount: float) -> None:
        r, c = cell
        # An oval blaze centred just above the key (taller than wide), drawn over the flames so the
        # flame-height envelope can't mask it. It is brighter than the normal flames and fades quickly.
        r0, r1 = max(0, r - 9), min(_GRID_H, r + 5)
        c0, c1 = max(0, c - 8), min(_GRID_W, c + 9)
        yy = (np.arange(r0, r1, dtype=np.float32) - (r - 2.0))[:, None] / 4.0
        xx = (np.arange(c0, c1, dtype=np.float32) - c)[None, :] / 3.2
        blob = np.exp(-(xx * xx + yy * yy) * 1.2) * min(1.0, amount) * 1.25
        self._blaze[r0:r1, c0:c1] = np.maximum(self._blaze[r0:r1, c0:c1], blob)
        # And make that stretch of flame leap up for a moment.
        node = int(round(c / _NODE_GAP))
        self._tongue[max(0, node - 1) : node + 2] = np.maximum(self._tongue[max(0, node - 1) : node + 2], 0.2 * amount)

    def _step(self, intensity: float, height: float) -> None:
        # Texture: soft blobs that drift up the flames, one new coarse row at a time.
        self._coarse[:-1] = self._coarse[1:]
        self._coarse[-1] = self._rng.random(_TEX_W, dtype=np.float32)
        tex = _RY @ self._coarse @ _RX.T

        # Tongues: one slowly drifting control point per key or so, joined by straight lines, so each
        # flame tip is a point and the flames merge into a solid bed lower down.
        t = 0.92 * self._tongue + 0.22 * (self._rng.random(len(_NODE_X), dtype=np.float32) - 0.5)
        self._tongue = t
        peaks = np.clip(0.5 + t * 2.5, 0.1, 1.0)
        pts = np.empty(2 * len(peaks) - 1, dtype=np.float32)
        pts[0::2] = peaks
        pts[1::2] = 0.4 * np.minimum(peaks[:-1], peaks[1:])
        tongue = np.interp(_CELL_X, _POINT_X, pts).astype(np.float32)
        reach = np.maximum(height * (0.1 + 0.95 * tongue) + 0.06, 0.05)[None, :]  # flame top per column

        u = _UP / reach  # 0 at the base of a flame, 1 at its tip
        wobble = (tex - 0.5) * np.minimum(1.0, u * 3.0)  # no texture on the very bottom: it stays solid
        ue = u + wobble * 0.3 * u  # ripples the flame outline
        inside = np.clip((1.0 - ue) / 0.14, 0.0, 1.0)  # fairly crisp edge, a little cartoonish
        g = np.clip(1.2 - 1.1 * ue + wobble * 0.35, 0.0, 1.0)  # gradient position: base 1 -> tip 0
        g *= 0.2 + 0.8 * intensity
        self._view_prev, self._view = self._view, (g * inside).astype(np.float32)

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        n = ctx.n
        dt = min(max(ctx.dt, 0.0), 0.25)
        keys = ctx.inputs.get("keys") if params["react"] else None
        self.notice = keys.error if keys is not None and keys.error else None
        if keys is not None and n:
            cache: dict = {}
            amount = min(1.0, 0.8 * params["flare"])
            for ev in keys.events:
                if ev.seq <= self._seen:
                    continue
                self._seen = ev.seq
                cell = self._key_cell(ev.name, ctx, cache)
                if cell:
                    self._ignite(cell, amount)
            for name in keys.pressed:  # held keys keep burning
                cell = self._key_cell(name, ctx, cache)
                if cell:
                    self._ignite(cell, amount * 0.7)
        self._blaze *= float(np.exp(-dt / 0.3))  # real time, independent of the speed setting
        self._acc += dt * params["speed"]
        steps = 0
        while self._acc >= _STEP and steps < 8:
            self._acc -= _STEP
            self._step(params["intensity"], params["height"])
            steps += 1
        if self._acc > _STEP:
            self._acc = 0.0
        if n == 0:
            return np.zeros((0, 3), dtype=np.float32)
        # Blend between the last two ticks so slow speeds glide instead of stepping.
        alpha = min(self._acc / _STEP, 1.0)
        alpha = alpha * alpha * (3.0 - 2.0 * alpha)
        field = self._view_prev + (self._view - self._view_prev) * alpha
        heat = _bilinear(np.maximum(field, self._blaze), bottom_rows(ctx, strip_y=0.8))
        return np.clip(sample_gradient(params["palette"], heat), 0.0, 1.0).astype(np.float32)
