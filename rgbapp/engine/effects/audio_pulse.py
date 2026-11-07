from __future__ import annotations

import numpy as np

from .audio_spectrum import audio_notice
from .base import Ctx, Effect, Param, parse_color, sample_gradient


class AudioPulse(Effect):
    """The whole device throbs with the bass."""

    id = "audio_pulse"
    name = "Audio Pulse"
    needs = {"audio"}
    params = [
        Param("gradient", "Colors (quiet to loud)", "gradient", [(0.0, "#2a0050"), (0.6, "#ff1493"), (1.0, "#ffffff")]),
        Param("background", "Background", "color", "#000000"),
        Param("sensitivity", "Sensitivity", "float", 1.0, min=0.3, max=3.0),
        Param("threshold", "Beat threshold", "float", 0.35, min=0.0, max=0.9),
    ]

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        audio = ctx.inputs.get("audio")
        self.notice = audio_notice(audio)
        bass = (audio.bass if audio is not None else 0.0) * params["sensitivity"]
        thr = params["threshold"]
        level = float(np.clip((bass - thr) / (1.0 - thr), 0.0, 1.0))
        bg = parse_color(params["background"])
        col = sample_gradient(params["gradient"], np.array(level))
        out = bg + (col - bg) * level
        return np.broadcast_to(out.astype(np.float32), (ctx.n, 3)).copy()
