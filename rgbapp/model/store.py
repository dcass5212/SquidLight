"""Load and save the user's lighting setup as ``profiles.json`` (docs/03 "Data formats").

``profiles`` holds the saved profiles; ``rules`` the auto-switch rules. What the user is
editing right now (the "working copy") is stored under ``working`` and saved continuously,
so a restart picks up where they left off; it only becomes part of a profile on an explicit
Save (see ``model/profiles.py``). ``active_profile_id`` says which profile the working copy
came from. A file with no ``working`` key (older versions) loads the active profile.

Writes are atomic (temp file, then rename) and keep one ``.bak``. A missing or broken
file loads as "nothing saved" and the broken one is left in place as the ``.bak`` source.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Optional

from ..engine.compositor import BLEND_MODES, LayerSpec
from ..engine.host import GroupSettings
from ..paths import data_dir

log = logging.getLogger(__name__)

VERSION = 1
FILE_NAME = "profiles.json"
HARDWARE_FILE_NAME = "hardware.json"


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


def state_to_json(master: float, groups: dict[str, GroupSettings]) -> dict:
    """The part of a profile that is lighting state (shared by profiles and the working copy)."""
    return {
        "master_brightness": master,
        "devices": {
            key: {
                "brightness": s.brightness,
                "enabled": s.enabled,
                "sync_keyboard": s.sync_keyboard,
                "layers": [layer_to_json(spec) for spec in s.layers],
            }
            for key, s in groups.items()
        },
    }


def state_from_json(raw: dict) -> tuple[float, dict[str, GroupSettings]]:
    groups: dict[str, GroupSettings] = {}
    devices = raw.get("devices")
    for key, dev in (devices.items() if isinstance(devices, dict) else ()):
        if not isinstance(dev, dict):
            continue
        layers = []
        for item in dev.get("layers", []):
            try:
                layers.append(layer_from_json(item))
            except (KeyError, TypeError, ValueError, AttributeError):
                log.warning("profiles.json: skipped a bad layer on %s: %r", key, item)
        groups[key] = GroupSettings(
            layers=layers,
            brightness=float(min(max(dev.get("brightness", 1.0), 0.0), 1.0)),
            enabled=bool(dev.get("enabled", True)),
            sync_keyboard=bool(dev.get("sync_keyboard", False)),
        )
    try:
        master = float(min(max(raw.get("master_brightness", 1.0), 0.0), 1.0))
    except (TypeError, ValueError):
        master = 1.0
    return master, groups


class SettingsStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else data_dir() / FILE_NAME
        self._lock = threading.RLock()

    # -- reading

    def load(self) -> tuple[float, dict[str, GroupSettings]]:
        """The working copy: ``(master_brightness, {group key: settings})``; empty when nothing
        usable is saved."""
        doc = self._read()
        if not doc:
            return 1.0, {}
        working = doc.get("working")
        if isinstance(working, dict):
            return state_from_json(working)
        profile = self.active_profile(doc)
        return state_from_json(profile) if profile is not None else (1.0, {})

    # -- writing

    def save(self, master: float, groups: dict[str, GroupSettings]) -> None:
        """Save the working copy. The very first save also creates the "Default" profile."""

        def apply(doc: dict) -> None:
            self.ensure_profiles(doc, master, groups)
            doc["working"] = state_to_json(master, groups)

        self.update(apply)

    # -- the whole document, for ProfileManager. Read-modify-write is serialized by a lock
    #    because the host's save timer and the GUI thread both write.

    def read_doc(self) -> dict:
        with self._lock:
            return self._read() or {}

    def update(self, fn) -> None:
        with self._lock:
            doc = self._read() or {}
            fn(doc)
            doc["version"] = VERSION
            if not isinstance(doc.get("profiles"), list):
                doc["profiles"] = []
            if not isinstance(doc.get("rules"), list):
                doc["rules"] = []
            self._write(doc)

    @staticmethod
    def ensure_profiles(doc: dict, master: float = 1.0, groups: Optional[dict] = None) -> None:
        """Make sure at least one profile exists (a "Default" holding the given state)."""
        profiles = [p for p in doc.get("profiles", []) if isinstance(p, dict)]
        if not profiles:
            default = {"id": uuid.uuid4().hex, "name": "Default", **state_to_json(master, groups or {})}
            profiles = [default]
            doc["default_profile_id"] = default["id"]
        doc["profiles"] = profiles
        if not any(p.get("id") == doc.get("default_profile_id") for p in profiles):
            doc["default_profile_id"] = profiles[0].get("id")
        if not any(p.get("id") == doc.get("active_profile_id") for p in profiles):
            doc["active_profile_id"] = doc["default_profile_id"]
        doc.setdefault("rules", [])

    @staticmethod
    def active_profile(doc: Optional[dict]) -> Optional[dict]:
        if not doc:
            return None
        profiles = [p for p in doc.get("profiles", []) if isinstance(p, dict)]
        for wanted in (doc.get("active_profile_id"), doc.get("default_profile_id")):
            hit = next((p for p in profiles if p.get("id") == wanted), None)
            if hit is not None:
                return hit
        return profiles[0] if profiles else None

    # -- LED counts of ARGB zones: a property of the wiring, so not part of any profile

    @property
    def hardware_path(self) -> Path:
        return self.path.with_name(HARDWARE_FILE_NAME)

    def load_zone_sizes(self) -> dict[str, int]:
        doc = _HardwareFile(self.hardware_path)._read() or {}
        sizes = doc.get("zone_sizes")
        if not isinstance(sizes, dict):
            return {}
        return {str(k): int(v) for k, v in sizes.items() if isinstance(v, int) and v >= 0}

    def save_zone_sizes(self, sizes: dict[str, int]) -> None:
        _HardwareFile(self.hardware_path)._write({"version": VERSION, "zone_sizes": dict(sizes)})

    # -- internals

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


class _HardwareFile(SettingsStore):
    """hardware.json shares the atomic write and ``.bak`` handling of the profiles file."""


class MemoryStore(SettingsStore):
    """Same interface, nothing on disk: for the fake backend and tests."""

    def __init__(self) -> None:
        super().__init__(Path("memory-only.json"))
        self._doc: Optional[dict] = None
        self._zone_sizes: dict[str, int] = {}

    def _read(self) -> Optional[dict]:
        return json.loads(json.dumps(self._doc)) if self._doc is not None else None

    def _write(self, doc: dict) -> None:
        self._doc = json.loads(json.dumps(doc))

    def load_zone_sizes(self) -> dict[str, int]:
        return dict(self._zone_sizes)

    def save_zone_sizes(self, sizes: dict[str, int]) -> None:
        self._zone_sizes = dict(sizes)
