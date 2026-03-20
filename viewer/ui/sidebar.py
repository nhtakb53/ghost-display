"""Collapsible sidebar for streaming controls"""
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                                QPushButton, QComboBox, QWidget, QSizePolicy)
from PySide6.QtCore import Signal, Qt, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont


class Sidebar(QFrame):
    """Collapsible sidebar with monitor selection, settings, and stats."""

    monitor_selected = Signal(object)   # int index or "all"
    input_mode_changed = Signal(str)    # "kse" or "sendinput"

    EXPANDED_WIDTH = 220
    COLLAPSED_WIDTH = 0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = True
        self.setObjectName("sidebar")
        self.setFixedWidth(self.EXPANDED_WIDTH)

        self._monitor_buttons: list[QPushButton] = []
        self._selected_monitor = None

        self._build_ui()

    # ------------------------------------------------------------------ #
    #  UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(12)

        # --- Section 1: Header ---
        title = QLabel("Ghost Display")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(16)
        title.setFont(title_font)
        title.setStyleSheet("color: #89b4fa;")  # blue accent
        layout.addWidget(title)

        # --- Section 2: Connection ---
        conn_header = QLabel("연결 상태")
        conn_header.setStyleSheet("color: #a6adc8; font-size: 11px;")
        layout.addWidget(conn_header)

        conn_row = QHBoxLayout()
        self.status_dot = QLabel("\u2b24")  # filled circle
        self.status_dot.setStyleSheet("color: #a6adc8; font-size: 10px;")
        self.status_dot.setFixedWidth(16)
        conn_row.addWidget(self.status_dot)

        self.host_ip_label = QLabel("미연결")
        self.host_ip_label.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        conn_row.addWidget(self.host_ip_label)
        conn_row.addStretch()
        layout.addLayout(conn_row)

        self._add_separator(layout)

        # --- Section 3: Monitor Selection ---
        monitor_header = QLabel("모니터")
        monitor_header.setStyleSheet("color: #a6adc8; font-size: 11px;")
        layout.addWidget(monitor_header)

        self.monitor_buttons_layout = QVBoxLayout()
        self.monitor_buttons_layout.setSpacing(4)
        layout.addLayout(self.monitor_buttons_layout)

        self._add_separator(layout)

        # --- Section 4: Input Mode ---
        input_header = QLabel("입력 모드")
        input_header.setStyleSheet("color: #a6adc8; font-size: 11px;")
        layout.addWidget(input_header)

        self.input_mode_combo = QComboBox()
        self.input_mode_combo.addItems(["KSE", "SendInput"])
        self.input_mode_combo.currentTextChanged.connect(
            lambda text: self.input_mode_changed.emit(text.lower())
        )
        layout.addWidget(self.input_mode_combo)

        self._add_separator(layout)

        # --- Section 5: Stats ---
        stats_header = QLabel("상태")
        stats_header.setStyleSheet("color: #a6adc8; font-size: 11px;")
        layout.addWidget(stats_header)

        self.fps_label = QLabel("FPS: --")
        self.fps_label.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        layout.addWidget(self.fps_label)

        self.bitrate_label = QLabel("비트레이트: --")
        self.bitrate_label.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        layout.addWidget(self.bitrate_label)

        self.keyframes_label = QLabel("키프레임: --")
        self.keyframes_label.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        layout.addWidget(self.keyframes_label)

        # --- Section 6: Spacer ---
        layout.addStretch()

    def _add_separator(self, layout: QVBoxLayout):
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #45475a;")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

    # ------------------------------------------------------------------ #
    #  Monitor buttons
    # ------------------------------------------------------------------ #
    def set_monitors(self, monitors: list, selected=None):
        """Populate monitor buttons. *monitors* is a list of monitor info dicts/objects."""
        # Clear existing buttons
        for btn in self._monitor_buttons:
            btn.setParent(None)
            btn.deleteLater()
        self._monitor_buttons.clear()

        # "전체" button
        all_btn = self._make_monitor_button("전체", "all")
        self.monitor_buttons_layout.addWidget(all_btn)
        self._monitor_buttons.append(all_btn)

        # Per-monitor buttons
        for idx, _mon in enumerate(monitors):
            label = f"모니터 {idx + 1}"
            btn = self._make_monitor_button(label, idx)
            self.monitor_buttons_layout.addWidget(btn)
            self._monitor_buttons.append(btn)

        self._selected_monitor = selected
        self._refresh_monitor_highlight()

    def _make_monitor_button(self, label: str, value) -> QPushButton:
        btn = QPushButton(label)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setProperty("monitor_value", value)
        btn.clicked.connect(lambda _checked, v=value: self._on_monitor_clicked(v))
        return btn

    def _on_monitor_clicked(self, value):
        self._selected_monitor = value
        self._refresh_monitor_highlight()
        self.monitor_selected.emit(value)

    def _refresh_monitor_highlight(self):
        for btn in self._monitor_buttons:
            if btn.property("monitor_value") == self._selected_monitor:
                btn.setStyleSheet(
                    "background-color: #89b4fa; color: #1e1e2e; "
                    "border: none; border-radius: 4px; padding: 6px;"
                )
            else:
                btn.setStyleSheet(
                    "background-color: #313244; color: #cdd6f4; "
                    "border: none; border-radius: 4px; padding: 6px;"
                )

    # ------------------------------------------------------------------ #
    #  Stats
    # ------------------------------------------------------------------ #
    def update_stats(self, stats: dict):
        """Update stat labels. Keys: fps, bitrate, keyframes."""
        if "fps" in stats:
            self.fps_label.setText(f"FPS: {stats['fps']}")
        if "bitrate" in stats:
            self.bitrate_label.setText(f"비트레이트: {stats['bitrate']}")
        if "keyframes" in stats:
            self.keyframes_label.setText(f"키프레임: {stats['keyframes']}")

    # ------------------------------------------------------------------ #
    #  Connection
    # ------------------------------------------------------------------ #
    def update_connection(self, host_ip: str, connected: bool):
        """Update connection status dot and IP label."""
        if connected:
            self.status_dot.setStyleSheet("color: #a6e3a1; font-size: 10px;")  # green
            self.host_ip_label.setText(host_ip)
        else:
            self.status_dot.setStyleSheet("color: #a6adc8; font-size: 10px;")  # grey
            self.host_ip_label.setText("미연결")

    # ------------------------------------------------------------------ #
    #  Toggle (collapse / expand)
    # ------------------------------------------------------------------ #
    def toggle(self):
        """Animate sidebar between expanded and collapsed states."""
        start = self.EXPANDED_WIDTH if self._expanded else self.COLLAPSED_WIDTH
        end = self.COLLAPSED_WIDTH if self._expanded else self.EXPANDED_WIDTH

        anim = QPropertyAnimation(self, b"maximumWidth", self)
        anim.setDuration(200)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # Also animate minimumWidth so the frame truly collapses
        anim_min = QPropertyAnimation(self, b"minimumWidth", self)
        anim_min.setDuration(200)
        anim_min.setStartValue(start)
        anim_min.setEndValue(end)
        anim_min.setEasingCurve(QEasingCurve.Type.InOutQuad)

        anim.start()
        anim_min.start()

        # Keep references so they aren't garbage-collected mid-animation
        self._anim = anim
        self._anim_min = anim_min

        self._expanded = not self._expanded
