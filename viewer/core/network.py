"""Network client for Ghost Display viewer - TCP control + UDP video"""

import sys
import os
import socket
import struct
import threading
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from common.upnp import setup_upnp, cleanup_upnp
from common.stun import stun_get_mapped_address

from PySide6.QtCore import QObject, Signal

# ── Protocol constants ──────────────────────────────────────────────
HEADER_FMT = "!BBHI"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
MAX_TCP_PACKET_SIZE = 4 * 1024 * 1024  # 4MB - NAL 최대 허용 크기

PKT_VIDEO = 0x01
PKT_INPUT = 0x10
PKT_CONTROL = 0x20
PKT_PING = 0x30
PKT_PONG = 0x31

FLAG_KEYFRAME = 0x01
FLAG_FRAGMENT = 0x02
FLAG_LAST_FRAGMENT = 0x04


class NetworkClient(QObject):
    """TCP control + UDP video network client for Ghost Display viewer."""

    # ── Signals ─────────────────────────────────────────────────────
    connected = Signal()
    disconnected = Signal()
    nal_received = Signal(bytes, int)          # (nal_data, flags)
    control_received = Signal(dict)
    sps_pps_received = Signal(bytes)
    stats_updated = Signal(dict)               # {"recv_mbps", "packets", "nals", "keyframes"}

    def __init__(self, parent=None):
        super().__init__(parent)

        self.tcp_sock: socket.socket | None = None
        self.udp_sock: socket.socket | None = None
        self.running = False

        # Fragment reassembly
        self.frag_buffer = bytearray()
        self._frag_seq_start = -1  # 첫 fragment의 seq
        self._last_udp_seq = -1    # 마지막 수신 seq (패킷 손실 감지)

        # SPS/PPS tracking
        self.got_sps_pps = False
        self.sps_pps_data = b""

        # Stats counters
        self.bytes_received = 0
        self.packets_received = 0
        self.nals_received = 0
        self.keyframes_received = 0
        self._last_stats_time = 0.0
        self._last_stats_bytes = 0
        self._last_stats_packets = 0
        self._last_stats_nals = 0
        self._last_stats_keyframes = 0

        # Host STUN address (set via control message)
        self.host_udp_addr: tuple | None = None

        # TCP video fallback
        self._tcp_video_requested = False

    # ── Public API ──────────────────────────────────────────────────

    def connect_to_host(self, host_ip: str, video_port: int = 9000,
                        control_port: int = 9001) -> None:
        """Connect to host in a background thread."""
        self.running = True
        t = threading.Thread(
            target=self._connect_worker,
            args=(host_ip, video_port, control_port),
            daemon=True,
        )
        t.start()

    def send_control(self, data: dict) -> None:
        """Send a control message over TCP."""
        self._tcp_send(PKT_CONTROL, data)

    def send_input(self, data: dict) -> None:
        """Send an input event over TCP."""
        self._tcp_send(PKT_INPUT, data)

    def disconnect(self) -> None:
        """Tear down all sockets and threads, emit disconnected()."""
        self.running = False
        cleanup_upnp()
        if self.tcp_sock:
            try:
                self.tcp_sock.close()
            except OSError:
                pass
            self.tcp_sock = None
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except OSError:
                pass
            self.udp_sock = None
        self.disconnected.emit()

    # ── Connection worker (runs in thread) ──────────────────────────

    def _connect_worker(self, host_ip: str, video_port: int,
                        control_port: int) -> None:
        try:
            # 1. UPnP
            setup_upnp([video_port])

            # 2. TCP connect
            self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.tcp_sock.connect((host_ip, control_port))
            self._tcp_send(PKT_CONTROL, {"cmd": "set_udp_port", "port": video_port})

            # 3. UDP socket
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF,
                                     4 * 1024 * 1024)
            self.udp_sock.bind(("0.0.0.0", video_port))
            self.udp_sock.settimeout(0.5)

            # STUN
            stun_result = stun_get_mapped_address(self.udp_sock)
            if stun_result:
                pub_ip, pub_port = stun_result
                self._tcp_send(PKT_CONTROL, {
                    "cmd": "set_udp_addr", "ip": pub_ip, "port": pub_port,
                })

            # Hole punch
            punch = struct.pack(HEADER_FMT, PKT_PING, 0, 0, 0)
            for _ in range(10):
                self.udp_sock.sendto(punch, (host_ip, video_port))

            # 4. Start threads
            threading.Thread(target=self._tcp_recv_loop, daemon=True).start()
            threading.Thread(target=self._udp_recv_loop, daemon=True).start()
            threading.Thread(target=self._keepalive_loop,
                             args=(host_ip, video_port), daemon=True).start()
            self._last_stats_time = time.time()
            threading.Thread(target=self._stats_loop, daemon=True).start()

            # 5. Wait for SPS/PPS (max 1 s)
            for _ in range(20):
                if self.got_sps_pps:
                    break
                time.sleep(0.05)

            if self.got_sps_pps and self.sps_pps_data:
                self.sps_pps_received.emit(self.sps_pps_data)

            # 6. Ready
            self.connected.emit()

        except Exception as exc:
            print(f"[NetworkClient] connect failed: {exc}")
            self.disconnect()

    # ── TCP helpers ─────────────────────────────────────────────────

    def _tcp_send(self, pkt_type: int, data: dict) -> None:
        if not self.tcp_sock:
            return
        payload = json.dumps(data).encode("utf-8")
        header = struct.pack(HEADER_FMT, pkt_type, 0, 0, len(payload))
        try:
            self.tcp_sock.sendall(header + payload)
        except OSError:
            pass

    # ── TCP receive loop ────────────────────────────────────────────

    def _tcp_recv_loop(self) -> None:
        buf = b""
        self.tcp_sock.settimeout(1.0)
        self.tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        while self.running:
            try:
                data = self.tcp_sock.recv(65536)
                if not data:
                    break
                buf += data

                while len(buf) >= HEADER_SIZE:
                    pkt_type, flags, seq, size = struct.unpack(
                        HEADER_FMT, buf[:HEADER_SIZE])
                    if size > MAX_TCP_PACKET_SIZE:
                        print(f"  [Network] TCP 스트림 손상 감지 (size={size}), 버퍼 리셋")
                        buf = b""
                        break
                    if len(buf) < HEADER_SIZE + size:
                        break
                    payload = buf[HEADER_SIZE:HEADER_SIZE + size]
                    buf = buf[HEADER_SIZE + size:]

                    if pkt_type == PKT_VIDEO:
                        if self._is_sps_or_pps(payload):
                            self.sps_pps_data += payload
                            self.got_sps_pps = True
                            self.sps_pps_received.emit(payload)
                        elif self._tcp_video_requested:
                            # TCP 비디오 폴백: NAL 데이터 처리
                            self.nals_received += 1
                            self.bytes_received += len(payload) + HEADER_SIZE
                            self.packets_received += 1
                            if flags & FLAG_KEYFRAME:
                                self.keyframes_received += 1
                            self.nal_received.emit(payload, flags)

                    elif pkt_type == PKT_CONTROL:
                        try:
                            ctrl = json.loads(payload.decode("utf-8"))
                            self.control_received.emit(ctrl)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass

            except socket.timeout:
                continue
            except OSError:
                break

        if self.running:
            self.disconnect()

    # ── UDP receive loop ────────────────────────────────────────────

    def _udp_recv_loop(self) -> None:
        while self.running:
            try:
                data, _addr = self.udp_sock.recvfrom(65536)
                if len(data) < HEADER_SIZE:
                    continue

                pkt_type, flags, seq, size = struct.unpack(
                    HEADER_FMT, data[:HEADER_SIZE])
                payload = data[HEADER_SIZE:]

                self.packets_received += 1
                self.bytes_received += len(data)

                if pkt_type == PKT_VIDEO:
                    if (flags & FLAG_KEYFRAME) and not (
                        (flags & FLAG_FRAGMENT) and self.frag_buffer
                    ):
                        self.keyframes_received += 1
                    self._handle_video(flags, seq, payload)

            except socket.timeout:
                continue
            except OSError:
                break

    # ── Video / fragment handling ───────────────────────────────────

    def _handle_video(self, flags: int, seq: int, payload: bytes) -> None:
        if flags & FLAG_FRAGMENT:
            # seq 연속성 확인 — 패킷 손실 시 불완전 NAL 폐기
            if self.frag_buffer:
                expected = (self._last_udp_seq + 1) & 0xFFFF
                if seq != expected:
                    self.frag_buffer = bytearray()
                    self._frag_seq_start = -1

            if not self.frag_buffer:
                self._frag_seq_start = seq
            self._last_udp_seq = seq

            self.frag_buffer.extend(payload)
            if flags & FLAG_LAST_FRAGMENT:
                nal = bytes(self.frag_buffer)
                self.frag_buffer = bytearray()
                self._frag_seq_start = -1
                self._process_nal(nal, flags)
        else:
            # 비분할 NAL 도착 시 불완전 fragment 폐기
            if self.frag_buffer:
                self.frag_buffer = bytearray()
                self._frag_seq_start = -1
            self._last_udp_seq = seq
            self._process_nal(payload, flags)

    def _process_nal(self, nal_data: bytes, flags: int) -> None:
        # Detect SPS/PPS arriving over UDP
        if self._is_sps_or_pps(nal_data):
            if not self.got_sps_pps:
                self.got_sps_pps = True
                self.sps_pps_data = nal_data
            self.sps_pps_received.emit(nal_data)

        self.nals_received += 1
        self.nal_received.emit(nal_data, flags)

    @staticmethod
    def _is_sps_or_pps(data: bytes) -> bool:
        """Return True if *data* starts with a NAL unit of type 7 (SPS) or 8 (PPS)."""
        if not data:
            return False
        if data[:4] == b'\x00\x00\x00\x01' and len(data) > 4:
            nal_byte = data[4]
        elif data[:3] == b'\x00\x00\x01' and len(data) > 3:
            nal_byte = data[3]
        else:
            nal_byte = data[0]
        return (nal_byte & 0x1F) in (7, 8)

    # ── Keep-alive loop ─────────────────────────────────────────────

    def _keepalive_loop(self, host_ip: str, video_port: int) -> None:
        punch = struct.pack(HEADER_FMT, PKT_PING, 0, 0, 0)
        while self.running:
            try:
                if self.udp_sock:
                    self.udp_sock.sendto(punch, (host_ip, video_port))
                    if self.host_udp_addr:
                        self.udp_sock.sendto(punch, self.host_udp_addr)
            except OSError:
                pass
            time.sleep(1)

    # ── Stats loop ──────────────────────────────────────────────────

    def _stats_loop(self) -> None:
        # 3초 후 UDP 수신 확인 → 실패 시 TCP 폴백
        time.sleep(3)
        if self.running and self.packets_received == 0 and not self._tcp_video_requested:
            print(f"  [Network] UDP 수신 없음 → TCP 비디오 폴백 요청")
            self._tcp_video_requested = True
            self._tcp_send(PKT_CONTROL, {"cmd": "request_tcp_video"})

        while self.running:
            time.sleep(5)
            if not self.running:
                break

            # TCP 폴백 후에도 데이터 없으면 재요청
            if self._tcp_video_requested and self.packets_received == 0:
                print(f"  [Network] TCP 폴백 재요청 (아직 수신 없음)")
                self._tcp_send(PKT_CONTROL, {"cmd": "request_tcp_video"})

            now = time.time()
            dt = now - self._last_stats_time
            if dt <= 0:
                continue

            d_bytes = self.bytes_received - self._last_stats_bytes
            recv_mbps = d_bytes * 8 / dt / 1024 / 1024

            stats = {
                "recv_mbps": round(recv_mbps, 2),
                "packets": self.packets_received,
                "nals": self.nals_received,
                "keyframes": self.keyframes_received,
            }
            self.stats_updated.emit(stats)

            d_nals = self.nals_received - self._last_stats_nals
            print(f"  [Network] recv: {recv_mbps:.1f}Mbps, "
                  f"pkts:{self.packets_received} (+{self.packets_received - self._last_stats_packets}), "
                  f"nals:{self.nals_received} (+{d_nals}), "
                  f"kf:{self.keyframes_received}")

            self._last_stats_time = now
            self._last_stats_bytes = self.bytes_received
            self._last_stats_packets = self.packets_received
            self._last_stats_nals = self.nals_received
            self._last_stats_keyframes = self.keyframes_received
