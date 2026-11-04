"""Headless engine CLI.

    python -m rgbapp.engine --headless --effect rainbow_wave
    python -m rgbapp.engine --headless --effect breathing --param color=#ff8800 --duration 20
    python -m rgbapp.engine --headless --backend fake --duration 5      # no hardware
    python -m rgbapp.engine --list-effects

Ctrl+C stops it; devices are put back the way they were found.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from ..logging_setup import setup_logging
from .backend import BackendError, FakeBackend, OpenRGBBackend
from .compositor import LayerSpec
from .core import Engine
from .effects import listed_effects, get_effect_class
from .loop import LoopConfig
from .openrgb_manager import DEFAULT_PORT, OpenRGBManager

log = logging.getLogger("rgbapp.engine")


def _parse_params(effect_id: str, items: list[str]) -> dict:
    cls = get_effect_class(effect_id)
    by_id = {p.id: p for p in cls.params}
    out: dict = {}
    for item in items:
        key, sep, raw = item.partition("=")
        if not sep or key not in by_id:
            raise SystemExit(
                f"bad --param {item!r}; {effect_id} has: {', '.join(by_id) or '(none)'}"
            )
        out[key] = raw.lower() in ("1", "true", "yes", "on") if by_id[key].type == "bool" else raw
    return out


def _print_effects() -> None:
    for cls in listed_effects():
        print(f"{cls.id}  ({cls.name})")
        for p in cls.params:
            rng = f" [{p.min}..{p.max}]" if p.min is not None or p.max is not None else ""
            print(f"    {p.id}: {p.type}{rng} default={p.default!r}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m rgbapp.engine", description=__doc__.split("\n\n")[0])
    ap.add_argument("--headless", action="store_true", help="run without a GUI (the app with a window is python -m rgbapp)")
    ap.add_argument("--effect", default="rainbow_wave", help="effect id (see --list-effects)")
    ap.add_argument("--param", action="append", default=[], metavar="KEY=VALUE", help="effect parameter, repeatable")
    ap.add_argument("--devices", default="", help="comma-separated group keys to light (default: all)")
    ap.add_argument("--brightness", type=float, default=1.0, help="master brightness 0..1")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--write-hz", action="append", default=[], metavar="GROUP=HZ", help="override a group's write cap")
    ap.add_argument("--duration", type=float, default=0.0, help="seconds to run; 0 = until Ctrl+C")
    ap.add_argument("--backend", choices=["openrgb", "fake"], default="openrgb")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-manager", action="store_true", help="don't start/supervise OpenRGB; require a running server")
    ap.add_argument("--list-effects", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_effects:
        _print_effects()
        return 0
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    try:
        get_effect_class(args.effect)
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 2
    params = _parse_params(args.effect, args.param)

    write_hz = {}
    for item in args.write_hz:
        key, _, hz = item.partition("=")
        write_hz[key] = float(hz)
    config = LoopConfig(fps=args.fps, write_hz=write_hz)

    if args.backend == "fake":
        backend, manager = FakeBackend(), None
    else:
        backend = OpenRGBBackend(port=args.port, settle_seconds=15.0)
        manager = None if args.no_manager else OpenRGBManager(port=args.port)

    stop = threading.Event()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK = Ctrl+Break on Windows
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, lambda *_: stop.set())

    engine = Engine(backend, config, manager)
    try:
        engine.start()
    except BackendError as exc:
        print(f"Could not start: {exc}", file=sys.stderr)
        return 1

    try:
        wanted = {k for k in args.devices.split(",") if k}
        keys = [g.key for g in engine.groups]
        unknown = wanted - set(keys)
        if unknown:
            print(f"Unknown device group(s) {sorted(unknown)}; available: {keys}", file=sys.stderr)
            return 2
        engine.set_master_brightness(args.brightness)
        lit = []
        for group in engine.groups:
            if wanted and group.key not in wanted:
                continue
            engine.set_layers(group.key, [LayerSpec(args.effect, dict(params))])
            lit.append(f"{group.key} ({group.name}, {group.led_count} LEDs)")
        print(f"Running {args.effect} on: {', '.join(lit) or 'nothing'}. Ctrl+C to stop.")

        deadline = time.monotonic() + args.duration if args.duration > 0 else None
        while not stop.wait(0.2):  # short waits so signals are handled promptly on Windows
            if deadline is not None and time.monotonic() >= deadline:
                break
        print("Stopping...")
        print(engine.stats.summary())
    finally:
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
