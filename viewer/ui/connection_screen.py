"""Connection screen - host IP input and connect"""
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                                QLineEdit, QPushButton, QSpinBox, QFrame)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont


class ConnectionScreen(QWidget):
    """Connection screen shown at startup for entering host IP and ports."""

    connect_requested = Signal(str, int, int)  # host_ip, video_port, control_port

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # ------------------------------------------------------------------ #
    #  UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Centering: stretch - card - stretch
        outer.addStretch()

        card_row = QHBoxLayout()
        card_row.addStretch()

        # --- Card container ---
        card = QFrame()
        card.setObjectName("connection-card")
        card.setMaximumWidth(400)
        card.setMinimumWidth(360)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 32, 32, 32)
        card_layout.setSpacing(8)

        # 1. Title
        title = QLabel("Ghost Display")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(24)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #89b4fa;")  # blue accent
        card_layout.addWidget(title)

        # 2. Subtitle
        subtitle = QLabel("원격 디스플레이 뷰어")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #a6adc8; font-size: 13px;")
        card_layout.addWidget(subtitle)

        # 3. Spacer
        card_layout.addSpacing(20)

        # 4. Host IP label
        ip_label = QLabel("호스트 IP")
        ip_label.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        card_layout.addWidget(ip_label)

        # 5. Host IP input
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("192.168.0.x 또는 공인 IP")
        self.ip_input.setStyleSheet("font-size: 14px; padding: 8px;")
        self.ip_input.returnPressed.connect(self._on_connect)
        card_layout.addWidget(self.ip_input)

        # 6. Spacer
        card_layout.addSpacing(10)

        # 7. Advanced section (initially hidden)
        self.advanced_toggle = QPushButton("고급 설정 \u25bc")
        self.advanced_toggle.setFlat(True)
        self.advanced_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.advanced_toggle.setStyleSheet(
            "color: #a6adc8; font-size: 12px; text-align: left; border: none;"
        )
        self.advanced_toggle.clicked.connect(self._toggle_advanced)
        card_layout.addWidget(self.advanced_toggle)

        self.advanced_widget = QWidget()
        adv_layout = QVBoxLayout(self.advanced_widget)
        adv_layout.setContentsMargins(0, 4, 0, 0)
        adv_layout.setSpacing(6)

        # Video port
        vport_label = QLabel("비디오 포트")
        vport_label.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        adv_layout.addWidget(vport_label)

        self.video_port_spin = QSpinBox()
        self.video_port_spin.setRange(1, 65535)
        self.video_port_spin.setValue(9000)
        self.video_port_spin.setStyleSheet("padding: 4px;")
        adv_layout.addWidget(self.video_port_spin)

        # Control port
        cport_label = QLabel("제어 포트")
        cport_label.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        adv_layout.addWidget(cport_label)

        self.control_port_spin = QSpinBox()
        self.control_port_spin.setRange(1, 65535)
        self.control_port_spin.setValue(9001)
        self.control_port_spin.setStyleSheet("padding: 4px;")
        adv_layout.addWidget(self.control_port_spin)

        self.advanced_widget.setVisible(False)
        card_layout.addWidget(self.advanced_widget)

        # 8. Spacer
        card_layout.addSpacing(15)

        # 9. Connect button
        self.connect_btn = QPushButton("연결")
        self.connect_btn.setObjectName("connect-btn")
        self.connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.connect_btn.setFixedHeight(44)
        self.connect_btn.setStyleSheet(
            "background-color: #89b4fa; color: #1e1e2e; font-size: 15px; "
            "font-weight: bold; border: none; border-radius: 8px;"
        )
        self.connect_btn.clicked.connect(self._on_connect)
        card_layout.addWidget(self.connect_btn)

        # 10. Status label
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #f38ba8; font-size: 12px;")
        card_layout.addWidget(self.status_label)

        card_row.addWidget(card)
        card_row.addStretch()
        outer.addLayout(card_row)
        outer.addStretch()

    # ------------------------------------------------------------------ #
    #  Slots
    # ------------------------------------------------------------------ #
    def _on_connect(self):
        ip = self.ip_input.text().strip()
        if not ip:
            self.set_status("IP 주소를 입력하세요.", error=True)
            return
        video_port = self.video_port_spin.value()
        control_port = self.control_port_spin.value()
        self.connect_requested.emit(ip, video_port, control_port)

    def _toggle_advanced(self):
        visible = not self.advanced_widget.isVisible()
        self.advanced_widget.setVisible(visible)
        arrow = "\u25b2" if visible else "\u25bc"
        self.advanced_toggle.setText(f"고급 설정 {arrow}")

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #
    def set_status(self, msg: str, error: bool = False):
        """Show a status message below the connect button."""
        color = "#f38ba8" if error else "#a6e3a1"
        self.status_label.setStyleSheet(f"color: {color}; font-size: 12px;")
        self.status_label.setText(msg)

    def set_connecting(self, loading: bool):
        """Toggle the button between normal and loading state."""
        if loading:
            self.connect_btn.setEnabled(False)
            self.connect_btn.setText("연결 중...")
            self.ip_input.setEnabled(False)
        else:
            self.connect_btn.setEnabled(True)
            self.connect_btn.setText("연결")
            self.ip_input.setEnabled(True)
