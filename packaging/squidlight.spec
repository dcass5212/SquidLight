# PyInstaller spec: one-folder, windowed build of SquidLight.
# Run through packaging/build.py (it also copies OpenRGB and the licenses next to the exe).

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent  # noqa: F821 (defined by PyInstaller)

datas = [
    (str(ROOT / "rgbapp" / "layouts" / "*.json"), "rgbapp/layouts"),
    (str(ROOT / "rgbapp" / "ui" / "assets" / "*.svg"), "rgbapp/ui/assets"),
]
datas += collect_data_files("soundcard")  # its cffi header files

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=[
        "soundcard",  # imported lazily on the audio thread
        "PySide6.QtSvg",  # the stylesheet's SVG arrows need the svg image plugin
    ],
    excludes=[
        "tkinter",
        "pytest",
        "pytestqt",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtPdf",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SquidLight",
    console=False,
    icon=str(ROOT / "packaging" / "squidlight.ico"),
    version=str(ROOT / "build" / "version_info.txt"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="SquidLight", upx=False)  # noqa: F821
