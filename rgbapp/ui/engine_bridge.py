"""The GUI's single door into the engine.

Widgets never touch ``EngineHost`` directly. They call the setters here (cheap: they
only store values the render thread picks up) and listen to the signals. Engine state
reaches the GUI by polling on QTimers, so no engine thread ever touches a Qt object.
"""

from __future__ import annotations

import dataclasses
import logging

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from ..engine.host import EngineHost, HostStatus
from ..engine.rules import Rule, RulesWatcher
from ..model.app_settings import AppSettings, AppSettingsStore, MemorySettingsStore
from ..model.profiles import ProfileManager

log = logging.getLogger(__name__)

PREVIEW_INTERVAL_MS = 33  # about 30 Hz, matching the render loop
STATUS_INTERVAL_MS = 250
# After resume from sleep, USB devices come back over a few seconds: reapply more than once.
REAPPLY_DELAYS_MS = (0, 3000, 8000)
_ORDER = {"keyboard": 0, "memory": 1, "mainboard": 2, "case": 3}


def _ordered(groups):
    """Sidebar/dashboard order from docs/02, then anything else OpenRGB found."""
    return sorted(groups, key=lambda g: (_ORDER.get(g.key.rstrip("0123456789"), 9), g.key))


class EngineBridge(QObject):
    frames = Signal(dict)  # group key -> uint8 (n, 3), only groups whose frame changed
    status_changed = Signal(object)  # HostStatus
    groups_changed = Signal(list)  # list[DeviceGroup]
    health_changed = Signal(dict)  # group key -> "ok" | "error" | "disconnected" | "paused"
    settings_changed = Signal(str)  # group key whose effect/brightness/on-off changed
    master_changed = Signal(float)
    paused_changed = Signal(bool)
    profiles_changed = Signal()  # profile list, active/default profile or rules changed
    notices_changed = Signal(object)  # {group key: {layer id: text}}: layers that can't work right now
    dirty_changed = Signal(bool)  # the live lighting differs from the saved profile
    app_settings_changed = Signal(object)  # AppSettings
    accent_changed = Signal(str)
    _rule_profile = Signal(str)  # from the rules thread; queued onto the GUI thread

    def __init__(
        self, host: EngineHost, parent: QObject | None = None, app_store: AppSettingsStore | None = None
    ) -> None:
        super().__init__(parent)
        self.host = host
        self.app_store = app_store if app_store is not None else MemorySettingsStore()
        self.app_settings: AppSettings = self.app_store.load()
        self._status: HostStatus | None = None
        self._generation = -1
        self._health: dict[str, str] = {}
        self._notices: dict = {}
        self._last_frames: dict[str, np.ndarray] = {}

        self.profiles = ProfileManager(host, host.store)
        self._dirty = False
        self._rule_profile.connect(self._on_rule_profile)
        self._watcher = RulesWatcher(self.profiles.rules_snapshot, self._rule_profile.emit)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(STATUS_INTERVAL_MS)
        self._status_timer.timeout.connect(self.poll_status)
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(PREVIEW_INTERVAL_MS)
        self._preview_timer.timeout.connect(self.poll_frames)

    def start(self) -> None:
        self._status_timer.start()
        self.poll_status()
        self._watcher.start()

    def stop(self) -> None:
        self._watcher.stop()
        self._status_timer.stop()
        self._preview_timer.stop()

    def set_preview_active(self, active: bool) -> None:
        """Previews only run while the window is visible (keeps idle CPU low)."""
        if active and not self._preview_timer.isActive():
            self._last_frames.clear()  # repaint everything once on show
            self._preview_timer.start()
        elif not active:
            self._preview_timer.stop()

    # -- polling

    def poll_status(self) -> None:
        gen = self.host.generation
        if gen != self._generation:
            self._generation = gen
            self._last_frames.clear()
            self.groups_changed.emit(self.groups)
        status = self.host.status()
        if status != self._status:
            self._status = status
            self.status_changed.emit(status)
        dirty = self.profiles.dirty
        if dirty != self._dirty:
            self._dirty = dirty
            self.dirty_changed.emit(dirty)
        health = {g.key: self.host.group_health(g.key) for g in self.host.groups}
        if health != self._health:
            self._health = health
            self.health_changed.emit(health)
        notices = self.host.notices()
        if notices != self._notices:
            self._notices = notices
            self.notices_changed.emit(notices)

    def notice(self, key: str, layer_id: str) -> str:
        return self._notices.get(key, {}).get(layer_id, "")

    def poll_frames(self) -> None:
        changed = {}
        for key, frame in self.host.latest_frames().items():
            last = self._last_frames.get(key)
            if last is frame or (last is not None and np.array_equal(last, frame)):
                continue
            self._last_frames[key] = frame
            changed[key] = frame
        if changed:
            self.frames.emit(changed)

    # -- reads

    @property
    def status(self) -> HostStatus:
        return self._status or self.host.status()

    @property
    def groups(self):
        return _ordered(self.host.groups)

    def health(self, key: str) -> str:
        return self._health.get(key, "disconnected")

    def settings(self, key: str):
        return self.host.settings(key)

    def latest_frame(self, key: str):
        return self.host.latest_frames().get(key)

    # -- writes

    def set_effect(self, key: str, effect_id: str) -> None:
        self.host.set_effect(key, effect_id)
        self.settings_changed.emit(key)

    def set_param(self, key: str, param_id: str, value) -> None:
        self.host.set_param(key, param_id, value)
        self.settings_changed.emit(key)

    def set_brightness(self, key: str, value: float) -> None:
        self.host.set_brightness(key, value)
        self.settings_changed.emit(key)

    def set_enabled(self, key: str, enabled: bool) -> None:
        self.host.set_enabled(key, enabled)
        self.settings_changed.emit(key)

    # -- layer stack (bottom to top). Every change emits settings_changed(key).

    def layers(self, key: str):
        return self.host.layers(key)

    def add_layer(self, key: str, effect_id: str, params: dict | None = None) -> str:
        layer_id = self.host.add_layer(key, effect_id, params)
        self.settings_changed.emit(key)
        return layer_id

    def remove_layer(self, key: str, layer_id: str) -> None:
        self.host.remove_layer(key, layer_id)
        self.settings_changed.emit(key)

    def move_layer(self, key: str, layer_id: str, index: int) -> None:
        self.host.move_layer(key, layer_id, index)
        self.settings_changed.emit(key)

    def set_layer_effect(self, key: str, layer_id: str, effect_id: str) -> None:
        self.host.set_layer_effect(key, layer_id, effect_id)
        self.settings_changed.emit(key)

    def set_layer_param(self, key: str, layer_id: str, param_id: str, value) -> None:
        self.host.set_layer_param(key, layer_id, param_id, value)
        self.settings_changed.emit(key)

    def set_layer_props(self, key: str, layer_id: str, **props) -> None:
        self.host.set_layer_props(key, layer_id, **props)
        self.settings_changed.emit(key)

    # -- zones (M5)

    def set_sync_keyboard(self, key: str, sync: bool) -> None:
        self.host.set_sync_keyboard(key, sync)
        self.settings_changed.emit(key)

    def set_zone_size(self, device_id: str, zone_name: str, size: int) -> None:
        """Set an ARGB zone's LED count. The engine applies it on a worker thread; when it is
        done the generation changes and ``groups_changed`` rebuilds the pages."""
        self.host.set_zone_size(device_id, zone_name, size)
        self.poll_status()

    # -- profiles (M6). Switching replaces the whole lighting state, so every page rebuilds.

    @property
    def dirty(self) -> bool:
        return self.profiles.dirty

    def _after_switch(self) -> None:
        self._last_frames.clear()
        self.groups_changed.emit(self.groups)
        self.master_changed.emit(self.host.master_brightness)
        self.profiles_changed.emit()
        self.poll_status()

    def activate_profile(self, profile_id: str) -> None:
        """Make a profile live. Unsaved edits to the current one are discarded: ask first."""
        self.profiles.activate(profile_id)
        self._after_switch()

    def _on_rule_profile(self, profile_id: str) -> None:
        if profile_id != self.profiles.active_id and any(p.id == profile_id for p in self.profiles.profiles):
            self.activate_profile(profile_id)

    def save_profile(self) -> None:
        self.profiles.save_active()
        self.profiles_changed.emit()
        self.poll_status()

    def revert_profile(self) -> None:
        self.profiles.revert()
        self._after_switch()

    def create_profile(self, name: str) -> str:
        pid = self.profiles.create(name)
        self._after_switch()
        return pid

    def duplicate_profile(self, profile_id: str) -> str:
        pid = self.profiles.duplicate(profile_id)
        self.profiles_changed.emit()
        return pid

    def rename_profile(self, profile_id: str, name: str) -> None:
        self.profiles.rename(profile_id, name)
        self.profiles_changed.emit()

    def delete_profile(self, profile_id: str) -> None:
        was_active = profile_id == self.profiles.active_id
        self.profiles.delete(profile_id)
        self._after_switch() if was_active else self.profiles_changed.emit()

    def set_default_profile(self, profile_id: str) -> None:
        self.profiles.set_default(profile_id)
        self.profiles_changed.emit()

    def import_profile(self, path) -> str:
        pid = self.profiles.import_profile(path)
        self.profiles_changed.emit()
        return pid

    def export_profile(self, profile_id: str, path) -> None:
        self.profiles.export_profile(profile_id, path)

    def set_rules(self, rules: list[Rule]) -> None:
        self.profiles.set_rules(rules)
        self.profiles_changed.emit()

    def set_master_brightness(self, value: float) -> None:
        if abs(value - self.host.master_brightness) < 1e-6:
            return
        self.host.set_master_brightness(value)
        self.master_changed.emit(self.host.master_brightness)

    # -- app settings (M8). Saved right away; everything but the OpenRGB path and port
    # applies live (those need "Restart OpenRGB").

    def update_app_settings(self, **changes) -> AppSettings:
        old = self.app_settings
        new = dataclasses.replace(old, **changes).normalized()
        if new == old:
            return new
        self.app_settings = new
        try:
            self.app_store.save(new)
        except OSError:
            log.exception("could not save settings")
        if new.write_cap_hz != old.write_cap_hz:
            self.host.set_write_cap(new.write_cap_hz)
        if (new.audio_device, new.lhm_url) != (old.audio_device, old.lhm_url):
            self.host.set_input_options(new.audio_device, new.lhm_url)
        if new.accent != old.accent:
            self.accent_changed.emit(new.accent)
        self.app_settings_changed.emit(new)
        return new

    def server_settings_pending(self) -> bool:
        """The saved OpenRGB path/port differ from what the running engine uses."""
        manager = getattr(self.host.engine, "manager", None)
        port = getattr(self.host.engine.backend, "port", self.app_settings.openrgb_port)
        path = manager.configured_exe if manager is not None else self.app_settings.openrgb_path
        return port != self.app_settings.openrgb_port or path != self.app_settings.openrgb_path

    def restart_openrgb(self) -> None:
        """Stop and start OpenRGB and the engine (off the GUI thread), using the saved
        OpenRGB path and port. Pages rebuild when the engine is back."""
        s = self.app_settings
        self.host.restart(reconfigure=lambda engine: engine.configure_server(s.openrgb_path or None, s.openrgb_port))
        self.poll_status()

    def reapply(self) -> None:
        """Resume from sleep: put devices back in Direct mode and resend, a few times while
        USB devices come back."""
        for delay in REAPPLY_DELAYS_MS:
            QTimer.singleShot(delay, self.host.reapply)

    def set_paused(self, paused: bool) -> None:
        if paused == self.host.paused:
            return
        self.host.set_paused(paused)
        self.paused_changed.emit(paused)
        self.poll_status()
