"""Connection screen - host IP input, saved hosts, and connect"""
import json
import os

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                                QLineEdit, QPushButton, QSpinBox, QFrame,
                                QScrollArea)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont

_HOSTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "saved_hosts.json")


def _load_hosts() -> list[dict]:
    if os.path.exists(_HOSTS_FILE):
        try:
            with open(_HOSTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_hosts(hosts: list[dict]):
    with open(_HOSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(hosts, f, indent=2, ensure_ascii=False)


class ConnectionScreen(QWidget):
    """Connection screen with saved hosts list."""

    connect_requested = Signal(str, int, int)  # host_ip, video_port, control_port

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._load_saved_hosts()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch()

        card_row = QHBoxLayout()
        card_row.addStretch()

        card = QFrame()
        card.setObjectName("connection-card")
        card.setMaximumWidth(420)
        card.setMinimumWidth(380)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 28, 32, 28)
        card_layout.setSpacing(8)

        # Title
        title = QLabel("Ghost Display")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(24)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #89b4fa;")
        card_layout.addWidget(title)

        subtitle = QLabel("원격 디스플레이 뷰어")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #a6adc8; font-size: 13px;")
        card_layout.addWidget(subtitle)

        card_layout.addSpacing(16)

        # ── Saved hosts ───────────────────────────────
        saved_header = QLabel("저장된 PC")
        saved_header.setStyleSheet("color: #a6adc8; font-size: 11px;")
        card_layout.addWidget(saved_header)

        self.hosts_container = QVBoxLayout()
        self.hosts_container.setSpacing(6)
        card_layout.addLayout(self.hosts_container)

        card_layout.addSpacing(12)

        # ── New connection ────────────────────────────
        ip_label = QLabel("호스트 IP")
        ip_label.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        card_layout.addWidget(ip_label)

        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("192.168.0.x 또는 공인 IP")
        self.ip_input.setStyleSheet("font-size: 14px; padding: 8px;")
        self.ip_input.returnPressed.connect(self._on_connect)
        card_layout.addWidget(self.ip_input)

        card_layout.addSpacing(6)

        # Advanced
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

        vport_label = QLabel("비디오 포트")
        vport_label.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        adv_layout.addWidget(vport_label)
        self.video_port_spin = QSpinBox()
        self.video_port_spin.setRange(1, 65535)
        self.video_port_spin.setValue(9000)
        self.video_port_spin.setStyleSheet("padding: 4px;")
        adv_layout.addWidget(self.video_port_spin)

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

        card_layout.addSpacing(12)

        # Connect button
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

        # Status label
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #f38ba8; font-size: 12px;")
        card_layout.addWidget(self.status_label)

        card_row.addWidget(card)
        card_row.addStretch()
        outer.addLayout(card_row)
        outer.addStretch()

    # ── Saved hosts ───────────────────────────────────

    def _load_saved_hosts(self):
        hosts = _load_hosts()
        self._refresh_host_list(hosts)

    def _refresh_host_list(self, hosts: list[dict]):
        # Clear
        while self.hosts_container.count():
            item = self.hosts_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not hosts:
            empty = QLabel("저장된 PC가 없습니다")
            empty.setStyleSheet("color: #585b70; font-size: 12px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.hosts_container.addWidget(empty)
            return

        for host in hosts:
            row = self._create_host_row(host)
            self.hosts_container.addWidget(row)

    def _create_host_row(self, host: dict) -> QFrame:
        row = QFrame()
        row.setStyleSheet(
            "QFrame { background: rgba(49, 50, 68, 0.6); border-radius: 8px; }"
            "QFrame:hover { background: rgba(69, 71, 90, 0.8); }"
        )
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(8)

        # Info (clickable area)
        info = QVBoxLayout()
        info.setSpacing(2)

        name_label = QLabel(host.get("name", host["ip"]))
        name_label.setStyleSheet("color: #cdd6f4; font-size: 13px; font-weight: bold; background: transparent;")
        info.addWidget(name_label)

        detail = f"{host['ip']}:{host.get('control_port', 9001)}"
        detail_label = QLabel(detail)
        detail_label.setStyleSheet("color: #a6adc8; font-size: 11px; background: transparent;")
        info.addWidget(detail_label)

        layout.addLayout(info, 1)

        # Connect button
        connect_btn = QPushButton("연결")
        connect_btn.setFixedSize(52, 32)
        connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        connect_btn.setStyleSheet(
            "QPushButton { background: #89b4fa; color: #1e1e2e; border: none; "
            "border-radius: 6px; font-size: 12px; font-weight: bold; }"
            "QPushButton:hover { background: #b4d0fb; }"
        )
        connect_btn.clicked.connect(lambda _, h=host: self._connect_saved(h))
        layout.addWidget(connect_btn)

        # Delete button
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(28, 28)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #585b70; border: none; "
            "border-radius: 4px; font-size: 14px; }"
            "QPushButton:hover { color: #f38ba8; background: rgba(243, 139, 168, 0.15); }"
        )
        del_btn.clicked.connect(lambda _, h=host: self._delete_host(h))
        layout.addWidget(del_btn)

        return row

    def _connect_saved(self, host: dict):
        self.ip_input.setText(host["ip"])
        self.video_port_spin.setValue(host.get("video_port", 9000))
        self.control_port_spin.setValue(host.get("control_port", 9001))
        self._on_connect()

    def _delete_host(self, host: dict):
        hosts = _load_hosts()
        hosts = [h for h in hosts if h["ip"] != host["ip"]]
        _save_hosts(hosts)
        self._refresh_host_list(hosts)

    def save_host(self, ip: str, video_port: int = 9000, control_port: int = 9001):
        """Save a host after successful connection."""
        hosts = _load_hosts()
        # Update existing or add new
        for h in hosts:
            if h["ip"] == ip:
                h["video_port"] = video_port
                h["control_port"] = control_port
                _save_hosts(hosts)
                self._refresh_host_list(hosts)
                return
        hosts.insert(0, {
            "ip": ip,
            "name": ip,
            "video_port": video_port,
            "control_port": control_port,
        })
        # Max 10 saved
        hosts = hosts[:10]
        _save_hosts(hosts)
        self._refresh_host_list(hosts)

    # ── Slots ─────────────────────────────────────────

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

    # ── Public API ────────────────────────────────────

    def set_status(self, msg: str, error: bool = False):
        color = "#f38ba8" if error else "#a6e3a1"
        self.status_label.setStyleSheet(f"color: {color}; font-size: 12px;")
        self.status_label.setText(msg)

    def set_connecting(self, loading: bool):
        if loading:
            self.connect_btn.setEnabled(False)
            self.connect_btn.setText("연결 중...")
            self.ip_input.setEnabled(False)
        else:
            self.connect_btn.setEnabled(True)
            self.connect_btn.setText("연결")
            self.ip_input.setEnabled(True)
