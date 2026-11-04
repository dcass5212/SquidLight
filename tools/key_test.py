"""Check that OpenRGB's keyboard LED names match the physical keys.

Lights the keyboard by OpenRGB LED *name*, using our layout file:

  rows (default)  each key row gets its own color, top to bottom:
                  top bar WHITE, F-row RED, number row ORANGE, Q-row YELLOW,
                  A-row GREEN, Z-row CYAN, bottom row BLUE.
                  If names match the hardware you see clean horizontal stripes.
                  Diagonal or scattered stripes mean OpenRGB's LED order is off.
  --keys          light only the named keys, each in its own color, e.g.
                  --keys Escape,Q,A,Z,Space,"Number Pad 5"
  --index         light LED numbers, e.g. --index 5,47,68

Quit SquidLight first (it would overwrite the test), and keep OpenRGB running
elevated. The keyboard's original mode is restored at the end.

    python tools/key_test.py
    python tools/key_test.py --keys Escape,Q,A,Z --seconds 30
    python tools/key_test.py --dry-run          # print the plan, touch nothing
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rgbapp.engine.layout import match_layout  # noqa: E402

ROW_COLORS = [
    ("WHITE", (255, 255, 255)),
    ("RED", (255, 0, 0)),
    ("ORANGE", (255, 110, 0)),
    ("YELLOW", (255, 230, 0)),
    ("GREEN", (0, 255, 0)),
    ("CYAN", (0, 220, 255)),
    ("BLUE", (0, 0, 255)),
]
KEY_COLORS = ROW_COLORS[1:] + [("MAGENTA", (255, 0, 255)), ("PINK", (255, 120, 160))]
ROW_NAMES = ["top bar", "F-row", "number row", "Q-row", "A-row", "Z-row", "bottom row"]


def plan_rows(names: list[str]) -> tuple[list[tuple[int, int, int]], list[str]]:
    lay = match_layout(names)
    if lay is None:
        raise SystemExit("No layout file fits this keyboard.")
    colors = [(0, 0, 0)] * len(names)
    lines = []
    rows: dict[int, list[int]] = {}
    for i, (_, cy) in enumerate(lay.centers()):
        if i in lay.unmatched:
            continue
        rows.setdefault(int(math.floor(cy)), []).append(i)
    for r, idx in sorted(rows.items()):
        label, rgb = ROW_COLORS[min(r, len(ROW_COLORS) - 1)]
        for i in idx:
            colors[i] = rgb
        keys = ", ".join(names[i].removeprefix("Key: ") for i in sorted(idx, key=lambda i: lay.rects[i, 0]))
        lines.append(f"  {label:<7} {ROW_NAMES[r] if r < len(ROW_NAMES) else f'row {r}'}: {keys}")
    return colors, lines


def plan_pick(names: list[str], keys: list[str], indices: list[int]) -> tuple[list[tuple[int, int, int]], list[str]]:
    colors = [(0, 0, 0)] * len(names)
    lines = []
    picks = [(k, [i for i, n in enumerate(names) if n in (k, f"Key: {k}")]) for k in keys]
    picks += [(f"LED {i} ({names[i]})", [i]) for i in indices if 0 <= i < len(names)]
    for n, (what, idx) in enumerate(picks):
        label, rgb = KEY_COLORS[n % len(KEY_COLORS)]
        if not idx:
            lines.append(f"  (no LED named {what!r})")
            continue
        for i in idx:
            colors[i] = rgb
        lines.append(f"  {label:<7} {what}  (LED {', '.join(map(str, idx))})")
    return colors, lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6742)
    ap.add_argument("--keys", default="", help="comma-separated key names (without 'Key: ')")
    ap.add_argument("--index", default="", help="comma-separated LED numbers")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--dry-run", action="store_true", help="print what would light; don't touch the keyboard")
    args = ap.parse_args()

    from openrgb import OpenRGBClient
    from openrgb.utils import DeviceType, RGBColor

    try:
        client = OpenRGBClient(address=args.host, port=args.port, name="rgbapp-key-test")
    except OSError as exc:
        print(f"Could not connect to OpenRGB at {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1
    try:
        kb = next((d for d in client.devices if d.type == DeviceType.KEYBOARD), None)
        if kb is None:
            print("No keyboard found in OpenRGB.", file=sys.stderr)
            return 1
        names = [led.name for led in kb.leds]
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        indices = [int(i) for i in args.index.split(",") if i.strip()]
        colors, lines = plan_pick(names, keys, indices) if keys or indices else plan_rows(names)
        print(f"{kb.name}, {len(names)} LEDs")
        print("\n".join(lines))
        if args.dry_run:
            return 0

        prev_mode = int(kb.active_mode)
        try:
            kb.set_mode("Direct")
            kb.set_colors([RGBColor(*c) for c in colors])
            print(f"\nLit for {args.seconds:g}s. Look at the keyboard (Ctrl+C to stop early)...", flush=True)
            time.sleep(args.seconds)
        except KeyboardInterrupt:
            print("\nInterrupted.")
        finally:
            try:
                kb.set_colors([RGBColor(0, 0, 0)] * len(names))
                kb.set_mode(prev_mode)
            except Exception as exc:
                print(f"Cleanup warning: {exc}", file=sys.stderr)
        print("Done. Keyboard mode restored.")
        return 0
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
