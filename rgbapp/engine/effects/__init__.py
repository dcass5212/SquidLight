"""Effect registry. Add an effect: write the class, then list it in ``_BUILTIN``."""

from __future__ import annotations

from .base import Ctx, Effect, Param
from .breathing import Breathing
from .color_cycle import ColorCycle
from .gradient_wave import GradientWave
from .rainbow_wave import RainbowWave
from .starlight import Starlight
from .static import Static
from .static_keys import StaticKeys

# Library order (docs/02): simple to animated.
_BUILTIN: list[type[Effect]] = [Static, Breathing, ColorCycle, RainbowWave, GradientWave, Starlight, StaticKeys]

EFFECTS: dict[str, type[Effect]] = {cls.id: cls for cls in _BUILTIN}


def listed_effects() -> list[type[Effect]]:
    """Effects the user can pick from the library (tool-made ones like Paint are left out)."""
    return [cls for cls in EFFECTS.values() if cls.listed]


def get_effect_class(effect_id: str) -> type[Effect]:
    try:
        return EFFECTS[effect_id]
    except KeyError:
        raise KeyError(f"unknown effect {effect_id!r}; available: {', '.join(sorted(EFFECTS))}") from None


def create_effect(effect_id: str) -> Effect:
    return get_effect_class(effect_id)()


__all__ = ["Ctx", "Effect", "Param", "EFFECTS", "listed_effects", "get_effect_class", "create_effect"]
