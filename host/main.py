"""
Ghost Display - Host
게임 PC에서 실행. 화면 캡처 → 인코딩 → 스트리밍.
"""

import sys
import os
import time
import signal
import argparse
import threading

import cv2
import numpy as np
from capture import ScreenCapture as WGCCapture
from encoder import H264Encoder
from network import StreamServer
from input_handler import InputHandler
from virtual_display import VirtualDisplayManager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from common.upnp import setup_upnp, cleanup_upnp


class GhostHost:
    def __init__(self, args):
        self.args = args
        capture_mode = getattr(args, 'capture_mode', 'wgc')
        if capture_mode == 'dxgi':
            from capture_dxgi import DXGICapture
            self.capture = DXGICapture(
                monitor_index=args.monitor,
                target_fps=args.fps,
            )
        else:
            self.capture = WGCCapture(
                monitor_index=args.monitor,
                target_fps=args.fps,
            )
        self.encoder = None
        self.network = StreamServer(
            host="0.0.0.0",
            video_port=args.video_port,
            control_port=args.control_port,
        )
        self.input_handler = InputHandler(force_sendinput=getattr(args, 'sendinput', False))
        self.vdisplay = VirtualDisplayManager()
        self.running = False
        self.scale = 1.0
        self.enc_w = 0
        self.enc_h = 0

    def start(self):
        print("=" * 50)
        print("  Ghost Display - Host")
        print("=" * 50)

        # 0. UPnP 자동 포트포워딩
        upnp_ok, external_ip = setup_upnp(
            [self.args.video_port, self.args.control_port],
            protocol="UDP",
            description="Ghost Display"
        )
        # TCP 포트도 등록
        setup_upnp([self.args.control_port], protocol="TCP", description="Ghost Display")
        if upnp_ok:
            print(f"  [Host] UPnP: 포트포워딩 완료 (외부 IP: {external_ip})")
        else:
            print(f"  [Host] UPnP: 자동 포트포워딩 실패 - 수동 설정 필요할 수 있음")

        # 1. 가상 디스플레이 (모니터 없으면 자동 생성)
        if not self.args.no_virtual_display:
            self.vdisplay.ensure_display(width=1920, height=1080, refresh=self.args.fps)

        # 2. 커널 드라이버 연결
        if not self.input_handler.connect():
            print("  [Host] WARNING: Kernel input not available, input disabled")

        # 2. 네트워크 시작 (viewer 연결 대기)
        self.network.on_input = self.input_handler.handle_event
        self.network.on_connected = self._on_viewer_connected
        self.network.start()

        # 3. 캡처 시작
        self.capture.on_reconnect = self._on_capture_reconnect
        self.capture.start()

        # 첫 프레임 대기 (해상도 확인)
        print("\n  [Host] Waiting for first frame...")
        frame = None
        for _ in range(50):
            frame = self.capture.get_frame(timeout=0.1)
            if frame is not None:
                break

        if frame is None:
            print("  [Host] ERROR: No frames captured!")
            return

        cap_h, cap_w = frame.shape[:2]
        print(f"  [Host] Capture: {cap_w}x{cap_h}")

        # 스케일 결정 (4K면 자동으로 1080p로)
        scale = self.args.scale
        if scale == 0:
            if cap_w > 1920:
                scale = 1920 / cap_w
            else:
                scale = 1.0
        self.scale = scale
        self.enc_w = int(cap_w * scale) // 2 * 2
        self.enc_h = int(cap_h * scale) // 2 * 2

        if scale < 1.0:
            print(f"  [Host] Encoding at: {self.enc_w}x{self.enc_h} (scale {scale:.2f})")

        # 4. 인코더 시작 (입력=출력 해상도, Python에서 미리 리사이즈)
        self.encoder = H264Encoder(
            width=self.enc_w,
            height=self.enc_h,
            fps=self.args.fps,
            bitrate=self.args.bitrate,
            use_nvenc=not self.args.software,
        )
        self.encoder.on_nal = self._on_nal
        self.encoder.start()

        self.input_handler.update_resolution(self.enc_w, self.enc_h)

        # 5. 메인 루프
        self.running = True
        print(f"\n  [Host] Streaming at {self.args.fps}fps, {self.args.bitrate}")
        print(f"  [Host] Press Ctrl+C to stop\n")

        self._main_loop()

    def _on_capture_reconnect(self):
        """캡처 세션 재연결 시 호출"""
        self._restart_encoder()

    def _restart_encoder(self):
        """인코더 재시작 (스레드 안전)"""
        if not hasattr(self, '_encoder_lock'):
            self._encoder_lock = threading.Lock()
        if not self._encoder_lock.acquire(blocking=False):
            return  # 이미 재시작 중
        try:
            print("  [Host] Restarting encoder...")
            old_encoder = self.encoder
            if old_encoder:
                old_encoder.on_nal = None  # 콜백 해제 먼저
                old_encoder.stop()
            self.encoder = H264Encoder(
                width=self.enc_w,
                height=self.enc_h,
                fps=self.args.fps,
                bitrate=self.args.bitrate,
                use_nvenc=not self.args.software,
            )
            self.encoder.on_nal = self._on_nal
            self.encoder.start()
            # 새 SPS/PPS가 나오면 연결된 viewer에게 재전송
            self._need_resend_sps = True
        finally:
            self._encoder_lock.release()

    def _main_loop(self):
        """캡처 → 리사이즈 → 인코딩 메인 루프"""
        frame_interval = 1.0 / self.args.fps
        stats_interval = 5.0
        last_stats = time.time()

        while self.running:
            loop_start = time.time()

            frame = self.capture.get_frame(timeout=frame_interval)
            if frame is None:
                continue

            # Python에서 리사이즈 (cv2 사용, FFmpeg 파이프 병목 해소)
            h, w = frame.shape[:2]
            if w != self.enc_w or h != self.enc_h:
                frame = cv2.resize(frame, (self.enc_w, self.enc_h),
                                   interpolation=cv2.INTER_LINEAR)

            # 인코딩
            if self.encoder and self.encoder.running:
                self.encoder.encode_frame(frame)

            # 통계 출력
            now = time.time()
            if now - last_stats >= stats_interval:
                self._print_stats()
                last_stats = now

            # FPS 제한
            elapsed = time.time() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _on_nal(self, nal_data, nal_type, is_keyframe):
        """NAL unit 콜백 — NAL 단위로 UDP 전송"""
        # 인코더 재시작 후 새 SPS/PPS를 viewer에게 TCP로 재전송
        if getattr(self, '_need_resend_sps', False) and self.encoder:
            sps_pps = self.encoder.get_sps_pps()
            if sps_pps:
                self.network.send_sps_pps(sps_pps)
                self.network.send_control({
                    "cmd": "stream_info",
                    "width": self.enc_w,
                    "height": self.enc_h,
                    "fps": self.args.fps,
                    "codec": "h264",
                })
                print(f"  [Host] New SPS/PPS sent to viewer ({len(sps_pps)} bytes)")
                self._need_resend_sps = False
        self.network.send_video_nal(nal_data, nal_type, is_keyframe)

    def _on_viewer_connected(self, addr):
        """Viewer 연결 시 SPS/PPS + 스트림 정보 즉시 전송"""
        if self.encoder:
            # 1. 스트림 정보 (TCP)
            self.network.send_control({
                "cmd": "stream_info",
                "width": self.enc_w,
                "height": self.enc_h,
                "fps": self.args.fps,
                "codec": "h264",
            })
            # 2. SPS/PPS 즉시 전송 (TCP — 확실한 전달)
            sps_pps = self.encoder.get_sps_pps()
            if sps_pps:
                self.network.send_sps_pps(sps_pps)
                print(f"  [Host] SPS/PPS sent to viewer ({len(sps_pps)} bytes)")

        # 3. DXGI 캡처: 정적 화면에서도 프레임 반복 전달 (연결 안정화)
        if hasattr(self.capture, 'force_repeat'):
            self.capture.force_repeat(duration=5.0)

    def _print_stats(self):
        cap_fps = self.capture.get_fps()
        enc_fps = self.encoder.get_fps() if self.encoder else 0
        net = self.network.get_stats()
        elapsed = time.time() - self.capture.start_time
        mbps = net["bytes_sent"] * 8 / max(elapsed, 1) / 1024 / 1024
        connected = "connected" if net["connected"] else "waiting"

        print(f"  [Stats] cap:{cap_fps:.0f}fps | enc:{enc_fps:.0f}fps | "
              f"net:{mbps:.1f}Mbps {net['packets_sent']}pkts | {connected}")

    def stop(self):
        print("\n  [Host] Stopping...")
        self.running = False
        if self.encoder:
            self.encoder.stop()
        self.capture.stop()
        self.network.stop()
        self.input_handler.close()
        self.vdisplay.close()
        cleanup_upnp()
        print("  [Host] Done.")


def main():
    parser = argparse.ArgumentParser(description="Ghost Display - Host")
    parser.add_argument("--monitor", type=int, default=0, help="Monitor index (default: 0)")
    parser.add_argument("--fps", type=int, default=60, help="Target FPS (default: 60)")
    parser.add_argument("--bitrate", type=str, default="8M", help="Video bitrate (default: 8M)")
    parser.add_argument("--video-port", type=int, default=9000, help="UDP video port (default: 9000)")
    parser.add_argument("--control-port", type=int, default=9001, help="TCP control port (default: 9001)")
    parser.add_argument("--software", action="store_true", help="Use software encoder (no NVENC)")
    parser.add_argument("--scale", type=float, default=0, help="Scale factor (e.g. 0.5 for half res). 0=auto")
    parser.add_argument("--no-virtual-display", action="store_true", help="Disable auto virtual display")
    parser.add_argument("--sendinput", action="store_true", help="Force SendInput mode (skip kernel driver)")
    parser.add_argument("--capture-mode", type=str, default="wgc", choices=["wgc", "dxgi"],
                        help="Capture mode: wgc (default) or dxgi (supports lock screen with SYSTEM privilege)")
    parser.add_argument("--log-file", type=str, default=None, help="Log output to file (for service mode)")
    args = parser.parse_args()

    # 로그 파일 설정 (서비스 모드)
    if args.log_file:
        import logging
        logging.basicConfig(
            filename=args.log_file,
            level=logging.INFO,
            format="%(asctime)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        class LogWriter:
            def __init__(self, level):
                self.logger = logging.getLogger()
                self.level = level
            def write(self, msg):
                if msg.strip():
                    self.logger.log(self.level, msg.strip())
            def flush(self):
                pass
        sys.stdout = LogWriter(logging.INFO)
        sys.stderr = LogWriter(logging.ERROR)

    host = GhostHost(args)

    def signal_handler(sig, frame):
        host.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        host.start()
    except KeyboardInterrupt:
        host.stop()


if __name__ == "__main__":
    main()
