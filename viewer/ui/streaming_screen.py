"""Streaming screen - floating panel over fullscreen video"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton
from PySide6.QtCore import Qt

from viewer.ui.sidebar import Sidebar
from viewer.ui.video_widget import VideoWidget


class StreamingScreen(QWidget):
    """Video takes full area. Sidebar floats on top."""

    def __init__(self, parent=None):
        super().__init__(parent)

        # Video fills entire area
        self.video = VideoWidget(self)

        # Floating sidebar (child of this widget, not in layout)
        self.sidebar = Sidebar(self)

        # Toggle button (top-left corner)
        self.toggle_btn = QPushButton("☰")
        self.toggle_btn.setParent(self)
        self.toggle_btn.setFixedSize(36, 36)
        self.toggle_btn.setObjectName("sidebar-toggle")
        self.toggle_btn.setStyleSheet(
            "QPushButton#sidebar-toggle {"
            "  background: rgba(24, 24, 37, 0.7);"
            "  color: #cdd6f4; border: none; border-radius: 8px;"
            "  font-size: 18px;"
            "}"
            "QPushButton#sidebar-toggle:hover {"
            "  background: rgba(49, 50, 68, 0.9);"
            "}"
        )
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.clicked.connect(self._toggle_sidebar)

        # No layout needed - manual positioning
        self.toggle_btn.move(12, 12)
        self.toggle_btn.raise_()

    def _toggle_sidebar(self):
        self.sidebar.toggle()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Video fills entire area
        self.video.setGeometry(0, 0, self.width(), self.height())
        # Keep toggle button on top
        self.toggle_btn.raise_()
        self.sidebar.raise_()
