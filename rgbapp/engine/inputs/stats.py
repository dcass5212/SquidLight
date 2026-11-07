"""System stats: the ``StatsProvider`` interface, a psutil provider (CPU/RAM) and a
LibreHardwareMonitor provider (GPU load and temperatures via its local web server)."""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.request
from dataclasses import dataclass, field
from typing import Mapping, Optional, Protocol

log = logging.getLogger(__name__)

LHM_URL = "http://127.0.0.1:8085/data.json"

# Values a provider may report: loads are 0..1, temperatures are degrees C.
STAT_KEYS = ("cpu_load", "ram_load", "gpu_load", "cpu_temp", "gpu_temp")


class StatsUnavailable(Exception):
    """The source can't be read right now; the message is shown to the user."""


class StatsProvider(Protocol):
    need: str  # the effect input this provider serves: "cpu" or "sensors"
    interval: float  # seconds between polls

    def read(self) -> dict[str, float]: ...


@dataclass(frozen=True)
class StatsSnapshot:
    values: Mapping[str, float] = field(default_factory=dict)
    errors: Mapping[str, str] = field(default_factory=dict)  # need -> message

    def get(self, key: str) -> Optional[float]:
        return self.values.get(key)


class PsutilProvider:
    need = "cpu"
    interval = 0.5

    def __init__(self) -> None:
        import psutil

        self._ps = psutil
        psutil.cpu_percent(None)  # the first call has no interval to measure

    def read(self) -> dict[str, float]:
        return {
            "cpu_load": self._ps.cpu_percent(None) / 100.0,
            "ram_load": self._ps.virtual_memory().percent / 100.0,
        }


_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def _number(text: object) -> Optional[float]:
    m = _NUM.search(str(text))
    return float(m.group(0).replace(",", ".")) if m else None


class LibreHardwareMonitorProvider:
    """Reads the JSON tree LibreHardwareMonitor serves when its remote web server is on."""

    need = "sensors"
    interval = 1.0

    def __init__(self, url: str = LHM_URL, timeout: float = 0.6) -> None:
        self.url, self.timeout = url, timeout

    def read(self) -> dict[str, float]:
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout) as resp:
                tree = json.load(resp)
        except Exception as exc:
            raise StatsUnavailable(
                "No sensor source: start LibreHardwareMonitor and turn on Options > Remote Web Server."
            ) from exc
        return parse_lhm(tree)


def _hardware_kind(node: dict) -> str:
    icon = str(node.get("ImageURL", "")).lower()
    name = str(node.get("Text", "")).lower()
    if "cpu" in icon:
        return "cpu"
    if any(k in icon for k in ("nvidia", "ati", "amd", "gpu")) or any(k in name for k in ("geforce", "radeon", "gpu")):
        return "gpu"
    return ""


def parse_lhm(tree: dict) -> dict[str, float]:
    """Pull CPU/GPU load and temperature out of LibreHardwareMonitor's data.json tree."""
    out: dict[str, float] = {}
    best: dict[str, int] = {}  # temp key -> rank of the sensor chosen so far (lower wins)

    def temp_rank(name: str) -> int:
        if "package" in name or "tctl" in name or "tdie" in name or name == "gpu core":
            return 0
        return 1  # any other temperature sensor: the hottest one stands in

    def walk(node: dict, hw: str, group: str) -> None:
        kids = node.get("Children") or []
        if kids:
            kind = _hardware_kind(node)
            for child in kids:
                ctext = str(child.get("Text", "")).lower()
                walk(child, kind or hw, ctext if ctext in ("temperatures", "load") else group)
            return
        val = _number(node.get("Value", ""))
        if val is None or not hw:
            return
        name = str(node.get("Text", "")).lower()
        if group == "temperatures":
            key, rank = f"{hw}_temp", temp_rank(name)
            if key not in out or rank < best[key] or (rank == best[key] and rank == 1 and val > out[key]):
                out[key], best[key] = val, rank
        elif group == "load" and name in ("gpu core", "cpu total"):
            out[f"{hw}_load"] = min(max(val / 100.0, 0.0), 1.0)

    walk(tree, "", "")
    return out


class StatsPoller:
    """Polls one provider on a thread; a failing source is retried slowly, never raised."""

    def __init__(self, provider: StatsProvider) -> None:
        self.provider = provider
        self._lock = threading.Lock()
        self._values: dict[str, float] = {}
        self._error = ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"stats-{self.provider.need}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(2.0)

    def poll_once(self) -> None:
        try:
            values = self.provider.read()
            error = ""
        except StatsUnavailable as exc:
            values, error = {}, str(exc)
        except Exception as exc:
            log.warning("stats provider %s failed: %s", self.provider.need, exc)
            values, error = {}, "Could not read system stats."
        with self._lock:
            self._values, self._error = values, error

    def read(self) -> tuple[dict[str, float], str]:
        with self._lock:
            return dict(self._values), self._error

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            slow = self._error != ""  # no source: don't hammer it
            if self._stop.wait(5.0 if slow else self.provider.interval):
                return
