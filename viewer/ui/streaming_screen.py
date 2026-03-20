"""Streaming screen - sidebar + video display composite"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QVBoxLayout
from PySide6.QtCore import Qt

from viewer.ui.sidebar import Sidebar
from viewer.ui.video_widget import VideoWidget


class StreamingScreen(QWidget):
    """Composite widget: collapsible sidebar + video rendering area."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.sidebar = Sidebar()
        self.video = VideoWidget()

        # Sidebar toggle button (overlays top-left of video)
        self.toggle_btn = QPushButton("◀")
        self.toggle_btn.setFixedSize(28, 28)
        self.toggle_btn.setObjectName("sidebar-toggle")
        self.toggle_btn.setStyleSheet(
            "QPushButton#sidebar-toggle {"
            "  background: rgba(49, 50, 68, 0.8);"
            "  color: #cdd6f4; border: none; border-radius: 4px;"
            "  font-size: 14px;"
            "}"
            "QPushButton#sidebar-toggle:hover { background: rgba(69, 71, 90, 0.9); }"
        )
        self.toggle_btn.clicked.connect(self._toggle_sidebar)

        # Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addWidget(self.video, 1)

        # Position toggle button over video area
        self.toggle_btn.setParent(self.video)
        self.toggle_btn.move(8, 8)
        self.toggle_btn.raise_()

    def _toggle_sidebar(self):
        self.sidebar.toggle()
        if self.sidebar._expanded:
            self.toggle_btn.setText("◀")
        else:
            self.toggle_btn.setText("▶")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.toggle_btn.raise_()
