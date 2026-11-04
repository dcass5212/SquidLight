"""Find which motherboard ARGB header drives which case lights/fans.

Lights ONE addressable header at a time so you can see what reacts:
header 0 -> RED, header 1 -> GREEN, header 2 -> BLUE.
LEDs are drawn in blocks of --block (default 10): the header's color, then white,
alternating. Count the blocks on a strip to estimate its real length.

Needs the OpenRGB server running (elevated) and SignalRGB/Armoury Crate/iCUE closed.
Temporary: it resizes the board's ARGB zones, and puts them back to their original
size at the end (use --keep to leave the test length in place).

    python tools/header_test.py                  # all headers, 8 s each, 60 LEDs
    python tools/header_test.py --header 1       # just header 1 (zero-based)
    python tools/header_test.py --length 120 --seconds 15
"""

from __future__ import annotations

import argparse
import sys
import time

from openrgb import OpenRGBClient
from openrgb.utils import DeviceType, RGBColor

COLORS = [
    ("RED", RGBColor(255, 0, 0)),
    ("GREEN", RGBColor(0, 255, 0)),
    ("BLUE", RGBColor(0, 0, 255)),
]
WHITE = RGBColor(255, 255, 255)
OFF = RGBColor(0, 0, 0)


def find_board(client: OpenRGBClient):
    for dev in client.devices:
        if dev.type == DeviceType.MOTHERBOARD:
            return dev
    raise SystemExit("No motherboard device found in OpenRGB.")


def pattern(n: int, color: RGBColor, block: int) -> list[RGBColor]:
    return [color if (i // block) % 2 == 0 else WHITE for i in range(n)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6742)
    ap.add_argument("--header", type=int, help="only this zone index (zero-based)")
    ap.add_argument("--length", type=int, default=60, help="test LED count per header")
    ap.add_argument("--block", type=int, default=10, help="LEDs per color block")
    ap.add_argument("--seconds", type=float, default=8.0, help="how long to hold each header")
    ap.add_argument("--keep", action="store_true", help="don't restore the original zone sizes")
    args = ap.parse_args()

    try:
        client = OpenRGBClient(address=args.host, port=args.port, name="rgbapp-header-test")
    except OSError as exc:
        print(f"Could not connect to OpenRGB at {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1

    board = find_board(client)
    zones = board.zones
    original = [len(z.leds) for z in zones]
    print(f"Board: {board.name}")
    for i, z in enumerate(zones):
        print(f"  zone {i}: {z.name!r} (original size {original[i]})")

    targets = [args.header] if args.header is not None else list(range(len(zones)))
    try:
        board.set_mode("Direct")
        for i in targets:
            zone = zones[i]
            name, color = COLORS[i % len(COLORS)]
            zone.resize(args.length)
            zone.set_colors(pattern(len(zone.leds), color, args.block))
            print(
                f"\n>>> {zone.name!r} (zone {i}) is {name}, {len(zone.leds)} LEDs, "
                f"blocks of {args.block}. Watch what lights up for {args.seconds:g}s...",
                flush=True,
            )
            time.sleep(args.seconds)
            zone.set_colors([OFF] * len(zone.leds))
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        # Always turn everything off and put sizes back.
        for i in targets:
            zone = zones[i]
            try:
                zone.set_colors([OFF] * len(zone.leds))
                if not args.keep:
                    zone.resize(original[i])
            except Exception as exc:  # best effort on the way out
                print(f"Cleanup warning for zone {i}: {exc}", file=sys.stderr)
        try:
            client.disconnect()
        except Exception:
            pass
    print("\nDone." + (" (test sizes kept)" if args.keep else " Zone sizes restored."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
