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
        self.capture = None
        self.encoder = None
        self.network = StreamServer(
            host="0.0.0.0",
            video_port=args.video_port,
            control_port=args.control_port,
        )
        force_sendinput = getattr(args, 'sendinput', False) or getattr(args, 'input_mode', 'kse') == 'sendinput'
        self.input_handler = InputHandler(force_sendinput=force_sendinput)
        self.vdisplay = VirtualDisplayManager()
        self.running = False
        self.streaming = False
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

        # 3. 네트워크 시작 (viewer 연결 대기)
        self.network.on_input = self.input_handler.handle_event
        self.network.on_connected = self._on_viewer_connected
        self.network.on_disconnected = self._on_viewer_disconnected
        self.network.on_control = self._on_control
        self.network.start()

        # 4. 대기 루프 (뷰어 연결 시 캡처/인코딩 시작)
        self.running = True
        print(f"\n  [Host] Waiting for viewer...")
        print(f"  [Host] Press Ctrl+C to stop\n")

        self._wait_loop()

    def _on_viewer_connected(self, addr):
        """Viewer 연결 → 캡처 + 인코더 시작"""
        print(f"  [Host] Viewer connected from {addr}")

        if not self.streaming:
            try:
                self._start_streaming()
            except Exception as e:
                print(f"  [Host] 스트리밍 시작 실패: {e}")
                self.network.send_control({"cmd": "error", "msg": str(e)})
                return

        # 스트림 정보 + SPS/PPS 전송
        if self.encoder:
            self.network.send_control({
                "cmd": "stream_info",
                "width": self.enc_w,
                "height": self.enc_h,
                "fps": self.args.fps,
                "codec": "h264",
            })
            sps_pps = self.encoder.get_sps_pps()
            if sps_pps:
                self.network.send_sps_pps(sps_pps)
                print(f"  [Host] SPS/PPS sent to viewer ({len(sps_pps)} bytes)")

        # 멀티모니터 정보 전송 (연결 시 물리 모니터 감지)
        if hasattr(self.capture, 'get_monitor_info'):
            monitors = self.capture.get_monitor_info()
            if len(monitors) == 1:
                self.capture.select_monitor(monitors[0]["index"])
            selected = "all" if self.capture.selected is None else self.capture.selected
            self.network.send_control({
                "cmd": "monitor_info",
                "monitors": monitors,
                "selected": selected,
            })

        # DXGI: 정적 화면에서도 프레임 반복
        if hasattr(self.capture, 'force_repeat'):
            self.capture.force_repeat(duration=5.0)

    def _on_viewer_disconnected(self):
        """Viewer 연결 해제 → 캡처 + 인코더 중지"""
        print(f"  [Host] Viewer disconnected")
        self._stop_streaming()

    def _start_streaming(self):
        """캡처 + 인코더 시작"""
        if self.streaming:
            return

        capture_mode = getattr(self.args, 'capture_mode', 'wgc')
        monitor = getattr(self.args, 'monitor', '0')

        # 캡처 생성
        if str(monitor).lower() == 'all':
            from capture_multi import MultiMonitorCapture
            self.capture = MultiMonitorCapture(
                capture_mode=capture_mode,
                target_fps=self.args.fps,
            )
        elif capture_mode == 'dxgi':
            from capture_dxgi import DXGICapture
            self.capture = DXGICapture(
                monitor_index=int(monitor),
                target_fps=self.args.fps,
            )
        else:
            self.capture = WGCCapture(
                monitor_index=int(monitor),
                target_fps=self.args.fps,
            )

        self.capture.start()

        # 첫 프레임 대기
        print("  [Host] Waiting for first frame...")
        frame = None
        for i in range(300):
            frame = self.capture.get_frame(timeout=0.1)
            if frame is not None:
                break
            if i > 0 and i % 50 == 0:
                print(f"  [Host] Still waiting... ({i//10}s)")

        if frame is not None:
            cap_h, cap_w = frame.shape[:2]
            print(f"  [Host] Capture: {cap_w}x{cap_h}")
        else:
            cap_w, cap_h = 1920, 1080
            print(f"  [Host] No frames yet, using default {cap_w}x{cap_h}")

        # 스케일 결정
        scale = self.args.scale
        if scale == 0:
            scale = min(1.0, 1920 / cap_w) if cap_w > 1920 else 1.0
        self.scale = scale
        self.enc_w = int(cap_w * scale) // 2 * 2
        self.enc_h = int(cap_h * scale) // 2 * 2

        if scale < 1.0:
            print(f"  [Host] Encoding: {self.enc_w}x{self.enc_h} (scale {scale:.2f})")

        # 인코더 시작
        self.encoder = H264Encoder(
            width=self.enc_w,
            height=self.enc_h,
            fps=self.args.fps,
            bitrate=self.args.bitrate,
            use_nvenc=not self.args.software,
        )
        self.encoder.on_nal = self._on_nal
        self.encoder.start()

        if frame is None:
            black = np.zeros((self.enc_h, self.enc_w, 4), dtype=np.uint8)
            self.encoder.encode_frame(black)
            for _ in range(20):
                if self.encoder.sps and self.encoder.pps:
                    break
                time.sleep(0.05)

        self.input_handler.update_resolution(self.enc_w, self.enc_h)

        self.streaming = True
        print(f"  [Host] Streaming at {self.args.fps}fps, {self.args.bitrate}")

        # 스트리밍 스레드 시작
        self._stream_thread = threading.Thread(target=self._main_loop, daemon=True)
        self._stream_thread.start()

    def _stop_streaming(self):
        """캡처 + 인코더 중지"""
        if not self.streaming:
            return

        self.streaming = False
        time.sleep(0.2)  # 메인 루프 종료 대기

        if self.encoder:
            self.encoder.stop()
            self.encoder = None
        if self.capture:
            self.capture.stop()
            self.capture = None

        print(f"  [Host] Streaming stopped, waiting for viewer...")

    def _on_capture_reconnect(self):
        self._restart_encoder()

    def _restart_encoder(self):
        if not hasattr(self, '_encoder_lock'):
            self._encoder_lock = threading.Lock()
        if not self._encoder_lock.acquire(blocking=False):
            return
        try:
            print("  [Host] Restarting encoder...")
            old_encoder = self.encoder
            if old_encoder:
                old_encoder.on_nal = None
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
            self._need_resend_sps = True
        finally:
            self._encoder_lock.release()

    def _wait_loop(self):
        """뷰어 연결/해제를 기다리는 메인 루프"""
        while self.running:
            time.sleep(1)

    def _main_loop(self):
        """캡처 → 리사이즈 → 인코딩 스트리밍 루프"""
        frame_interval = 1.0 / self.args.fps
        stats_interval = 5.0
        last_stats = time.time()

        while self.streaming and self.running:
            loop_start = time.time()

            frame = self.capture.get_frame(timeout=frame_interval)
            if frame is None:
                continue

            h, w = frame.shape[:2]
            if w != self.enc_w or h != self.enc_h:
                frame = cv2.resize(frame, (self.enc_w, self.enc_h),
                                   interpolation=cv2.INTER_LINEAR)

            if self.encoder and self.encoder.running:
                self.encoder.encode_frame(frame)

            now = time.time()
            if now - last_stats >= stats_interval:
                self._print_stats()
                last_stats = now

            elapsed = time.time() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _on_nal(self, nal_data, nal_type, is_keyframe):
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

    def _on_control(self, ctrl):
        cmd = ctrl.get("cmd")
        if cmd == "switch_input_mode":
            mode = ctrl.get("mode")
            if mode in ("kse", "sendinput"):
                self.input_handler.switch_mode(mode)
                self.network.send_control({"cmd": "input_mode_changed", "mode": self.input_handler.get_mode()})
        elif cmd == "select_monitor":
            self._handle_select_monitor(ctrl.get("monitor"))

    def _handle_select_monitor(self, monitor):
        if not self.capture or not hasattr(self.capture, 'select_monitor'):
            self.network.send_control({"cmd": "monitor_changed", "monitor": 0, "count": 1})
            return

        self.capture.select_monitor(monitor)

        time.sleep(0.3)
        frame = self.capture.get_frame(timeout=1.0)
        if frame is not None:
            h, w = frame.shape[:2]
            scale = self.args.scale
            if scale == 0:
                scale = min(1.0, 1920 / w) if w > 1920 else 1.0
            new_w = int(w * scale) // 2 * 2
            new_h = int(h * scale) // 2 * 2

            if new_w != self.enc_w or new_h != self.enc_h:
                self.enc_w = new_w
                self.enc_h = new_h
                self.scale = scale
                self.input_handler.update_resolution(self.enc_w, self.enc_h)
                self._restart_encoder()
                print(f"  [Host] 모니터 전환 → {self.enc_w}x{self.enc_h}")

        selected = "all" if self.capture.selected is None else self.capture.selected
        self.network.send_control({
            "cmd": "monitor_changed",
            "monitor": selected,
            "count": self.capture.get_monitor_count(),
        })

    def _print_stats(self):
        cap_fps = self.capture.get_fps() if self.capture else 0
        enc_fps = self.encoder.get_fps() if self.encoder else 0
        net = self.network.get_stats()
        elapsed = time.time() - (self.capture.start_time if self.capture else time.time())
        mbps = net["bytes_sent"] * 8 / max(elapsed, 1) / 1024 / 1024
        connected = "connected" if net["connected"] else "waiting"

        print(f"  [Stats] cap:{cap_fps:.0f}fps | enc:{enc_fps:.0f}fps | "
              f"net:{mbps:.1f}Mbps {net['packets_sent']}pkts | {connected}")

    def stop(self):
        print("\n  [Host] Stopping...")
        self.running = False
        self._stop_streaming()
        self.network.stop()
        self.input_handler.close()
        self.vdisplay.close()
        cleanup_upnp()
        print("  [Host] Done.")


def main():
    parser = argparse.ArgumentParser(description="Ghost Display - Host")
    parser.add_argument("--monitor", type=str, default="all", help="Monitor index (default: all)")
    parser.add_argument("--fps", type=int, default=60, help="Target FPS (default: 60)")
    parser.add_argument("--bitrate", type=str, default="20M", help="Video bitrate (default: 20M)")
    parser.add_argument("--video-port", type=int, default=9000, help="UDP video port (default: 9000)")
    parser.add_argument("--control-port", type=int, default=9001, help="TCP control port (default: 9001)")
    parser.add_argument("--software", action="store_true", help="Use software encoder (no NVENC)")
    parser.add_argument("--scale", type=float, default=0, help="Scale factor (0=auto)")
    parser.add_argument("--no-virtual-display", action="store_true", help="Disable auto virtual display")
    parser.add_argument("--input-mode", type=str, default="kse", choices=["kse", "sendinput"],
                        help="Input mode: kse (kernel driver) or sendinput (Windows API)")
    parser.add_argument("--sendinput", action="store_true", help="(deprecated) Same as --input-mode sendinput")
    parser.add_argument("--capture-mode", type=str, default="dxgi", choices=["wgc", "dxgi"],
                        help="Capture mode: wgc or dxgi (default: dxgi)")
    parser.add_argument("--log-file", type=str, default=None, help="Log output to file")
    args = parser.parse_args()

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
