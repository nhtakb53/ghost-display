"""
네트워크 모듈
- UDP: H.264 스트림 전송 (Host → Viewer)
- TCP: 제어 + 입력 수신 (Viewer → Host)
"""

import socket
import struct
import threading
import json
import time
import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from common.stun import stun_get_mapped_address


# 패킷 헤더 (8 bytes)
# [type:1][flags:1][seq:2][size:4]
HEADER_FMT = "!BBHI"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

# 패킷 타입
PKT_VIDEO = 0x01       # H.264 데이터
PKT_AUDIO = 0x02       # 오디오 (나중에)
PKT_INPUT = 0x10       # 입력 이벤트
PKT_CONTROL = 0x20     # 제어 (해상도, fps 등)
PKT_PING = 0x30        # 핑/퐁
PKT_PONG = 0x31

# UDP 최대 페이로드 (MTU 고려)
MAX_UDP_PAYLOAD = 1400

# 플래그
FLAG_KEYFRAME = 0x01
FLAG_FRAGMENT = 0x02
FLAG_LAST_FRAGMENT = 0x04


class StreamServer:
    def __init__(self, host="0.0.0.0", video_port=9000, control_port=9001):
        self.host = host
        self.video_port = video_port
        self.control_port = control_port
        self.running = False

        # UDP (영상 전송)
        self.udp_sock = None
        self.viewer_addr = None  # viewer의 UDP 주소

        # TCP (제어 + 입력)
        self.tcp_sock = None
        self.tcp_conn = None
        self.tcp_addr = None

        # 콜백
        self.on_input = None         # 입력 이벤트 콜백
        self.on_connected = None     # viewer 연결 콜백
        self.on_disconnected = None  # viewer 연결 해제 콜백
        self.on_control = None       # 제어 명령 콜백

        # TCP 비디오 폴백
        self.tcp_video = False
        self._tcp_lock = threading.Lock()

        # 통계
        self.bytes_sent = 0
        self.packets_sent = 0
        self.seq = 0

    def start(self):
        self.running = True

        # UDP 소켓 (바인딩해서 viewer의 홀펀치 패킷 수신)
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
        self.udp_sock.bind((self.host, self.video_port))

        # STUN으로 UDP 소켓의 공인 주소 확인
        self.stun_addr = None
        stun_result = stun_get_mapped_address(self.udp_sock)
        if stun_result:
            self.stun_addr = stun_result
            print(f"  [Network] STUN: 공인 UDP 주소 {stun_result[0]}:{stun_result[1]}")
        else:
            print(f"  [Network] STUN: 공인 주소 확인 실패")

        # UDP 수신 스레드 (홀펀치 패킷으로 viewer 주소 파악)
        threading.Thread(target=self._udp_recv_loop, daemon=True).start()

        # TCP 소켓
        self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp_sock.bind((self.host, self.control_port))
        self.tcp_sock.listen(1)
        self.tcp_sock.settimeout(1.0)

        # TCP 수신 스레드
        self._tcp_thread = threading.Thread(target=self._tcp_accept_loop, daemon=True)
        self._tcp_thread.start()

        print(f"  [Network] Video UDP :{self.video_port}")
        print(f"  [Network] Control TCP :{self.control_port}")
        print(f"  [Network] Waiting for viewer...")

    def _tcp_accept_loop(self):
        """TCP 연결 대기 및 수신"""
        while self.running:
            try:
                conn, addr = self.tcp_sock.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.tcp_conn = conn
                self.tcp_addr = addr
                # viewer의 UDP 주소도 같은 IP로 설정
                self.viewer_addr = (addr[0], self.video_port)
                print(f"  [Network] Viewer connected: {addr[0]}:{addr[1]}")

                # 연결 정보 전송
                if self.on_connected:
                    self.on_connected(addr)

                self._tcp_recv_loop(conn)

            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"  [Network] TCP error: {e}")

    def _tcp_recv_loop(self, conn):
        """TCP에서 제어/입력 메시지 수신"""
        conn.settimeout(1.0)
        buf = b""

        while self.running:
            try:
                data = conn.recv(4096)
                if not data:
                    print("  [Network] Viewer disconnected")
                    self.viewer_addr = None
                    if self.on_disconnected:
                        self.on_disconnected()
                    break

                buf += data
                while len(buf) >= HEADER_SIZE:
                    pkt_type, flags, seq, size = struct.unpack(HEADER_FMT, buf[:HEADER_SIZE])
                    if len(buf) < HEADER_SIZE + size:
                        break  # 아직 데이터 부족

                    payload = buf[HEADER_SIZE:HEADER_SIZE + size]
                    buf = buf[HEADER_SIZE + size:]

                    if pkt_type == PKT_INPUT and self.on_input:
                        try:
                            event = json.loads(payload.decode("utf-8"))
                            if not hasattr(self, '_input_count'):
                                self._input_count = 0
                            self._input_count += 1
                            if self._input_count <= 3 or self._input_count % 500 == 0:
                                print(f"  [Network] Input event #{self._input_count}: {event.get('type','?')}")
                            self.on_input(event)
                        except:
                            pass
                    elif pkt_type == PKT_PING:
                        self._send_tcp(PKT_PONG, payload)
                    elif pkt_type == PKT_CONTROL:
                        try:
                            ctrl = json.loads(payload.decode("utf-8"))
                            self._handle_control(ctrl)
                            if self.on_control:
                                self.on_control(ctrl)
                        except:
                            pass

            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"  [Network] Recv error: {e}")
                break

    def _handle_control(self, ctrl):
        """제어 메시지 처리"""
        cmd = ctrl.get("cmd")
        if cmd == "set_udp_port":
            # viewer가 수신할 UDP 포트 지정
            port = ctrl.get("port", self.video_port)
            if self.tcp_addr:
                self.viewer_addr = (self.tcp_addr[0], port)
                print(f"  [Network] Viewer UDP target: {self.viewer_addr}")
            # Host의 STUN 주소를 Viewer에게 전송
            if self.stun_addr:
                self.send_control({"cmd": "host_udp_addr", "ip": self.stun_addr[0], "port": self.stun_addr[1]})
                print(f"  [Network] Host STUN 주소 전송: {self.stun_addr[0]}:{self.stun_addr[1]}")
        elif cmd == "set_udp_addr":
            # Viewer의 STUN 기반 공인 주소
            ip = ctrl.get("ip")
            port = ctrl.get("port")
            if ip and port:
                old = self.viewer_addr
                self.viewer_addr = (ip, port)
                print(f"  [Network] Viewer STUN 주소: {ip}:{port} (기존: {old})")
                # Viewer에게 홀펀칭 패킷 전송
                punch = struct.pack(HEADER_FMT, PKT_PONG, 0, 0, 0)
                for _ in range(5):
                    self.udp_sock.sendto(punch, (ip, port))
                print(f"  [Network] STUN 기반 hole-punch → {ip}:{port}")
        elif cmd == "request_tcp_video":
            self.tcp_video = True
            print(f"  [Network] TCP 비디오 폴백 활성화 (UDP 수신 불가)")

    def _udp_recv_loop(self):
        """UDP 수신 - viewer의 홀펀치 패킷으로 실제 NAT 주소 파악"""
        self.udp_sock.settimeout(1.0)
        while self.running:
            try:
                data, addr = self.udp_sock.recvfrom(2048)
                if addr != self.viewer_addr:
                    self.viewer_addr = addr
                    print(f"  [Network] Viewer UDP address (hole-punch): {addr[0]}:{addr[1]}")
            except socket.timeout:
                continue
            except Exception:
                if not self.running:
                    break

    def send_video_nal(self, nal_data, nal_type, is_keyframe=False):
        """하나의 NAL unit을 UDP(또는 TCP 폴백)로 전송"""
        if self.tcp_video:
            flags = FLAG_KEYFRAME if is_keyframe else 0
            self._send_tcp_with_flags(PKT_VIDEO, flags, nal_data)
            return

        if not self.viewer_addr or not self.udp_sock:
            return

        flags = FLAG_KEYFRAME if is_keyframe else 0

        if len(nal_data) <= MAX_UDP_PAYLOAD:
            self._send_udp(PKT_VIDEO, flags, nal_data)
        else:
            # NAL이 MTU보다 크면 분할
            offset = 0
            while offset < len(nal_data):
                chunk = nal_data[offset:offset + MAX_UDP_PAYLOAD]
                offset += MAX_UDP_PAYLOAD

                frag_flags = flags | FLAG_FRAGMENT
                if offset >= len(nal_data):
                    frag_flags |= FLAG_LAST_FRAGMENT

                self._send_udp(PKT_VIDEO, frag_flags, chunk)

    def _send_udp(self, pkt_type, flags, payload):
        """UDP 패킷 전송"""
        self.seq = (self.seq + 1) & 0xFFFF
        header = struct.pack(HEADER_FMT, pkt_type, flags, self.seq, len(payload))
        try:
            self.udp_sock.sendto(header + payload, self.viewer_addr)
            self.bytes_sent += len(header) + len(payload)
            self.packets_sent += 1
        except Exception:
            pass

    def _send_tcp(self, pkt_type, payload=b""):
        """TCP 패킷 전송"""
        self._send_tcp_with_flags(pkt_type, 0, payload)

    def _send_tcp_with_flags(self, pkt_type, flags, payload=b""):
        """TCP 패킷 전송 (플래그 포함, 스레드 안전)"""
        if not self.tcp_conn:
            return
        with self._tcp_lock:
            self.seq = (self.seq + 1) & 0xFFFF
            header = struct.pack(HEADER_FMT, pkt_type, flags, self.seq, len(payload))
            try:
                self.tcp_conn.sendall(header + payload)
                if pkt_type == PKT_VIDEO:
                    self.bytes_sent += len(header) + len(payload)
                    self.packets_sent += 1
            except Exception as e:
                if self.running:
                    logging.debug(f"TCP send error: {e}")

    def send_sps_pps(self, sps_pps_data):
        """SPS+PPS를 TCP로 확실하게 전송 (Viewer 접속 시)"""
        if sps_pps_data and self.tcp_conn:
            self._send_tcp(PKT_VIDEO, sps_pps_data)

    def send_control(self, data):
        """제어 메시지 전송 (TCP)"""
        payload = json.dumps(data).encode("utf-8")
        self._send_tcp(PKT_CONTROL, payload)

    def get_stats(self):
        return {
            "bytes_sent": self.bytes_sent,
            "packets_sent": self.packets_sent,
            "mbps": (self.bytes_sent * 8) / (1024 * 1024),
            "connected": self.viewer_addr is not None,
        }

    def stop(self):
        self.running = False
        if self.tcp_conn:
            try:
                self.tcp_conn.close()
            except:
                pass
        if self.tcp_sock:
            try:
                self.tcp_sock.close()
            except:
                pass
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except:
                pass
        print(f"  [Network] Stopped ({self.bytes_sent / 1024 / 1024:.1f} MB sent)")
