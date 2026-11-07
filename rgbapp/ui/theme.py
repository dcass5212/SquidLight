"""Design tokens and the app stylesheet built from them (docs/02-ux-design.md).

The chrome stays greyscale on purpose: the only saturated colors on screen are the
accent (what is selected or on) and the LEDs themselves (what the hardware shows).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase

ASSETS = Path(__file__).resolve().parent / "assets"


@dataclass(frozen=True)
class Tokens:
    bg: str = "#121212"
    panel: str = "#1b1b1b"
    raised: str = "#242424"
    border: str = "#2e2e2e"
    hover: str = "#2c2c2c"
    track: str = "#3a3a3a"  # off toggles, empty slider tracks
    text: str = "#e8e8e8"
    muted: str = "#9a9a9a"
    accent: str = "#44d62c"
    on_accent: str = "#0e1a0b"  # text drawn on top of the accent
    # status colors stay fixed when the user changes the accent
    ok: str = "#44d62c"
    warn: str = "#e3b341"
    error: str = "#ef5b5b"
    # the inset the LED previews are drawn into: darker than the page, so light reads as light
    well: str = "#0b0b0b"
    led_off: str = "#262626"
    kb_body: str = "#141414"  # the keyboard case drawn behind the keys

    # 8px grid
    unit: int = 8
    radius: int = 6

    # type scale (px): caption, body, title, page
    caption: int = 12
    body: int = 14
    title: int = 18
    page: int = 24


T = Tokens()

TEXT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Arial"]
DISPLAY_FAMILIES = ["Segoe UI Variable Display", "Segoe UI", "Arial"]


# Accent presets offered in Settings (the first is the default). Each reads well on the
# dark chrome and stays distinct from the warn/error status colors.
ACCENT_PRESETS = (
    ("Razer green", "#44d62c"),
    ("Cyan", "#22c3e6"),
    ("Blue", "#4c8dff"),
    ("Violet", "#9b6bff"),
    ("Pink", "#ff4fa3"),
    ("Orange", "#ff8a2a"),
    ("White", "#e8e8e8"),
)


def text_on(hex_: str) -> str:
    """Text color for a label drawn on ``hex_``: near-black on light colors, white on dark."""
    c = QColor(hex_)
    luminance = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
    return "#0e1a0b" if luminance > 0.45 else "#ffffff"


def apply_accent(hex_: str) -> None:
    """Change the accent everywhere. ``T`` is shared by reference and widgets read it when
    they paint, so updating it in place (it is frozen against accidents, not this) and
    rebuilding the stylesheet is enough; the caller re-applies the QSS."""
    if not QColor.isValidColorName(hex_):
        return
    object.__setattr__(T, "accent", QColor(hex_).name())
    object.__setattr__(T, "on_accent", text_on(T.accent))


def qcolor(hex_: str, alpha: int = 255) -> QColor:
    c = QColor(hex_)
    c.setAlpha(alpha)
    return c


def health_color(health: str) -> str:
    return {"ok": T.ok, "error": T.error, "disconnected": T.error, "paused": T.muted}.get(health, T.muted)


def health_text(health: str) -> str:
    return {
        "ok": "Connected",
        "error": "Not responding, retrying",
        "disconnected": "Disconnected",
        "paused": "Paused",
    }.get(health, "Unknown")


def status_color(state: str) -> str:
    return {
        "connected": T.ok,
        "starting": T.warn,
        "reconnecting": T.warn,
        "failed": T.error,
        "paused": T.muted,
        "stopping": T.muted,
    }.get(state, T.muted)


def app_font() -> QFont:
    f = QFont()
    available = set(QFontDatabase.families())
    f.setFamilies([fam for fam in TEXT_FAMILIES if fam in available] or ["Segoe UI"])
    f.setPixelSize(T.body)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


def _families(fams: list[str]) -> str:
    return ", ".join(f'"{f}"' for f in fams)


def build_qss(t: Tokens = T) -> str:
    chevron = (ASSETS / "chevron-down.svg").as_posix()
    check = (ASSETS / "check.svg").as_posix()
    return f"""
* {{
    color: {t.text};
    font-family: {_families(TEXT_FAMILIES)};
    font-size: {t.body}px;
}}
QMainWindow, #Content {{ background: {t.bg}; }}
QToolTip {{
    background: {t.raised}; color: {t.text}; border: 1px solid {t.border};
    padding: 4px 8px; border-radius: {t.radius}px;
}}

/* sidebar */
#Sidebar {{ background: {t.panel}; border-right: 1px solid {t.border}; }}
#Wordmark {{
    font-family: {_families(DISPLAY_FAMILIES)};
    font-size: 16px; font-weight: 600; padding: 0 {t.unit}px;
}}
#SidebarSection {{ color: {t.muted}; font-size: {t.caption}px; padding: 0 {t.unit * 2}px; }}

/* header */
#Header {{ background: {t.bg}; border-bottom: 1px solid {t.border}; }}
#PageTitle {{
    font-family: {_families(DISPLAY_FAMILIES)};
    font-size: {t.page}px; font-weight: 600;
}}
#StatusChip {{
    background: {t.raised}; border: 1px solid {t.border}; border-radius: {t.radius}px;
}}
#StatusChip QLabel {{ font-size: {t.caption}px; }}

/* cards */
#Card {{
    background: {t.panel}; border: 1px solid {t.border}; border-radius: {t.radius + 2}px;
}}
#CardTitle {{
    font-family: {_families(DISPLAY_FAMILIES)};
    font-size: {t.title}px; font-weight: 600;
}}
#Muted, #CardModel, #FieldLabel {{ color: {t.muted}; }}
#CardModel, #Caption {{ font-size: {t.caption}px; }}
#Caption {{ color: {t.muted}; }}
#ValueLabel {{ color: {t.muted}; font-size: {t.caption}px; }}
#MasterBar {{
    background: {t.panel}; border: 1px solid {t.border}; border-radius: {t.radius + 2}px;
}}
#MasterLabel {{ font-weight: 600; }}
#BottomBar {{ background: {t.panel}; border-top: 1px solid {t.border}; }}
#EmptyTitle {{
    font-family: {_families(DISPLAY_FAMILIES)};
    font-size: {t.title}px; font-weight: 600;
}}
#EmptyBody {{ color: {t.muted}; }}

/* device page: layers panel */
#RightPanel {{ background: {t.panel}; border-left: 1px solid {t.border}; }}
#PanelTitle {{
    font-family: {_families(DISPLAY_FAMILIES)};
    font-size: 15px; font-weight: 600;
}}
#Rule {{ background: {t.border}; }}
#LayerRow {{ background: transparent; border: 1px solid transparent; border-radius: {t.radius}px; }}
#LayerRow:hover {{ background: {t.hover}; }}
#LayerRow[selected="true"] {{ background: {t.raised}; border-color: {t.track}; }}
#LayerName {{ font-weight: 600; }}
#LayerName:disabled {{ color: {t.muted}; }}
#AccentButton {{
    background: {t.accent}; color: {t.on_accent}; border: 1px solid {t.accent}; font-weight: 600;
    padding: 5px 12px;
}}
#AccentButton:hover {{ background: {QColor(t.accent).lighter(115).name()}; }}
#AccentButton:focus {{ border: 2px solid {t.text}; }}
#SmallButton {{ padding: 0; font-size: 16px; }}
#PanelButton {{ padding: 6px 6px; }}
#EffectLibrary {{ background: {t.panel}; }}

/* inputs */
QPushButton {{
    background: {t.raised}; border: 1px solid {t.border}; border-radius: {t.radius}px;
    padding: 6px 14px;
}}
QPushButton:hover {{ background: {t.hover}; }}
QPushButton:pressed {{ background: {t.border}; }}
QPushButton:focus {{ border-color: {t.accent}; }}
QPushButton:disabled {{ color: {t.muted}; }}
QPushButton:checked {{ background: {t.track}; border-color: {t.accent}; }}

QComboBox {{
    background: {t.raised}; border: 1px solid {t.border}; border-radius: {t.radius}px;
    padding: 5px 10px; min-height: 22px;
}}
QComboBox:hover {{ background: {t.hover}; }}
QComboBox:focus, QComboBox:on {{ border-color: {t.accent}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {t.raised}; border: 1px solid {t.border}; outline: 0; padding: 4px;
    selection-background-color: {t.hover}; selection-color: {t.text};
}}

QSlider {{ min-height: 22px; }}
QSlider::groove:horizontal {{ height: 4px; background: {t.track}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
QSlider::sub-page:horizontal:disabled {{ background: {t.muted}; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: {t.text};
}}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
QSlider::handle:horizontal:focus {{
    width: 12px; height: 12px; margin: -6px 0; border-radius: 8px;
    background: {t.text}; border: 2px solid {t.accent};
}}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.track}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {t.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; height: 0;
}}

QMenu {{
    background: {t.panel}; border: 1px solid {t.border}; border-radius: {t.radius}px; padding: 4px;
}}
QMenu::item {{ padding: 6px 24px 6px 28px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t.raised}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 8px; }}
QMenu::indicator:checked {{ image: url("{check}"); }}

QColorDialog, QColorDialog QWidget {{ background: {t.panel}; }}
QLineEdit, QSpinBox {{
    background: {t.raised}; border: 1px solid {t.border}; border-radius: 4px; padding: 3px 6px;
}}
QLineEdit:focus, QSpinBox:focus {{ border-color: {t.accent}; }}
"""
