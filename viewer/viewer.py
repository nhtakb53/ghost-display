"""
Ghost Display - Viewer
pygame 기반: H.264 디코딩 + 화면 표시 + 마우스/키보드 캡처
--test 모드: pygame 없이 CLI에서 패킷 흐름 모니터링
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

# --test 모드에서는 pygame 불필요
_test_mode = "--test" in sys.argv
if not _test_mode:
    import pygame

# 모든 print에 타임스탬프 자동 추가
import builtins
from datetime import datetime
_original_print = builtins.print
def _timed_print(*args, **kwargs):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _original_print(f"{ts}", *args, **kwargs)
builtins.print = _timed_print

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


# pygame scancode → keyboard scan code 매핑 (테스트 모드에서는 불필요)
SDL_TO_SCAN = {}
SDL_TO_SCAN_E0 = {}

def _init_keymaps():
    global SDL_TO_SCAN, SDL_TO_SCAN_E0
    if _test_mode:
        return
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
    SDL_TO_SCAN_E0 = {
        pygame.K_RCTRL: 0x1D, pygame.K_RALT: 0x38,
        pygame.K_UP: 0x48, pygame.K_DOWN: 0x50,
        pygame.K_LEFT: 0x4B, pygame.K_RIGHT: 0x4D,
        pygame.K_HOME: 0x47, pygame.K_END: 0x4F,
        pygame.K_PAGEUP: 0x49, pygame.K_PAGEDOWN: 0x51,
        pygame.K_INSERT: 0x52, pygame.K_DELETE: 0x53,
    }


# NAL type 이름 매핑
NAL_TYPE_NAMES = {
    1: "P-frame", 2: "SLICE_A", 3: "SLICE_B", 4: "SLICE_C",
    5: "IDR", 6: "SEI", 7: "SPS", 8: "PPS", 9: "AUD",
}


class GhostViewer:
    def __init__(self, host_ip, video_port=9000, control_port=9001, test_mode=False):
        self.host_ip = host_ip
        self.video_port = video_port
        self.control_port = control_port
        self.test_mode = test_mode

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
        self.frames_decoded = 0
        self.frames_rendered = 0
        self.packets_received = 0
        self.keyframes_received = 0
        self._stats_start = 0
        self._last_stats_time = 0
        self._last_stats_bytes = 0
        self._last_stats_nals = 0
        self._last_stats_frames_decoded = 0
        self._last_stats_frames_rendered = 0
        self._last_stats_packets = 0

        # 입력 활성화 (창 클릭으로 캡처, Escape로 해제)
        self.input_active = False
        # 입력 모드 (F10으로 전환)
        self.input_mode = "kse"

    def start(self):
        if not self.test_mode:
            _init_keymaps()
        mode_str = " (TEST MODE)" if self.test_mode else ""
        print("=" * 50)
        print(f"  Ghost Display - Viewer{mode_str}")
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
        self.tcp_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
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
            self._send_control({"cmd": "set_udp_addr", "ip": pub_ip, "port": pub_port})
        else:
            print(f"  [Viewer] STUN: 공인 주소 확인 실패")

        # 2-2. Host의 STUN 주소 수신 대기용
        self.host_udp_addr = None

        # NAT 홀펀칭: 호스트에 UDP 패킷 먼저 보내서 NAT 매핑 생성
        punch = struct.pack(HEADER_FMT, PKT_PING, 0, 0, 0)
        for _ in range(10):
            self.udp_sock.sendto(punch, (self.host_ip, self.video_port))
        print(f"  [Viewer] UDP hole-punch sent to {self.host_ip}:{self.video_port}")

        # 주기적 NAT keep-alive + 홀펀칭 (매핑 만료 방지)
        def _udp_keepalive():
            while self.running:
                try:
                    self.udp_sock.sendto(punch, (self.host_ip, self.video_port))
                    if self.host_udp_addr:
                        self.udp_sock.sendto(punch, self.host_udp_addr)
                except:
                    pass
                time.sleep(1)
        threading.Thread(target=_udp_keepalive, daemon=True).start()

        # 3. TCP 수신 스레드
        threading.Thread(target=self._tcp_recv_loop, daemon=True).start()

        # SPS/PPS 대기 (최대 1초)
        print(f"  [Viewer] Waiting for SPS/PPS...")
        for _ in range(20):
            if self.got_sps_pps:
                break
            time.sleep(0.05)
        print(f"  [Viewer] SPS/PPS {'received' if self.got_sps_pps else 'TIMEOUT'}")

        if self.test_mode:
            # 테스트 모드: 디코더 없이 패킷 모니터링만
            print(f"  [Test] 디코더 생략 - 패킷 모니터링 모드")
            if self.sps_pps_data:
                self._dump_nal_info("TCP SPS/PPS", self.sps_pps_data)

            # UDP 수신 (테스트용 상세 로그)
            threading.Thread(target=self._udp_recv_loop, daemon=True).start()
            print(f"  [Test] UDP 수신 시작")

            # 상태 로그 (2초 간격)
            self._stats_start = time.time()
            self._last_stats_time = self._stats_start
            threading.Thread(target=self._stats_loop, daemon=True).start()

            # 테스트 CLI 루프
            self._test_cli_loop()
        else:
            # 4. FFmpeg 디코더 시작 (H.264 → raw BGR)
            self._start_decoder()
            print(f"  [Viewer] Decoder ready")

            # SPS/PPS 주입
            if self.sps_pps_data:
                self._feed_decoder(self.sps_pps_data)
                print(f"  [Viewer] SPS/PPS injected ({len(self.sps_pps_data)} bytes)")

            # 5. UDP 수신 스레드
            threading.Thread(target=self._udp_recv_loop, daemon=True).start()
            print(f"  [Viewer] UDP recv started")

            # 6. 상태 로그 스레드
            self._stats_start = time.time()
            self._last_stats_time = self._stats_start
            threading.Thread(target=self._stats_loop, daemon=True).start()

            # pygame 메인 루프 (표시 + 입력)
            self._pygame_loop()

    def _start_decoder(self):
        """FFmpeg H.264 → raw BGR24 디코더"""
        ffmpeg = find_ffmpeg()
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-flags", "low_delay",
            "-fflags", "+nobuffer+fastseek+flush_packets",
            "-flags2", "fast",
            "-f", "h264",
            "-probesize", "1024",
            "-analyzeduration", "0",
            "-i", "pipe:0",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{self.stream_width}x{self.stream_height}",
            "-threads", "1",
            "-flush_packets", "1",
            "-avioflags", "direct",
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
        first_frame = True

        while self.running and self.decoder:
            try:
                chunk = self.decoder.stdout.read(min(65536, frame_size - len(buf)))
                if not chunk:
                    break

                buf.extend(chunk)

                while len(buf) >= frame_size:
                    frame_data = bytes(buf[:frame_size])
                    del buf[:frame_size]

                    self.frames_decoded += 1

                    if first_frame:
                        print(f"  [Viewer] *** First frame decoded! ***")
                        first_frame = False

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
        pygame.display.set_caption("Ghost Display - 클릭하여 입력 캡처")
        clock = pygame.time.Clock()

        # 커서 이미지 생성 (흰색 테두리 + 검은색 화살표)
        cursor_surface = self._create_cursor_surface()

        print(f"  [Viewer] Window: {display_w}x{display_h}")
        print(f"  [Viewer] 창 클릭: 입력 캡처 | Escape: 캡처 해제")

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    break

                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE and self.input_active:
                        # Escape: 입력 캡처 해제
                        self.input_active = False
                        pygame.event.set_grab(False)
                        pygame.mouse.set_visible(True)
                        self._update_title()
                        print(f"  [Viewer] 입력 캡처 해제 (Escape)")
                    elif event.key == pygame.K_F10:
                        new_mode = "sendinput" if self.input_mode == "kse" else "kse"
                        self._send_control({"cmd": "switch_input_mode", "mode": new_mode})
                        self.input_mode = new_mode
                        self._update_title()
                        print(f"  [Viewer] 입력 모드 전환 요청: {new_mode}")
                    elif self.input_active:
                        self._send_key(event.key, down=True)

                elif event.type == pygame.KEYUP:
                    if self.input_active and event.key not in (pygame.K_ESCAPE, pygame.K_F10):
                        self._send_key(event.key, down=False)

                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if not self.input_active:
                        # 창 클릭 → 입력 캡처 시작
                        self.input_active = True
                        pygame.event.set_grab(True)
                        pygame.mouse.set_visible(False)
                        self._update_title()
                        print(f"  [Viewer] 입력 캡처 시작 (클릭)")
                    else:
                        btn = {1: "left", 2: "middle", 3: "right"}.get(event.button, "left")
                        if event.button in (4, 5):
                            delta = 120 if event.button == 4 else -120
                            self._send_input({"type": "mouse_wheel", "delta": delta})
                        else:
                            self._send_input({"type": "mouse_down", "button": btn})

                elif event.type == pygame.MOUSEBUTTONUP:
                    if self.input_active:
                        btn = {1: "left", 2: "middle", 3: "right"}.get(event.button, "left")
                        if event.button not in (4, 5):
                            self._send_input({"type": "mouse_up", "button": btn})

                elif event.type == pygame.MOUSEMOTION:
                    if self.input_active:
                        x, y = event.pos
                        stream_x = int(x * self.stream_width / display_w)
                        stream_y = int(y * self.stream_height / display_h)
                        self._send_input({
                            "type": "mouse_move",
                            "x": stream_x,
                            "y": stream_y,
                        })

                elif event.type == pygame.VIDEORESIZE:
                    display_w, display_h = event.w, event.h
                    screen = pygame.display.set_mode((display_w, display_h), pygame.RESIZABLE)

            # 새 프레임 표시
            with self.frame_lock:
                if self.new_frame and self.latest_surface:
                    self.frames_rendered += 1
                    if not hasattr(self, '_first_render_logged'):
                        print(f"  [Viewer] *** First frame rendered! ***")
                        self._first_render_logged = True
                    scaled = pygame.transform.scale(self.latest_surface, (display_w, display_h))
                    screen.blit(scaled, (0, 0))
                    self.new_frame = False

            # 입력 활성 시 마우스 커서 그리기
            if self.input_active:
                mx, my = pygame.mouse.get_pos()
                screen.blit(cursor_surface, (mx, my))

            pygame.display.flip()
            clock.tick(60)

        pygame.quit()

    def _create_cursor_surface(self):
        """화살표 모양 커서 Surface 생성"""
        size = 24
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        # 화살표 폴리곤 (Windows 기본 커서 형태)
        arrow = [(0, 0), (0, 18), (4, 14), (8, 22), (11, 20), (7, 13), (12, 13)]
        # 검은 테두리
        pygame.draw.polygon(surf, (0, 0, 0), arrow, 0)
        # 흰색 내부
        inner = [(1, 2), (1, 15), (5, 12), (9, 20), (10, 19), (6, 11), (11, 11)]
        pygame.draw.polygon(surf, (255, 255, 255), inner, 0)
        return surf

    def _update_title(self):
        if self.input_active:
            mode = self.input_mode.upper()
            pygame.display.set_caption(f"Ghost Display - 캡처 중 [Esc:해제] [F10:{mode}]")
        else:
            pygame.display.set_caption(f"Ghost Display - 클릭하여 입력 캡처")

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

    def _dump_nal_info(self, label, data):
        """NAL unit들을 파싱해서 상세 정보 출력"""
        pos = 0
        nal_idx = 0
        while pos < len(data) - 4:
            # start code 찾기
            if data[pos:pos+4] == b'\x00\x00\x00\x01':
                sc_len = 4
            elif data[pos:pos+3] == b'\x00\x00\x01':
                sc_len = 3
            else:
                pos += 1
                continue

            # 다음 start code 찾기
            next_pos = len(data)
            for j in range(pos + sc_len, len(data) - 3):
                if data[j:j+4] == b'\x00\x00\x00\x01' or data[j:j+3] == b'\x00\x00\x01':
                    next_pos = j
                    break

            nal_body = data[pos+sc_len:next_pos]
            if nal_body:
                nal_type = nal_body[0] & 0x1F
                nal_name = NAL_TYPE_NAMES.get(nal_type, f"type_{nal_type}")
                nal_size = next_pos - pos
                print(f"  [{label}] NAL #{nal_idx}: {nal_name} (type={nal_type}, {nal_size}B)")
            nal_idx += 1
            pos = next_pos

    def _test_cli_loop(self):
        """테스트 모드 CLI - Ctrl+C로 종료"""
        print()
        print("  ╔══════════════════════════════════════════╗")
        print("  ║  TEST MODE - 패킷 모니터링 중            ║")
        print("  ║  Ctrl+C 로 종료                          ║")
        print("  ║  2초마다 통계, 패킷별 상세 로그 출력      ║")
        print("  ╚══════════════════════════════════════════╝")
        print()
        try:
            while self.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n  [Test] 종료 중...")
            self.running = False

    def _stats_loop(self):
        """상태 로그 출력 (테스트모드 2초, 일반 5초)"""
        interval = 2 if self.test_mode else 5
        while self.running:
            time.sleep(interval)
            if not self.running:
                break

            now = time.time()
            dt = now - self._last_stats_time
            if dt <= 0:
                continue

            # 구간 통계 계산
            d_bytes = self.bytes_received - self._last_stats_bytes
            d_nals = self.nals_received - self._last_stats_nals
            d_decoded = self.frames_decoded - self._last_stats_frames_decoded
            d_rendered = self.frames_rendered - self._last_stats_frames_rendered
            d_packets = self.packets_received - self._last_stats_packets
            mbps = d_bytes * 8 / dt / 1024 / 1024

            # 상태 결정
            if d_decoded > 0:
                status = "streaming"
            elif d_packets > 0:
                status = "receiving (no decode)"
            elif self.got_sps_pps:
                status = "waiting for video"
            elif self.tcp_sock:
                status = "connected (no SPS/PPS)"
            else:
                status = "disconnected"

            if self.test_mode:
                total_elapsed = now - self._stats_start
                total_mb = self.bytes_received / 1024 / 1024
                print(f"  [Stats] ▼{mbps:.1f}Mbps {d_packets}pkts {d_nals}nals | "
                      f"keyframes:{self.keyframes_received} | "
                      f"총 {total_mb:.1f}MB {self.packets_received}pkts "
                      f"{total_elapsed:.0f}s | {status}")
            else:
                print(f"  [Stats] recv:{mbps:.1f}Mbps {d_packets}pkts {d_nals}nals | "
                      f"dec:{d_decoded/dt:.0f}fps render:{d_rendered/dt:.0f}fps | "
                      f"keyframes:{self.keyframes_received} | {status}")

            # 스냅샷 갱신
            self._last_stats_time = now
            self._last_stats_bytes = self.bytes_received
            self._last_stats_nals = self.nals_received
            self._last_stats_frames_decoded = self.frames_decoded
            self._last_stats_frames_rendered = self.frames_rendered
            self._last_stats_packets = self.packets_received

    # --- 네트워크 ---

    def _udp_recv_loop(self):
        first_udp = True
        first_video = True
        first_keyframe = True
        PKT_NAMES = {PKT_VIDEO: "VIDEO", PKT_INPUT: "INPUT", PKT_CONTROL: "CTRL",
                     PKT_PING: "PING", PKT_PONG: "PONG"}
        while self.running:
            try:
                data, addr = self.udp_sock.recvfrom(65536)
                if first_udp:
                    print(f"  [Viewer] First UDP packet from {addr}")
                    first_udp = False
                if len(data) < HEADER_SIZE:
                    if self.test_mode:
                        print(f"  [UDP<] 짧은 패킷 {len(data)}B from {addr}")
                    continue

                pkt_type, flags, seq, size = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
                payload = data[HEADER_SIZE:]

                self.packets_received += 1

                if self.test_mode:
                    pkt_name = PKT_NAMES.get(pkt_type, f"0x{pkt_type:02X}")
                    flag_parts = []
                    if flags & FLAG_KEYFRAME:
                        flag_parts.append("KEY")
                    if flags & FLAG_FRAGMENT:
                        flag_parts.append("FRAG")
                    if flags & FLAG_LAST_FRAGMENT:
                        flag_parts.append("LAST")
                    flag_str = "|".join(flag_parts) if flag_parts else "-"

                    if pkt_type == PKT_VIDEO:
                        # NAL 타입은 첫 fragment나 단독 패킷에서만 식별 가능
                        nal_info = ""
                        is_first_or_solo = not (flags & FLAG_FRAGMENT) or \
                            (flags & FLAG_FRAGMENT and getattr(self, '_frag_count', 0) == 0)
                        if is_first_or_solo and payload:
                            if payload[:4] == b'\x00\x00\x00\x01' and len(payload) > 4:
                                nt = payload[4] & 0x1F
                            elif payload[:3] == b'\x00\x00\x01' and len(payload) > 3:
                                nt = payload[3] & 0x1F
                            else:
                                nt = payload[0] & 0x1F if payload else -1
                            nal_name = NAL_TYPE_NAMES.get(nt, f"type_{nt}")
                            nal_info = f" NAL={nal_name}"

                        # fragment 중간 패킷은 카운트만 하고 출력 생략
                        if flags & FLAG_FRAGMENT and not (flags & FLAG_LAST_FRAGMENT):
                            if not hasattr(self, '_frag_count'):
                                self._frag_count = 0
                                self._frag_total_bytes = 0
                            self._frag_count += 1
                            self._frag_total_bytes += len(payload)
                            # 첫 fragment만 출력 (NAL 타입 포함)
                            if self._frag_count == 1:
                                print(f"  [UDP<] {pkt_name} seq={seq} flags=[{flag_str}] "
                                      f"{len(payload)}B{nal_info} ...")
                        elif flags & FLAG_LAST_FRAGMENT:
                            frag_count = getattr(self, '_frag_count', 0) + 1
                            frag_bytes = getattr(self, '_frag_total_bytes', 0) + len(payload)
                            print(f"  [UDP<] {pkt_name} seq={seq} flags=[{flag_str}] "
                                  f"조립완료 {frag_count}조각 {frag_bytes}B")
                            self._frag_count = 0
                            self._frag_total_bytes = 0
                        else:
                            print(f"  [UDP<] {pkt_name} seq={seq} flags=[{flag_str}] "
                                  f"{len(payload)}B{nal_info}")

                        # fragment 상태 추적
                        if flags & FLAG_FRAGMENT and not (flags & FLAG_LAST_FRAGMENT):
                            self._in_fragment = True
                        else:
                            self._in_fragment = False
                    else:
                        print(f"  [UDP<] {pkt_name} seq={seq} {len(payload)}B")

                if pkt_type == PKT_VIDEO:
                    if first_video:
                        print(f"  [Viewer] First video packet (flags={flags:#x}, {len(payload)}B)")
                        first_video = False
                    # 키프레임은 단독 패킷이거나 첫 fragment일 때만 카운트
                    if (flags & FLAG_KEYFRAME) and not (
                        (flags & FLAG_FRAGMENT) and self.frag_buffer
                    ):
                        self.keyframes_received += 1
                        if first_keyframe:
                            print(f"  [Viewer] First KEYFRAME received!")
                            first_keyframe = False
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
        # UDP로 받은 SPS/PPS도 디코더에 주입 (TCP에서 못 받았을 때 보완)
        if self._is_sps_or_pps(nal_data):
            if not self.got_sps_pps:
                print(f"  [Viewer] SPS/PPS received via UDP ({len(nal_data)}B)")
            self.got_sps_pps = True
            self._feed_decoder(nal_data)
            self.nals_received += 1
            return

        if self.waiting_for_keyframe:
            if flags & FLAG_KEYFRAME:
                self.waiting_for_keyframe = False
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
                    if self.test_mode:
                        print(f"  [TCP<] 연결 끊김")
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
                        if self.test_mode:
                            self._dump_nal_info("TCP<VIDEO", payload)
                    elif pkt_type == PKT_CONTROL:
                        try:
                            ctrl = json.loads(payload.decode("utf-8"))
                            if self.test_mode:
                                print(f"  [TCP<CTRL] {json.dumps(ctrl, ensure_ascii=False)}")
                            self._handle_control(ctrl)
                        except:
                            pass
                    elif self.test_mode:
                        print(f"  [TCP<] type=0x{pkt_type:02X} flags={flags:#x} {len(payload)}B")
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
        elif cmd == "input_mode_changed":
            self.input_mode = ctrl.get("mode", self.input_mode)
            print(f"  [Viewer] 호스트 입력 모드: {self.input_mode}")
            if not _test_mode:
                self._update_title()
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
    parser.add_argument("--test", action="store_true",
                        help="테스트 모드: pygame 없이 CLI에서 패킷 모니터링")
    args = parser.parse_args()

    viewer = GhostViewer(args.host, args.video_port, args.control_port,
                         test_mode=args.test)
    try:
        viewer.start()
    except KeyboardInterrupt:
        pass
    finally:
        viewer.stop()


if __name__ == "__main__":
    main()
