"""Design tokens and the app stylesheet built from them (docs/02-ux-design.md).

The idea: light in deep water. The chrome is still, cool ink and stays out of the way;
the only saturated colors on screen are the accent (what is selected or on) and the
LEDs themselves (what the hardware shows). Device previews float on the page without a
frame so their light spills onto the surface around them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase

ASSETS = Path(__file__).resolve().parent / "assets"


@dataclass(frozen=True)
class Tokens:
    # surfaces, darkest to lightest: the page, side panels and cards, controls
    bg: str = "#0e0f11"
    panel: str = "#15171a"
    raised: str = "#1d2024"
    hover: str = "#24272c"
    border: str = "#262a2f"  # hairlines
    track: str = "#353a41"  # off toggles, empty slider tracks, control outlines on hover
    text: str = "#e8eaed"
    muted: str = "#8f949b"  # labels and hints: 5:1 or better on every surface
    accent: str = "#44d62c"
    on_accent: str = "#0e1a0b"  # text drawn on top of the accent
    # status colors stay fixed when the user changes the accent
    ok: str = "#44d62c"
    warn: str = "#e3b341"
    error: str = "#ef5b5b"
    # LED previews: unlit LEDs keep the device's shape visible; the keyboard has a case
    well: str = "#0b0c0e"  # inset behind small previews (effect library)
    led_off: str = "#25282d"
    kb_body: str = "#14161a"

    # 8px grid
    unit: int = 8
    radius: int = 6
    control_h: int = 32  # buttons, combos, line edits

    # type scale (px)
    caption: int = 12
    label: int = 13
    body: int = 14
    section: int = 16
    page: int = 22


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


def mix(a: str, b: str, t: float) -> str:
    """``a`` moved ``t`` of the way to ``b`` (both '#rrggbb')."""
    ca, cb = QColor(a), QColor(b)
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
    ).name()


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
    check = (ASSETS / "check-light.svg").as_posix()  # menus: neutral, whatever the accent
    # a checkbox's tick sits on the accent: dark on light accents, white on dark ones
    tick = (ASSETS / ("check-dark.svg" if t.on_accent != "#ffffff" else "check-light.svg")).as_posix()
    display = _families(DISPLAY_FAMILIES)
    accent_hover = QColor(t.accent).lighter(112).name()
    accent_soft = mix(t.raised, t.accent, 0.16)  # selected segment / checked tool
    ctl = t.control_h
    return f"""
* {{
    color: {t.text};
    font-family: {_families(TEXT_FAMILIES)};
    font-size: {t.body}px;
}}
QMainWindow, #Content, QDialog {{ background: {t.bg}; }}
QToolTip {{
    background: {t.raised}; color: {t.text}; border: 1px solid {t.track};
    padding: 5px 8px; border-radius: {t.radius}px;
}}

/* ---- type roles */
#PageTitle {{ font-family: {display}; font-size: {t.page}px; font-weight: 600; }}
#PageSubtitle {{ color: {t.muted}; font-size: {t.label}px; }}
#CardTitle, #SectionTitle, #PanelTitle, #EmptyTitle {{
    font-family: {display}; font-size: {t.section}px; font-weight: 600;
}}
#EmptyTitle {{ font-size: 20px; }}
#SettingName, #LayerName, #MasterLabel {{ font-weight: 600; }}
#Muted, #EmptyBody {{ color: {t.muted}; }}
#FieldLabel {{ color: {t.muted}; font-size: {t.label}px; }}
#Caption, #CardModel {{ color: {t.muted}; font-size: {t.caption}px; }}
#ValueLabel {{ color: {t.text}; font-size: {t.label}px; }}
#LayerName:disabled {{ color: {t.muted}; }}

/* ---- frame */
#Sidebar {{ background: {t.panel}; border-right: 1px solid {t.border}; }}
#Brand {{ background: transparent; border-bottom: 1px solid {t.border}; }}
#Wordmark {{ font-family: {display}; font-size: 17px; font-weight: 600; }}
#SidebarSection {{ color: {t.muted}; font-size: {t.caption}px; padding: 0 {t.unit * 2}px; }}
#TitleBar {{ background: {t.panel}; border-bottom: 1px solid {t.border}; }}
#TitleBar QLabel {{ background: transparent; }}
#TitleBar #PageTitle {{ font-size: 16px; }}
#TitleBar QPushButton {{ min-height: 26px; max-height: 26px; padding: 0 12px; }}
#TitleRule {{ background: {t.border}; }}
#StatusChip {{ background: transparent; border: 1px solid transparent; border-radius: 13px; }}
#StatusChip[problem="true"] {{ background: {t.raised}; border-color: {t.track}; }}
#StatusChip QLabel {{ color: {t.muted}; font-size: {t.label}px; }}
#StatusChip[problem="true"] QLabel {{ color: {t.text}; }}
#Unsaved {{ color: {t.warn}; font-size: {t.label}px; }}
#Rule {{ background: {t.border}; }}

/* ---- surfaces */
#Card {{ background: {t.panel}; border: 1px solid {t.border}; border-radius: {t.radius + 4}px; }}
#Group {{ background: {t.panel}; border: 1px solid {t.border}; border-radius: {t.radius + 4}px; }}
#MasterBar {{ background: {t.panel}; border: 1px solid {t.border}; border-radius: {t.radius + 4}px; }}
#BottomBar {{ background: {t.bg}; border-top: 1px solid {t.border}; }}
#RightPanel {{ background: {t.panel}; border-left: 1px solid {t.border}; }}
#EffectLibrary {{ background: {t.bg}; }}

/* ---- rows that can be selected: layers, zones, rules */
#LayerRow, #ZoneRow {{ background: transparent; border: 1px solid transparent; border-radius: {t.radius}px; }}
#LayerRow:hover, #ZoneRow:hover {{ background: {t.hover}; }}
#LayerRow[selected="true"], #ZoneRow[selected="true"] {{ background: {t.raised}; border-color: {t.track}; }}
#RuleRow {{ background: transparent; border-bottom: 1px solid {t.border}; }}
#ZonePick {{
    background: transparent; border: none; text-align: left; padding: 0 {t.unit}px;
    font-weight: 600; min-height: {ctl}px;
}}
#ZonePick:hover {{ background: transparent; color: {t.text}; }}
#ZonePick:checked {{ background: transparent; color: {t.accent}; }}
#ZonePick:focus {{ border: 1px solid {t.accent}; }}

/* ---- buttons */
QPushButton {{
    background: {t.raised}; border: 1px solid {t.border}; border-radius: {t.radius}px;
    padding: 0 14px; min-height: {ctl}px;
}}
QPushButton:hover {{ background: {t.hover}; border-color: {t.track}; }}
QPushButton:pressed {{ background: {t.border}; }}
QPushButton:focus {{ border-color: {t.accent}; }}
QPushButton:disabled {{ color: {mix(t.muted, t.raised, 0.35)}; background: {t.panel}; border-color: {t.border}; }}
QPushButton:checked {{ background: {accent_soft}; border-color: {t.accent}; }}
QPushButton::menu-indicator {{
    image: url("{chevron}"); subcontrol-position: right center; subcontrol-origin: padding;
    width: 10px; height: 10px; right: 8px;
}}
#MenuButton {{ padding-right: 26px; }}
#AccentButton {{
    background: {t.accent}; color: {t.on_accent}; border: 1px solid {t.accent}; font-weight: 600;
}}
#AccentButton:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
#AccentButton:focus {{ border: 2px solid {t.text}; }}
#AccentButton:disabled {{ background: {t.raised}; color: {t.muted}; border-color: {t.border}; }}
#GhostButton {{ background: transparent; border-color: transparent; color: {t.muted}; padding: 0 10px; }}
#GhostButton:hover {{ background: {t.hover}; color: {t.text}; }}
#GhostButton:focus {{ border-color: {t.accent}; }}
#GhostButton:disabled {{ background: transparent; border-color: transparent; }}
#DangerButton {{ background: transparent; border-color: transparent; color: {t.muted}; padding: 0 10px; }}
#DangerButton:hover {{ background: {mix(t.raised, t.error, 0.14)}; color: {t.error}; }}
#DangerButton:focus {{ border-color: {t.accent}; }}
#DangerButton:disabled {{ background: transparent; border-color: transparent; }}
#SmallButton {{ padding: 0; min-height: 0; font-size: 15px; }}
#PanelButton {{ padding: 0 10px; }}

/* segmented control: buttons in a row, the checked one carries the accent */
#Segmented {{ background: {t.panel}; border: 1px solid {t.border}; border-radius: {t.radius + 2}px; }}
#Segmented QPushButton {{
    background: transparent; border: 1px solid transparent; border-radius: {t.radius}px;
    min-height: {ctl - 6}px; padding: 0 16px; color: {t.muted};
}}
#Segmented QPushButton:hover {{ color: {t.text}; }}
#Segmented QPushButton:checked {{ background: {t.raised}; color: {t.text}; border-color: {t.track}; }}
#Segmented QPushButton:focus {{ border-color: {t.accent}; }}

/* ---- inputs */
QComboBox {{
    background: {t.raised}; border: 1px solid {t.border}; border-radius: {t.radius}px;
    padding: 0 10px; min-height: {ctl}px;
}}
QComboBox:hover {{ border-color: {t.track}; }}
QComboBox:focus, QComboBox:on {{ border-color: {t.accent}; }}
QComboBox:disabled {{ color: {t.muted}; background: {t.panel}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {t.raised}; border: 1px solid {t.track}; outline: 0; padding: 4px;
    selection-background-color: {t.hover}; selection-color: {t.text};
}}
QLineEdit, QSpinBox, QTimeEdit {{
    background: {t.raised}; border: 1px solid {t.border}; border-radius: {t.radius}px;
    padding: 0 8px; min-height: {ctl}px; selection-background-color: {accent_soft};
}}
QLineEdit:hover, QSpinBox:hover, QTimeEdit:hover {{ border-color: {t.track}; }}
QLineEdit:focus, QSpinBox:focus, QTimeEdit:focus {{ border-color: {t.accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QTimeEdit:disabled {{ color: {t.muted}; background: {t.panel}; }}
QSpinBox::up-button, QSpinBox::down-button, QTimeEdit::up-button, QTimeEdit::down-button {{ width: 0; border: none; }}
QSpinBox, QTimeEdit {{ padding-right: 8px; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px; border: 1px solid {t.track}; background: {t.raised};
}}
QCheckBox::indicator:hover {{ border-color: {t.muted}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url("{tick}"); }}
QCheckBox:focus {{ color: {t.text}; }}

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
QSlider::handle:horizontal:disabled {{ background: {t.muted}; }}

QListWidget {{
    background: {t.bg}; border: 1px solid {t.border}; border-radius: {t.radius}px; outline: 0; padding: 4px;
}}
QListWidget:focus {{ border-color: {t.accent}; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 4px; color: {t.text}; }}
QListWidget::item:hover {{ background: {t.hover}; }}
QListWidget::item:selected {{ background: {t.raised}; color: {t.text}; }}

QPlainTextEdit {{
    background: {t.bg}; border: 1px solid {t.border}; border-radius: {t.radius}px;
    selection-background-color: {accent_soft};
}}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.track}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {t.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.track}; border-radius: 3px; min-width: 32px; }}
QScrollBar::handle:horizontal:hover {{ background: {t.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none; height: 0; width: 0;
}}

QMenu {{
    background: {t.raised}; border: 1px solid {t.track}; border-radius: {t.radius}px; padding: 4px;
}}
QMenu::item {{ padding: 6px 24px 6px 28px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t.hover}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 8px; }}
QMenu::indicator:checked {{ image: url("{check}"); }}

QColorDialog, QColorDialog QWidget {{ background: {t.panel}; }}
QMessageBox, QInputDialog {{ background: {t.panel}; }}
"""
