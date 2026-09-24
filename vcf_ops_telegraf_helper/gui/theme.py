"""Lattice design system tokens translated to Qt Style Sheets (QSS).

Derived from sentania-labs/lattice design tokens (tokens.json).
Adheres to the core Lattice principles:
- Borders, not shadows (no drop shadows or elevation).
- One accent for primary actions, focus, and selection.
- ok, warn, bad carry operational state and nothing else.
- Density is prioritized for infrastructure administration.
"""

from __future__ import annotations

DARK_TOKENS = {
    "bg": "#1b1f24",
    "surface": "#23282f",
    "surface-sunken": "#2a3038",
    "line": "#363d47",
    "line-soft": "#2a3038",
    "ink": "#f2f4f7",
    "ink-muted": "#b7bfca",
    "ink-subtle": "#7f8896",
    "accent": "#3987e5",
    "accent-strong": "#1f5fbf",
    "accent-soft": "#193557",
    "accent-ink": "#8fbcf5",
    "ok": "#199e70",
    "ok-soft": "#0d3728",
    "ok-ink": "#67c9a2",
    "warn": "#e0a400",
    "warn-soft": "#3f300c",
    "warn-ink": "#e0a400",
    "bad": "#d95926",
    "bad-soft": "#3d1b11",
    "bad-ink": "#d95926",
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
    "ink-subtle": "#8b95a3",
    "accent": "#1f5fbf",
    "accent-strong": "#1a4fa0",
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


def build_stylesheet(theme: str = "dark") -> str:
    """Generate Qt Style Sheet (QSS) adhering to Lattice tokens."""
    t = DARK_TOKENS if theme == "dark" else LIGHT_TOKENS

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
    padding: 14px;
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
    font-size: 11px;
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
    color: #ffffff;
    border: 1px solid {t["accent-strong"]};
    font-weight: 600;
}}

QPushButton.primary:hover {{
    background-color: {t["accent-strong"]};
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
}}

/* Text Inputs and Combo Boxes (radius-lg: 6px) */
QLineEdit, QComboBox, QTextEdit, QPlainTextEdit {{
    background-color: {t["surface-sunken"]};
    border: 1px solid {t["line"]};
    border-radius: 6px;
    color: {t["ink"]};
    padding: 6px 8px;
    font-size: 13px;
}}

QLineEdit:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {t["accent"]};
    background-color: {t["surface"]};
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
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
    font-size: 11px;
}}

QFrame.status-badge-warn {{
    background-color: {t["warn-soft"]};
    border: 1px solid {t["warn"]};
    border-radius: 4px;
}}
QLabel.status-badge-warn-text {{
    color: {t["warn-ink"]};
    font-weight: 600;
    font-size: 11px;
}}

QFrame.status-badge-bad {{
    background-color: {t["bad-soft"]};
    border: 1px solid {t["bad"]};
    border-radius: 4px;
}}
QLabel.status-badge-bad-text {{
    color: {t["bad-ink"]};
    font-weight: 600;
    font-size: 11px;
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
"""
