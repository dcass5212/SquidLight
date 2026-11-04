"""Per-app profile switching.

A rule is "when ``match`` (a process name like ``valorant.exe``) is running, or with
``focused`` set, is the foreground app, use profile P". Rules are checked in list order and
the first match wins. ``RulesWatcher`` polls about once a second and calls back only when
the *matched rule* changes:

- an app starts (or comes to the front)  -> its profile,
- the matched app closes                 -> the default profile,
- nothing matched, nothing changed       -> it leaves the profile alone, so a manual choice
  sticks and changing the default profile doesn't switch anything by itself.

At startup, a matching app switches the profile; no match leaves the restored one alone.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

log = logging.getLogger(__name__)

POLL_SECONDS = 1.0


def norm_name(name: str) -> str:
    return name.strip().strip('"').replace("/", "\\").rsplit("\\", 1)[-1].casefold()


@dataclass(frozen=True)
class Rule:
    match: str
    profile_id: str
    focused: bool = False  # only while it is the foreground app

    def to_json(self) -> dict:
        out = {"match": self.match.strip(), "profile_id": self.profile_id}
        if self.focused:
            out["focused"] = True
        return out

    @staticmethod
    def from_json(raw) -> Optional["Rule"]:
        if not isinstance(raw, dict) or not raw.get("match") or not raw.get("profile_id"):
            return None
        return Rule(str(raw["match"]), str(raw["profile_id"]), bool(raw.get("focused", False)))

    def applies(self, running: set[str], foreground: Optional[str]) -> bool:
        want = norm_name(self.match)
        if not want:
            return False
        names = {foreground} if self.focused else running
        return want in names or (not want.endswith(".exe") and want + ".exe" in names)


def pick_rule(rules, valid_ids, running: set[str], foreground: Optional[str]) -> Optional[Rule]:
    """The first rule that applies and points at a profile that still exists."""
    for rule in rules:
        if rule.profile_id in valid_ids and rule.applies(running, foreground):
            return rule
    return None


class ProcessSource(Protocol):
    def running(self) -> set[str]: ...
    def foreground(self) -> Optional[str]: ...


class SystemProcesses:
    """Process names from psutil; the foreground app from the Win32 API (ctypes, no extra dependency)."""

    def running(self) -> set[str]:
        import psutil

        names = set()
        for p in psutil.process_iter(["name"]):
            name = p.info.get("name")
            if name:
                names.add(name.casefold())
        return names

    def foreground(self) -> Optional[str]:
        try:
            import psutil

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return psutil.Process(pid.value).name().casefold()
        except Exception:
            return None


def running_apps() -> list[str]:
    """Distinct process names for the rule editor's picker."""
    try:
        return sorted(SystemProcesses().running())
    except Exception:
        log.exception("could not list processes")
        return []


class RulesWatcher:
    """``state()`` returns ``(rules, default_profile_id, valid_profile_ids)``;
    ``on_profile(profile_id)`` is called from the watcher thread (hop to the GUI thread there)."""

    def __init__(
        self,
        state: Callable[[], tuple],
        on_profile: Callable[[str], None],
        source: Optional[ProcessSource] = None,
        interval: float = POLL_SECONDS,
    ) -> None:
        self._state = state
        self._on_profile = on_profile
        self._source = source or SystemProcesses()
        self._interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._first = True
        self._last: Optional[Rule] = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="rules", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                log.exception("rules poll failed")
            self._stop.wait(self._interval)

    def poll_once(self) -> None:
        rules, default_id, valid = self._state()
        if not rules:
            running, fg = set(), None
        else:
            running = self._source.running()
            fg = self._source.foreground() if any(r.focused for r in rules) else None
        hit = pick_rule(rules, valid, running, fg)
        first, self._first = self._first, False
        previous, self._last = self._last, hit
        if hit == previous:
            return
        if hit is not None:
            self._on_profile(hit.profile_id)
        elif not first and default_id in valid:
            self._on_profile(default_id)
