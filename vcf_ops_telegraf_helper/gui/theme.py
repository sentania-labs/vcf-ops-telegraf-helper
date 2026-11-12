"""Lattice design system tokens translated to Qt Style Sheets (QSS).

Derived from sentania-labs/lattice design tokens (tokens.json).
Adheres to the core Lattice principles:
- Borders, not shadows (no drop shadows or elevation).
- One accent for primary actions, focus, and selection.
- ok, warn, bad carry operational state and nothing else.
- Density is prioritized for infrastructure administration.
"""

from __future__ import annotations

from pathlib import Path

DARK_TOKENS = {
    "bg": "#1b1f24",
    "surface": "#23282f",
    "surface-sunken": "#2a3038",
    "line": "#363d47",
    "line-soft": "#2a3038",
    "ink": "#f2f4f7",
    "ink-muted": "#b7bfca",
    "ink-subtle": "#868f9c",
    "accent": "#3987e5",
    "accent-strong": "#5a9cec",
    "on-accent": "#0b1220",
    "link": "#5a9cec",
    "accent-soft": "rgba(57,135,229,0.16)",
    "accent-ink": "#8fbcf5",
    "ok": "#199e70",
    "ok-soft": "rgba(25,158,112,0.16)",
    "ok-ink": "#67c9a2",
    "warn": "#e0a400",
    "warn-soft": "rgba(224,164,0,0.09)",
    "warn-ink": "#e0a400",
    "bad": "#c64e1e",
    "bad-soft": "rgba(217,89,38,0.12)",
    "bad-ink": "#ea8c64",
    "font-family": "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
    "font-mono": "ui-monospace, Menlo, Consolas, monospace",
}

LIGHT_TOKENS = {
    "bg": "#f6f7f9",
    "surface": "#ffffff",
    "surface-sunken": "#eef1f5",
    "line": "#d9dee5",
    "line-soft": "#eef1f5",
    "ink": "#1d2430",
    "ink-muted": "#5a6676",
    "ink-subtle": "#6c7788",
    "accent": "#1f5fbf",
    "accent-strong": "#1a4fa0",
    "on-accent": "#ffffff",
    "link": "#1f5fbf",
    "accent-soft": "#e8f0fc",
    "accent-ink": "#12376f",
    "ok": "#1a7f4b",
    "ok-soft": "#e6f4ec",
    "ok-ink": "#14532d",
    "warn": "#8a6100",
    "warn-soft": "#fdf3d8",
    "warn-ink": "#8a6100",
    "bad": "#a22c1c",
    "bad-soft": "#fbe9e6",
    "bad-ink": "#7f1d1d",
    "font-family": "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
    "font-mono": "ui-monospace, Menlo, Consolas, monospace",
}


def tokens(theme: str = "dark") -> dict:
    """The colour tokens of a theme, for the few places that colour a word inside rich text."""
    return DARK_TOKENS if theme == "dark" else LIGHT_TOKENS


def build_stylesheet(theme: str = "dark") -> str:
    """Generate Qt Style Sheet (QSS) adhering to Lattice tokens."""
    t = DARK_TOKENS if theme == "dark" else LIGHT_TOKENS

    chevron_file = "chevron_down_dark.svg" if theme == "dark" else "chevron_down_light.svg"
    chevron_path = (Path(__file__).parent / "assets" / chevron_file).as_posix()

    return f"""
/* Global Application Reset */
QWidget {{
    background-color: {t["bg"]};
    color: {t["ink"]};
    font-family: {t["font-family"]};
    font-size: 13px;
    selection-background-color: {t["accent"]};
    selection-color: #ffffff;
}}

QMainWindow {{
    background-color: {t["bg"]};
}}

QLabel, QCheckBox, QRadioButton {{
    background: transparent;
}}

QWidget.clear, QScrollArea.clear, QScrollArea.clear > QWidget > QWidget {{
    background: transparent;
    border: none;
}}

/* Scroll bars */
QScrollBar:vertical {{
    background: {t["bg"]};
    width: 8px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {t["line"]};
    min-height: 20px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t["ink-subtle"]};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

/* Containers and Cards (radius-xl: 9px, line border, no shadow) */
QFrame.lattice-card {{
    background-color: {t["surface"]};
    border: 1px solid {t["line"]};
    border-radius: 9px;
}}

QFrame.lattice-header {{
    background-color: {t["surface"]};
    border-bottom: 1px solid {t["line"]};
    padding: 10px 16px;
}}

QFrame.lattice-sidebar {{
    background-color: {t["surface"]};
    border-right: 1px solid {t["line"]};
}}

/* Typography */
QLabel.lattice-title {{
    font-size: 17px;
    font-weight: 650;
    color: {t["ink"]};
}}

QLabel.lattice-section-label {{
    font-size: 12px;
    font-weight: 650;
    color: {t["ink-subtle"]};
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}

QLabel.lattice-muted {{
    color: {t["ink-muted"]};
    font-size: 12px;
}}

QLabel.lattice-caption {{
    color: {t["ink-subtle"]};
    font-size: 12px;
}}

/* Buttons (radius-lg: 6px) */
QPushButton {{
    background-color: {t["surface"]};
    color: {t["ink"]};
    border: 1px solid {t["line"]};
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 13px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {t["surface-sunken"]};
    border-color: {t["ink-subtle"]};
}}

QPushButton:pressed {{
    background-color: {t["surface-sunken"]};
}}

QPushButton:disabled {{
    color: {t["ink-subtle"]};
    border-color: {t["line"]};
    background-color: {t["surface"]};
}}

QPushButton.primary {{
    background-color: {t["accent"]};
    color: {t["on-accent"]};
    border: 1px solid {t["accent"]};
    font-weight: 600;
}}

QPushButton.primary:hover {{
    background-color: {t["accent-strong"]};
    border-color: {t["accent-strong"]};
}}

QPushButton.primary:disabled {{
    background-color: {t["line"]};
    border-color: {t["line"]};
    color: {t["ink-subtle"]};
}}

QPushButton.danger {{
    background-color: {t["bad"]};
    color: #ffffff;
    border: 1px solid {t["bad"]};
    font-weight: 600;
}}

QPushButton.big {{
    font-size: 14px;
    padding: 7px 16px;
}}

QPushButton.sm {{
    font-size: 12px;
    padding: 4px 9px;
}}

QPushButton.ghost {{
    border-color: transparent;
    background: transparent;
    color: {t["ink-muted"]};
    padding: 4px 7px;
}}

QPushButton.ghost:hover {{
    border-color: {t["line"]};
    background-color: {t["surface"]};
    color: {t["ink"]};
}}

/* A link that does something: underlined text, no button frame */
QPushButton.link {{
    border: none;
    background: transparent;
    color: {t["link"]};
    text-decoration: underline;
    padding: 0px;
    font-size: 12px;
    font-weight: 400;
    text-align: left;
}}

QPushButton.link:hover {{
    color: {t["ink"]};
    background: transparent;
}}

QPushButton.link:focus, QPushButton.namebtn:focus {{
    border: 1px solid {t["accent"]};
    border-radius: 3px;
}}

QPushButton.namebtn {{
    border: none;
    background: transparent;
    color: {t["ink"]};
    padding: 0px;
    font-weight: 600;
    text-align: left;
}}

QPushButton.namebtn:hover {{
    text-decoration: underline;
    background: transparent;
}}

/* Text Inputs (radius-lg: 6px) */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {t["surface-sunken"]};
    border: 1px solid {t["line"]};
    border-radius: 6px;
    color: {t["ink"]};
    padding: 6px 8px;
    font-size: 13px;
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {t["accent"]};
    background-color: {t["surface"]};
}}

QLineEdit.bad {{
    border: 2px solid {t["bad"]};
    padding: 5px 7px;
}}

QLabel.readonly-field {{
    background-color: {t["surface-sunken"]};
    border: 1px solid {t["line"]};
    border-radius: 6px;
    padding: 6px 8px;
}}

/* Combo Boxes (radius-lg: 6px, clear pulldown chevron indicator) */
QComboBox {{
    background-color: {t["surface-sunken"]};
    border: 1px solid {t["line"]};
    border-radius: 6px;
    color: {t["ink"]};
    padding: 6px 28px 6px 10px;
    font-size: 13px;
    min-height: 20px;
}}

QComboBox.sm {{
    padding: 2px 26px 2px 8px;
    font-size: 12px;
    min-height: 18px;
}}

QComboBox:hover {{
    border: 1px solid {t["ink-subtle"]};
}}

QComboBox:focus {{
    border: 1px solid {t["accent"]};
    background-color: {t["surface"]};
}}

QComboBox:disabled {{
    color: {t["ink-subtle"]};
    border-color: {t["line"]};
}}

QComboBox:editable {{
    background-color: {t["surface-sunken"]};
}}

QComboBox QLineEdit {{
    border: none;
    background: transparent;
    padding: 0px 4px;
    color: {t["ink"]};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid {t["line"]};
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    background-color: transparent;
}}

QComboBox::drop-down:hover {{
    background-color: {t["line-soft"]};
}}

QComboBox::down-arrow {{
    image: url("{chevron_path}");
    width: 12px;
    height: 12px;
}}

QComboBox::down-arrow:disabled {{
    image: url("{chevron_path}");
}}

QComboBox QAbstractItemView {{
    background-color: {t["surface"]};
    border: 1px solid {t["line"]};
    border-radius: 6px;
    selection-background-color: {t["accent"]};
    selection-color: #ffffff;
    padding: 4px;
    outline: none;
}}

QComboBox QAbstractItemView::item {{
    min-height: 24px;
    padding: 4px 8px;
    border-radius: 4px;
    color: {t["ink"]};
}}

QComboBox QAbstractItemView::item:hover {{
    background-color: {t["surface-sunken"]};
    color: {t["ink"]};
}}

QComboBox QAbstractItemView::item:selected {{
    background-color: {t["accent"]};
    color: #ffffff;
}}

/* Checkboxes */
QCheckBox {{
    color: {t["ink"]};
    spacing: 8px;
    font-size: 13px;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {t["line"]};
    border-radius: 4px;
    background-color: {t["surface-sunken"]};
}}

QCheckBox::indicator:checked {{
    background-color: {t["accent"]};
    border-color: {t["accent"]};
    image: none;
}}

/* Radio Buttons */
QRadioButton {{
    color: {t["ink"]};
    spacing: 8px;
}}

QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {t["line"]};
    border-radius: 8px;
    background-color: {t["surface-sunken"]};
}}

QRadioButton::indicator:checked {{
    background-color: {t["accent"]};
    border-color: {t["accent"]};
}}

/* Code blocks (monospace, surface-sunken, line border) */
QPlainTextEdit.code-block {{
    font-family: {t["font-mono"]};
    font-size: 12px;
    background-color: {t["surface-sunken"]};
    border: 1px solid {t["line"]};
    border-radius: 6px;
    color: {t["ink"]};
}}

/* Status Banners and Badges */
QFrame.status-badge-ok {{
    background-color: {t["ok-soft"]};
    border: 1px solid {t["ok"]};
    border-radius: 4px;
}}
QLabel.status-badge-ok-text {{
    color: {t["ok-ink"]};
    font-weight: 600;
    font-size: 12px;
}}

QFrame.status-badge-warn {{
    background-color: {t["warn-soft"]};
    border: 1px solid {t["warn"]};
    border-radius: 4px;
}}
QLabel.status-badge-warn-text {{
    color: {t["warn-ink"]};
    font-weight: 600;
    font-size: 12px;
}}

QFrame.status-badge-bad {{
    background-color: {t["bad-soft"]};
    border: 1px solid {t["bad"]};
    border-radius: 4px;
}}
QLabel.status-badge-bad-text {{
    color: {t["bad-ink"]};
    font-weight: 600;
    font-size: 12px;
}}

/* Navigation step list */
QListWidget.step-list {{
    background-color: transparent;
    border: none;
    outline: none;
}}

QListWidget.step-list::item {{
    padding: 10px 14px;
    border-radius: 6px;
    color: {t["ink-muted"]};
    font-weight: 500;
    margin-bottom: 4px;
}}

QListWidget.step-list::item:selected {{
    background-color: {t["accent-soft"]};
    color: {t["accent-ink"]};
    border-left: 3px solid {t["accent"]};
    font-weight: 600;
}}

QListWidget.step-list::item:hover:!selected {{
    background-color: {t["surface-sunken"]};
    color: {t["ink"]};
}}

QListWidget.step-list::item:disabled {{
    color: {t["ink-subtle"]};
    font-style: italic;
    background-color: transparent;
}}
/* ---------------------------------------------------------------------------------------
   Redesign (machines, guided paths, activity, settings)
   --------------------------------------------------------------------------------------- */
QFrame.side {{
    background-color: {t["surface"]};
    border: none;
    border-right: 1px solid {t["line"]};
}}

QFrame.foot {{
    background-color: {t["surface"]};
    border: none;
    border-top: 1px solid {t["line"]};
}}

QFrame.page, QWidget.page, QScrollArea.page-scroll {{
    background-color: {t["bg"]};
    border: none;
}}

QLabel.brand {{ font-size: 14px; font-weight: 700; color: {t["ink"]}; }}
QLabel.brand-sub {{ font-size: 12px; color: {t["ink-subtle"]}; }}
QLabel.h1 {{ font-size: 20px; font-weight: 650; color: {t["ink"]}; }}
QLabel.h1-big {{ font-size: 22px; font-weight: 650; color: {t["ink"]}; }}
QLabel.h2 {{ font-size: 14px; font-weight: 650; color: {t["ink"]}; }}
QLabel.h2-big {{ font-size: 16px; font-weight: 650; color: {t["ink"]}; }}
QLabel.sub {{ font-size: 13px; color: {t["ink-muted"]}; }}
QLabel.small {{ font-size: 12px; color: {t["ink-muted"]}; }}
QLabel.plain {{ font-size: 13px; color: {t["ink"]}; }}
QLabel.hint {{ font-size: 12px; color: {t["ink-muted"]}; }}
QLabel.hint-bad {{ font-size: 12px; font-weight: 600; color: {t["bad-ink"]}; }}
QLabel.saved {{ font-size: 12px; font-weight: 600; color: {t["ok-ink"]}; }}
QLabel.field-label {{ font-size: 12px; font-weight: 600; color: {t["ink"]}; }}
QLabel.note {{ font-size: 12px; color: {t["ink-muted"]}; }}
QLabel.card-title, QLabel.grp-h, QLabel.flow-h {{
    font-size: 12px;
    font-weight: 650;
    color: {t["ink-subtle"]};
    letter-spacing: 0.7px;
}}
QLabel.flow-what {{ font-size: 13px; font-weight: 600; color: {t["ink"]}; }}
QLabel.mono {{ font-family: {t["font-mono"]}; font-size: 12px; }}

/* Main navigation */
QPushButton.nav {{
    border: none;
    border-left: 3px solid transparent;
    border-radius: 6px;
    background: transparent;
    color: {t["ink-muted"]};
    font-size: 13px;
    font-weight: 400;
    padding: 8px 10px;
    text-align: left;
}}
QPushButton.nav:hover {{ background-color: {t["surface-sunken"]}; color: {t["ink"]}; }}
QPushButton.nav:checked {{
    background-color: {t["accent-soft"]};
    color: {t["accent-ink"]};
    border-left: 3px solid {t["accent"]};
    font-weight: 600;
}}
QLabel.nav-badge {{
    background-color: {t["bad-soft"]};
    color: {t["bad-ink"]};
    border-radius: 8px;
    min-height: 16px;
    padding: 0px 7px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton.conn {{
    border: 1px solid {t["line"]};
    border-radius: 6px;
    background: transparent;
    color: {t["ink-muted"]};
    font-size: 12px;
    font-weight: 400;
    padding: 7px 9px;
    text-align: left;
}}
QPushButton.conn:hover {{ border-color: {t["ink-subtle"]}; background: transparent; }}

/* Steps of a guided path */
QFrame.step {{ border: none; border-radius: 6px; background: transparent; }}
QFrame.step-now {{ border: none; border-radius: 6px; background-color: {t["accent-soft"]}; }}
QLabel.step-text {{ font-size: 13px; color: {t["ink-subtle"]}; }}
QLabel.step-text-done {{ font-size: 13px; color: {t["ink-muted"]}; }}
QLabel.step-text-now {{ font-size: 13px; font-weight: 600; color: {t["accent-ink"]}; }}
QLabel.num {{
    border: 1px solid {t["ink-subtle"]};
    border-radius: 10px;
    color: {t["ink-subtle"]};
    font-size: 12px;
}}
QLabel.num-done {{
    border: 1px solid {t["ok"]};
    border-radius: 10px;
    background-color: {t["ok-soft"]};
    color: {t["ok-ink"]};
    font-size: 12px;
}}
QLabel.num-now {{
    border: 1px solid {t["accent"]};
    border-radius: 10px;
    background-color: {t["accent"]};
    color: {t["on-accent"]};
    font-size: 12px;
    font-weight: 600;
}}

/* Filter chips */
QPushButton.chip {{
    border: 1px solid transparent;
    border-radius: 11px;
    min-height: 16px;
    background-color: {t["surface-sunken"]};
    color: {t["ink-muted"]};
    font-size: 12px;
    font-weight: 400;
    padding: 3px 8px;
}}
QPushButton.chip:hover {{ border-color: {t["ink-subtle"]}; }}
QPushButton.chip:checked {{
    background-color: {t["accent-soft"]};
    color: {t["accent-ink"]};
    font-weight: 600;
}}

/* Status pills: colour carries state and nothing else */
QLabel.pill {{
    border-radius: 9px;
    min-height: 15px;
    padding: 2px 10px 2px 8px;
    font-size: 12px;
    background-color: {t["surface-sunken"]};
    color: {t["ink-muted"]};
}}
QLabel.pill-ok {{ background-color: {t["ok-soft"]}; color: {t["ok-ink"]}; }}
QLabel.pill-bad {{ background-color: {t["bad-soft"]}; color: {t["bad-ink"]}; }}
QLabel.pill-warn {{ background-color: {t["warn-soft"]}; color: {t["warn-ink"]}; }}
QLabel.pill-acc {{ background-color: {t["accent-soft"]}; color: {t["accent-ink"]}; }}

QLabel.tag {{
    border-radius: 8px;
    min-height: 15px;
    padding: 1px 9px;
    font-size: 12px;
    background-color: {t["surface-sunken"]};
    color: {t["ink-muted"]};
}}
QLabel.tag-add {{ background-color: {t["ok-soft"]}; color: {t["ok-ink"]}; }}
QLabel.tag-rem {{ background-color: {t["bad-soft"]}; color: {t["bad-ink"]}; text-decoration: line-through; }}
QLabel.found {{
    border-radius: 7px;
    min-height: 14px;
    padding: 0px 7px;
    font-size: 12px;
    background-color: {t["ok-soft"]};
    color: {t["ok-ink"]};
}}
QLabel.found-off {{ background-color: {t["warn-soft"]}; color: {t["warn-ink"]}; }}

/* Banners and the result headline */
QFrame.banner {{
    border: none;
    border-left: 3px solid {t["line"]};
    border-radius: 6px;
    background-color: {t["surface-sunken"]};
}}
QFrame.banner QLabel {{ font-size: 13px; color: {t["ink-muted"]}; }}
QFrame.banner-ok {{ border-left-color: {t["ok"]}; background-color: {t["ok-soft"]}; }}
QFrame.banner-ok QLabel {{ color: {t["ok-ink"]}; }}
QFrame.banner-warn {{ border-left-color: {t["warn"]}; background-color: {t["warn-soft"]}; }}
QFrame.banner-warn QLabel {{ color: {t["warn-ink"]}; }}
QFrame.banner-bad {{ border-left-color: {t["bad"]}; background-color: {t["bad-soft"]}; }}
QFrame.banner-bad QLabel {{ color: {t["bad-ink"]}; }}
QFrame.banner-info {{ border-left-color: {t["accent"]}; background-color: {t["accent-soft"]}; }}
QFrame.banner-info QLabel {{ color: {t["accent-ink"]}; }}

QFrame.headline {{ border: none; border-radius: 9px; }}
QFrame.headline QLabel.headline-title {{ font-size: 15px; font-weight: 600; }}
QFrame.headline QLabel.headline-icon {{ font-size: 22px; font-weight: 600; }}
QFrame.headline QLabel.headline-small {{ font-size: 12px; font-weight: 400; }}
QFrame.headline-ok {{ background-color: {t["ok-soft"]}; }}
QFrame.headline-ok QLabel {{ color: {t["ok-ink"]}; }}
QFrame.headline-warn {{ background-color: {t["warn-soft"]}; }}
QFrame.headline-warn QLabel {{ color: {t["warn-ink"]}; }}
QFrame.headline-warn QLabel.headline-small {{ color: {t["ink"]}; }}
QFrame.headline-bad {{ background-color: {t["bad-soft"]}; }}
QFrame.headline-bad QLabel {{ color: {t["bad-ink"]}; }}
QFrame.headline-info {{ background-color: {t["accent-soft"]}; }}
QFrame.headline-info QLabel {{ color: {t["accent-ink"]}; }}

/* A bordered list: machines, checks, results */
QFrame.listbox {{
    background-color: {t["surface"]};
    border: 1px solid {t["line"]};
    border-radius: 9px;
}}
QFrame.listbox QScrollArea, QFrame.listbox QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QLabel.more {{
    background-color: {t["accent-soft"]};
    color: {t["accent-ink"]};
    font-size: 12px;
    padding: 3px 14px;
    border-top: 1px solid {t["line"]};
    border-bottom-left-radius: 8px;
    border-bottom-right-radius: 8px;
}}
QFrame.mrow {{ border: none; border-bottom: 1px solid {t["line-soft"]}; background: transparent; }}
QFrame.mrow-sel {{ background-color: {t["accent-soft"]}; }}
QFrame.mrow-bad {{ background-color: {t["bad-soft"]}; border-bottom: 2px solid {t["surface"]}; }}
QFrame.mrow QLabel.nm {{ font-size: 13px; font-weight: 600; color: {t["ink"]}; }}
QFrame.mrow QLabel.nm-sub {{ font-size: 12px; color: {t["ink-subtle"]}; }}
QFrame.mrow QLabel.msg {{ font-size: 13px; color: {t["ink-muted"]}; }}
QFrame.mrow QLabel.cell {{ font-size: 13px; color: {t["ink-muted"]}; }}
QFrame.mrow QLabel.why {{ font-size: 12px; color: {t["ink-muted"]}; }}
QFrame.thead {{ border: none; border-bottom: 1px solid {t["line"]}; background: transparent; }}
QFrame.thead QLabel {{ font-size: 12px; font-weight: 500; color: {t["ink-subtle"]}; letter-spacing: 0.6px; }}

/* A choice on "What to collect" */
QFrame.opt {{
    border: 1px solid {t["line"]};
    border-radius: 6px;
    background-color: {t["surface"]};
}}
QFrame.opt:hover {{ border-color: {t["ink-subtle"]}; }}
QFrame.opt-on {{ border: 1px solid {t["accent"]}; background-color: {t["accent-soft"]}; }}
QFrame.opt-sug {{ border: 1px dashed {t["warn"]}; }}
QFrame.opt QLabel.opt-name {{ font-size: 13px; font-weight: 600; color: {t["ink"]}; }}
QFrame.opt QLabel.opt-hint {{ font-size: 12px; color: {t["ink-muted"]}; }}

/* Tabs and settings rows */
QPushButton.tab {{
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
    background: transparent;
    color: {t["ink-muted"]};
    font-size: 13px;
    font-weight: 400;
    padding: 8px 14px;
}}
QPushButton.tab:hover {{ color: {t["ink"]}; background: transparent; }}
QPushButton.tab:checked {{ color: {t["ink"]}; border-bottom: 2px solid {t["accent"]}; font-weight: 600; }}
QFrame.tabline {{ border: none; border-bottom: 1px solid {t["line"]}; }}
QFrame.setrow {{ border: none; border-bottom: 1px solid {t["line-soft"]}; background: transparent; }}
QFrame.setrow-last {{ border: none; background: transparent; }}
QPushButton.seg {{
    border: 1px solid {t["ink-subtle"]};
    border-radius: 0px;
    background: transparent;
    color: {t["ink-muted"]};
    font-weight: 400;
    padding: 5px 13px;
}}
QPushButton.seg-first {{ border-top-left-radius: 6px; border-bottom-left-radius: 6px; }}
QPushButton.seg-last {{ border-top-right-radius: 6px; border-bottom-right-radius: 6px; border-left: none; }}
QPushButton.seg:checked {{ background-color: {t["accent"]}; color: {t["on-accent"]}; font-weight: 600; }}

/* Progress */
QProgressBar {{
    border: none;
    border-radius: 3px;
    background-color: {t["surface-sunken"]};
    max-height: 6px;
    min-height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{ background-color: {t["accent"]}; border-radius: 3px; }}

/* Dialogs */
QDialog {{ background-color: {t["surface"]}; }}
QDialog QLabel.dlg-title {{ font-size: 16px; font-weight: 650; color: {t["ink"]}; }}
QFrame.dlg-foot {{ border: none; border-top: 1px solid {t["line"]}; background: transparent; }}
QDialog QWidget.dlg-body {{ background-color: {t["surface"]}; }}

QLabel.toast {{
    background-color: {t["surface"]};
    color: {t["ok-ink"]};
    border: 1px solid {t["ok"]};
    border-radius: 13px;
    min-height: 14px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 600;
}}

QPushButton.conn QLabel {{ font-size: 12px; color: {t["ink-muted"]}; }}
QLabel.tick {{ color: {t["ok-ink"]}; font-weight: 700; }}
QLabel.cross {{ color: {t["bad-ink"]}; font-weight: 700; }}
"""
