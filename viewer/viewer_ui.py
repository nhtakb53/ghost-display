"""
Ghost Display - Modern PySide6 Viewer
Entry point for the new UI-based viewer.

Usage:
    python viewer_ui.py
"""
import sys
import os

# Ensure viewer package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from viewer.ui.theme import DARK_THEME_QSS
from viewer.ui.main_window import MainWindow


def main():
    # High DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Ghost Display")
    app.setStyleSheet(DARK_THEME_QSS)

    window = MainWindow()
    window.resize(1280, 800)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
