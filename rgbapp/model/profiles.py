"""Profiles: create, switch, save, revert, import/export, and the auto-switch rules list.

A profile is a named snapshot of the lighting state (master brightness plus every device
group's layers, brightness and on/off). Edits are always live and are autosaved as the
*working copy* by the host (see ``model/store.py``); the active profile itself changes only
on an explicit Save. ``dirty`` says whether the working copy differs from the saved profile.

No Qt here. Call it from one thread (the GUI thread). The rules watcher only reads
``rules_snapshot()``, which is swapped atomically.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..engine.host import EngineHost, GroupSettings
from ..engine.rules import Rule
from .store import MemoryStore, SettingsStore, state_from_json, state_to_json

log = logging.getLogger(__name__)

EXPORT_VERSION = 1


@dataclass(frozen=True)
class ProfileInfo:
    id: str
    name: str
    is_default: bool = False


class ProfileManager:
    def __init__(self, host: EngineHost, store: Optional[SettingsStore] = None) -> None:
        self.host = host
        self.store = store if store is not None else MemoryStore()
        master, groups = host.snapshot()
        self.store.update(lambda doc: self.store.ensure_profiles(doc, master, groups))
        self._infos: list[ProfileInfo] = []
        self._active_id = ""
        self._default_id = ""
        self._rules: tuple[Rule, ...] = ()
        self._baseline = ""
        self._seen_revision = -1
        self._dirty = False
        self._reload()
        active = self.store.active_profile(self.store.read_doc())
        self._baseline = self._fingerprint(*state_from_json(active or {}))

    # -- reads

    @property
    def profiles(self) -> list[ProfileInfo]:
        return list(self._infos)

    @property
    def active_id(self) -> str:
        return self._active_id

    @property
    def default_id(self) -> str:
        return self._default_id

    @property
    def active_name(self) -> str:
        return self.name_of(self._active_id)

    def name_of(self, profile_id: str) -> str:
        return next((p.name for p in self._infos if p.id == profile_id), "")

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)

    def rules_snapshot(self) -> tuple[tuple[Rule, ...], str, frozenset[str]]:
        """What the rules watcher needs: ``(rules, default profile id, all profile ids)``."""
        return self._rules, self._default_id, frozenset(p.id for p in self._infos)

    @property
    def dirty(self) -> bool:
        """True when the live settings differ from the saved active profile."""
        revision = self.host.revision
        if revision != self._seen_revision:
            self._seen_revision = revision
            self._dirty = self._fingerprint(*self.host.snapshot()) != self._baseline
        return self._dirty

    # -- switching and saving

    def activate(self, profile_id: str) -> None:
        """Make a saved profile live. Unsaved edits to the current one are discarded."""
        raw = self._raw(profile_id)
        master, groups = state_from_json(raw)
        self.host.replace_settings(master, groups)
        self.store.update(lambda doc: doc.update(active_profile_id=profile_id, working=state_to_json(master, groups)))
        self._baseline = self._fingerprint(master, groups)
        self._seen_revision = -1
        self._reload()
        log.info("profile: %s", self.name_of(profile_id))

    def save_active(self) -> None:
        master, groups = self.host.snapshot()
        state = state_to_json(master, groups)
        pid = self._active_id

        def apply(doc: dict) -> None:
            for p in doc["profiles"]:
                if p.get("id") == pid:
                    p.update(state)
            doc["working"] = state

        self.store.update(apply)
        self._baseline = self._fingerprint(master, groups)
        self._seen_revision = -1

    def revert(self) -> None:
        self.activate(self._active_id)

    # -- CRUD

    def create(self, name: str) -> str:
        """A new profile holding the current lighting; it becomes the active one (saved, clean)."""
        master, groups = self.host.snapshot()
        pid = self._add_profile(name, state_to_json(master, groups))
        self.activate(pid)
        return pid

    def duplicate(self, profile_id: str, name: Optional[str] = None) -> str:
        raw = self._raw(profile_id)
        base = name or f"{raw.get('name') or 'Profile'} copy"
        return self._add_profile(base, state_to_json(*state_from_json(raw)))

    def rename(self, profile_id: str, name: str) -> None:
        name = self._clean_name(name)
        self._raw(profile_id)
        if any(p.name.casefold() == name.casefold() for p in self._infos if p.id != profile_id):
            raise ValueError(f"There is already a profile named {name!r}.")

        def apply(doc: dict) -> None:
            for p in doc["profiles"]:
                if p.get("id") == profile_id:
                    p["name"] = name

        self.store.update(apply)
        self._reload()

    def delete(self, profile_id: str) -> None:
        self._raw(profile_id)
        if len(self._infos) <= 1:
            raise ValueError("The last profile can't be deleted.")
        was_active = profile_id == self._active_id

        def apply(doc: dict) -> None:
            doc["profiles"] = [p for p in doc["profiles"] if p.get("id") != profile_id]
            doc["rules"] = [r for r in doc["rules"] if not (isinstance(r, dict) and r.get("profile_id") == profile_id)]
            if doc.get("default_profile_id") == profile_id:
                doc["default_profile_id"] = doc["profiles"][0]["id"]
            if doc.get("active_profile_id") == profile_id:
                doc["active_profile_id"] = doc["default_profile_id"]

        self.store.update(apply)
        self._reload()
        if was_active:
            self.activate(self._default_id)

    def set_default(self, profile_id: str) -> None:
        self._raw(profile_id)
        self.store.update(lambda doc: doc.update(default_profile_id=profile_id))
        self._reload()

    # -- import / export

    def export_profile(self, profile_id: str, path: Path | str) -> None:
        """Write the *saved* version of a profile as a standalone JSON file."""
        raw = self._raw(profile_id)
        state = state_to_json(*state_from_json(raw))
        doc = {"version": EXPORT_VERSION, "profile": {"name": raw.get("name") or "Profile", **state}}
        Path(path).write_text(json.dumps(doc, indent=2), encoding="utf-8")

    def import_profile(self, path: Path | str) -> str:
        """Add a profile from a file made by ``export_profile``. Returns its (new) id."""
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Could not read that file: {exc}") from exc
        raw = doc.get("profile", doc) if isinstance(doc, dict) else None
        if not isinstance(raw, dict) or not isinstance(raw.get("devices"), dict):
            raise ValueError("That file is not a SquidLight profile.")
        return self._add_profile(str(raw.get("name") or "Imported"), state_to_json(*state_from_json(raw)))

    # -- rules

    def set_rules(self, rules: list[Rule]) -> None:
        valid = {p.id for p in self._infos}
        kept = [r for r in rules if r.match.strip() and r.profile_id in valid]
        self.store.update(lambda doc: doc.update(rules=[r.to_json() for r in kept]))
        self._reload()

    # -- internals

    def _fingerprint(self, master: float, groups: dict[str, GroupSettings]) -> str:
        """Comparable form of a state: ignores layer ids and groups still at their untouched
        default, so a profile saved before a device appeared isn't "changed" by that device."""
        default = self.host.default_effect
        untouched = {"effect": default, "params": {}, "mask": "all", "blend": "normal", "opacity": 1.0, "visible": True}
        out: dict = {"master": round(master, 4), "groups": {}}
        for key, s in sorted(groups.items()):
            layers = [
                {
                    "effect": l.effect,
                    "params": l.params,
                    "mask": l.mask if isinstance(l.mask, str) else list(l.mask),
                    "blend": l.blend,
                    "opacity": round(l.opacity, 4),
                    "visible": l.visible,
                }
                for l in s.layers
            ]
            pristine = (
                s.brightness == 1.0
                and s.enabled
                and not s.sync_keyboard
                and len(layers) == 1
                and layers[0] == untouched
            )
            if not pristine:
                out["groups"][key] = {
                    "brightness": round(s.brightness, 4),
                    "enabled": s.enabled,
                    "sync": s.sync_keyboard,
                    "layers": layers,
                }
        return json.dumps(out, sort_keys=True, default=str)

    def _raw(self, profile_id: str) -> dict:
        for p in self.store.read_doc().get("profiles", []):
            if isinstance(p, dict) and p.get("id") == profile_id:
                return p
        raise KeyError(f"no profile {profile_id!r}")

    @staticmethod
    def _clean_name(name: str) -> str:
        name = " ".join(str(name).split())
        if not name:
            raise ValueError("A profile needs a name.")
        return name[:60]

    def _unique_name(self, name: str) -> str:
        name = self._clean_name(name)
        taken = {p.name.casefold() for p in self._infos}
        if name.casefold() not in taken:
            return name
        n = 2
        while f"{name} ({n})".casefold() in taken:
            n += 1
        return f"{name} ({n})"

    def _add_profile(self, name: str, state: dict) -> str:
        pid = uuid.uuid4().hex
        profile = {"id": pid, "name": self._unique_name(name), **state}
        self.store.update(lambda doc: doc["profiles"].append(profile))
        self._reload()
        return pid

    def _reload(self) -> None:
        doc = self.store.read_doc()
        self.store.ensure_profiles(doc)
        default = doc.get("default_profile_id")
        self._infos = [
            ProfileInfo(str(p["id"]), str(p.get("name") or "Profile"), p["id"] == default)
            for p in doc["profiles"]
            if isinstance(p, dict) and "id" in p
        ]
        self._default_id = str(default)
        self._active_id = str(doc.get("active_profile_id") or default)
        self._rules = tuple(r for r in (Rule.from_json(x) for x in doc.get("rules", [])) if r is not None)
