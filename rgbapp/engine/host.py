"""EngineHost: runs the engine for an interactive app without blocking the caller.

The GUI talks only to this object. It:

- starts the engine on a worker thread and keeps retrying with backoff until OpenRGB
  answers (so the window can open immediately and show "starting" or "failed"),
- holds the user's settings (effect, brightness, on/off per group, master, pause) and
  applies them whenever the engine comes up, so controls work before it is ready,
- stops everything on a worker thread on quit (stopping restores devices, which is I/O).

No Qt in here: it is tested headless against ``FakeBackend``.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .backend import Backoff, ConnectionState
from .compositor import BLEND_MODES, LayerSpec
from .core import Engine, zone_key
from .devices import DeviceGroup
from .effects import get_effect_class

log = logging.getLogger(__name__)

DEFAULT_EFFECT = "rainbow_wave"


@dataclass
class GroupSettings:
    """What the user set for one device group. ``layers`` run bottom to top."""

    layers: list[LayerSpec] = field(default_factory=list)
    brightness: float = 1.0
    enabled: bool = True
    # Mirror the keyboard's dominant effect onto this group (ignored for the keyboard itself).
    sync_keyboard: bool = False
    # layer id -> effect id -> params, so switching a layer's effect away and back
    # keeps the user's colors
    remembered: dict[str, dict[str, dict]] = field(default_factory=dict)

    @property
    def effect(self) -> str:
        """The base (bottom) layer's effect: what the dashboard shows and edits."""
        return self.layers[0].effect if self.layers else ""

    def current_params(self) -> dict:
        return self.layers[0].params if self.layers else {}

    def layer(self, layer_id: str) -> LayerSpec:
        for spec in self.layers:
            if spec.id == layer_id:
                return spec
        raise KeyError(f"no layer {layer_id!r}")


@dataclass(frozen=True)
class HostStatus:
    state: str  # "starting" | "connected" | "reconnecting" | "failed" | "paused" | "stopping"
    text: str  # one line for the status chip
    external: bool = False


class EngineHost:
    def __init__(
        self,
        engine: Engine,
        default_effect: str = DEFAULT_EFFECT,
        clock: Callable[[], float] = time.monotonic,
        retry_first: float = 2.0,
        store=None,
        save_delay: float = 0.5,
    ) -> None:
        get_effect_class(default_effect)  # fail fast on a typo
        self.engine = engine
        self.default_effect = default_effect
        self._clock = clock
        self._retry_first = retry_first
        self._lock = threading.RLock()
        self._settings: dict[str, GroupSettings] = {}
        self._master = 1.0
        self._want_paused = False
        self._phase = "idle"  # idle | starting | failed | running | stopping | stopped
        self._fail_reason = ""
        self._retry_at = 0.0
        self._revision = 0  # bumps on every change to what the user set (drives "unsaved changes")
        self._generation = 0  # bumps each time the engine comes up (groups may change)
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Optional persistence: anything with load() -> (master, {key: GroupSettings}) and
        # save(master, {key: GroupSettings}). Saves are debounced and run off the caller's thread.
        self._store = store
        self._save_delay = save_delay
        self._save_timer: Optional[threading.Timer] = None
        self._resize_thread: Optional[threading.Thread] = None
        self._resize_pending: dict[tuple[str, str], int] = {}
        self._restart_lock = threading.Lock()
        self._restarting = False
        if store is not None:
            try:
                self._master, self._settings = store.load()
            except Exception:
                log.exception("could not load saved settings; starting fresh")
            try:
                engine.zone_sizes.update(store.load_zone_sizes())
            except Exception:
                log.exception("could not load saved LED counts")

    # -- lifecycle

    def start(self) -> None:
        if self._thread is not None:
            return
        self._phase = "starting"
        self._thread = threading.Thread(target=self._run_start, name="engine-start", daemon=True)
        self._thread.start()

    def wait_running(self, timeout: float) -> bool:
        """Test helper: block until the engine is up (or the timeout passes)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._phase == "running":
                return True
            time.sleep(0.01)
        return self._phase == "running"

    def stop(self, timeout: float = 20.0) -> None:
        """Blocking. Call it off the GUI thread (see ``stop_async``)."""
        with self._lock:  # after this, UI calls only update stored settings
            self._phase = "stopping"
        self._stop_evt.set()
        self._flush_save()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        resizer = self._resize_thread
        if resizer is not None and resizer is not threading.current_thread():
            resizer.join(timeout)
        try:
            self.engine.stop()
        except Exception:
            log.exception("engine.stop failed")
        self._phase = "stopped"

    def stop_async(self, done: Optional[Callable[[], None]] = None) -> threading.Thread:
        def work() -> None:
            try:
                self.stop()
            finally:
                if done is not None:
                    done()

        t = threading.Thread(target=work, name="engine-stop", daemon=True)
        t.start()
        return t

    def restart(
        self, reconfigure: Optional[Callable[[Engine], None]] = None, done: Optional[Callable[[], None]] = None
    ) -> threading.Thread:
        """Restart OpenRGB and the engine on a worker thread ("Restart OpenRGB" in Settings).

        Stopping restores the devices and stops OpenRGB if we started it; ``reconfigure``
        then runs while everything is down (e.g. ``engine.configure_server``), and the
        engine starts again with the usual retries. A quit during a restart wins."""

        def work() -> None:
            try:
                self._restart(reconfigure)
            except Exception:
                log.exception("restart failed")
            finally:
                if done is not None:
                    done()

        t = threading.Thread(target=work, name="engine-restart", daemon=True)
        t.start()
        return t

    @property
    def restarting(self) -> bool:
        return self._restarting

    def _restart(self, reconfigure: Optional[Callable[[Engine], None]]) -> None:
        if not self._restart_lock.acquire(blocking=False):
            log.info("restart already in progress")
            return
        try:
            with self._lock:
                if self._phase in ("stopping", "stopped"):
                    return
                log.info("restarting OpenRGB and the engine")
                self._restarting = True
                self._phase = "starting"  # UI changes are now only stored
                self._generation += 1  # the pages drop their device views
            self._stop_evt.set()
            for t in (self._thread, self._resize_thread):
                if t is not None and t is not threading.current_thread():
                    t.join(30.0)
            self._flush_save()
            try:
                self.engine.stop()
            except Exception:
                log.exception("engine.stop failed during restart")
            if reconfigure is not None:
                try:
                    reconfigure(self.engine)
                except Exception:
                    log.exception("reconfigure failed during restart")
            with self._lock:  # start under the lock, so a concurrent stop() sees the new thread
                if self._phase in ("stopping", "stopped"):
                    return
                self._stop_evt.clear()
                self._thread = None
                self.start()
        finally:
            self._restarting = False
            self._restart_lock.release()

    def _run_start(self) -> None:
        backoff = Backoff(first=self._retry_first, cap=15.0)
        while not self._stop_evt.is_set():
            self._phase = "starting"
            try:
                self.engine.start(configure=self._configure)
            except _Cancelled:
                return  # stop() tears the engine down
            except Exception as exc:
                delay = backoff.next_delay()
                self._fail_reason = str(exc) or exc.__class__.__name__
                self._retry_at = self._clock() + delay
                self._phase = "failed"
                log.warning("engine did not start (%s); retrying in %.0fs", self._fail_reason, delay)
                if self._stop_evt.wait(delay):
                    return
                continue
            log.info("engine running with groups: %s", ", ".join(g.key for g in self.engine.groups) or "none")
            return

    def _configure(self, engine: Engine) -> None:
        """Runs inside ``engine.start`` before the first frame."""
        with self._lock:
            if self._stop_evt.is_set():
                raise _Cancelled()
            self._generation += 1
            self._apply_all_locked()
            self._phase = "running"

    # -- what the UI reads

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def groups(self) -> list[DeviceGroup]:
        return self.engine.groups if self._phase == "running" else []

    def status(self) -> HostStatus:
        phase = self._phase
        if phase in ("stopping", "stopped"):
            return HostStatus("stopping", "Stopping...")
        if phase == "failed":
            wait = max(0, int(round(self._retry_at - self._clock())))
            reason = _sentence(self._fail_reason)
            return HostStatus("failed", f"{reason} Retrying in {wait}s." if wait else f"{reason} Retrying...")
        if phase != "running":
            return HostStatus("starting", "Starting OpenRGB...")
        external = self.engine.external
        if self.engine.paused or self._want_paused:
            return HostStatus("paused", "Lighting paused", external)
        if self.engine.connection_state is ConnectionState.CONNECTED:
            return HostStatus("connected", "Connected to your OpenRGB" if external else "Connected to OpenRGB", external)
        return HostStatus("reconnecting", "OpenRGB disconnected, retrying...", external)

    def latest_frames(self) -> dict[str, np.ndarray]:
        return self.engine.latest_frames() if self._phase == "running" else {}

    def group_health(self, key: str) -> str:
        return self.engine.group_health(key) if self._phase == "running" else "disconnected"

    def notices(self) -> dict:
        """group key -> {layer id: why that layer isn't working} (no sensor source, ...)."""
        return self.engine.notices() if self._phase == "running" else {}

    def settings(self, key: str) -> GroupSettings:
        with self._lock:
            return copy.deepcopy(self._settings_locked(key))

    @property
    def store(self):
        return self._store

    @property
    def revision(self) -> int:
        return self._revision

    def snapshot(self) -> tuple[float, dict[str, GroupSettings]]:
        """A copy of everything a profile holds: master brightness and every group's settings."""
        with self._lock:
            return self._master, copy.deepcopy(self._settings)

    def replace_settings(self, master: float, groups: dict[str, GroupSettings]) -> None:
        """Swap in a whole profile (profile switch). Applied to the hardware right away when
        the engine is up, otherwise when it comes up. Groups the profile doesn't mention get
        the default effect."""
        with self._lock:
            self._master = float(min(max(master, 0.0), 1.0))
            self._settings = copy.deepcopy(groups)
            self._touch_locked()
            if self._running():
                self.engine.begin_crossfade()  # blend from what is lit now
                self._apply_all_locked()

    @property
    def master_brightness(self) -> float:
        return self._master

    @property
    def paused(self) -> bool:
        return self._want_paused

    # -- what the UI changes (cheap: no device I/O on the caller's thread)

    def set_effect(self, key: str, effect_id: str) -> None:
        """Change the base layer's effect (the dashboard's effect picker)."""
        get_effect_class(effect_id)
        with self._lock:
            s = self._settings_locked(key)
            if not s.layers:
                s.layers.append(LayerSpec(effect_id))
            else:
                self._switch_effect_locked(s, s.layers[0], effect_id)
            self._apply_layers_locked(key)

    def set_param(self, key: str, param_id: str, value) -> None:
        """Set a parameter on the base layer."""
        with self._lock:
            s = self._settings_locked(key)
            if s.layers:
                s.layers[0].params[param_id] = value
                self._apply_layers_locked(key)

    # -- the layer stack (bottom to top; index 0 is the base layer)

    def layers(self, key: str) -> list[LayerSpec]:
        with self._lock:
            return copy.deepcopy(self._settings_locked(key).layers)

    def add_layer(self, key: str, effect_id: str, params: Optional[dict] = None, index: Optional[int] = None) -> str:
        """Add a layer (on top unless ``index`` is given). Returns its id."""
        get_effect_class(effect_id)
        with self._lock:
            s = self._settings_locked(key)
            spec = LayerSpec(effect_id, dict(params or {}))
            s.layers.insert(len(s.layers) if index is None else max(0, min(index, len(s.layers))), spec)
            self._apply_layers_locked(key)
            return spec.id

    def remove_layer(self, key: str, layer_id: str) -> None:
        with self._lock:
            s = self._settings_locked(key)
            s.layers.remove(s.layer(layer_id))
            s.remembered.pop(layer_id, None)
            self._apply_layers_locked(key)

    def move_layer(self, key: str, layer_id: str, index: int) -> None:
        """Move a layer to ``index`` in bottom-to-top order."""
        with self._lock:
            s = self._settings_locked(key)
            spec = s.layer(layer_id)
            s.layers.remove(spec)
            s.layers.insert(max(0, min(index, len(s.layers))), spec)
            self._apply_layers_locked(key)

    def set_layer_effect(self, key: str, layer_id: str, effect_id: str) -> None:
        get_effect_class(effect_id)
        with self._lock:
            s = self._settings_locked(key)
            self._switch_effect_locked(s, s.layer(layer_id), effect_id)
            self._apply_layers_locked(key)

    def set_layer_param(self, key: str, layer_id: str, param_id: str, value) -> None:
        with self._lock:
            self._settings_locked(key).layer(layer_id).params[param_id] = value
            self._apply_layers_locked(key)

    def set_layer_props(
        self,
        key: str,
        layer_id: str,
        *,
        visible: Optional[bool] = None,
        opacity: Optional[float] = None,
        blend: Optional[str] = None,
        mask=None,
    ) -> None:
        """Change a layer's compositing settings; ``None`` leaves a setting as it is."""
        if blend is not None and blend not in BLEND_MODES:
            raise ValueError(f"unknown blend mode {blend!r}")
        with self._lock:
            spec = self._settings_locked(key).layer(layer_id)
            if visible is not None:
                spec.visible = bool(visible)
            if opacity is not None:
                spec.opacity = float(min(max(opacity, 0.0), 1.0))
            if blend is not None:
                spec.blend = blend
            if mask is not None:
                spec.mask = mask if isinstance(mask, str) else list(mask)
            self._apply_layers_locked(key)

    def set_sync_keyboard(self, key: str, sync: bool) -> None:
        """Make ``key`` follow the keyboard's dominant effect (or go back to its own layers)."""
        if key == "keyboard":
            return
        with self._lock:
            self._settings_locked(key).sync_keyboard = bool(sync)
            self._apply_layers_locked(key)

    def zone_size(self, device_id: str, zone_name: str) -> Optional[int]:
        """The saved LED count for a zone, or None if the user never set one."""
        return self.engine.zone_sizes.get(zone_key(device_id, zone_name))

    def set_zone_size(self, device_id: str, zone_name: str, size: int) -> None:
        """Set an ARGB zone's LED count. Saved right away; applied on a worker thread
        because it means device I/O (the engine rebuilds its groups and generation bumps)."""
        size = max(0, int(size))
        with self._lock:
            self.engine.zone_sizes[zone_key(device_id, zone_name)] = size
            self._touch_locked()
            if not self._running():
                return  # applied when the engine comes up
            self._resize_pending[(device_id, zone_name)] = size
            if self._resize_thread is None or not self._resize_thread.is_alive():
                self._resize_thread = threading.Thread(target=self._run_resizes, name="engine-resize", daemon=True)
                self._resize_thread.start()

    def _run_resizes(self) -> None:
        while True:
            with self._lock:
                if not self._resize_pending or not self._running():
                    self._resize_pending.clear()
                    return
                (device_id, zone_name), size = next(iter(self._resize_pending.items()))
                del self._resize_pending[(device_id, zone_name)]
            try:
                self.engine.resize_zone(device_id, zone_name, size, configure=self._configure)
            except _Cancelled:
                return
            except Exception:
                log.exception("could not resize %s", zone_name)

    def set_brightness(self, key: str, value: float) -> None:
        with self._lock:
            self._settings_locked(key).brightness = float(min(max(value, 0.0), 1.0))
            self._touch_locked()
            if self._running():
                self.engine.set_brightness(key, self._settings[key].brightness)

    def set_enabled(self, key: str, enabled: bool) -> None:
        with self._lock:
            self._settings_locked(key).enabled = bool(enabled)
            self._touch_locked()
            if self._running():
                self.engine.set_enabled(key, bool(enabled))

    def set_master_brightness(self, value: float) -> None:
        with self._lock:
            self._master = float(min(max(value, 0.0), 1.0))
            self._touch_locked()
            if self._running():
                self.engine.set_master_brightness(self._master)

    # -- options from Settings

    def reapply(self) -> None:
        """Put the devices back in Direct mode and resend everything (resume from sleep)."""
        with self._lock:
            if self._running():
                self.engine.reapply()

    def set_write_cap(self, hz: Optional[float]) -> None:
        with self._lock:
            self.engine.set_write_cap(hz)

    def set_input_options(self, audio_device: Optional[str], lhm_url: Optional[str]) -> None:
        with self._lock:
            self.engine.set_input_options(audio_device, lhm_url)

    def set_away(self, away: bool) -> None:
        """Fade the lights out (locked, idle, scheduled off) or back in. Not saved in a profile."""
        with self._lock:
            self.engine.set_away(away)

    @property
    def away(self) -> bool:
        return self.engine.away

    def set_fade_ms(self, ms: int) -> None:
        with self._lock:
            self.engine.set_fade(max(0, int(ms)) / 1000.0)

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._want_paused = bool(paused)
            if self._running():
                self.engine.pause() if paused else self.engine.resume()

    # -- internals

    def _running(self) -> bool:
        # Set to "running" under the lock right after the settings are applied, so a
        # change made while the engine is coming up is stored and then picked up.
        return self._phase == "running"

    def _settings_locked(self, key: str) -> GroupSettings:
        if key not in self._settings:
            self._settings[key] = GroupSettings(layers=[LayerSpec(self.default_effect)])
        return self._settings[key]

    @staticmethod
    def _switch_effect_locked(s: GroupSettings, spec: LayerSpec, effect_id: str) -> None:
        if spec.effect == effect_id:
            return
        memory = s.remembered.setdefault(spec.id, {})
        memory[spec.effect] = dict(spec.params)
        spec.effect = effect_id
        spec.params = dict(memory.get(effect_id, {}))

    def _known(self, key: str) -> bool:
        return any(g.key == key for g in self.engine.groups)

    def _push_layers_locked(self, key: str) -> None:
        # Copies: the render thread keeps what it is given while the UI edits ours.
        # Layer ids are stable, so effects keep their animation state across edits.
        self.engine.set_layers(key, copy.deepcopy(self._effective_layers_locked(key)))

    def _effective_layers_locked(self, key: str) -> list[LayerSpec]:
        """What actually renders: the group's own layers, or the keyboard's dominant effect
        when the group is synced to it."""
        s = self._settings[key]
        if s.sync_keyboard and key != "keyboard":
            dominant = self._dominant_keyboard_layer_locked()
            if dominant is not None:
                mirror = copy.deepcopy(dominant)
                mirror.id = f"sync-{key}"
                mirror.mask, mirror.opacity, mirror.blend, mirror.visible = "all", 1.0, "normal", True
                return [mirror]
        return s.layers

    def _dominant_keyboard_layer_locked(self) -> Optional[LayerSpec]:
        """The topmost visible effect layer on the keyboard. Painted layers don't count:
        a per-key picture has no effect to carry over."""
        kb = self._settings.get("keyboard")
        if kb is None:
            return None
        return next((l for l in reversed(kb.layers) if l.visible and l.effect != "static_keys"), None)

    def _touch_locked(self) -> None:
        """Something the user set changed: save soon (restarting the timer while they drag)."""
        self._revision += 1
        if self._store is None or self._phase in ("stopping", "stopped"):
            return
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(self._save_delay, self._save_now)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _save_now(self) -> None:
        with self._lock:
            self._save_timer = None
            master, snapshot = self._master, copy.deepcopy(self._settings)
            sizes = dict(self.engine.zone_sizes)
        try:
            self._store.save(master, snapshot)
        except Exception:
            log.exception("could not save settings")
        try:
            self._store.save_zone_sizes(sizes)
        except Exception:
            log.exception("could not save LED counts")

    def _flush_save(self) -> None:
        with self._lock:
            timer, self._save_timer = self._save_timer, None
        if timer is not None:
            timer.cancel()
            self._save_now()

    def _apply_layers_locked(self, key: str) -> None:
        self._touch_locked()
        if not self._running() or not self._known(key):
            return
        self._push_layers_locked(key)
        if key == "keyboard":  # groups synced to it follow every keyboard edit
            for other, st in self._settings.items():
                if st.sync_keyboard and other != key and self._known(other):
                    self._push_layers_locked(other)

    def _apply_all_locked(self) -> None:
        self.engine.set_master_brightness(self._master)
        for g in self.engine.groups:
            s = self._settings_locked(g.key)
            self._push_layers_locked(g.key)
            self.engine.set_brightness(g.key, s.brightness)
            self.engine.set_enabled(g.key, s.enabled)
        if self._want_paused:
            self.engine.pause()


class _Cancelled(Exception):
    """stop() was called while the engine was coming up."""


def _sentence(text: str) -> str:
    text = text.strip() or "OpenRGB is not available"
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."
