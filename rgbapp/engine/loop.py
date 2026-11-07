"""The render loop: one thread that owns compositing and all backend I/O.

Each frame: for every device group, composite its layers, apply brightness, convert to
uint8, and write the pieces that changed to their devices, never faster than each
device's write-rate cap. The loop never raises: bad effects and failed writes are
logged and skipped.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .backend import Backend, BackendError, ConnectionState
from .compositor import Compositor, LayerSpec, to_uint8
from .devices import DeviceGroup
from .effects import Ctx
from .inputs import InputHub

log = logging.getLogger(__name__)

# Device writes per second. SMBus devices (RAM, board) stay low so they don't starve
# other SMBus users (docs/04). The keyboard may be raised to 60 once proven stable.
DEFAULT_WRITE_HZ_BY_TYPE = {"keyboard": 30.0, "dram": 15.0, "motherboard": 15.0}
FALLBACK_WRITE_HZ = 30.0
# Choices for the user's write-rate cap (Settings). The keyboard (USB) writes at the cap;
# everything else stays at its type default or the cap, whichever is lower.
WRITE_CAP_CHOICES = (15, 20, 30, 60)
_CAP_TOLERANCE_S = 0.002  # scheduler jitter allowance when checking the cap
# A frame that starts this late means the PC was asleep (or badly stalled): devices may
# have reset, so take Direct mode again and resend everything.
STALL_REASSERT_S = 2.0


@dataclass
class LoopConfig:
    fps: float = 30.0
    write_hz: dict[str, float] = field(default_factory=dict)  # by group key, overrides types
    device_retry_s: float = 3.0  # how long a device that failed a write sits out
    max_write_hz: Optional[float] = None  # the user's cap (Settings); None keeps the defaults

    def write_hz_for(self, group: DeviceGroup) -> float:
        if group.key in self.write_hz:
            return self.write_hz[group.key]
        cap = self.max_write_hz

        def rate(device_type: str) -> float:
            default = DEFAULT_WRITE_HZ_BY_TYPE.get(device_type, FALLBACK_WRITE_HZ)
            if cap is None:
                return default
            # USB keyboards write at the cap; SMBus devices never go above their default
            return cap if device_type == "keyboard" else min(default, cap)

        # a merged group is as slow as its slowest member
        return min((rate(t) for t in group.device_types), default=cap or FALLBACK_WRITE_HZ)


@dataclass
class LoopStats:
    frames: int = 0
    late_frames: int = 0  # frames that finished after the next frame was due
    writes: int = 0
    skipped_unchanged: int = 0
    skipped_rate_cap: int = 0
    write_errors: int = 0
    reconnects: int = 0
    max_frame_ms: float = 0.0
    total_frame_ms: float = 0.0

    @property
    def avg_frame_ms(self) -> float:
        return self.total_frame_ms / self.frames if self.frames else 0.0

    def summary(self) -> str:
        return (
            f"frames={self.frames} late={self.late_frames} writes={self.writes} "
            f"unchanged={self.skipped_unchanged} rate_capped={self.skipped_rate_cap} "
            f"write_errors={self.write_errors} reconnects={self.reconnects} "
            f"frame_ms avg={self.avg_frame_ms:.2f} max={self.max_frame_ms:.2f}"
        )


@dataclass
class _GroupRuntime:
    group: DeviceGroup
    compositor: Compositor
    min_interval: float
    brightness: float = 1.0
    enabled: bool = True  # False renders black (the dashboard's on/off toggle)
    last_frame: Optional[np.ndarray] = None  # latest uint8 output, for the UI preview
    last_sent: dict[str, np.ndarray] = field(default_factory=dict)  # device_id -> uint8 frame
    last_write: dict[str, float] = field(default_factory=dict)  # device_id -> clock time
    retry_at: dict[str, float] = field(default_factory=dict)  # device_id -> clock time


class RenderLoop:
    def __init__(
        self,
        backend: Backend,
        groups: list[DeviceGroup],
        config: Optional[LoopConfig] = None,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], None] = time.sleep,
        inputs: Optional[InputHub] = None,
    ) -> None:
        self.backend = backend
        self.inputs = inputs if inputs is not None else InputHub()
        self.config = config or LoopConfig()
        self.stats = LoopStats()
        self.master_brightness = 1.0
        self._clock = clock
        self._sleep = sleep
        self._runtimes = {
            g.key: _GroupRuntime(g, Compositor(g), 1.0 / self.config.write_hz_for(g)) for g in groups
        }
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._t0: Optional[float] = None
        self._last_t = 0.0
        # Pause is requested from any thread but carried out on the render thread,
        # because it means device I/O (restoring each device's own mode).
        self._want_paused = False
        self._paused = False
        # Set from any thread; the render thread clears what was sent so the next frame is
        # written in full ("reassert" first puts devices back in Direct mode).
        self._resend: Optional[str] = None  # None | "resend" | "reassert"

    # -- control surface (safe to call from other threads)

    @property
    def groups(self) -> list[DeviceGroup]:
        return [r.group for r in self._runtimes.values()]

    def set_layers(self, group_key: str, specs: list[LayerSpec]) -> None:
        self._runtimes[group_key].compositor.set_layers(specs)

    def set_brightness(self, group_key: str, value: float) -> None:
        self._runtimes[group_key].brightness = float(np.clip(value, 0.0, 1.0))

    def set_enabled(self, group_key: str, enabled: bool) -> None:
        self._runtimes[group_key].enabled = bool(enabled)

    def set_master_brightness(self, value: float) -> None:
        self.master_brightness = float(np.clip(value, 0.0, 1.0))

    def set_write_cap(self, hz: Optional[float]) -> None:
        """Change the user's write-rate cap; takes effect on the next frame."""
        self.config.max_write_hz = hz
        for rt in self._runtimes.values():
            rt.min_interval = 1.0 / self.config.write_hz_for(rt.group)

    def request_resend(self, reassert: bool = False) -> None:
        """Write every device in full on the next frame (e.g. after resume from sleep).
        ``reassert`` also puts each device back in Direct mode first."""
        if reassert or self._resend is None:
            self._resend = "reassert" if reassert else "resend"

    def pause(self) -> None:
        """Hand devices back to their own modes on the next frame, and stop writing."""
        self._want_paused = True

    def resume(self) -> None:
        """Retake Direct mode on the next frame and resend the current output."""
        self._want_paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    def latest_frames(self) -> dict[str, np.ndarray]:
        """The most recent uint8 output per group. The arrays are never mutated."""
        return {k: rt.last_frame for k, rt in self._runtimes.items() if rt.last_frame is not None}

    def notices(self) -> dict[str, dict[str, str]]:
        """group key -> {layer id: notice} for layers that can't work right now."""
        found = {k: rt.compositor.notices() for k, rt in self._runtimes.items()}
        return {k: v for k, v in found.items() if v}

    def group_health(self, group_key: str) -> str:
        """'paused', 'disconnected', 'error' (a device is sitting out) or 'ok'."""
        if self._paused:
            return "paused"
        if self.backend.state is not ConnectionState.CONNECTED:
            return "disconnected"
        now = self._clock()
        if any(now < t for t in list(self._runtimes[group_key].retry_at.values())):
            return "error"
        return "ok"

    # -- running

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="rgb-engine", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                log.error("render thread did not stop within %.1fs", timeout)
        self.inputs.shutdown()

    def run(self) -> None:
        """Blocking loop with absolute-deadline pacing (jitter doesn't accumulate)."""
        period = 1.0 / self.config.fps
        next_deadline = self._clock()
        while not self._stop.is_set():
            started = self._clock()
            self.step(started)
            elapsed = self._clock() - started
            self.stats.total_frame_ms += elapsed * 1000.0
            self.stats.max_frame_ms = max(self.stats.max_frame_ms, elapsed * 1000.0)

            next_deadline += period
            delay = next_deadline - self._clock()
            if delay > 0:
                # time.sleep (not Event.wait): on Windows it uses a high-resolution timer
                # since Python 3.11. A frame is at most 1/fps long, so stop() is prompt.
                self._sleep(delay)
            else:
                self.stats.late_frames += 1
                if delay < -5 * period:  # long stall (sleep/resume): resync, don't burst
                    next_deadline = self._clock()
                    if -delay >= STALL_REASSERT_S:
                        log.info("render loop stalled %.1fs (sleep?); reapplying lighting", -delay)
                        self.request_resend(reassert=True)

    def step(self, now: float) -> None:
        """Render and write one frame. Never raises."""
        if self._t0 is None:
            self._t0 = now
            self._last_t = 0.0
        t = now - self._t0
        dt = max(t - self._last_t, 0.0)
        self._last_t = t
        self.stats.frames += 1

        if self._want_paused != self._paused:
            self._apply_pause(self._want_paused)

        if self._paused:
            self.inputs.set_needs(set())  # nothing is lit, so nothing needs listening to
            for rt in self._runtimes.values():  # keep the preview alive; no device I/O
                try:
                    self._step_group(rt, t, dt, now, connected=False)
                except Exception:
                    log.exception("group %s failed this frame", rt.group.key)
            return

        try:
            if self.backend.maintain():
                self.stats.reconnects += 1
                log.info("reconnected; resending current frame")
                for rt in self._runtimes.values():
                    rt.last_sent.clear()
                    rt.retry_at.clear()
        except Exception:
            log.exception("backend.maintain failed")

        connected = self.backend.state is ConnectionState.CONNECTED
        if connected and self._resend is not None:
            self._do_resend()
            connected = self.backend.state is ConnectionState.CONNECTED  # reassert can drop it
        inputs = self._read_inputs(now)
        for rt in self._runtimes.values():
            try:
                self._step_group(rt, t, dt, now, connected, inputs)
            except Exception:
                log.exception("group %s failed this frame", rt.group.key)

    def _do_resend(self) -> None:
        kind, self._resend = self._resend, None
        if kind == "reassert":
            reassert = getattr(self.backend, "reassert", None)
            if reassert is not None:
                try:
                    reassert()
                except Exception:
                    log.exception("backend.reassert failed")
        for rt in self._runtimes.values():
            rt.last_sent.clear()
            rt.retry_at.clear()

    def _read_inputs(self, now: float) -> dict:
        """Start/stop workers to match what the layers need, then take this frame's snapshot."""
        try:
            needs: set[str] = set()
            for rt in self._runtimes.values():
                if rt.enabled and rt.brightness * self.master_brightness > 0.0:
                    needs |= rt.compositor.needs()
            self.inputs.set_needs(needs)
            return self.inputs.snapshot(now) if needs else {}
        except Exception:
            log.exception("reading inputs failed")
            return {}

    def _apply_pause(self, pause: bool) -> None:
        if pause:
            log.info("pausing: restoring device modes")
            try:
                self.backend.close()
            except Exception:
                log.exception("backend.close failed while pausing")
        else:
            log.info("resuming: retaking Direct mode")
            try:
                self.backend.connect()
            except BackendError as exc:  # maintain() keeps retrying with backoff
                log.warning("resume: could not reconnect yet (%s)", exc)
            for rt in self._runtimes.values():
                rt.last_sent.clear()
                rt.retry_at.clear()
        self._paused = pause

    def _step_group(
        self, rt: _GroupRuntime, t: float, dt: float, now: float, connected: bool, inputs: Optional[dict] = None
    ) -> None:
        g = rt.group
        ctx = Ctx(
            t=t, dt=dt, led_positions=g.positions, inputs=inputs or {}, led_names=g.led_names, aspect=g.aspect
        )
        level = rt.brightness * self.master_brightness if rt.enabled else 0.0
        frame = to_uint8(rt.compositor.render(ctx), level)
        rt.last_frame = frame
        if not connected:
            return
        for device_id, colors in rt.group.split(frame):
            if len(colors) == 0:  # an ARGB header with no LEDs set yet: nothing to send
                continue
            last = rt.last_sent.get(device_id)
            if last is not None and np.array_equal(last, colors):
                self.stats.skipped_unchanged += 1
                continue
            if now < rt.retry_at.get(device_id, 0.0):
                continue
            if now - rt.last_write.get(device_id, -1e9) < rt.min_interval - _CAP_TOLERANCE_S:
                self.stats.skipped_rate_cap += 1
                continue
            try:
                self.backend.write(device_id, colors)
            except BackendError as exc:
                self.stats.write_errors += 1
                rt.retry_at[device_id] = now + self.config.device_retry_s
                log.warning("write to %s failed (%s); retrying in %.0fs", device_id, exc, self.config.device_retry_s)
                continue
            rt.last_sent[device_id] = colors.copy()
            rt.last_write[device_id] = now
            self.stats.writes += 1
