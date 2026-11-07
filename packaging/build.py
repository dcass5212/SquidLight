"""Build SquidLight: PyInstaller one-folder app, bundled OpenRGB, licenses, installer.

    python packaging/build.py                 # dist/SquidLight/ and dist/SquidLight-Setup-<version>.exe
    python packaging/build.py --no-installer  # just the folder

Needs: the ``build`` extra (``pip install -e .[gui,audio,build]``), OpenRGB unzipped in
``vendor/openrgb/`` with its version in ``vendor/OPENRGB_VERSION``, and Inno Setup 6 for the
installer (found on PATH, in Program Files, or in %LOCALAPPDATA%\\Programs; or set ISCC).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_DIR = DIST / "SquidLight"
VENDOR = ROOT / "vendor"


def version() -> str:
    ns: dict = {}
    exec((ROOT / "rgbapp" / "__init__.py").read_text(encoding="utf-8"), ns)
    return ns["__version__"]


def write_version_info(ver: str) -> None:
    """The exe's Properties > Details tab."""
    nums = [int(x) for x in ver.split(".")[:3]] + [0]
    while len(nums) < 4:
        nums.append(0)
    t = tuple(nums[:4])
    BUILD.mkdir(exist_ok=True)
    (BUILD / "version_info.txt").write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'SquidLight'),
      StringStruct('FileDescription', 'SquidLight RGB lighting'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('ProductVersion', '{ver}'),
      StringStruct('InternalName', 'SquidLight'),
      StringStruct('OriginalFilename', 'SquidLight.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )


def run(args: list, **kw) -> None:
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, **kw)


def find_iscc() -> Path | None:
    candidates = [os.environ.get("ISCC", "")]
    found = shutil.which("ISCC")
    candidates.append(found or "")
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            candidates.append(os.path.join(base, "Inno Setup 6", "ISCC.exe"))
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-installer", action="store_true")
    args = ap.parse_args()

    ver = version()
    openrgb = VENDOR / "openrgb"
    if not (openrgb / "OpenRGB.exe").is_file():
        print(f"error: {openrgb / 'OpenRGB.exe'} not found (see docs/04-hardware-and-setup.md)", file=sys.stderr)
        return 1
    openrgb_version = (VENDOR / "OPENRGB_VERSION").read_text(encoding="utf-8").strip()

    if not (ROOT / "packaging" / "squidlight.ico").is_file():
        run([sys.executable, ROOT / "tools" / "make_icon.py"])
    write_version_info(ver)

    run([
        sys.executable, "-m", "PyInstaller", ROOT / "packaging" / "squidlight.spec",
        "--noconfirm", "--clean", "--distpath", DIST, "--workpath", BUILD / "pyinstaller",
    ])

    # OpenRGB beside the exe, unmodified, as a separate program (GPL): <app>\openrgb\OpenRGB.exe
    target = APP_DIR / "openrgb"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(openrgb, target)
    (target / "OPENRGB_VERSION").write_text(openrgb_version + "\n", encoding="utf-8")
    licenses = APP_DIR / "LICENSES"
    if licenses.exists():
        shutil.rmtree(licenses)
    shutil.copytree(ROOT / "LICENSES", licenses)
    print(f"built {APP_DIR} (SquidLight {ver}, OpenRGB {openrgb_version})")

    if args.no_installer:
        return 0
    iscc = find_iscc()
    if iscc is None:
        print("Inno Setup 6 not found: skipped the installer (set ISCC or install it)", file=sys.stderr)
        return 1
    run([iscc, f"/DAppVersion={ver}", f"/DSourceDir={APP_DIR}", f"/O{DIST}", ROOT / "packaging" / "squidlight.iss"])
    print(f"installer: {DIST / f'SquidLight-Setup-{ver}.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
