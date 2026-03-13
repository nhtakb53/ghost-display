"""
가상 디스플레이 관리 모듈
모니터 없이(headless) 스트리밍 가능하게 가상 모니터 생성/제거

지원 드라이버:
  1. Parsec VDD (IOCTL 기반)
  2. VirtualDrivers VDD (Named Pipe 기반)

드라이버 미설치 시 번들된 VirtualDrivers VDD 자동 설치
"""

import ctypes
import ctypes.wintypes
import struct
import subprocess
import threading
import time
import os
import sys

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

# --- 공통 상수 ---
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value

# SetupAPI
DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10

# 번들된 드라이버 경로 (프로젝트 루트/drivers/)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVERS_DIR = os.path.join(_PROJECT_ROOT, "drivers")
NEFCON_EXE = os.path.join(DRIVERS_DIR, "nefconw.exe")
VDD_DIR = os.path.join(DRIVERS_DIR, "vdd", "VirtualDisplayDriver")
VDD_INF = os.path.join(VDD_DIR, "MttVDD.inf")
VDD_CAT = os.path.join(VDD_DIR, "mttvdd.cat")
VDD_HARDWARE_ID = "Root\\MttVDD"


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("InterfaceClassGuid", ctypes.c_byte * 16),
        ("Flags", ctypes.wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]


class SP_DEVICE_INTERFACE_DETAIL_DATA_A(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("DevicePath", ctypes.c_char * 256),
    ]


# ============================================================
#  드라이버 자동 설치
# ============================================================

def is_admin():
    """관리자 권한 확인"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def is_vdd_installed():
    """VirtualDrivers VDD 설치 여부 확인 (pnputil)"""
    try:
        result = subprocess.run(
            ["pnputil", "/enum-devices", "/class", "Display"],
            capture_output=True, text=True, timeout=10
        )
        return "MttVDD" in result.stdout or "Root\\MttVDD" in result.stdout
    except Exception:
        return False


def _import_certificates():
    """드라이버 카탈로그(.cat)에서 인증서 추출 후 TrustedPublisher에 등록"""
    if not os.path.exists(VDD_CAT):
        return False

    ps_script = f'''
$catFile = "{VDD_CAT.replace(os.sep, '/')}"
$catBytes = [System.IO.File]::ReadAllBytes($catFile)
$certs = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2Collection
$certs.Import($catBytes)
$store = New-Object System.Security.Cryptography.X509Certificates.X509Store("TrustedPublisher", "LocalMachine")
$store.Open("ReadWrite")
foreach ($cert in $certs) {{
    $store.Add($cert)
}}
$store.Close()
Write-Output "OK"
'''
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", ps_script],
            capture_output=True, text=True, timeout=30
        )
        return "OK" in result.stdout
    except Exception as e:
        print(f"  [VDisplay] Certificate import error: {e}")
        return False


def install_vdd():
    """번들된 VirtualDrivers VDD 설치"""
    print("  [VDisplay] === Virtual Display Driver Install ===")

    # 번들 파일 확인
    if not os.path.exists(NEFCON_EXE):
        print(f"  [VDisplay] nefconw.exe not found at {NEFCON_EXE}")
        return False
    if not os.path.exists(VDD_INF):
        print(f"  [VDisplay] MttVDD.inf not found at {VDD_INF}")
        return False

    # 관리자 권한 확인
    if not is_admin():
        print("  [VDisplay] Admin privileges required - requesting elevation...")
        # 관리자 권한으로 설치 스크립트 실행
        install_script = os.path.join(DRIVERS_DIR, "_install.py")
        host_dir = os.path.dirname(os.path.abspath(__file__))

        with open(install_script, "w") as f:
            f.write(f'''import sys, os
sys.path.insert(0, r"{host_dir}")
from virtual_display import _do_install
success = _do_install()
sys.exit(0 if success else 1)
''')

        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{install_script}"', None, 1)
        if ret > 32:
            print("  [VDisplay] Waiting for elevated installer...")
            for _ in range(60):
                time.sleep(1)
                if is_vdd_installed():
                    print("  [VDisplay] Driver installed successfully!")
                    return True
            print("  [VDisplay] Install timed out")
        else:
            print("  [VDisplay] UAC denied or elevation failed")
        return False

    return _do_install()


def _do_install():
    """실제 설치 수행 (관리자 권한 필요)"""
    # 1. 인증서 등록 (신뢰 프롬프트 방지)
    print("  [VDisplay] Importing driver certificates...")
    _import_certificates()

    # 2. nefcon으로 드라이버 설치
    print("  [VDisplay] Installing driver with nefcon...")
    try:
        result = subprocess.run(
            [NEFCON_EXE, "install", VDD_INF, VDD_HARDWARE_ID],
            capture_output=True, text=True, timeout=30,
            cwd=VDD_DIR
        )
        print(f"  [VDisplay] nefcon output: {result.stdout.strip()}")
        if result.returncode != 0 and result.stderr:
            print(f"  [VDisplay] nefcon stderr: {result.stderr.strip()}")
    except Exception as e:
        print(f"  [VDisplay] nefcon error: {e}")
        return False

    # 3. 설치 확인
    time.sleep(2)
    if is_vdd_installed():
        print("  [VDisplay] Driver installed successfully!")
        return True

    print("  [VDisplay] Driver install may have failed, check Device Manager")
    return False


# ============================================================
#  Parsec VDD 백엔드
# ============================================================

# Parsec VDD GUID: {00b41627-04c4-429e-a26e-0265cf50c8fa}
PARSEC_ADAPTER_GUID = (ctypes.c_byte * 16)(
    0x27, 0x16, 0xb4, 0x00, 0xc4, 0x04, 0x9e, 0x42,
    0xa2, 0x6e, 0x02, 0x65, 0xcf, 0x50, 0xc8, 0xfa,
)

# IOCTL codes
VDD_IOCTL_ADD = 0x0022e004
VDD_IOCTL_REMOVE = 0x0022a008
VDD_IOCTL_UPDATE = 0x0022a00c
VDD_IOCTL_VERSION = 0x0022e010


class ParsecVDD:
    """Parsec Virtual Display Driver 제어"""

    def __init__(self):
        self.handle = None
        self.displays = []
        self._keepalive_thread = None
        self._running = False

    def is_available(self):
        """Parsec VDD 드라이버 설치 여부 확인"""
        path = self._get_device_path()
        return path is not None

    def _get_device_path(self):
        """SetupDi API로 Parsec VDD 디바이스 경로 검색"""
        try:
            setupapi = ctypes.WinDLL('setupapi', use_last_error=True)
        except OSError:
            return None

        guid = (ctypes.c_byte * 16)(*PARSEC_ADAPTER_GUID)

        dev_info = setupapi.SetupDiGetClassDevsA(
            ctypes.byref(guid), None, None,
            DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
        )
        if dev_info == INVALID_HANDLE_VALUE:
            return None

        iface_data = SP_DEVICE_INTERFACE_DATA()
        iface_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)

        result = setupapi.SetupDiEnumDeviceInterfaces(
            dev_info, None, ctypes.byref(guid), 0, ctypes.byref(iface_data)
        )
        if not result:
            setupapi.SetupDiDestroyDeviceInfoList(dev_info)
            return None

        # 디바이스 경로 크기 확인
        required_size = ctypes.wintypes.DWORD(0)
        setupapi.SetupDiGetDeviceInterfaceDetailA(
            dev_info, ctypes.byref(iface_data),
            None, 0, ctypes.byref(required_size), None
        )

        detail = SP_DEVICE_INTERFACE_DETAIL_DATA_A()
        detail.cbSize = 5  # 32bit: 5, 64bit: 8 (packed struct)
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            detail.cbSize = 8

        result = setupapi.SetupDiGetDeviceInterfaceDetailA(
            dev_info, ctypes.byref(iface_data),
            ctypes.byref(detail), required_size.value, None, None
        )

        setupapi.SetupDiDestroyDeviceInfoList(dev_info)

        if result:
            return detail.DevicePath.decode('ascii')
        return None

    def open(self):
        """드라이버 핸들 열기"""
        path = self._get_device_path()
        if not path:
            return False

        self.handle = kernel32.CreateFileA(
            path.encode('ascii'),
            GENERIC_READ | GENERIC_WRITE,
            0, None, OPEN_EXISTING, 0, None
        )

        if self.handle == INVALID_HANDLE_VALUE or self.handle is None:
            self.handle = None
            return False

        # keep-alive 스레드 시작
        self._running = True
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()

        return True

    def _ioctl(self, code, in_buf=None, in_size=0, out_buf=None, out_size=0):
        """DeviceIoControl 호출"""
        if not self.handle:
            return False
        bytes_returned = ctypes.wintypes.DWORD(0)
        result = kernel32.DeviceIoControl(
            self.handle, code,
            in_buf, in_size,
            out_buf, out_size,
            ctypes.byref(bytes_returned), None
        )
        return bool(result)

    def add_display(self):
        """가상 디스플레이 추가, 인덱스 반환"""
        buf = (ctypes.c_byte * 32)()
        if self._ioctl(VDD_IOCTL_ADD, buf, 32, buf, 32):
            # 드라이버가 반환한 인덱스
            index = struct.unpack_from('<i', bytes(buf), 0)[0]
            self.displays.append(index)
            # 즉시 업데이트
            self._update()
            print(f"  [VDisplay] Parsec VDD: display added (index {index})")
            return index
        return -1

    def remove_display(self, index):
        """가상 디스플레이 제거"""
        buf = (ctypes.c_byte * 32)()
        # 인덱스를 big-endian 16bit로
        struct.pack_into('>H', buf, 0, index)
        if self._ioctl(VDD_IOCTL_REMOVE, buf, 32):
            if index in self.displays:
                self.displays.remove(index)
            self._update()
            print(f"  [VDisplay] Parsec VDD: display removed (index {index})")
            return True
        return False

    def _update(self):
        """keep-alive 핑"""
        self._ioctl(VDD_IOCTL_UPDATE)

    def _keepalive_loop(self):
        """100ms마다 드라이버에 핑 (안 하면 디스플레이 사라짐)"""
        while self._running and self.handle:
            self._update()
            time.sleep(0.1)

    def get_version(self):
        """드라이버 버전 조회"""
        buf = (ctypes.c_byte * 32)()
        if self._ioctl(VDD_IOCTL_VERSION, buf, 32, buf, 32):
            return bytes(buf[:4])
        return None

    def close(self):
        """정리"""
        self._running = False
        # 모든 가상 디스플레이 제거
        for idx in list(self.displays):
            self.remove_display(idx)
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None


# ============================================================
#  VirtualDrivers VDD 백엔드 (Named Pipe)
# ============================================================

VDD_PIPE_NAME = r"\\.\pipe\MTTVirtualDisplayPipe"


class VirtualDriversVDD:
    """VirtualDrivers Virtual Display Driver 제어 (Named Pipe)"""

    def __init__(self):
        self.pipe = None
        self.display_count = 0

    def is_available(self):
        """Named Pipe 존재 여부로 드라이버 확인"""
        try:
            handle = kernel32.CreateFileW(
                VDD_PIPE_NAME,
                GENERIC_READ | GENERIC_WRITE,
                0, None, OPEN_EXISTING, 0, None
            )
            if handle != INVALID_HANDLE_VALUE and handle is not None:
                kernel32.CloseHandle(handle)
                return True
        except Exception:
            pass
        return False

    def open(self):
        """Named Pipe 연결"""
        self.pipe = kernel32.CreateFileW(
            VDD_PIPE_NAME,
            GENERIC_READ | GENERIC_WRITE,
            0, None, OPEN_EXISTING, 0, None
        )
        if self.pipe == INVALID_HANDLE_VALUE or self.pipe is None:
            self.pipe = None
            return False
        return True

    def _send_command(self, cmd):
        """파이프에 UTF-16LE 명령 전송 및 응답 읽기"""
        if not self.pipe:
            return None

        encoded = cmd.encode('utf-16-le')
        written = ctypes.wintypes.DWORD(0)
        kernel32.WriteFile(
            self.pipe, encoded, len(encoded),
            ctypes.byref(written), None
        )

        # 응답 읽기
        buf = ctypes.create_string_buffer(512)
        read = ctypes.wintypes.DWORD(0)
        kernel32.ReadFile(
            self.pipe, buf, 512,
            ctypes.byref(read), None
        )
        if read.value > 0:
            return buf.raw[:read.value].decode('utf-16-le', errors='ignore').strip()
        return None

    def add_display(self, width=1920, height=1080, refresh=60):
        """가상 디스플레이 추가"""
        # 파이프 재연결 (각 명령마다)
        if not self.pipe:
            if not self.open():
                return -1

        resp = self._send_command(f"add {width} {height} {refresh}")
        if resp:
            self.display_count += 1
            print(f"  [VDisplay] VirtualDrivers VDD: display added "
                  f"({width}x{height}@{refresh}Hz) - {resp}")
            return self.display_count - 1
        return -1

    def remove_display(self, index=0):
        """가상 디스플레이 제거"""
        if not self.pipe:
            if not self.open():
                return False

        resp = self._send_command(f"remove {index}")
        if resp:
            self.display_count = max(0, self.display_count - 1)
            print(f"  [VDisplay] VirtualDrivers VDD: display removed - {resp}")
            return True
        return False

    def close(self):
        """정리"""
        for i in range(self.display_count):
            try:
                self.remove_display(0)
            except Exception:
                pass
        if self.pipe:
            kernel32.CloseHandle(self.pipe)
            self.pipe = None


# ============================================================
#  통합 매니저 (자동 감지 + 자동 설치)
# ============================================================

class VirtualDisplayManager:
    """가상 디스플레이 매니저 - 사용 가능한 드라이버 자동 감지/설치"""

    def __init__(self):
        self.backend = None
        self.backend_name = None
        self.active_displays = []

    def detect(self):
        """설치된 가상 디스플레이 드라이버 감지"""
        # 1. Parsec VDD
        parsec = ParsecVDD()
        if parsec.is_available():
            self.backend = parsec
            self.backend_name = "Parsec VDD"
            print(f"  [VDisplay] Detected: Parsec VDD")
            return True

        # 2. VirtualDrivers VDD
        vd = VirtualDriversVDD()
        if vd.is_available():
            self.backend = vd
            self.backend_name = "VirtualDrivers VDD"
            print(f"  [VDisplay] Detected: VirtualDrivers VDD")
            return True

        # 3. pnputil로 설치는 됐지만 파이프가 아직 안 열린 경우
        if is_vdd_installed():
            self.backend = vd
            self.backend_name = "VirtualDrivers VDD"
            print(f"  [VDisplay] Detected: VirtualDrivers VDD (device present)")
            return True

        return False

    def has_physical_display(self):
        """물리 모니터 연결 여부 확인"""
        try:
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            count = user32.GetSystemMetrics(80)  # SM_CMONITORS
            return count > 0
        except Exception:
            return True  # 확인 불가면 있다고 가정

    def ensure_display(self, width=1920, height=1080, refresh=60):
        """
        모니터가 없으면 가상 디스플레이 자동 생성.
        드라이버 미설치 시 자동 설치 시도.
        이미 모니터가 있으면 아무것도 안 함.
        반환: True(준비 완료), False(실패)
        """
        if self.has_physical_display():
            print(f"  [VDisplay] Physical display detected, skipping virtual display")
            return True

        print(f"  [VDisplay] No physical display - creating virtual display...")

        if not self.backend:
            if not self.detect():
                # 드라이버 없음 → 자동 설치 시도
                print(f"  [VDisplay] No driver found, attempting auto-install...")
                if install_vdd():
                    # 설치 후 재감지
                    time.sleep(2)
                    if not self.detect():
                        print(f"  [VDisplay] Driver installed but not responding yet")
                        # VirtualDrivers VDD를 기본으로 설정
                        self.backend = VirtualDriversVDD()
                        self.backend_name = "VirtualDrivers VDD"
                else:
                    print(f"  [VDisplay] Auto-install failed")
                    return False

        # 드라이버 열기
        if not self.backend.open():
            print(f"  [VDisplay] Failed to open {self.backend_name}")
            return False

        # 디스플레이 추가
        if isinstance(self.backend, ParsecVDD):
            index = self.backend.add_display()
        else:
            index = self.backend.add_display(width, height, refresh)

        if index >= 0:
            self.active_displays.append(index)
            # 디스플레이가 Windows에 인식될 때까지 대기
            time.sleep(1.0)
            print(f"  [VDisplay] Virtual display ready ({width}x{height}@{refresh}Hz)")
            return True

        print(f"  [VDisplay] Failed to create virtual display")
        return False

    def close(self):
        """모든 가상 디스플레이 제거 및 정리"""
        if self.backend:
            self.backend.close()
            print(f"  [VDisplay] Cleaned up virtual displays")
