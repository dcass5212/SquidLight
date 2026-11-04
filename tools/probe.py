"""M0 probe: dump every OpenRGB device, mode, zone and LED name.

Read-only: connects to the OpenRGB SDK server, never changes modes or colors.
Output goes to stdout and to docs/device-dump.txt (override with --out).

Start the server first (elevated):  OpenRGB.exe --server --startminimized
Then:                               python tools/probe.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from openrgb import OpenRGBClient

REPO = Path(__file__).resolve().parent.parent


def _name(value) -> str:
    """Enum -> its name, anything else -> str."""
    return getattr(value, "name", str(value))


def dump_device(index: int, dev, out: list[str]) -> None:
    meta = getattr(dev, "metadata", None)
    out.append("=" * 72)
    out.append(f"[{index}] {dev.name}")
    out.append(f"    type:        {_name(dev.type)}")
    if meta is not None:
        for field in ("vendor", "description", "version", "serial", "location"):
            out.append(f"    {field + ':':<12} {getattr(meta, field, '')!r}")

    active = getattr(dev, "active_mode", None)
    modes = list(getattr(dev, "modes", []))
    active_name = modes[active].name if isinstance(active, int) and 0 <= active < len(modes) else active
    out.append(f"    active mode: {active_name}")
    out.append(f"    modes ({len(modes)}): " + ", ".join(m.name for m in modes))

    zones = list(getattr(dev, "zones", []))
    out.append(f"    zones ({len(zones)}):")
    for z_idx, zone in enumerate(zones):
        n = len(zone.leds)
        seg = f"  segments={len(zone.segments)}" if getattr(zone, "segments", None) else ""
        extra = ""
        if getattr(zone, "mat_height", 0) and getattr(zone, "mat_width", 0):
            extra = f"  matrix={zone.mat_height}x{zone.mat_width}"
        out.append(
            f"      zone {z_idx}: {zone.name!r} type={_name(zone.type)} "
            f"leds={n} min={getattr(zone, 'leds_min', '?')} max={getattr(zone, 'leds_max', '?')}"
            f"{extra}{seg}"
        )

    leds = list(getattr(dev, "leds", []))
    out.append(f"    leds ({len(leds)}):")
    for l_idx, led in enumerate(leds):
        out.append(f"      {l_idx:>4}: {led.name}")


def build_dump(host: str, port: int) -> list[str]:
    client = OpenRGBClient(address=host, port=port, name="rgbapp-probe")
    out: list[str] = []
    out.append(f"OpenRGB device dump  ({datetime.now().isoformat(timespec='seconds')})")
    out.append(f"server: {host}:{port}")
    proto = getattr(client, "protocol_version", None) or getattr(
        getattr(client, "comms", None), "protocol_version", None
    )
    out.append(f"SDK protocol version: {proto}")
    devices = list(client.devices)
    out.append(f"devices found: {len(devices)}")
    for i, dev in enumerate(devices):
        dump_device(i, dev, out)
    out.append("=" * 72)
    try:
        client.disconnect()
    except Exception:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6742)
    ap.add_argument("--out", type=Path, default=REPO / "docs" / "device-dump.txt")
    args = ap.parse_args()

    try:
        lines = build_dump(args.host, args.port)
    except (ConnectionRefusedError, OSError) as exc:
        print(
            f"Could not connect to OpenRGB at {args.host}:{args.port}: {exc}\n"
            "Start it first (as Administrator): OpenRGB.exe --server --startminimized",
            file=sys.stderr,
        )
        return 1

    text = "\n".join(lines) + "\n"
    print(text)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
