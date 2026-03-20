"""
멀티 모니터 캡처 - 여러 모니터를 하나의 프레임으로 합성
capture.py / capture_dxgi.py 의 래퍼

뷰어에서 모니터 선택 가능: select_monitor(n) or select_monitor("all")
물리 모니터만 자동 감지 (RDP 가상 모니터 제외)
"""

import ctypes
import ctypes.wintypes
import threading
import time
import numpy as np


def get_physical_monitor_count():
    """DXGI 어댑터 기반 물리 모니터 수 반환 (가상 어댑터 제외)"""
    try:
        from ctypes import POINTER, byref, c_void_p, c_uint

        dxgi = ctypes.windll.dxgi

        # DXGI Factory 생성
        IID_IDXGIFactory1 = (ctypes.c_byte * 16)(
            0x78, 0xae, 0x0a, 0x77, 0x6f, 0xf2, 0xba, 0x4d,
            0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87,
        )
        factory = c_void_p()
        hr = dxgi.CreateDXGIFactory1(byref(IID_IDXGIFactory1), byref(factory))
        if hr != 0:
            return 0

        factory_vt = ctypes.cast(
            ctypes.cast(factory, POINTER(c_void_p))[0],
            POINTER(c_void_p * 30)
        ).contents

        # EnumAdapters (index 7)
        EnumAdapters = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_uint, POINTER(c_void_p))(factory_vt[7])

        class DXGI_ADAPTER_DESC(ctypes.Structure):
            _fields_ = [
                ("Description", ctypes.c_wchar * 128),
                ("VendorId", ctypes.c_uint),
                ("DeviceId", ctypes.c_uint),
                ("SubSysId", ctypes.c_uint),
                ("Revision", ctypes.c_uint),
                ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid_Low", ctypes.c_ulong),
                ("AdapterLuid_High", ctypes.c_long),
            ]

        virtual_keywords = ["microsoft", "remote", "virtual", "basic render"]
        physical_outputs = 0

        adapter_idx = 0
        while True:
            adapter = c_void_p()
            hr = EnumAdapters(factory, adapter_idx, byref(adapter))
            if hr != 0:
                break
            adapter_idx += 1

            # GetDesc (index 8)
            adapter_vt = ctypes.cast(
                ctypes.cast(adapter, POINTER(c_void_p))[0],
                POINTER(c_void_p * 20)
            ).contents
            GetDesc = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(DXGI_ADAPTER_DESC))(adapter_vt[8])
            desc = DXGI_ADAPTER_DESC()
            GetDesc(adapter, byref(desc))

            adapter_name = desc.Description.lower()
            is_virtual = any(kw in adapter_name for kw in virtual_keywords)

            if is_virtual:
                print(f"  [MultiCapture] 가상 어댑터 제외: {desc.Description}")
                # Release adapter
                Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(adapter_vt[2])
                Release(adapter)
                continue

            # 이 어댑터의 출력(모니터) 수 세기
            EnumOutputs = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_uint, POINTER(c_void_p))(adapter_vt[7])
            output_idx = 0
            while True:
                output = c_void_p()
                hr2 = EnumOutputs(adapter, output_idx, byref(output))
                if hr2 != 0:
                    break
                output_idx += 1
                # Release output
                out_vt = ctypes.cast(
                    ctypes.cast(output, POINTER(c_void_p))[0],
                    POINTER(c_void_p * 3)
                ).contents
                ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(out_vt[2])(output)

            print(f"  [MultiCapture] 물리 어댑터: {desc.Description} → 출력 {output_idx}개")
            physical_outputs += output_idx

            # Release adapter
            Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(adapter_vt[2])
            Release(adapter)

        # Release factory
        fac_release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(factory_vt[2])
        fac_release(factory)

        return physical_outputs
    except Exception as e:
        print(f"  [MultiCapture] 물리 모니터 감지 실패: {e}")
        return 0


class MultiMonitorCapture:
    """여러 모니터 캡처 + 뷰어에서 선택 가능"""

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

        # 선택된 모니터 (None = 전체)
        self.selected = None

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
        self._stitch_thread = threading.Thread(target=self._output_loop, daemon=True)
        self._stitch_thread.start()

    def _create_capture(self, monitor_index):
        """캡처 인스턴스 생성. DXGI는 init만 먼저 테스트."""
        if self.capture_mode == "dxgi":
            from capture_dxgi import DXGICapture
            cap = DXGICapture(monitor_index=monitor_index, target_fps=self.target_fps)
            # init만 먼저 테스트 (실패 시 즉시 예외)
            cap._init_dxgi()
            # 성공하면 정리 후 정상 start()에서 다시 초기화
            for obj_name in ['_staging_tex', '_duplication', '_context', '_device']:
                obj = getattr(cap, obj_name, None)
                if obj and obj.value:
                    try:
                        import ctypes
                        from ctypes import POINTER, c_void_p
                        vt = ctypes.cast(
                            ctypes.cast(obj, POINTER(c_void_p))[0],
                            POINTER(c_void_p * 3)
                        ).contents
                        Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vt[2])
                        Release(obj)
                    except:
                        pass
                    setattr(cap, obj_name, None)
            return cap
        else:
            from capture import ScreenCapture as WGCCapture
            return WGCCapture(monitor_index=monitor_index, target_fps=self.target_fps)

    def select_monitor(self, monitor):
        """모니터 선택: 0~N-1 = 개별 모니터, None/'all' = 전체"""
        if monitor is None or str(monitor).lower() == "all":
            self.selected = None
            print(f"  [MultiCapture] 전체 모니터 표시")
        else:
            idx = int(monitor)
            if 0 <= idx < len(self.captures):
                self.selected = idx
                print(f"  [MultiCapture] 모니터 {idx} 선택")
            else:
                print(f"  [MultiCapture] 모니터 {monitor} 없음 (총 {len(self.captures)}개)")

    def get_monitor_count(self):
        return len(self.captures)

    def get_monitor_info(self):
        """각 모니터의 해상도 정보 반환 (물리 모니터만 필터링)"""
        physical_count = get_physical_monitor_count()
        info = []
        for i, cap in enumerate(self.captures):
            # 물리 모니터 수 이내면 물리, 아니면 가상
            is_physical = (physical_count == 0) or (i < physical_count)
            if is_physical:
                info.append({
                    "index": i,
                    "width": cap.width,
                    "height": cap.height,
                })
        return info

    def _output_loop(self):
        """선택된 모니터 또는 전체를 출력"""
        frame_interval = 1.0 / self.target_fps

        while self.running:
            loop_start = time.time()

            selected = self.selected

            if selected is not None and 0 <= selected < len(self.captures):
                # 단일 모니터
                frame = self._get_single_frame(selected, frame_interval)
            else:
                # 전체 합성
                frame = self._get_stitched_frame(frame_interval)

            if frame is not None:
                with self.frame_lock:
                    self.latest_frame = frame
                    self.frame_count += 1
                    self.width = frame.shape[1]
                    self.height = frame.shape[0]
                self.frame_event.set()

            elapsed = time.time() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _get_single_frame(self, index, timeout):
        cap = self.captures[index]
        f = cap.get_frame(timeout=timeout)
        if f is not None:
            return f
        with cap.frame_lock:
            return cap.latest_frame

    def _get_stitched_frame(self, timeout):
        frames = []
        max_h = 0
        for cap in self.captures:
            if not cap.running:
                continue
            f = cap.get_frame(timeout=timeout)
            if f is not None:
                frames.append(f)
                max_h = max(max_h, f.shape[0])
            elif cap.latest_frame is not None:
                with cap.frame_lock:
                    if cap.latest_frame is not None:
                        frames.append(cap.latest_frame)
                        max_h = max(max_h, cap.latest_frame.shape[0])

        if not frames:
            return None

        # 높이가 다르면 패딩 (검은색)
        padded = []
        for f in frames:
            if f.shape[0] < max_h:
                pad = np.zeros((max_h - f.shape[0], f.shape[1], f.shape[2]), dtype=np.uint8)
                f = np.vstack([f, pad])
            padded.append(f)

        return np.hstack(padded)

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
