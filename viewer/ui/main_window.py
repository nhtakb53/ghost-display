"""Main window - orchestrates connection, streaming, and UI"""
import threading

from PySide6.QtWidgets import QMainWindow, QStackedWidget
from PySide6.QtCore import Slot

from viewer.ui.connection_screen import ConnectionScreen
from viewer.ui.streaming_screen import StreamingScreen
from viewer.core.network import NetworkClient
from viewer.core.decoder import FrameDecoder


class MainWindow(QMainWindow):
    """Ghost Display viewer main window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ghost Display")
        self.setMinimumSize(960, 600)

        self._network: NetworkClient | None = None
        self._decoder: FrameDecoder | None = None

        # Screens
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._connect_screen = ConnectionScreen()
        self._stream_screen = StreamingScreen()

        self._stack.addWidget(self._connect_screen)
        self._stack.addWidget(self._stream_screen)
        self._stack.setCurrentIndex(0)

        # Connection screen signals
        self._connect_screen.connect_requested.connect(self._on_connect_requested)

        # Streaming screen signals
        sidebar = self._stream_screen.sidebar
        video = self._stream_screen.video

        sidebar.monitor_selected.connect(self._on_monitor_selected)
        sidebar.input_mode_changed.connect(self._on_input_mode_changed)

        video.input_event.connect(self._on_input_event)
        video.capture_started.connect(lambda: sidebar.setVisible(not sidebar._expanded or True))
        video.capture_ended.connect(lambda: None)

    # ── Connection ─────────────────────────────────────

    @Slot(str, int, int)
    def _on_connect_requested(self, host_ip, video_port, control_port):
        """User clicked Connect."""
        self._connect_screen.set_connecting(True)

        # Create network client
        self._network = NetworkClient()
        self._network.connected.connect(self._on_connected)
        self._network.disconnected.connect(self._on_disconnected)
        self._network.control_received.connect(self._on_control)
        self._network.sps_pps_received.connect(self._on_sps_pps)
        self._network.nal_received.connect(self._on_nal)
        self._network.stats_updated.connect(self._on_stats)

        # Create decoder
        self._decoder = FrameDecoder()
        self._decoder.frame_ready.connect(self._stream_screen.video.update_frame)

        # Connect in background thread
        self._host_ip = host_ip
        self._video_port = video_port
        self._control_port = control_port

        def _do_connect():
            try:
                self._network.connect_to_host(host_ip, video_port, control_port)
            except Exception as e:
                print(f"  [Viewer] Connection error: {e}")
                from PySide6.QtCore import QMetaObject, Qt as QtNs, Q_ARG
                QMetaObject.invokeMethod(
                    self._connect_screen, "set_status",
                    QtNs.QueuedConnection,
                    Q_ARG(str, f"연결 실패: {e}"),
                    Q_ARG(bool, True),
                )
                QMetaObject.invokeMethod(
                    self._connect_screen, "set_connecting",
                    QtNs.QueuedConnection,
                    Q_ARG(bool, False),
                )

        threading.Thread(target=_do_connect, daemon=True).start()

    @Slot()
    def _on_connected(self):
        """Network connected - switch to streaming screen."""
        self._connect_screen.set_connecting(False)
        self._stack.setCurrentIndex(1)
        self._stream_screen.sidebar.update_connection(self._host_ip, True)
        # 기본 모니터 1개 표시 (monitor_info가 오면 업데이트됨)
        self._stream_screen.sidebar.set_monitors([{"index": 0}], 0)
        print(f"  [Viewer] Connected to {self._host_ip}")

    @Slot()
    def _on_disconnected(self):
        """Network disconnected - back to connection screen."""
        if self._decoder:
            self._decoder.stop()
        self._stack.setCurrentIndex(0)
        self._connect_screen.set_connecting(False)
        self._connect_screen.set_status("연결이 끊어졌습니다", error=True)
        self._stream_screen.sidebar.update_connection("", False)

    # ── Video pipeline ─────────────────────────────────

    @Slot(bytes)
    def _on_sps_pps(self, data):
        """SPS/PPS received - start/restart decoder and inject."""
        if self._decoder:
            if not self._decoder.running:
                video = self._stream_screen.video
                self._decoder.start(video._stream_width, video._stream_height)
            self._decoder.feed(data)

    @Slot(bytes, int)
    def _on_nal(self, nal_data, flags):
        """NAL unit received - feed to decoder."""
        if self._decoder and self._decoder.running:
            self._decoder.feed(nal_data)

    # ── Control messages ───────────────────────────────

    @Slot(dict)
    def _on_control(self, ctrl):
        """Handle control messages from host."""
        cmd = ctrl.get("cmd")
        video = self._stream_screen.video
        sidebar = self._stream_screen.sidebar

        if cmd == "stream_info":
            w = ctrl.get("width", 1920)
            h = ctrl.get("height", 1080)
            video.set_stream_size(w, h)
            # Restart decoder with correct resolution
            if self._decoder:
                self._decoder.restart(w, h)
            print(f"  [Viewer] Stream: {w}x{h} @ {ctrl.get('fps')}fps")

        elif cmd == "monitor_info":
            monitors = ctrl.get("monitors", [])
            selected = ctrl.get("selected", "all")
            sidebar.set_monitors(monitors, selected)
            print(f"  [Viewer] {len(monitors)} monitors available")

        elif cmd == "monitor_changed":
            selected = ctrl.get("monitor", "all")
            count = ctrl.get("count", 1)
            # Update sidebar selection highlight
            sidebar._selected_monitor = selected
            sidebar.set_monitors(
                [{"index": i} for i in range(count)],
                selected
            )

        elif cmd == "input_mode_changed":
            mode = ctrl.get("mode", "kse")
            sidebar._mode_combo.setCurrentText(mode.upper())

        elif cmd == "host_udp_addr":
            ip = ctrl.get("ip")
            port = ctrl.get("port")
            if ip and port and self._network:
                self._network.host_udp_addr = (ip, port)

    # ── Sidebar actions ────────────────────────────────

    @Slot(object)
    def _on_monitor_selected(self, monitor):
        if self._network:
            self._network.send_control({"cmd": "select_monitor", "monitor": monitor})

    @Slot(str)
    def _on_input_mode_changed(self, mode):
        if self._network:
            self._network.send_control({"cmd": "switch_input_mode", "mode": mode})

    @Slot(dict)
    def _on_input_event(self, event):
        if self._network:
            self._network.send_input(event)

    @Slot(dict)
    def _on_stats(self, stats):
        self._stream_screen.sidebar.update_stats(stats)

    # ── Cleanup ────────────────────────────────────────

    def closeEvent(self, event):
        if self._network:
            self._network.disconnect()
        if self._decoder:
            self._decoder.stop()
        super().closeEvent(event)
