"""Write packaging/squidlight.ico from the same drawing the app uses (ui.tray.make_icon).

    python tools/make_icon.py

The .ico holds PNG images at the usual Windows sizes (Vista+ reads PNG entries). The
packaged exe and the installer use it; the running app draws its icons itself so they
follow the accent color.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
OUT = Path(__file__).resolve().parents[1] / "packaging" / "squidlight.ico"


def render_pngs() -> list[tuple[int, bytes]]:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (needed for painting)
    from rgbapp.ui.tray import make_icon

    icon = make_icon(True)
    out = []
    for size in SIZES:
        pm = icon.pixmap(QSize(size, size))
        if pm.width() != size:
            pm = pm.scaled(size, size)
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.WriteOnly)
        pm.save(buf, "PNG")
        buf.close()
        out.append((size, bytes(data)))
    return out


def write_ico(images: list[tuple[int, bytes]], path: Path) -> None:
    header = struct.pack("<HHH", 0, 1, len(images))
    entries, blobs = b"", b""
    offset = 6 + 16 * len(images)
    for size, png in images:
        dim = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + blobs)


def main() -> int:
    write_ico(render_pngs(), OUT)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
