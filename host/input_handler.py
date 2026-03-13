"""
입력 처리 모듈 - KSE 커널 드라이버 사용
kbdclass/mouclass 서비스 콜백 직접 호출 → 물리 입력과 동일
NCGuard 등 안티치트가 탐지 불가
"""

import ctypes
import ctypes.wintypes
import struct
import subprocess
import sys
import time
import os
import winreg

# IOCTL
FILE_DEVICE_UNKNOWN = 0x00000022
METHOD_BUFFERED = 0
FILE_ANY_ACCESS = 0
IOCTL_KSE_REQUEST = ((FILE_DEVICE_UNKNOWN << 16) | (FILE_ANY_ACCESS << 14) |
                     (0x800 << 2) | METHOD_BUFFERED)

# Commands
CMD_INPUT = 3
CMD_HANDSHAKE = 7
SUB_INPUT_KEYBOARD = 2
SUB_INPUT_MOUSE = 3

# Handshake magic
HANDSHAKE_MAGIC_REQUEST = 0x4B534500
HANDSHAKE_MAGIC_RESPONSE = 0x4B534501

# Mouse button flags (ntddmou.h)
MOUSE_LEFT_BUTTON_DOWN = 0x0001
MOUSE_LEFT_BUTTON_UP = 0x0002
MOUSE_RIGHT_BUTTON_DOWN = 0x0004
MOUSE_RIGHT_BUTTON_UP = 0x0008
MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
MOUSE_MIDDLE_BUTTON_UP = 0x0020
MOUSE_WHEEL = 0x0400

# Mouse move flags
MOUSE_MOVE_RELATIVE = 0
MOUSE_MOVE_ABSOLUTE = 1

# Keyboard flags
KEY_MAKE = 0
KEY_BREAK = 1
KEY_E0 = 2

# KSE_REQUEST layout (must match driver struct exactly)
# Total struct: cmd(4) + sub(4) + status(4) + union(max size)
# Union max = largest member. For mouse: dx(4)+dy(4)+buttonFlags(2)+buttonData(2)+mouseFlags(2) = 14
# For handshake: magic(4)+version(4) = 8
# For keyboard: scanCode(2)+flags(2) = 4
# Entire struct padded to fixed size
KSE_REQUEST_SIZE = 64  # safe upper bound

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value

# 번들된 드라이버 경로
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KERNEL_DIR = os.path.join(_PROJECT_ROOT, "drivers", "kernel")
KDU_EXE = os.path.join(KERNEL_DIR, "kdu.exe")
DRIVER_SYS = os.path.join(KERNEL_DIR, "wcnfs.sys")
SERVICE_NAME = "wcnfs"


def is_driver_loaded():
    """wcnfs 서비스가 실행 중인지 확인"""
    try:
        result = subprocess.run(
            ["sc", "query", SERVICE_NAME],
            capture_output=True, text=True, timeout=5
        )
        return "RUNNING" in result.stdout
    except Exception:
        return False


def _is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def load_driver():
    """KDU를 이용해 커널 드라이버 로드 (DSE 우회)"""
    if is_driver_loaded():
        print("  [Input] Driver already loaded")
        return True

    if not os.path.exists(DRIVER_SYS):
        print(f"  [Input] Driver not found: {DRIVER_SYS}")
        return False
    if not os.path.exists(KDU_EXE):
        print(f"  [Input] KDU not found: {KDU_EXE}")
        return False

    if not _is_admin():
        print("  [Input] Admin privileges required for driver loading")
        print("  [Input] Requesting elevation...")

        # 관리자 권한으로 로드 스크립트 실행
        load_script = os.path.join(KERNEL_DIR, "_load.py")
        host_dir = os.path.dirname(os.path.abspath(__file__))

        with open(load_script, "w") as f:
            f.write(f'''import sys, os
sys.path.insert(0, r"{host_dir}")
from input_handler import _do_load_driver
success = _do_load_driver()
input("Press Enter to close..." if not success else "")
sys.exit(0 if success else 1)
''')

        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{load_script}"', None, 1)
        if ret > 32:
            print("  [Input] Waiting for driver load...")
            for _ in range(30):
                time.sleep(1)
                if is_driver_loaded():
                    print("  [Input] Driver loaded successfully!")
                    return True
                # 레지스트리에 디바이스 경로가 등록되었는지도 확인
                if get_device_path():
                    print("  [Input] Driver loaded successfully!")
                    return True
            print("  [Input] Driver load timed out")
        else:
            print("  [Input] UAC denied or elevation failed")
        return False

    return _do_load_driver()


def _do_load_driver():
    """실제 드라이버 로드 수행 (관리자 권한 필요)"""
    print("  [Input] === Kernel Driver Loader ===")

    # 기존 서비스 정리
    print("  [Input] Cleaning up existing service...")
    subprocess.run(["sc", "stop", SERVICE_NAME],
                   capture_output=True, timeout=10)
    time.sleep(1)
    subprocess.run(["sc", "delete", SERVICE_NAME],
                   capture_output=True, timeout=10)
    time.sleep(1)

    # ThrottleStop 충돌 방지
    ts_result = subprocess.run(
        ["tasklist", "/fi", "imagename eq ThrottleStop.exe"],
        capture_output=True, text=True, timeout=5
    )
    restart_ts = "ThrottleStop" in ts_result.stdout
    if restart_ts:
        print("  [Input] Stopping ThrottleStop...")
        subprocess.run(["taskkill", "/f", "/im", "ThrottleStop.exe"],
                       capture_output=True, timeout=5)
        time.sleep(2)

    # DSE 비활성화 (KDU provider 55)
    print("  [Input] Disabling DSE...")
    result = subprocess.run(
        [KDU_EXE, "-prv", "55", "-dse", "0"],
        capture_output=True, text=True, timeout=30,
        cwd=KERNEL_DIR
    )

    if "Abort" in result.stdout or "Write result verification succeeded" not in result.stdout:
        print(f"  [Input] DSE disable failed: {result.stdout.strip()}")
        _restart_throttlestop(restart_ts)
        return False
    print("  [Input] DSE disabled")

    # 서비스 생성 및 시작
    print("  [Input] Creating and starting driver service...")
    driver_path = os.path.abspath(DRIVER_SYS)

    result = subprocess.run(
        ["sc", "create", SERVICE_NAME, "type=", "kernel",
         f"binPath=", driver_path],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        print(f"  [Input] sc create failed: {result.stderr.strip()}")
        _restore_dse(restart_ts)
        return False

    result = subprocess.run(
        ["sc", "start", SERVICE_NAME],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        print(f"  [Input] sc start failed: {result.stderr.strip()}")
        subprocess.run(["sc", "delete", SERVICE_NAME], capture_output=True)
        _restore_dse(restart_ts)
        return False

    print("  [Input] Driver started")

    # DSE 복원
    _restore_dse(restart_ts)

    # 확인
    time.sleep(1)
    if get_device_path():
        print("  [Input] Driver loaded and device registered!")
        return True

    print("  [Input] Driver may have loaded but device path not found yet")
    return True


def _restore_dse(restart_ts):
    """DSE 복원 + ThrottleStop 재시작"""
    print("  [Input] Restoring DSE...")
    result = subprocess.run(
        [KDU_EXE, "-prv", "55", "-dse", "6"],
        capture_output=True, text=True, timeout=30,
        cwd=KERNEL_DIR
    )
    if "Write result verification succeeded" in result.stdout:
        print("  [Input] DSE restored")
    else:
        print("  [Input] WARNING: DSE restore failed! Reboot recommended")

    _restart_throttlestop(restart_ts)


def _restart_throttlestop(restart):
    """ThrottleStop 재시작"""
    if not restart:
        return
    print("  [Input] Restarting ThrottleStop...")
    for path in [r"C:\Program Files\ThrottleStop\ThrottleStop.exe",
                 os.path.expandvars(r"%LOCALAPPDATA%\ThrottleStop\ThrottleStop.exe")]:
        if os.path.exists(path):
            subprocess.Popen([path], creationflags=0x00000008)  # DETACHED_PROCESS
            break


class _KSE_Handshake(ctypes.Structure):
    _fields_ = [
        ("magic", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
    ]


class _KSE_Keyboard(ctypes.Structure):
    _fields_ = [
        ("scanCode", ctypes.c_uint16),
        ("flags", ctypes.c_uint16),
    ]


class _KSE_Mouse(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("buttonFlags", ctypes.c_uint16),
        ("buttonData", ctypes.c_uint16),
        ("mouseFlags", ctypes.c_uint16),
    ]


class _KSE_Union(ctypes.Union):
    _fields_ = [
        ("handshake", _KSE_Handshake),
        ("keyboard", _KSE_Keyboard),
        ("mouse", _KSE_Mouse),
        ("_pad", ctypes.c_byte * 48),
    ]


class KSERequest(ctypes.Structure):
    """KSE_REQUEST structure matching driver layout.
    Union contains ULONGLONG members → 8-byte alignment → 4 bytes padding after status.
    """
    _fields_ = [
        ("cmd", ctypes.c_uint32),
        ("sub", ctypes.c_uint32),
        ("status", ctypes.c_int32),  # NTSTATUS
        ("_pad", ctypes.c_uint32),   # 4 bytes alignment padding
        ("u", _KSE_Union),
    ]


def get_device_path():
    """레지스트리에서 커널 드라이버 디바이스 경로 읽기"""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Services\wcnfs",
            0,
            winreg.KEY_READ
        )
        value, _ = winreg.QueryValueEx(key, "DeviceName")
        winreg.CloseKey(key)
        # \DosDevices\sXXXXX -> \\.\sXXXXX
        if value.startswith("\\DosDevices\\"):
            return "\\\\.\\" + value[len("\\DosDevices\\"):]
        return value
    except FileNotFoundError:
        return None
    except PermissionError:
        return None


class InputHandler:
    """입력 인젝션 - KSE 커널 드라이버 우선, 실패 시 SendInput 폴백"""

    def __init__(self, screen_width=1920, screen_height=1080, force_sendinput=False):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.handle = None
        self.connected = False
        self.use_sendinput = False
        self.force_sendinput = force_sendinput

    def connect(self):
        """커널 드라이버에 연결 시도, 실패 시 SendInput 모드"""
        # 강제 SendInput 모드
        if self.force_sendinput:
            print("  [Input] Forced SendInput mode (--sendinput)")
            self.use_sendinput = True
            self.connected = True
            print("  [Input] SendInput mode active")
            return True

        # 1. KSE 드라이버 시도
        if self._connect_kse():
            return True

        # 2. SendInput 폴백
        print("  [Input] Falling back to SendInput mode")
        self.use_sendinput = True
        self.connected = True
        print("  [Input] SendInput mode active")
        return True

    def _connect_kse(self):
        """KSE 커널 드라이버 연결"""
        device_path = get_device_path()
        if not device_path:
            print("  [Input] Driver not found in registry, attempting auto-load...")
            if load_driver():
                time.sleep(1)
                device_path = get_device_path()
            if not device_path:
                print("  [Input] KSE driver not available")
                return False

        print(f"  [Input] Device: {device_path}")

        self.handle = kernel32.CreateFileW(
            device_path,
            GENERIC_READ | GENERIC_WRITE,
            0, None, OPEN_EXISTING, 0, None
        )

        if self.handle == INVALID_HANDLE_VALUE or self.handle is None:
            err = ctypes.get_last_error()
            print(f"  [Input] CreateFile failed: error {err}")
            self.handle = None
            return False

        req = KSERequest()
        req.cmd = CMD_HANDSHAKE
        req.sub = 0
        req.u.handshake.magic = HANDSHAKE_MAGIC_REQUEST

        if self._ioctl(req):
            if req.u.handshake.magic == HANDSHAKE_MAGIC_RESPONSE:
                version = req.u.handshake.version
                print(f"  [Input] KSE driver connected (v{version >> 8}.{version & 0xFF})")
                self.connected = True
                return True
            else:
                print(f"  [Input] Handshake failed: bad response magic")
        else:
            print(f"  [Input] Handshake IOCTL failed")

        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None
        return False

    def _ioctl(self, req):
        """DeviceIoControl 호출"""
        if not self.handle:
            return False

        bytes_returned = ctypes.wintypes.DWORD(0)
        result = kernel32.DeviceIoControl(
            self.handle,
            IOCTL_KSE_REQUEST,
            ctypes.byref(req), ctypes.sizeof(req),
            ctypes.byref(req), ctypes.sizeof(req),
            ctypes.byref(bytes_returned),
            None
        )
        if not result:
            err = ctypes.get_last_error()
            print(f"  [Input] IOCTL failed: error {err}, cmd={req.cmd} sub={req.sub} status=0x{req.status:08X}")
        elif req.status != 0:
            print(f"  [Input] IOCTL ok but status=0x{req.status:08X}, cmd={req.cmd} sub={req.sub}")
        return bool(result)

    def handle_event(self, event):
        """Viewer에서 받은 입력 이벤트 처리"""
        if not self.connected:
            return

        evt_type = event.get("type")

        if evt_type == "mouse_move":
            self._mouse_move(event["x"], event["y"])
        elif evt_type == "mouse_down":
            self._mouse_button(event.get("button", "left"), down=True)
        elif evt_type == "mouse_up":
            self._mouse_button(event.get("button", "left"), down=False)
        elif evt_type == "mouse_wheel":
            self._mouse_wheel(event.get("delta", 0))
        elif evt_type == "key_down":
            self._key_event(event.get("scan", 0), down=True,
                           e0=event.get("e0", False))
        elif evt_type == "key_up":
            self._key_event(event.get("scan", 0), down=False,
                           e0=event.get("e0", False))

    # --- SendInput 구조체 ---

    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1

    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_WHEEL = 0x0800
    MOUSEEVENTF_ABSOLUTE = 0x8000

    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008

    def _mouse_move(self, x, y):
        """절대 좌표 마우스 이동"""
        abs_x = int(x * 65535 / max(self.screen_width, 1))
        abs_y = int(y * 65535 / max(self.screen_height, 1))

        if self.use_sendinput:
            self._send_mouse_input(abs_x, abs_y,
                                   self.MOUSEEVENTF_MOVE | self.MOUSEEVENTF_ABSOLUTE,
                                   0)
        else:
            req = KSERequest()
            req.cmd = CMD_INPUT
            req.sub = SUB_INPUT_MOUSE
            req.u.mouse.dx = abs_x
            req.u.mouse.dy = abs_y
            req.u.mouse.buttonFlags = 0
            req.u.mouse.buttonData = 0
            req.u.mouse.mouseFlags = MOUSE_MOVE_ABSOLUTE
            self._ioctl(req)

    def _mouse_button(self, button, down):
        """마우스 버튼"""
        if self.use_sendinput:
            flag_map = {
                ("left", True): self.MOUSEEVENTF_LEFTDOWN,
                ("left", False): self.MOUSEEVENTF_LEFTUP,
                ("right", True): self.MOUSEEVENTF_RIGHTDOWN,
                ("right", False): self.MOUSEEVENTF_RIGHTUP,
                ("middle", True): self.MOUSEEVENTF_MIDDLEDOWN,
                ("middle", False): self.MOUSEEVENTF_MIDDLEUP,
            }
            flags = flag_map.get((button, down), 0)
            if flags:
                self._send_mouse_input(0, 0, flags, 0)
        else:
            flags = 0
            if button == "left":
                flags = MOUSE_LEFT_BUTTON_DOWN if down else MOUSE_LEFT_BUTTON_UP
            elif button == "right":
                flags = MOUSE_RIGHT_BUTTON_DOWN if down else MOUSE_RIGHT_BUTTON_UP
            elif button == "middle":
                flags = MOUSE_MIDDLE_BUTTON_DOWN if down else MOUSE_MIDDLE_BUTTON_UP

            req = KSERequest()
            req.cmd = CMD_INPUT
            req.sub = SUB_INPUT_MOUSE
            req.u.mouse.dx = 0
            req.u.mouse.dy = 0
            req.u.mouse.buttonFlags = flags
            req.u.mouse.buttonData = 0
            req.u.mouse.mouseFlags = 0
            self._ioctl(req)

    def _mouse_wheel(self, delta):
        """마우스 휠"""
        if self.use_sendinput:
            self._send_mouse_input(0, 0, self.MOUSEEVENTF_WHEEL, delta)
        else:
            req = KSERequest()
            req.cmd = CMD_INPUT
            req.sub = SUB_INPUT_MOUSE
            req.u.mouse.dx = 0
            req.u.mouse.dy = 0
            req.u.mouse.buttonFlags = MOUSE_WHEEL
            req.u.mouse.buttonData = delta & 0xFFFF
            req.u.mouse.mouseFlags = 0
            self._ioctl(req)

    def _key_event(self, scan, down, e0=False):
        """키보드 이벤트"""
        if self.use_sendinput:
            flags = self.KEYEVENTF_SCANCODE
            if e0:
                flags |= self.KEYEVENTF_EXTENDEDKEY
            if not down:
                flags |= self.KEYEVENTF_KEYUP
            self._send_key_input(scan, flags)
        else:
            flags = KEY_MAKE if down else KEY_BREAK
            if e0:
                flags |= KEY_E0

            req = KSERequest()
            req.cmd = CMD_INPUT
            req.sub = SUB_INPUT_KEYBOARD
            req.u.keyboard.scanCode = scan & 0xFFFF
            req.u.keyboard.flags = flags
            self._ioctl(req)

    def _send_mouse_input(self, dx, dy, flags, data):
        """SendInput으로 마우스 이벤트 전송"""
        # MOUSEINPUT: dx(4) dy(4) mouseData(4) dwFlags(4) time(4) dwExtraInfo(ptr)
        ptr_size = ctypes.sizeof(ctypes.c_void_p)
        # INPUT struct: type(4) + padding + MOUSEINPUT
        # 64bit: type(4) + pad(4) + MOUSEINPUT(32) = 40 bytes
        inp = (ctypes.c_byte * 40)()
        struct.pack_into("I", inp, 0, self.INPUT_MOUSE)  # type
        struct.pack_into("i", inp, 8, dx)       # dx
        struct.pack_into("i", inp, 12, dy)      # dy
        struct.pack_into("i", inp, 16, data)    # mouseData
        struct.pack_into("I", inp, 20, flags)   # dwFlags
        struct.pack_into("I", inp, 24, 0)       # time
        # dwExtraInfo = 0 (ptr at offset 28 on 64bit, already zeroed)
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.SendInput(1, ctypes.byref(inp), 40)

    def _send_key_input(self, scan, flags):
        """SendInput으로 키보드 이벤트 전송"""
        # KEYBDINPUT: wVk(2) wScan(2) dwFlags(4) time(4) dwExtraInfo(ptr)
        inp = (ctypes.c_byte * 40)()
        struct.pack_into("I", inp, 0, self.INPUT_KEYBOARD)  # type
        struct.pack_into("H", inp, 8, 0)           # wVk = 0 (scan code mode)
        struct.pack_into("H", inp, 10, scan)        # wScan
        struct.pack_into("I", inp, 12, flags)       # dwFlags
        struct.pack_into("I", inp, 16, 0)           # time
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.SendInput(1, ctypes.byref(inp), 40)

    def update_resolution(self, width, height):
        self.screen_width = width
        self.screen_height = height

    def close(self):
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None
        self.connected = False
        mode = "SendInput" if self.use_sendinput else "KSE driver"
        print(f"  [Input] {mode} closed")
