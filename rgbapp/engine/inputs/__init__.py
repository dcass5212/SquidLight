"""Input workers (audio, keys, system stats), started only while a visible layer needs them.

``InputHub.set_needs`` is called by the render loop with the union of the visible layers'
``needs``. Workers for needs that appear are started, workers for needs that vanish are
stopped. ``snapshot`` hands the latest values to effects through ``Ctx.inputs``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from .audio import AudioSnapshot, AudioWorker
from .keys import KeyEvent, KeyHook, KeySnapshot, KeyTracker
from .stats import (
    LHM_URL,
    LibreHardwareMonitorProvider,
    PsutilProvider,
    StatsPoller,
    StatsProvider,
    StatsSnapshot,
    StatsUnavailable,
)

log = logging.getLogger(__name__)

# Needs the hub knows. "cpu" is CPU/RAM load (psutil); "sensors" is GPU load and temperatures.
KNOWN_NEEDS = ("audio", "keys", "cpu", "sensors")

__all__ = [
    "InputHub", "AudioSnapshot", "KeySnapshot", "KeyEvent", "KeyTracker", "StatsSnapshot",
    "StatsProvider", "StatsUnavailable", "KNOWN_NEEDS",
]


class InputHub:
    """Owns the workers. ``factories`` maps a need to a callable that builds its worker
    (tests pass fakes). A worker has ``start()``, ``stop()`` and ``snapshot(now)``;
    stats pollers are the exception and are merged into one ``StatsSnapshot``."""

    def __init__(
        self,
        factories: Optional[dict[str, Callable[[], object]]] = None,
        audio_device: Optional[str] = None,
        lhm_url: Optional[str] = None,
    ) -> None:
        self.audio_device = audio_device or None  # speaker name; None follows the Windows default
        self.lhm_url = lhm_url or LHM_URL
        self._factories: dict[str, Callable[[], object]] = {
            "audio": lambda: AudioWorker(self.audio_device),
            "keys": KeyHook,
            "cpu": lambda: StatsPoller(PsutilProvider()),
            "sensors": lambda: StatsPoller(LibreHardwareMonitorProvider(self.lhm_url)),
        }
        if factories:
            self._factories.update(factories)
        self._lock = threading.Lock()
        self._workers: dict[str, object] = {}
        self._stale: set[str] = set()  # running workers to rebuild with new options

    def configure(self, audio_device: Optional[str] = None, lhm_url: Optional[str] = None) -> None:
        """Change the audio device or the hardware-monitor URL. Safe from any thread: a
        running worker that uses the old value is rebuilt on the render thread's next
        ``set_needs`` (so starting and stopping always happens there)."""
        with self._lock:
            audio_device = audio_device or None
            lhm_url = lhm_url or LHM_URL
            if audio_device != self.audio_device:
                self.audio_device = audio_device
                self._stale.add("audio")
            if lhm_url != self.lhm_url:
                self.lhm_url = lhm_url
                self._stale.add("sensors")

    @property
    def running(self) -> set[str]:
        return set(self._workers)

    def set_needs(self, needs: set[str]) -> None:
        wanted = {n for n in needs if n in self._factories}
        unknown = set(needs) - wanted
        if unknown:
            log.warning("effects asked for unknown inputs: %s", sorted(unknown))
        with self._lock:
            stale, self._stale = self._stale & set(self._workers), set()
            for need in (set(self._workers) - wanted) | stale:
                self._stop_locked(need)
            for need in wanted - set(self._workers):
                try:
                    worker = self._factories[need]()
                    worker.start()
                except Exception:
                    log.exception("could not start the %s input", need)
                    continue
                self._workers[need] = worker
                log.info("input started: %s", need)

    def _stop_locked(self, need: str) -> None:
        worker = self._workers.pop(need)
        try:
            worker.stop()
        except Exception:
            log.exception("could not stop the %s input", need)
        log.info("input stopped: %s", need)

    def shutdown(self) -> None:
        self.set_needs(set())

    def snapshot(self, now: float) -> dict:
        """What ``Ctx.inputs`` holds: ``audio``, ``keys`` and ``stats``, for running inputs only."""
        out: dict = {}
        values: dict[str, float] = {}
        errors: dict[str, str] = {}
        with self._lock:
            workers = dict(self._workers)
        for need, worker in workers.items():
            if isinstance(worker, StatsPoller):
                v, err = worker.read()
                values.update(v)
                if err:
                    errors[need] = err
            else:
                out[need] = worker.snapshot(now)
        if any(isinstance(w, StatsPoller) for w in workers.values()):
            out["stats"] = StatsSnapshot(values, errors)
        return out


class NullHub(InputHub):
    """A hub that never starts anything (headless tests that don't want real inputs)."""

    def set_needs(self, needs: set[str]) -> None:
        pass
