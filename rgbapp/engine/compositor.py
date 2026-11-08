"""Layer stack -> one float32 frame buffer per device group.

Layers are drawn bottom to top onto a black canvas. Each layer is blended with its
blend mode, scaled by its opacity, and only affects LEDs inside its mask.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .devices import DeviceGroup, Mask
from .effects import Ctx, Effect, get_effect_class

log = logging.getLogger(__name__)


def _normal(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    return src


def _add(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    return np.minimum(dst + src, 1.0)


def _multiply(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    return dst * src


def _screen(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    return 1.0 - (1.0 - dst) * (1.0 - src)


def _subtract(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    return np.maximum(dst - src, 0.0)


def _darken(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    return np.minimum(dst, src)


def _lighten(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    return np.maximum(dst, src)


def _overlay(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    # Multiply in the shadows of what is below, screen in its highlights.
    return np.where(dst < 0.5, 2.0 * dst * src, 1.0 - 2.0 * (1.0 - dst) * (1.0 - src))


def _difference(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    return np.abs(dst - src)


def _dodge(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    # dst / (1 - src); a full-strength src lifts any non-black dst to white, black stays black.
    denom = np.maximum(1.0 - src, np.float32(1e-6))
    return np.minimum(dst / denom, 1.0)


# Dict order is the order the layer row's dropdown shows.
BLEND_MODES: dict[str, Callable[[np.ndarray, np.ndarray], np.ndarray]] = {
    "normal": _normal,
    "add": _add,
    "subtract": _subtract,
    "multiply": _multiply,
    "screen": _screen,
    "overlay": _overlay,
    "darken": _darken,
    "lighten": _lighten,
    "difference": _difference,
    "dodge": _dodge,
}

# Label and one-line hint per mode, for the layer row (name, tooltip).
BLEND_INFO: dict[str, tuple[str, str]] = {
    "normal": ("Normal", "Covers what is below"),
    "add": ("Add", "Adds light to what is below"),
    "subtract": ("Subtract", "Removes this layer's light from what is below"),
    "multiply": ("Multiply", "Darkens: only light both layers share survives"),
    "screen": ("Screen", "Brightens without clipping, like projecting both lights"),
    "overlay": ("Overlay", "Deepens dark areas and brightens bright ones"),
    "darken": ("Darken", "Keeps the darker of each color channel"),
    "lighten": ("Lighten", "Keeps the lighter of each color channel"),
    "difference": ("Difference", "Shows how far apart the two layers are"),
    "dodge": ("Color dodge", "Pushes what is below toward white"),
}


@dataclass
class LayerSpec:
    """The serializable description of one layer (matches the profile JSON)."""

    effect: str
    params: dict = field(default_factory=dict)
    mask: Mask = "all"
    blend: str = "normal"
    opacity: float = 1.0
    visible: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex)  # keeps effect state across edits


@dataclass
class _Runtime:
    spec: LayerSpec
    effect: Effect
    params: dict
    mask: Optional[np.ndarray]  # None means "all LEDs"
    warned: bool = False


class Compositor:
    def __init__(self, group: DeviceGroup) -> None:
        self.group = group
        self._layers: list[_Runtime] = []

    def set_layers(self, specs: list[LayerSpec]) -> None:
        """Replace the stack. Safe to call from another thread: the swap is one assignment.

        A layer whose id and effect are unchanged keeps its effect instance (and so its
        animation state); everything else is rebuilt.
        """
        old = {r.spec.id: r for r in self._layers}
        new: list[_Runtime] = []
        for spec in specs:
            if spec.blend not in BLEND_MODES:
                log.error("layer %s: unknown blend %r; skipped", spec.id, spec.blend)
                continue
            try:
                cls = get_effect_class(spec.effect)
            except KeyError as exc:
                log.error("layer skipped: %s", exc)
                continue
            prev = old.get(spec.id)
            effect = prev.effect if prev and prev.spec.effect == spec.effect else cls()
            mask = None if spec.mask == "all" else self.group.resolve_mask(spec.mask)
            new.append(_Runtime(spec, effect, cls.resolve_params(spec.params), mask))
        self._layers = new

    def needs(self) -> set[str]:
        """Inputs required by the layers that actually draw (visible, not fully transparent)."""
        out: set[str] = set()
        for l in self._layers:
            if l.spec.visible and l.spec.opacity > 0.0:
                out |= type(l.effect).required_inputs(l.params)
        return out

    def notices(self) -> dict[str, str]:
        """layer id -> why that layer is not working right now."""
        return {l.spec.id: l.effect.notice for l in self._layers if l.effect.notice}

    def render(self, ctx: Ctx) -> np.ndarray:
        n = self.group.led_count
        out = np.zeros((n, 3), dtype=np.float32)
        # How much of each LED the layers so far have actually drawn. Where nothing is
        # below yet, a layer has nothing to blend with and lands as Normal.
        acc = np.zeros((n, 1), dtype=np.float32)
        for layer in self._layers:
            spec = layer.spec
            if not spec.visible or spec.opacity <= 0.0:
                continue
            try:
                src = layer.effect.render(ctx, layer.params)
                if src.shape != (n, 3):
                    raise ValueError(f"effect returned shape {src.shape}, expected {(n, 3)}")
            except Exception:
                if not layer.warned:  # one log line per broken layer, not one per frame
                    log.exception("effect %s failed on %s; layer skipped", spec.effect, self.group.key)
                    layer.warned = True
                continue
            src = np.clip(src, 0.0, 1.0)
            try:
                cov = layer.effect.coverage(ctx, layer.params)
            except Exception:
                cov = None
            blended = BLEND_MODES[spec.blend](out, src)
            if spec.blend != "normal":
                blended = src + (blended - src) * acc
            weight = np.float32(min(spec.opacity, 1.0))
            if layer.mask is not None:
                weight = weight * layer.mask[:, None].astype(np.float32)
            if cov is not None:
                weight = weight * cov[:, None].astype(np.float32)
            out = out + (blended - out) * weight
            acc = acc + weight * (1.0 - acc)
        return out


def to_uint8(frame: np.ndarray, brightness: float = 1.0) -> np.ndarray:
    """float 0..1 -> uint8 0..255 with rounding, after scaling by ``brightness``."""
    # Clip first so brightness is a true ceiling: an over-range value at 50% is 50%, not 100%.
    scaled = np.clip(frame, 0.0, 1.0) * np.float32(brightness) * 255.0 + 0.5
    return scaled.astype(np.uint8)
