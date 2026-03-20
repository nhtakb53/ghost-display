"""Floating panel for streaming controls"""
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                                QPushButton, QComboBox, QGraphicsOpacityEffect)
from PySide6.QtCore import Signal, Qt, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtGui import QFont


class Sidebar(QFrame):
    """Floating translucent panel over the video area."""

    monitor_selected = Signal(object)   # int index or "all"
    input_mode_changed = Signal(str)    # "kse" or "sendinput"
    close_requested = Signal()

    PANEL_WIDTH = 220

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = False  # 시작 시 숨김
        self.setObjectName("sidebar")
        self.setFixedWidth(self.PANEL_WIDTH)

        self._monitor_buttons: list[QPushButton] = []
        self._selected_monitor = None

        self.setStyleSheet(
            "QFrame#sidebar {"
            "  background-color: rgba(24, 24, 37, 0.92);"
            "  border-radius: 12px;"
            "  border: 1px solid rgba(69, 71, 90, 0.5);"
            "}"
        )

        self._build_ui()
        self.hide()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 16)
        layout.setSpacing(10)

        # --- Header ---
        header_row = QHBoxLayout()
        title = QLabel("Ghost Display")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(14)
        title.setFont(title_font)
        title.setStyleSheet("color: #89b4fa; background: transparent;")
        header_row.addWidget(title)
        header_row.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(32, 32)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            "QPushButton { background: rgba(69, 71, 90, 0.4); color: #cdd6f4; border: none;"
            "  border-radius: 6px; font-size: 18px; font-weight: bold; }"
            "QPushButton:hover { background: rgba(243, 139, 168, 0.3); color: #f38ba8; }"
        )
        close_btn.clicked.connect(self.close_requested.emit)
        header_row.addWidget(close_btn)
        layout.addLayout(header_row)

        # --- Connection ---
        conn_row = QHBoxLayout()
        self.status_dot = QLabel("\u2b24")
        self.status_dot.setStyleSheet("color: #a6adc8; font-size: 10px; background: transparent;")
        self.status_dot.setFixedWidth(16)
        conn_row.addWidget(self.status_dot)

        self.host_ip_label = QLabel("미연결")
        self.host_ip_label.setStyleSheet("color: #cdd6f4; font-size: 12px; background: transparent;")
        conn_row.addWidget(self.host_ip_label)
        conn_row.addStretch()
        layout.addLayout(conn_row)

        self._add_separator(layout)

        # --- Monitor Selection (hidden until monitor_info arrives) ---
        self._monitor_section = QFrame()
        self._monitor_section.setStyleSheet("background: transparent;")
        mon_layout = QVBoxLayout(self._monitor_section)
        mon_layout.setContentsMargins(0, 0, 0, 0)
        mon_layout.setSpacing(4)

        monitor_header = QLabel("모니터")
        monitor_header.setStyleSheet("color: #a6adc8; font-size: 11px; background: transparent;")
        mon_layout.addWidget(monitor_header)

        self.monitor_buttons_layout = QVBoxLayout()
        self.monitor_buttons_layout.setSpacing(4)
        mon_layout.addLayout(self.monitor_buttons_layout)

        self._monitor_section.hide()
        layout.addWidget(self._monitor_section)

        self._monitor_sep = self._make_separator()
        self._monitor_sep.hide()
        layout.addWidget(self._monitor_sep)

        # --- Input Mode ---
        input_header = QLabel("입력 모드")
        input_header.setStyleSheet("color: #a6adc8; font-size: 11px; background: transparent;")
        layout.addWidget(input_header)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["KSE", "SendInput"])
        self._mode_combo.setStyleSheet(
            "QComboBox { background: rgba(49, 50, 68, 0.7); color: #cdd6f4;"
            "  border: 1px solid rgba(69, 71, 90, 0.5); border-radius: 6px;"
            "  padding: 6px 10px; font-size: 13px; }"
            "QComboBox:hover { border-color: #89b4fa; }"
            "QComboBox::drop-down { border: none; width: 24px; }"
            "QComboBox::down-arrow { image: none; border-left: 4px solid transparent;"
            "  border-right: 4px solid transparent; border-top: 5px solid #cdd6f4; }"
            "QComboBox QAbstractItemView { background: #313244; color: #cdd6f4;"
            "  selection-background-color: #89b4fa; selection-color: #1e1e2e;"
            "  border: 1px solid #45475a; border-radius: 4px; padding: 4px; }"
        )
        self._mode_combo.currentTextChanged.connect(
            lambda text: self.input_mode_changed.emit(text.lower())
        )
        layout.addWidget(self._mode_combo)

        self._add_separator(layout)

        # --- Stats ---
        stats_header = QLabel("상태")
        stats_header.setStyleSheet("color: #a6adc8; font-size: 11px; background: transparent;")
        layout.addWidget(stats_header)

        self.fps_label = QLabel("FPS: --")
        self.fps_label.setStyleSheet("color: #cdd6f4; font-size: 12px; background: transparent;")
        layout.addWidget(self.fps_label)

        self.bitrate_label = QLabel("비트레이트: --")
        self.bitrate_label.setStyleSheet("color: #cdd6f4; font-size: 12px; background: transparent;")
        layout.addWidget(self.bitrate_label)

        self.keyframes_label = QLabel("키프레임: --")
        self.keyframes_label.setStyleSheet("color: #cdd6f4; font-size: 12px; background: transparent;")
        layout.addWidget(self.keyframes_label)

        layout.addStretch()

    def _add_separator(self, layout: QVBoxLayout):
        sep = self._make_separator()
        layout.addWidget(sep)

    def _make_separator(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(69, 71, 90, 0.6); background: transparent;")
        sep.setFixedHeight(1)
        return sep

    # ── Monitor buttons ───────────────────────────────

    def set_monitors(self, monitors: list, selected=None):
        # 모니터가 2개 이상일 때만 표시
        if len(monitors) >= 2:
            self._monitor_section.show()
            self._monitor_sep.show()
        else:
            self._monitor_section.hide()
            self._monitor_sep.hide()
            return

        for btn in self._monitor_buttons:
            btn.setParent(None)
            btn.deleteLater()
        self._monitor_buttons.clear()

        all_btn = self._make_monitor_button("전체", "all")
        self.monitor_buttons_layout.addWidget(all_btn)
        self._monitor_buttons.append(all_btn)

        for idx, _mon in enumerate(monitors):
            btn = self._make_monitor_button(f"모니터 {idx + 1}", idx)
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
                    "QPushButton { background-color: #89b4fa; color: #1e1e2e; "
                    "border: none; border-radius: 6px; padding: 6px 10px; font-size: 13px; }"
                    "QPushButton:hover { background-color: #b4d0fb; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background-color: rgba(49, 50, 68, 0.7); color: #cdd6f4; "
                    "border: 1px solid rgba(69, 71, 90, 0.5); border-radius: 6px; "
                    "padding: 6px 10px; font-size: 13px; }"
                    "QPushButton:hover { background-color: rgba(69, 71, 90, 0.8); "
                    "border-color: #89b4fa; }"
                )

    # ── Stats ─────────────────────────────────────────

    def update_stats(self, stats: dict):
        if "recv_mbps" in stats:
            self.bitrate_label.setText(f"비트레이트: {stats['recv_mbps']:.1f} Mbps")
        if "nals" in stats:
            self.fps_label.setText(f"NALs: {stats['nals']}")
        if "keyframes" in stats:
            self.keyframes_label.setText(f"키프레임: {stats['keyframes']}")

    # ── Connection ────────────────────────────────────

    def update_connection(self, host_ip: str, connected: bool):
        if connected:
            self.status_dot.setStyleSheet("color: #a6e3a1; font-size: 10px; background: transparent;")
            self.host_ip_label.setText(host_ip)
        else:
            self.status_dot.setStyleSheet("color: #a6adc8; font-size: 10px; background: transparent;")
            self.host_ip_label.setText("미연결")

    # ── Slide in/out ────────────────────────────────────

    def slide_in(self):
        self.show()
        self.raise_()
        start = QPoint(-self.PANEL_WIDTH, 12)
        end = QPoint(12, 12)
        self._animate_pos(start, end)
        self._expanded = True

    def slide_out(self):
        start = self.pos()
        end = QPoint(-self.PANEL_WIDTH, 12)
        anim = self._animate_pos(start, end)
        anim.finished.connect(self.hide)
        self._expanded = False

    def _animate_pos(self, start, end):
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(200)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._pos_anim = anim
        return anim
