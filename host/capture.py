"""
WGC (Windows.Graphics.Capture) 화면 캡처 모듈
windows-capture 패키지 사용 (Rust 백엔드)
세션 끊김(RDP 해제 등) 시 자동 재연결
"""

import threading
import time
import numpy as np
from windows_capture import WindowsCapture, Frame, InternalCaptureControl


class ScreenCapture:
    def __init__(self, monitor_index=0, target_fps=60):
        self.monitor_index = monitor_index
        self.target_fps = target_fps
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        self.frame_event = threading.Event()
        self.running = False
        self.capture = None
        self.control = None
        self.width = 0
        self.height = 0
        self.frame_count = 0
        self.start_time = 0
        self._session_active = False
        self.on_reconnect = None  # 재연결 콜백

    def start(self):
        """캡처 시작 (별도 스레드에서 실행)"""
        self.running = True
        self.start_time = time.time()
        self.frame_count = 0

        self._start_session()

        # 재연결 감시 스레드
        threading.Thread(target=self._reconnect_loop, daemon=True).start()

        print(f"  [Capture] Started (monitor {self.monitor_index}, target {self.target_fps}fps)")

    def _start_session(self):
        """캡처 세션 하나를 시작"""
        self._session_active = True

        self.capture = WindowsCapture(
            cursor_capture=True,
            draw_border=False,
            monitor_index=self.monitor_index + 1,  # 1-based
        )

        this = self

        def on_frame_arrived(frame: Frame, control: InternalCaptureControl):
            if not this.running:
                control.stop()
                return

            w = frame.width
            h = frame.height

            if this.width != w or this.height != h:
                this.width = w
                this.height = h
                print(f"  [Capture] Resolution: {w}x{h}")

            data = np.array(frame.frame_buffer, dtype=np.uint8).copy()

            with this.frame_lock:
                this.latest_frame = data
                this.frame_count += 1

            this.frame_event.set()

        def on_closed():
            print("  [Capture] Session closed")
            this._session_active = False

        self.capture.event(on_frame_arrived)
        self.capture.event(on_closed)

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self.capture.start()
        except Exception as e:
            print(f"  [Capture] Error: {e}")
            self._session_active = False

    def _reconnect_loop(self):
        """세션 끊김 감지 → 자동 재연결"""
        while self.running:
            time.sleep(2)
            if not self._session_active and self.running:
                # 세션 전환이 안정화될 때까지 대기
                print("  [Capture] Session lost, waiting 5s for session to stabilize...")
                time.sleep(5)
                print("  [Capture] Reconnecting...")
                for attempt in range(1, 61):
                    if not self.running:
                        return
                    try:
                        self._start_session()
                        # 프레임이 안정적으로 오는지 확인 (5초)
                        time.sleep(5)
                        if self._session_active:
                            print(f"  [Capture] Reconnected (attempt {attempt})")
                            if self.on_reconnect:
                                self.on_reconnect()
                            break
                    except Exception as e:
                        print(f"  [Capture] Reconnect failed ({attempt}): {e}")
                    time.sleep(5)
                else:
                    print("  [Capture] Reconnect failed after 60 attempts, giving up")
                    self.running = False

    def get_frame(self, timeout=0.1):
        """최신 프레임 가져오기 (블로킹)"""
        if self.frame_event.wait(timeout=timeout):
            self.frame_event.clear()
            with self.frame_lock:
                return self.latest_frame
        return None

    def get_fps(self):
        elapsed = time.time() - self.start_time
        if elapsed > 0:
            return self.frame_count / elapsed
        return 0

    def stop(self):
        self.running = False
        self.frame_event.set()
        print(f"  [Capture] Stopped (avg {self.get_fps():.1f} fps)")
