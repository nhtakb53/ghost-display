"""Streaming screen - floating panel over fullscreen video"""
from PySide6.QtWidgets import QWidget, QPushButton
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
        self.sidebar.close_requested.connect(self._close_sidebar)

        # Toggle button (top-left corner, visible when sidebar is hidden)
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
        self.toggle_btn.clicked.connect(self._open_sidebar)
        self.toggle_btn.move(12, 12)
        self.toggle_btn.raise_()

    def _open_sidebar(self):
        self.toggle_btn.hide()
        self.sidebar.slide_in()

    def _close_sidebar(self):
        self.sidebar.slide_out()
        self.toggle_btn.show()
        self.toggle_btn.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.video.setGeometry(0, 0, self.width(), self.height())
        self.toggle_btn.raise_()
        self.sidebar.raise_()
