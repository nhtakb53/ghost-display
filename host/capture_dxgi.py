"""
DXGI Desktop Duplication 화면 캡처 모듈
SYSTEM 권한으로 실행 시 잠금화면도 캡처 가능

capture.py(WGC)와 동일한 인터페이스 제공
"""

import ctypes
import ctypes.wintypes
import threading
import time
import numpy as np
from ctypes import POINTER, byref, c_void_p, c_uint, c_int

# COM GUIDs
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

def _make_guid(s):
    import uuid
    u = uuid.UUID(s)
    return GUID(u.time_low, u.time_mid, u.time_hi_version,
                (ctypes.c_ubyte * 8)(*u.bytes[8:]))

# DXGI / D3D11 IIDs
IID_IDXGIFactory1 = _make_guid("770aae78-f26f-4dba-a829-253c83d1b387")
IID_IDXGIOutput1 = _make_guid("00cddea8-939b-4b83-a340-a685226666cc")
IID_ID3D11Texture2D = _make_guid("6f15aaf2-d208-4e89-9ab4-489535d34f9c")

# D3D constants
D3D11_SDK_VERSION = 7
D3D_DRIVER_TYPE_UNKNOWN = 0
DXGI_FORMAT_B8G8R8A8_UNORM = 87
D3D11_MAP_READ = 1
D3D11_USAGE_STAGING = 3
D3D11_CPU_ACCESS_READ = 0x20000

# DXGI_OUTDUPL_FRAME_INFO
class DXGI_OUTDUPL_FRAME_INFO(ctypes.Structure):
    _fields_ = [
        ("LastPresentTime", ctypes.c_int64),
        ("LastMouseUpdateTime", ctypes.c_int64),
        ("AccumulatedFrames", ctypes.c_uint),
        ("RectsCoalesced", ctypes.c_int),
        ("ProtectedContentMaskedOut", ctypes.c_int),
        ("PointerPosition_Visible", ctypes.c_int),
        ("PointerPosition_X", ctypes.c_int),
        ("PointerPosition_Y", ctypes.c_int),
        ("TotalMetadataBufferSize", ctypes.c_uint),
        ("PointerShapeBufferSize", ctypes.c_uint),
    ]

# D3D11_TEXTURE2D_DESC
class D3D11_TEXTURE2D_DESC(ctypes.Structure):
    _fields_ = [
        ("Width", ctypes.c_uint),
        ("Height", ctypes.c_uint),
        ("MipLevels", ctypes.c_uint),
        ("ArraySize", ctypes.c_uint),
        ("Format", ctypes.c_uint),
        ("SampleDesc_Count", ctypes.c_uint),
        ("SampleDesc_Quality", ctypes.c_uint),
        ("Usage", ctypes.c_uint),
        ("BindFlags", ctypes.c_uint),
        ("CPUAccessFlags", ctypes.c_uint),
        ("MiscFlags", ctypes.c_uint),
    ]

# D3D11_MAPPED_SUBRESOURCE
class D3D11_MAPPED_SUBRESOURCE(ctypes.Structure):
    _fields_ = [
        ("pData", ctypes.c_void_p),
        ("RowPitch", ctypes.c_uint),
        ("DepthPitch", ctypes.c_uint),
    ]


class DXGICapture:
    """DXGI Desktop Duplication을 이용한 화면 캡처"""

    def __init__(self, monitor_index=0, target_fps=60):
        self.monitor_index = monitor_index
        self.target_fps = target_fps
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        self.frame_event = threading.Event()
        self.running = False
        self.width = 0
        self.height = 0
        self.frame_count = 0
        self.start_time = 0
        self.on_reconnect = None

        # COM objects
        self._device = None
        self._context = None
        self._duplication = None
        self._staging_tex = None

    def start(self):
        """캡처 시작"""
        self.running = True
        self.start_time = time.time()
        self.frame_count = 0

        self._init_dxgi()

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        print(f"  [Capture/DXGI] Started (monitor {self.monitor_index}, target {self.target_fps}fps)")

    def _init_dxgi(self):
        """D3D11 디바이스 + DXGI Output Duplication 초기화"""
        d3d11 = ctypes.windll.d3d11
        dxgi = ctypes.windll.dxgi

        # 1. DXGI Factory 생성
        factory = ctypes.c_void_p()
        hr = dxgi.CreateDXGIFactory1(byref(IID_IDXGIFactory1), byref(factory))
        if hr != 0:
            raise RuntimeError(f"CreateDXGIFactory1 failed: 0x{hr & 0xFFFFFFFF:08X}")

        # 2. 어댑터 열거 (첫 번째 GPU)
        factory_vt = ctypes.cast(
            ctypes.cast(factory, POINTER(c_void_p))[0],
            POINTER(c_void_p * 30)
        ).contents

        # IDXGIFactory1::EnumAdapters (index 7)
        EnumAdapters = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_uint, POINTER(c_void_p))(factory_vt[7])
        adapter = c_void_p()
        hr = EnumAdapters(factory, 0, byref(adapter))
        if hr != 0:
            raise RuntimeError(f"EnumAdapters failed: 0x{hr & 0xFFFFFFFF:08X}")

        # 3. D3D11 디바이스 생성
        device = c_void_p()
        context = c_void_p()
        feature_level = c_uint()

        hr = d3d11.D3D11CreateDevice(
            adapter,                    # pAdapter
            D3D_DRIVER_TYPE_UNKNOWN,    # DriverType (UNKNOWN = 어댑터 사용)
            None,                       # Software
            0,                          # Flags
            None,                       # pFeatureLevels
            0,                          # FeatureLevels count
            D3D11_SDK_VERSION,          # SDKVersion
            byref(device),             # ppDevice
            byref(feature_level),      # pFeatureLevel
            byref(context),            # ppImmediateContext
        )
        if hr != 0:
            raise RuntimeError(f"D3D11CreateDevice failed: 0x{hr & 0xFFFFFFFF:08X}")

        self._device = device
        self._context = context

        # 4. 어댑터에서 출력(모니터) 열거
        adapter_vt = ctypes.cast(
            ctypes.cast(adapter, POINTER(c_void_p))[0],
            POINTER(c_void_p * 30)
        ).contents

        # IDXGIAdapter::EnumOutputs (index 7)
        EnumOutputs = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_uint, POINTER(c_void_p))(adapter_vt[7])
        output = c_void_p()
        hr = EnumOutputs(adapter, self.monitor_index, byref(output))
        if hr != 0:
            raise RuntimeError(f"EnumOutputs({self.monitor_index}) failed: 0x{hr & 0xFFFFFFFF:08X}")

        # 5. IDXGIOutput → IDXGIOutput1 QueryInterface
        output_vt = ctypes.cast(
            ctypes.cast(output, POINTER(c_void_p))[0],
            POINTER(c_void_p * 30)
        ).contents

        # IUnknown::QueryInterface (index 0)
        QueryInterface = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))(output_vt[0])
        output1 = c_void_p()
        hr = QueryInterface(output, byref(IID_IDXGIOutput1), byref(output1))
        if hr != 0:
            raise RuntimeError(f"QueryInterface(IDXGIOutput1) failed: 0x{hr & 0xFFFFFFFF:08X}")

        # 6. DuplicateOutput
        output1_vt = ctypes.cast(
            ctypes.cast(output1, POINTER(c_void_p))[0],
            POINTER(c_void_p * 30)
        ).contents

        # IDXGIOutput1::DuplicateOutput (index 22)
        DuplicateOutput = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_void_p, POINTER(c_void_p))(output1_vt[22])
        duplication = c_void_p()
        hr = DuplicateOutput(output1, device, byref(duplication))
        if hr != 0:
            raise RuntimeError(f"DuplicateOutput failed: 0x{hr & 0xFFFFFFFF:08X}")

        self._duplication = duplication

        # Duplication의 DESC에서 해상도 가져오기
        dup_vt = ctypes.cast(
            ctypes.cast(duplication, POINTER(c_void_p))[0],
            POINTER(c_void_p * 20)
        ).contents

        # IDXGIOutputDuplication::GetDesc (index 7) — DXGI_OUTDUPL_DESC
        # 직접 desc 크기가 크니까 일단 첫 프레임에서 해상도 결정
        print(f"  [Capture/DXGI] Desktop Duplication initialized")

        # cleanup
        # Release output, output1, adapter, factory (device/context/duplication 유지)
        for obj in [output, output1, adapter, factory]:
            if obj.value:
                release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(
                    ctypes.cast(
                        ctypes.cast(obj, POINTER(c_void_p))[0],
                        POINTER(c_void_p * 3)
                    ).contents[2]
                )
                release(obj)

    def _create_staging_texture(self, width, height):
        """CPU 읽기용 스테이징 텍스처 생성"""
        desc = D3D11_TEXTURE2D_DESC()
        desc.Width = width
        desc.Height = height
        desc.MipLevels = 1
        desc.ArraySize = 1
        desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM
        desc.SampleDesc_Count = 1
        desc.SampleDesc_Quality = 0
        desc.Usage = D3D11_USAGE_STAGING
        desc.BindFlags = 0
        desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ
        desc.MiscFlags = 0

        device_vt = ctypes.cast(
            ctypes.cast(self._device, POINTER(c_void_p))[0],
            POINTER(c_void_p * 30)
        ).contents

        # ID3D11Device::CreateTexture2D (index 5)
        CreateTexture2D = ctypes.WINFUNCTYPE(
            ctypes.c_long, c_void_p, POINTER(D3D11_TEXTURE2D_DESC), c_void_p, POINTER(c_void_p)
        )(device_vt[5])

        staging = c_void_p()
        hr = CreateTexture2D(self._device, byref(desc), None, byref(staging))
        if hr != 0:
            raise RuntimeError(f"CreateTexture2D(staging) failed: 0x{hr & 0xFFFFFFFF:08X}")

        self._staging_tex = staging

    def _capture_loop(self):
        """프레임 캡처 루프"""
        frame_interval = 1.0 / self.target_fps
        DXGI_ERROR_WAIT_TIMEOUT = 0x887A0027
        DXGI_ERROR_ACCESS_LOST = 0x887A0026

        dup_vt = ctypes.cast(
            ctypes.cast(self._duplication, POINTER(c_void_p))[0],
            POINTER(c_void_p * 20)
        ).contents

        # IDXGIOutputDuplication::AcquireNextFrame (index 8)
        # c_long 사용 (HRESULT는 에러 시 자동 예외 발생하므로)
        AcquireNextFrame = ctypes.WINFUNCTYPE(
            ctypes.c_long, c_void_p, c_uint, POINTER(DXGI_OUTDUPL_FRAME_INFO), POINTER(c_void_p)
        )(dup_vt[8])

        # IDXGIOutputDuplication::ReleaseFrame (index 14)
        ReleaseFrame = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p)(dup_vt[14])

        while self.running:
            loop_start = time.time()

            frame_info = DXGI_OUTDUPL_FRAME_INFO()
            desktop_resource = c_void_p()

            timeout_ms = int(frame_interval * 1000) + 50
            hr = AcquireNextFrame(self._duplication, timeout_ms, byref(frame_info), byref(desktop_resource))

            if hr & 0xFFFFFFFF == DXGI_ERROR_WAIT_TIMEOUT:
                continue
            elif hr & 0xFFFFFFFF == DXGI_ERROR_ACCESS_LOST:
                print("  [Capture/DXGI] Access lost, reinitializing...")
                self._reinit_duplication()
                continue
            elif hr != 0:
                print(f"  [Capture/DXGI] AcquireNextFrame error: 0x{hr & 0xFFFFFFFF:08X}")
                time.sleep(0.1)
                continue

            try:
                self._process_frame(desktop_resource)
            except Exception as e:
                print(f"  [Capture/DXGI] Frame process error: {e}")
            finally:
                # desktop_resource Release
                if desktop_resource.value:
                    res_vt = ctypes.cast(
                        ctypes.cast(desktop_resource, POINTER(c_void_p))[0],
                        POINTER(c_void_p * 3)
                    ).contents
                    Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(res_vt[2])
                    Release(desktop_resource)

                ReleaseFrame(self._duplication)

            # FPS 제한
            elapsed = time.time() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _process_frame(self, desktop_resource):
        """캡처된 프레임을 numpy 배열로 변환"""
        # IDXGIResource → ID3D11Texture2D QueryInterface
        res_vt = ctypes.cast(
            ctypes.cast(desktop_resource, POINTER(c_void_p))[0],
            POINTER(c_void_p * 5)
        ).contents
        QueryInterface = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))(res_vt[0])

        texture = c_void_p()
        hr = QueryInterface(desktop_resource, byref(IID_ID3D11Texture2D), byref(texture))
        if hr != 0:
            return

        try:
            # 텍스처 DESC에서 해상도 읽기
            tex_vt = ctypes.cast(
                ctypes.cast(texture, POINTER(c_void_p))[0],
                POINTER(c_void_p * 20)
            ).contents

            # ID3D11Texture2D::GetDesc (index 10)
            GetDesc = ctypes.WINFUNCTYPE(None, c_void_p, POINTER(D3D11_TEXTURE2D_DESC))(tex_vt[10])
            desc = D3D11_TEXTURE2D_DESC()
            GetDesc(texture, byref(desc))

            w, h = desc.Width, desc.Height

            if self.width != w or self.height != h:
                self.width = w
                self.height = h
                self._create_staging_texture(w, h)
                print(f"  [Capture/DXGI] Resolution: {w}x{h}")

            # GPU 텍스처 → 스테이징 텍스처로 복사
            ctx_vt = ctypes.cast(
                ctypes.cast(self._context, POINTER(c_void_p))[0],
                POINTER(c_void_p * 120)
            ).contents

            # ID3D11DeviceContext::CopyResource (index 47)
            CopyResource = ctypes.WINFUNCTYPE(None, c_void_p, c_void_p, c_void_p)(ctx_vt[47])
            CopyResource(self._context, self._staging_tex, texture)

            # Map → 읽기 → Unmap
            # ID3D11DeviceContext::Map (index 14)
            Map = ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, c_void_p, c_uint, c_uint, c_uint, POINTER(D3D11_MAPPED_SUBRESOURCE)
            )(ctx_vt[14])
            mapped = D3D11_MAPPED_SUBRESOURCE()
            hr = Map(self._context, self._staging_tex, 0, D3D11_MAP_READ, 0, byref(mapped))
            if hr != 0:
                return

            try:
                # BGRA 데이터를 numpy로 복사
                row_pitch = mapped.RowPitch
                data_size = row_pitch * h
                buf = (ctypes.c_ubyte * data_size).from_address(mapped.pData)
                frame = np.frombuffer(buf, dtype=np.uint8).reshape(h, row_pitch // 4, 4)
                # row_pitch가 width*4보다 클 수 있음 (패딩)
                frame = frame[:, :w, :].copy()

                with self.frame_lock:
                    self.latest_frame = frame
                    self.frame_count += 1
                self.frame_event.set()

            finally:
                # ID3D11DeviceContext::Unmap (index 15)
                Unmap = ctypes.WINFUNCTYPE(None, c_void_p, c_void_p, c_uint)(ctx_vt[15])
                Unmap(self._context, self._staging_tex, 0)

        finally:
            # texture Release
            tex_vt = ctypes.cast(
                ctypes.cast(texture, POINTER(c_void_p))[0],
                POINTER(c_void_p * 3)
            ).contents
            Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(tex_vt[2])
            Release(texture)

    def _reinit_duplication(self):
        """Desktop Duplication 재초기화 (ACCESS_LOST 시)"""
        if self._duplication:
            dup_vt = ctypes.cast(
                ctypes.cast(self._duplication, POINTER(c_void_p))[0],
                POINTER(c_void_p * 3)
            ).contents
            Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(dup_vt[2])
            Release(self._duplication)
            self._duplication = None

        for attempt in range(1, 61):
            if not self.running:
                return
            try:
                self._init_dxgi()
                print(f"  [Capture/DXGI] Reinitialized (attempt {attempt})")
                if self.on_reconnect:
                    self.on_reconnect()
                return
            except Exception as e:
                print(f"  [Capture/DXGI] Reinit failed ({attempt}): {e}")
                time.sleep(2)

        print("  [Capture/DXGI] Reinit failed after 60 attempts")
        self.running = False

    def get_frame(self, timeout=0.1):
        """최신 프레임 가져오기 (블로킹) — WGC와 동일 인터페이스"""
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

        # COM 리소스 정리
        for obj_name in ['_staging_tex', '_duplication', '_context', '_device']:
            obj = getattr(self, obj_name, None)
            if obj and obj.value:
                try:
                    vt = ctypes.cast(
                        ctypes.cast(obj, POINTER(c_void_p))[0],
                        POINTER(c_void_p * 3)
                    ).contents
                    Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vt[2])
                    Release(obj)
                except:
                    pass
                setattr(self, obj_name, None)

        print(f"  [Capture/DXGI] Stopped (avg {self.get_fps():.1f} fps)")
