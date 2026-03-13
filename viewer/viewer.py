"""
Ghost Display - Viewer
pygame 기반: H.264 디코딩 + 화면 표시 + 마우스/키보드 캡처
"""

import socket
import struct
import threading
import json
import time
import subprocess
import sys
import os
import argparse
import numpy as np

import pygame

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from common.upnp import setup_upnp, cleanup_upnp
from common.stun import stun_get_mapped_address

# 패킷 프로토콜
HEADER_FMT = "!BBHI"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

PKT_VIDEO = 0x01
PKT_INPUT = 0x10
PKT_CONTROL = 0x20
PKT_PING = 0x30
PKT_PONG = 0x31

FLAG_KEYFRAME = 0x01
FLAG_FRAGMENT = 0x02
FLAG_LAST_FRAGMENT = 0x04

FFMPEG_PATHS = [
    os.path.expanduser("~/AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe"),
    "ffmpeg",
]


def find_ffmpeg():
    for path in FFMPEG_PATHS:
        if os.path.exists(path):
            return path
    return "ffmpeg"


# pygame scancode → keyboard scan code 매핑
# pygame은 SDL scancode 사용, Host는 HID/PS2 scan code 필요
SDL_TO_SCAN = {
    pygame.K_ESCAPE: 0x01, pygame.K_1: 0x02, pygame.K_2: 0x03, pygame.K_3: 0x04,
    pygame.K_4: 0x05, pygame.K_5: 0x06, pygame.K_6: 0x07, pygame.K_7: 0x08,
    pygame.K_8: 0x09, pygame.K_9: 0x0A, pygame.K_0: 0x0B,
    pygame.K_MINUS: 0x0C, pygame.K_EQUALS: 0x0D, pygame.K_BACKSPACE: 0x0E,
    pygame.K_TAB: 0x0F,
    pygame.K_q: 0x10, pygame.K_w: 0x11, pygame.K_e: 0x12, pygame.K_r: 0x13,
    pygame.K_t: 0x14, pygame.K_y: 0x15, pygame.K_u: 0x16, pygame.K_i: 0x17,
    pygame.K_o: 0x18, pygame.K_p: 0x19,
    pygame.K_LEFTBRACKET: 0x1A, pygame.K_RIGHTBRACKET: 0x1B,
    pygame.K_RETURN: 0x1C,
    pygame.K_a: 0x1E, pygame.K_s: 0x1F, pygame.K_d: 0x20, pygame.K_f: 0x21,
    pygame.K_g: 0x22, pygame.K_h: 0x23, pygame.K_j: 0x24, pygame.K_k: 0x25,
    pygame.K_l: 0x26,
    pygame.K_SEMICOLON: 0x27, pygame.K_QUOTE: 0x28, pygame.K_BACKQUOTE: 0x29,
    pygame.K_LSHIFT: 0x2A, pygame.K_BACKSLASH: 0x2B,
    pygame.K_z: 0x2C, pygame.K_x: 0x2D, pygame.K_c: 0x2E, pygame.K_v: 0x2F,
    pygame.K_b: 0x30, pygame.K_n: 0x31, pygame.K_m: 0x32,
    pygame.K_COMMA: 0x33, pygame.K_PERIOD: 0x34, pygame.K_SLASH: 0x35,
    pygame.K_RSHIFT: 0x36, pygame.K_LALT: 0x38,
    pygame.K_SPACE: 0x39, pygame.K_CAPSLOCK: 0x3A,
    pygame.K_F1: 0x3B, pygame.K_F2: 0x3C, pygame.K_F3: 0x3D, pygame.K_F4: 0x3E,
    pygame.K_F5: 0x3F, pygame.K_F6: 0x40, pygame.K_F7: 0x41, pygame.K_F8: 0x42,
    pygame.K_F9: 0x43, pygame.K_F10: 0x44, pygame.K_F11: 0x57, pygame.K_F12: 0x58,
    pygame.K_LCTRL: 0x1D,
}

# E0 확장 키 (scan code + E0 prefix)
SDL_TO_SCAN_E0 = {
    pygame.K_RCTRL: 0x1D, pygame.K_RALT: 0x38,
    pygame.K_UP: 0x48, pygame.K_DOWN: 0x50,
    pygame.K_LEFT: 0x4B, pygame.K_RIGHT: 0x4D,
    pygame.K_HOME: 0x47, pygame.K_END: 0x4F,
    pygame.K_PAGEUP: 0x49, pygame.K_PAGEDOWN: 0x51,
    pygame.K_INSERT: 0x52, pygame.K_DELETE: 0x53,
}


class GhostViewer:
    def __init__(self, host_ip, video_port=9000, control_port=9001):
        self.host_ip = host_ip
        self.video_port = video_port
        self.control_port = control_port

        self.tcp_sock = None
        self.udp_sock = None
        self.decoder = None
        self.running = False

        self.stream_info = None
        self.stream_width = 1920
        self.stream_height = 1080

        # 프래그먼트 재조립
        self.frag_buffer = bytearray()
        self.got_sps_pps = False
        self.sps_pps_data = b""
        self.waiting_for_keyframe = True

        # 디코딩된 프레임 (공유)
        self.frame_lock = threading.Lock()
        self.latest_surface = None
        self.new_frame = False

        # 통계
        self.bytes_received = 0
        self.nals_received = 0

        # 입력 활성화 (F12로 토글)
        self.input_active = False

    def start(self):
        print("=" * 50)
        print("  Ghost Display - Viewer")
        print("=" * 50)

        self.running = True

        # 0. UPnP 자동 포트포워딩
        upnp_ok, external_ip = setup_upnp([self.video_port])
        if upnp_ok:
            print(f"  [Viewer] UPnP: UDP {self.video_port} 포트포워딩 완료 (외부 IP: {external_ip})")
        else:
            print(f"  [Viewer] UPnP: 자동 포트포워딩 실패 - 수동 설정 필요할 수 있음")

        # 1. TCP 연결
        self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.tcp_sock.connect((self.host_ip, self.control_port))
        except ConnectionRefusedError:
            print(f"  [!] Cannot connect to {self.host_ip}:{self.control_port}")
            return
        print(f"  [Viewer] TCP connected to {self.host_ip}")

        self._send_control({"cmd": "set_udp_port", "port": self.video_port})

        # 2. UDP 소켓
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        self.udp_sock.bind(("0.0.0.0", self.video_port))
        self.udp_sock.settimeout(0.5)

        # 2-1. STUN으로 공인 주소 확인
        stun_result = stun_get_mapped_address(self.udp_sock)
        if stun_result:
            pub_ip, pub_port = stun_result
            print(f"  [Viewer] STUN: 공인 주소 {pub_ip}:{pub_port}")
            # Host에게 실제 공인 UDP 주소 알려주기
            self._send_control({"cmd": "set_udp_addr", "ip": pub_ip, "port": pub_port})
        else:
            print(f"  [Viewer] STUN: 공인 주소 확인 실패")

        # 2-2. Host의 STUN 주소 수신 대기용
        self.host_udp_addr = None

        # NAT 홀펀칭: 호스트에 UDP 패킷 먼저 보내서 NAT 매핑 생성
        punch = struct.pack(HEADER_FMT, PKT_PING, 0, 0, 0)
        for _ in range(3):
            self.udp_sock.sendto(punch, (self.host_ip, self.video_port))
        print(f"  [Viewer] UDP hole-punch sent to {self.host_ip}:{self.video_port}")

        # 주기적 NAT keep-alive + 홀펀칭 (매핑 만료 방지)
        def _udp_keepalive():
            while self.running:
                try:
                    self.udp_sock.sendto(punch, (self.host_ip, self.video_port))
                    # Host의 STUN 주소로도 홀펀칭
                    if self.host_udp_addr:
                        self.udp_sock.sendto(punch, self.host_udp_addr)
                except:
                    pass
                time.sleep(5)
        threading.Thread(target=_udp_keepalive, daemon=True).start()

        # 3. TCP 수신 스레드
        threading.Thread(target=self._tcp_recv_loop, daemon=True).start()

        # SPS/PPS 대기
        print("  [Viewer] Waiting for SPS/PPS...")
        for _ in range(30):
            if self.got_sps_pps:
                break
            time.sleep(0.1)

        # 4. FFmpeg 디코더 시작 (H.264 → raw BGR)
        self._start_decoder()

        # SPS/PPS 주입
        if self.sps_pps_data:
            self._feed_decoder(self.sps_pps_data)
            print(f"  [Viewer] SPS/PPS injected ({len(self.sps_pps_data)} bytes)")

        # 5. UDP 수신 스레드
        threading.Thread(target=self._udp_recv_loop, daemon=True).start()

        # 6. pygame 메인 루프 (표시 + 입력)
        self._pygame_loop()

    def _start_decoder(self):
        """FFmpeg H.264 → raw BGR24 디코더"""
        ffmpeg = find_ffmpeg()
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-flags", "low_delay",
            "-fflags", "nobuffer",
            "-f", "h264",
            "-probesize", "32768",
            "-analyzeduration", "0",
            "-i", "pipe:0",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{self.stream_width}x{self.stream_height}",
            "-threads", "2",
            "-flush_packets", "1",
            "pipe:1",
        ]

        self.decoder = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

        # 디코딩된 프레임 읽기 스레드
        threading.Thread(target=self._read_decoded_frames, daemon=True).start()
        # FFmpeg 에러 읽기 스레드
        threading.Thread(target=self._read_decoder_errors, daemon=True).start()
        print(f"  [Viewer] Decoder started ({self.stream_width}x{self.stream_height})")

    def _read_decoded_frames(self):
        """FFmpeg stdout에서 raw 프레임 읽기 → pygame Surface 변환"""
        frame_size = self.stream_width * self.stream_height * 3  # BGR24
        buf = bytearray()

        while self.running and self.decoder:
            try:
                chunk = self.decoder.stdout.read(min(65536, frame_size - len(buf)))
                if not chunk:
                    break

                buf.extend(chunk)

                while len(buf) >= frame_size:
                    frame_data = bytes(buf[:frame_size])
                    del buf[:frame_size]

                    frame = np.frombuffer(frame_data, dtype=np.uint8).reshape(
                        self.stream_height, self.stream_width, 3)
                    rgb = np.ascontiguousarray(frame[:, :, ::-1])
                    surface = pygame.surfarray.make_surface(rgb.transpose(1, 0, 2))

                    with self.frame_lock:
                        self.latest_surface = surface
                        self.new_frame = True

            except Exception as e:
                if self.running:
                    print(f"  [Viewer] Decode error: {e}")
                break

    def _read_decoder_errors(self):
        """FFmpeg stderr 비동기 읽기"""
        try:
            for line in self.decoder.stderr:
                msg = line.decode("utf-8", errors="replace").strip()
                if msg:
                    print(f"  [Viewer/FFmpeg] {msg}")
        except:
            pass

    def _feed_decoder(self, data):
        """H.264 데이터를 디코더에 전달"""
        if self.decoder and self.decoder.poll() is None:
            try:
                self.decoder.stdin.write(data)
            except (BrokenPipeError, OSError):
                pass

    def _pygame_loop(self):
        """pygame 메인 루프: 화면 표시 + 입력 캡처"""
        pygame.init()

        # 윈도우 크기 (스트림 해상도 또는 축소)
        display_w = min(self.stream_width, 1920)
        display_h = min(self.stream_height, 1080)
        scale = min(display_w / self.stream_width, display_h / self.stream_height)
        display_w = int(self.stream_width * scale)
        display_h = int(self.stream_height * scale)

        screen = pygame.display.set_mode((display_w, display_h), pygame.RESIZABLE)
        pygame.display.set_caption("Ghost Display [F12: Input OFF]")
        clock = pygame.time.Clock()

        print(f"  [Viewer] Window: {display_w}x{display_h}")
        print(f"  [Viewer] Press F12 to toggle input capture")

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    break

                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_F12:
                        self.input_active = not self.input_active
                        state = "ON" if self.input_active else "OFF"
                        pygame.display.set_caption(f"Ghost Display [F12: Input {state}]")
                        if self.input_active:
                            pygame.event.set_grab(True)
                            pygame.mouse.set_visible(False)
                        else:
                            pygame.event.set_grab(False)
                            pygame.mouse.set_visible(True)
                    elif self.input_active:
                        self._send_key(event.key, down=True)

                elif event.type == pygame.KEYUP:
                    if self.input_active and event.key != pygame.K_F12:
                        self._send_key(event.key, down=False)

                elif event.type == pygame.MOUSEMOTION:
                    if self.input_active:
                        x, y = event.pos
                        # 표시 좌표 → 스트림 좌표 변환
                        stream_x = int(x * self.stream_width / display_w)
                        stream_y = int(y * self.stream_height / display_h)
                        self._send_input({
                            "type": "mouse_move",
                            "x": stream_x,
                            "y": stream_y,
                        })

                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if self.input_active:
                        btn = {1: "left", 2: "middle", 3: "right"}.get(event.button, "left")
                        if event.button in (4, 5):
                            # 휠
                            delta = 120 if event.button == 4 else -120
                            self._send_input({"type": "mouse_wheel", "delta": delta})
                        else:
                            self._send_input({"type": "mouse_down", "button": btn})

                elif event.type == pygame.MOUSEBUTTONUP:
                    if self.input_active:
                        btn = {1: "left", 2: "middle", 3: "right"}.get(event.button, "left")
                        if event.button not in (4, 5):
                            self._send_input({"type": "mouse_up", "button": btn})

                elif event.type == pygame.VIDEORESIZE:
                    display_w, display_h = event.w, event.h
                    screen = pygame.display.set_mode((display_w, display_h), pygame.RESIZABLE)

            # 새 프레임 표시
            with self.frame_lock:
                if self.new_frame and self.latest_surface:
                    scaled = pygame.transform.scale(self.latest_surface, (display_w, display_h))
                    screen.blit(scaled, (0, 0))
                    self.new_frame = False

            pygame.display.flip()
            clock.tick(60)

        pygame.quit()

    def _send_key(self, key, down):
        """pygame 키 → scan code 변환 후 전송"""
        if key in SDL_TO_SCAN_E0:
            self._send_input({
                "type": "key_down" if down else "key_up",
                "scan": SDL_TO_SCAN_E0[key],
                "e0": True,
            })
        elif key in SDL_TO_SCAN:
            self._send_input({
                "type": "key_down" if down else "key_up",
                "scan": SDL_TO_SCAN[key],
                "e0": False,
            })

    # --- 네트워크 ---

    def _udp_recv_loop(self):
        while self.running:
            try:
                data, addr = self.udp_sock.recvfrom(65536)
                if len(data) < HEADER_SIZE:
                    continue

                pkt_type, flags, seq, size = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
                payload = data[HEADER_SIZE:]

                if pkt_type == PKT_VIDEO:
                    self._handle_video(flags, payload)
                self.bytes_received += len(data)

            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"  [Viewer] UDP error: {e}")
                break

    def _handle_video(self, flags, payload):
        if flags & FLAG_FRAGMENT:
            self.frag_buffer.extend(payload)
            if flags & FLAG_LAST_FRAGMENT:
                nal = bytes(self.frag_buffer)
                self.frag_buffer = bytearray()
                self._process_nal(nal, flags)
        else:
            self._process_nal(payload, flags)

    def _process_nal(self, nal_data, flags):
        if self.waiting_for_keyframe:
            if flags & FLAG_KEYFRAME:
                self.waiting_for_keyframe = False
            elif self._is_sps_or_pps(nal_data):
                pass
            else:
                return

        self._feed_decoder(nal_data)
        self.nals_received += 1

    def _is_sps_or_pps(self, data):
        if data[:4] == b'\x00\x00\x00\x01':
            nal_byte = data[4] if len(data) > 4 else 0
        elif data[:3] == b'\x00\x00\x01':
            nal_byte = data[3] if len(data) > 3 else 0
        else:
            nal_byte = data[0] if data else 0
        return (nal_byte & 0x1F) in (7, 8)

    def _tcp_recv_loop(self):
        buf = b""
        self.tcp_sock.settimeout(1.0)
        while self.running:
            try:
                data = self.tcp_sock.recv(4096)
                if not data:
                    break
                buf += data
                while len(buf) >= HEADER_SIZE:
                    pkt_type, flags, seq, size = struct.unpack(HEADER_FMT, buf[:HEADER_SIZE])
                    if len(buf) < HEADER_SIZE + size:
                        break
                    payload = buf[HEADER_SIZE:HEADER_SIZE + size]
                    buf = buf[HEADER_SIZE + size:]

                    if pkt_type == PKT_VIDEO:
                        self.sps_pps_data += payload
                        self.got_sps_pps = True
                    elif pkt_type == PKT_CONTROL:
                        try:
                            ctrl = json.loads(payload.decode("utf-8"))
                            self._handle_control(ctrl)
                        except:
                            pass
            except socket.timeout:
                continue
            except:
                break

    def _handle_control(self, ctrl):
        cmd = ctrl.get("cmd")
        if cmd == "stream_info":
            self.stream_info = ctrl
            self.stream_width = ctrl.get("width", 1920)
            self.stream_height = ctrl.get("height", 1080)
            print(f"  [Viewer] Stream: {self.stream_width}x{self.stream_height} "
                  f"@ {ctrl.get('fps')}fps")
        elif cmd == "host_udp_addr":
            ip = ctrl.get("ip")
            port = ctrl.get("port")
            if ip and port:
                self.host_udp_addr = (ip, port)
                print(f"  [Viewer] Host STUN 주소: {ip}:{port}")
                # 즉시 홀펀칭
                punch = struct.pack(HEADER_FMT, PKT_PING, 0, 0, 0)
                for _ in range(5):
                    self.udp_sock.sendto(punch, (ip, port))
                print(f"  [Viewer] STUN 기반 hole-punch → {ip}:{port}")

    def _send_control(self, data):
        payload = json.dumps(data).encode("utf-8")
        header = struct.pack(HEADER_FMT, PKT_CONTROL, 0, 0, len(payload))
        try:
            self.tcp_sock.sendall(header + payload)
        except:
            pass

    def _send_input(self, event):
        payload = json.dumps(event).encode("utf-8")
        header = struct.pack(HEADER_FMT, PKT_INPUT, 0, 0, len(payload))
        try:
            self.tcp_sock.sendall(header + payload)
        except:
            pass

    def stop(self):
        self.running = False
        cleanup_upnp()
        if self.decoder:
            try:
                self.decoder.stdin.close()
                self.decoder.terminate()
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


def main():
    parser = argparse.ArgumentParser(description="Ghost Display - Viewer")
    parser.add_argument("host", help="Host IP address")
    parser.add_argument("--video-port", type=int, default=9000)
    parser.add_argument("--control-port", type=int, default=9001)
    args = parser.parse_args()

    viewer = GhostViewer(args.host, args.video_port, args.control_port)
    try:
        viewer.start()
    except KeyboardInterrupt:
        pass
    finally:
        viewer.stop()


if __name__ == "__main__":
    main()
