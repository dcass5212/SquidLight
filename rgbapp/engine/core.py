"""Engine: ties manager, backend, device groups and the render loop together.

This is the object the GUI (M2) and the headless CLI both drive. It has no Qt
dependency and works the same against ``FakeBackend``.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from .backend import Backend, BackendError, ConnectionState
from .compositor import LayerSpec
from .devices import DeviceGroup, build_groups
from .loop import LoopConfig, LoopStats, RenderLoop
from .openrgb_manager import OpenRGBManager

log = logging.getLogger(__name__)


def zone_key(device_id: str, zone_name: str) -> str:
    """Stable key for a zone in saved settings."""
    return f"{device_id}|{zone_name}"


class Engine:
    def __init__(
        self,
        backend: Backend,
        config: Optional[LoopConfig] = None,
        manager: Optional[OpenRGBManager] = None,
    ) -> None:
        self.backend = backend
        self.manager = manager
        self.config = config or LoopConfig()
        self.loop: Optional[RenderLoop] = None
        self._started = False
        # Saved ARGB LED counts, {zone_key(device_id, zone name): count}, applied on every
        # connect (OpenRGB forgets them when it restarts).
        self.zone_sizes: dict[str, int] = {}

    # -- lifecycle

    def start(self, configure: Optional[Callable[["Engine"], None]] = None) -> None:
        """Bring up OpenRGB (if managed), connect, build device groups, start rendering.

        ``configure`` runs after the groups exist but before the first frame, so the
        hardware's first frame is already the user's effect (not a black flash).
        Raises BackendError if no server can be reached; the caller decides what to show.
        """
        if self._started:
            return
        try:
            if self.manager is not None:
                self.manager.start()
                if not self.manager.ready:
                    raise BackendError(self.manager.reason or "OpenRGB is not available")
                self.manager.start_supervisor()
            self.backend.connect()
            self._apply_zone_sizes()
            groups = build_groups(self.backend.devices())
            if not groups:
                log.warning("OpenRGB reported no controllable devices with LEDs")
            self.loop = RenderLoop(self.backend, groups, self.config)
            if configure is not None:
                configure(self)
            self.loop.start()
            self._started = True
        except Exception:
            self.stop()  # undo whatever came up
            raise

    def _apply_zone_sizes(self) -> None:
        for dev in self.backend.devices():
            for z in dev.zones:
                want = self.zone_sizes.get(zone_key(dev.id, z.name))
                if want is None or not z.resizable or want == z.size:
                    continue
                try:
                    self.backend.resize_zone(dev.id, z.name, max(z.min_size, min(want, z.max_size)))
                except BackendError as exc:
                    log.warning("could not set %s to %d LEDs: %s", z.name, want, exc)

    def resize_zone(
        self, device_id: str, zone_name: str, size: int, configure: Optional[Callable[["Engine"], None]] = None
    ) -> None:
        """Change an ARGB zone's LED count while running: pause rendering, resize, rebuild the
        groups and restart the loop. ``configure`` re-applies the user's settings to the new
        groups (as in ``start``). Blocking device I/O: call it off the GUI thread. The loop
        is always restarted, even when the resize fails (the error is then re-raised)."""
        if self.loop is None:
            raise RuntimeError("engine is not running")
        self.zone_sizes[zone_key(device_id, zone_name)] = size
        old = self.loop
        paused = old.paused
        old.stop()
        error: Optional[BaseException] = None
        try:
            self.backend.resize_zone(device_id, zone_name, size)
        except BackendError as exc:
            error = exc
            log.warning("resize of %s failed: %s", zone_name, exc)
        self.loop = RenderLoop(self.backend, build_groups(self.backend.devices()), self.config)
        try:
            if configure is not None:
                configure(self)
            if paused:
                self.loop.pause()
        finally:
            self.loop.start()
        if error is not None:
            raise error

    def stop(self) -> None:
        """Stop rendering, restore every device's original mode, stop OpenRGB if ours."""
        if self.loop is not None:
            self.loop.stop()
        try:
            self.backend.close()
        except Exception:
            log.exception("backend.close failed")
        if self.manager is not None:
            self.manager.stop()
        self._started = False
        self.loop = None

    def __enter__(self) -> "Engine":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- control (thread-safe)

    @property
    def groups(self) -> list[DeviceGroup]:
        return self.loop.groups if self.loop else []

    def set_layers(self, group_key: str, layers: list[LayerSpec]) -> None:
        self._require_loop().set_layers(group_key, layers)

    def set_brightness(self, group_key: str, value: float) -> None:
        self._require_loop().set_brightness(group_key, value)

    def set_master_brightness(self, value: float) -> None:
        self._require_loop().set_master_brightness(value)

    def set_enabled(self, group_key: str, enabled: bool) -> None:
        self._require_loop().set_enabled(group_key, enabled)

    def pause(self) -> None:
        self._require_loop().pause()

    def resume(self) -> None:
        self._require_loop().resume()

    @property
    def paused(self) -> bool:
        return bool(self.loop and self.loop.paused)

    @property
    def running(self) -> bool:
        return self._started

    @property
    def external(self) -> bool:
        """True when we are using someone else's OpenRGB server."""
        return bool(self.manager and self.manager.external)

    def latest_frames(self) -> dict:
        return self.loop.latest_frames() if self.loop else {}

    def group_health(self, group_key: str) -> str:
        return self.loop.group_health(group_key) if self.loop else "disconnected"

    @property
    def connection_state(self) -> ConnectionState:
        return self.backend.state

    @property
    def stats(self) -> LoopStats:
        return self._require_loop().stats

    def _require_loop(self) -> RenderLoop:
        if self.loop is None:
            raise RuntimeError("engine is not running")
        return self.loop
