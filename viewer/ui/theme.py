"""Dark modern theme for Ghost Display viewer (Catppuccin Mocha inspired)"""

COLORS = {
    "base": "#1e1e2e",
    "mantle": "#181825",
    "surface0": "#313244",
    "surface1": "#45475a",
    "surface2": "#585b70",
    "text": "#cdd6f4",
    "subtext": "#a6adc8",
    "blue": "#89b4fa",
    "green": "#a6e3a1",
    "red": "#f38ba8",
    "yellow": "#f9e2af",
    "overlay": "#6c7086",
}

DARK_THEME_QSS = f"""
/* ── Global ────────────────────────────────────────── */
QMainWindow, QWidget {{
    background-color: {COLORS["base"]};
    color: {COLORS["text"]};
    font-size: 13px;
}}

/* ── QPushButton ───────────────────────────────────── */
QPushButton {{
    background-color: {COLORS["surface0"]};
    color: {COLORS["text"]};
    border: 1px solid {COLORS["surface2"]};
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 13px;
}}
QPushButton:hover {{
    background-color: {COLORS["blue"]};
    color: {COLORS["base"]};
    border: 1px solid {COLORS["blue"]};
}}
QPushButton:pressed {{
    background-color: {COLORS["surface1"]};
    color: {COLORS["text"]};
}}
QPushButton:disabled {{
    background-color: {COLORS["surface0"]};
    color: {COLORS["overlay"]};
    border: 1px solid {COLORS["surface1"]};
}}

/* ── QLineEdit ─────────────────────────────────────── */
QLineEdit {{
    background-color: {COLORS["surface0"]};
    color: {COLORS["text"]};
    border: 1px solid {COLORS["surface2"]};
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 13px;
    selection-background-color: {COLORS["blue"]};
    selection-color: {COLORS["base"]};
}}
QLineEdit:focus {{
    border: 1px solid {COLORS["blue"]};
}}

/* ── QComboBox ─────────────────────────────────────── */
QComboBox {{
    background-color: {COLORS["surface0"]};
    color: {COLORS["text"]};
    border: 1px solid {COLORS["surface2"]};
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 13px;
}}
QComboBox:hover {{
    background-color: {COLORS["blue"]};
    color: {COLORS["base"]};
    border: 1px solid {COLORS["blue"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {COLORS["text"]};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {COLORS["surface0"]};
    color: {COLORS["text"]};
    border: 1px solid {COLORS["surface2"]};
    selection-background-color: {COLORS["blue"]};
    selection-color: {COLORS["base"]};
}}

/* ── QLabel ────────────────────────────────────────── */
QLabel {{
    color: {COLORS["text"]};
    font-size: 13px;
    background: transparent;
}}

/* ── QFrame ────────────────────────────────────────── */
QFrame {{
    background-color: {COLORS["base"]};
    border: none;
}}
QFrame[class="sidebar"] {{
    background-color: {COLORS["mantle"]};
}}

/* ── QScrollBar (vertical) ─────────────────────────── */
QScrollBar:vertical {{
    background-color: {COLORS["surface0"]};
    width: 8px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background-color: {COLORS["surface1"]};
    min-height: 30px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {COLORS["surface2"]};
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
    border: none;
}}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: none;
}}

/* ── QScrollBar (horizontal) ───────────────────────── */
QScrollBar:horizontal {{
    background-color: {COLORS["surface0"]};
    height: 8px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background-color: {COLORS["surface1"]};
    min-width: 30px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {COLORS["surface2"]};
}}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
    background: none;
    border: none;
}}
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{
    background: none;
}}

/* ── QToolTip ──────────────────────────────────────── */
QToolTip {{
    background-color: {COLORS["surface0"]};
    color: {COLORS["text"]};
    border: 1px solid {COLORS["surface2"]};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 13px;
}}
"""
