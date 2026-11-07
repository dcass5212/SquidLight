from __future__ import annotations

import numpy as np

from ..inputs.audio import N_BANDS
from ._common import bottom_rows, lerp, linear
from .base import Ctx, Effect, Param, parse_color, sample_gradient

_RAINBOW = [(0.0, "#ff0040"), (0.35, "#ffd000"), (0.65, "#00e070"), (1.0, "#0090ff")]


def audio_notice(audio) -> str | None:
    return audio.error if audio is not None and audio.error and audio.error != "starting" else None


class AudioSpectrum(Effect):
    """Equalizer bars: low pitches on the left, highs on the right."""

    id = "audio_spectrum"
    name = "Audio Spectrum"
    needs = {"audio"}
    params = [
        Param("gradient", "Colors (low to high pitch)", "gradient", _RAINBOW),
        Param("background", "Background", "color", "#000000"),
        Param("sensitivity", "Sensitivity", "float", 1.0, min=0.3, max=3.0),
    ]

    def render(self, ctx: Ctx, params: dict) -> np.ndarray:
        audio = ctx.inputs.get("audio")
        self.notice = audio_notice(audio)
        bands = np.zeros(N_BANDS) if audio is None else np.asarray(audio.bands, dtype=np.float64)
        bands = np.clip(bands * params["sensitivity"], 0.0, 1.0)
        x = ctx.led_positions[:, 0]
        # interpolate between bands so a strip with few LEDs still draws a smooth curve
        value = np.interp(x * (N_BANDS - 1), np.arange(N_BANDS), bands)
        color = sample_gradient(params["gradient"], x)
        if linear(ctx):  # a strip has no "up": brightness is the bar
            level = value
        else:  # a keyboard: bars rise from the bottom row
            height = 1.0 - bottom_rows(ctx)[:, 1]
            level = np.clip((value - height) / 0.12 + 0.5, 0.0, 1.0)  # soft top edge
            level = np.where(value < 0.03, 0.0, level)
        return lerp(parse_color(params["background"]), color, level)
