"""Load and save the user's lighting setup as ``profiles.json`` (docs/03 "Data formats").

Until profiles arrive (M6) there is exactly one profile, "Default", holding every device
group's layer stack and brightness. Other profiles and rules found in the file are kept
untouched, so M6 can take the file over as it is.

Writes are atomic (temp file, then rename) and keep one ``.bak``. A missing or broken
file loads as "nothing saved" and the broken one is left in place as the ``.bak`` source.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from ..engine.compositor import BLEND_MODES, LayerSpec
from ..engine.host import GroupSettings
from ..paths import data_dir

log = logging.getLogger(__name__)

VERSION = 1
FILE_NAME = "profiles.json"


def layer_to_json(spec: LayerSpec) -> dict:
    return {
        "id": spec.id,
        "effect": spec.effect,
        "params": spec.params,
        "mask": spec.mask if isinstance(spec.mask, str) else list(spec.mask),
        "blend": spec.blend,
        "opacity": spec.opacity,
        "visible": spec.visible,
    }


def layer_from_json(raw: dict) -> LayerSpec:
    blend = raw.get("blend", "normal")
    spec = LayerSpec(
        effect=str(raw["effect"]),
        params=dict(raw.get("params") or {}),
        mask=raw.get("mask", "all"),
        blend=blend if blend in BLEND_MODES else "normal",
        opacity=float(min(max(raw.get("opacity", 1.0), 0.0), 1.0)),
        visible=bool(raw.get("visible", True)),
    )
    if raw.get("id"):
        spec.id = str(raw["id"])
    return spec


class SettingsStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else data_dir() / FILE_NAME

    # -- reading

    def load(self) -> tuple[float, dict[str, GroupSettings]]:
        """``(master_brightness, {group key: settings})``; empty when nothing usable is saved."""
        doc = self._read()
        profile = self._default_profile(doc)
        if profile is None:
            return 1.0, {}
        groups: dict[str, GroupSettings] = {}
        for key, raw in (profile.get("devices") or {}).items():
            layers = []
            for item in raw.get("layers", []):
                try:
                    layers.append(layer_from_json(item))
                except (KeyError, TypeError, ValueError):
                    log.warning("profiles.json: skipped a bad layer on %s: %r", key, item)
            groups[key] = GroupSettings(
                layers=layers,
                brightness=float(min(max(raw.get("brightness", 1.0), 0.0), 1.0)),
                enabled=bool(raw.get("enabled", True)),
            )
        master = float(min(max(profile.get("master_brightness", 1.0), 0.0), 1.0))
        return master, groups

    # -- writing

    def save(self, master: float, groups: dict[str, GroupSettings]) -> None:
        doc = self._read() or {}
        profiles = [p for p in doc.get("profiles", []) if isinstance(p, dict)]
        current = self._default_profile(doc)
        if current is None:
            current = {"id": uuid.uuid4().hex, "name": "Default"}
            profiles.insert(0, current)
            doc["default_profile_id"] = current["id"]
        current["master_brightness"] = master
        current["devices"] = {
            key: {
                "brightness": s.brightness,
                "enabled": s.enabled,
                "layers": [layer_to_json(spec) for spec in s.layers],
            }
            for key, s in groups.items()
        }
        doc["version"] = VERSION
        doc["profiles"] = profiles
        doc.setdefault("rules", [])
        self._write(doc)

    # -- internals

    @staticmethod
    def _default_profile(doc: Optional[dict]) -> Optional[dict]:
        if not doc:
            return None
        profiles = [p for p in doc.get("profiles", []) if isinstance(p, dict)]
        wanted = doc.get("default_profile_id")
        return next((p for p in profiles if p.get("id") == wanted), profiles[0] if profiles else None)

    def _read(self) -> Optional[dict]:
        for path in (self.path, self.path.with_name(self.path.name + ".bak")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                log.warning("could not read %s; trying the backup", path.name)
                continue
            if isinstance(doc, dict):
                return doc
        return None

    @staticmethod
    def _parses(path: Path) -> bool:
        try:
            return isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
        except (OSError, ValueError):
            return False

    def _write(self, doc: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        if self._parses(self.path):  # never rotate a broken file over a good backup
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".bak"))
            except OSError:
                log.warning("could not refresh %s.bak", self.path.name)
        os.replace(tmp, self.path)
