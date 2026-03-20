"""
멀티 모니터 캡처 - 여러 모니터를 하나의 프레임으로 합성
capture.py / capture_dxgi.py 의 래퍼
"""

import threading
import time
import numpy as np


class MultiMonitorCapture:
    """여러 모니터를 가로로 이어붙여서 하나의 프레임으로 반환"""

    def __init__(self, capture_mode="dxgi", target_fps=60, max_monitors=8):
        self.capture_mode = capture_mode
        self.target_fps = target_fps
        self.max_monitors = max_monitors
        self.captures = []
        self.running = False
        self.frame_count = 0
        self.start_time = 0
        self.width = 0
        self.height = 0

        self.latest_frame = None
        self.frame_lock = threading.Lock()
        self.frame_event = threading.Event()
        self.on_reconnect = None

    def start(self):
        self.running = True
        self.start_time = time.time()

        # 모니터 0부터 순서대로 생성, 실패하면 중단
        for i in range(self.max_monitors):
            try:
                cap = self._create_capture(i)
                cap.start()
                # 첫 프레임 대기 (모니터가 실제로 존재하는지 확인)
                time.sleep(1)
                if cap.running:
                    self.captures.append(cap)
                    print(f"  [MultiCapture] 모니터 {i} 추가됨")
                else:
                    cap.stop()
                    break
            except Exception as e:
                print(f"  [MultiCapture] 모니터 {i} 없음: {e}")
                break

        if not self.captures:
            raise RuntimeError("캡처 가능한 모니터가 없습니다")

        print(f"  [MultiCapture] {len(self.captures)}개 모니터 캡처 시작")

        # 합성 스레드
        self._stitch_thread = threading.Thread(target=self._stitch_loop, daemon=True)
        self._stitch_thread.start()

    def _create_capture(self, monitor_index):
        if self.capture_mode == "dxgi":
            from capture_dxgi import DXGICapture
            return DXGICapture(monitor_index=monitor_index, target_fps=self.target_fps)
        else:
            from capture import ScreenCapture as WGCCapture
            return WGCCapture(monitor_index=monitor_index, target_fps=self.target_fps)

    def _stitch_loop(self):
        """각 캡처에서 프레임을 가져와 합성"""
        frame_interval = 1.0 / self.target_fps

        while self.running:
            loop_start = time.time()

            frames = []
            max_h = 0
            for cap in self.captures:
                f = cap.get_frame(timeout=frame_interval)
                if f is not None:
                    frames.append(f)
                    max_h = max(max_h, f.shape[0])
                elif cap.latest_frame is not None:
                    # 새 프레임이 없으면 마지막 프레임 사용
                    with cap.frame_lock:
                        frames.append(cap.latest_frame)
                        max_h = max(max_h, cap.latest_frame.shape[0])

            if not frames:
                continue

            # 높이가 다르면 패딩 (검은색)
            padded = []
            for f in frames:
                if f.shape[0] < max_h:
                    pad = np.zeros((max_h - f.shape[0], f.shape[1], f.shape[2]), dtype=np.uint8)
                    f = np.vstack([f, pad])
                padded.append(f)

            stitched = np.hstack(padded)

            with self.frame_lock:
                self.latest_frame = stitched
                self.frame_count += 1
                self.width = stitched.shape[1]
                self.height = stitched.shape[0]
            self.frame_event.set()

            elapsed = time.time() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def get_frame(self, timeout=0.1):
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

    def force_repeat(self, duration=5.0):
        for cap in self.captures:
            if hasattr(cap, 'force_repeat'):
                cap.force_repeat(duration)

    def stop(self):
        self.running = False
        self.frame_event.set()
        for cap in self.captures:
            cap.stop()
        print(f"  [MultiCapture] Stopped ({len(self.captures)} monitors, avg {self.get_fps():.1f} fps)")
