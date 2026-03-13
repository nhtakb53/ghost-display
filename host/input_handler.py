"""
입력 처리 모듈 - KSE 커널 드라이버 사용
kbdclass/mouclass 서비스 콜백 직접 호출 → 물리 입력과 동일
NCGuard 등 안티치트가 탐지 불가
"""

import ctypes
import ctypes.wintypes
import struct
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
    """KSE 커널 드라이버를 통한 입력 인젝션"""

    def __init__(self, screen_width=1920, screen_height=1080):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.handle = None
        self.connected = False

    def connect(self):
        """커널 드라이버에 연결"""
        device_path = get_device_path()
        if not device_path:
            print("  [Input] WARNING: KSE driver not found in registry")
            print("  [Input] Run load_driver.bat first")
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

        # 핸드셰이크
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
        return bool(result)

    def handle_event(self, event):
        """Viewer에서 받은 입력 이벤트 처리"""
        if not self.connected:
            print(f"  [Input] Event ignored (not connected): {event}")
            return

        evt_type = event.get("type")
        print(f"  [Input] Event: {event}")

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

    def _mouse_move(self, x, y):
        """절대 좌표 마우스 이동"""
        # 0~65535 정규화
        abs_x = int(x * 65535 / max(self.screen_width, 1))
        abs_y = int(y * 65535 / max(self.screen_height, 1))

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
        """키보드 이벤트 (스캔코드 기반)"""
        flags = KEY_MAKE if down else KEY_BREAK
        if e0:
            flags |= KEY_E0

        req = KSERequest()
        req.cmd = CMD_INPUT
        req.sub = SUB_INPUT_KEYBOARD
        req.u.keyboard.scanCode = scan & 0xFFFF
        req.u.keyboard.flags = flags
        self._ioctl(req)

    def update_resolution(self, width, height):
        self.screen_width = width
        self.screen_height = height

    def close(self):
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None
            self.connected = False
            print("  [Input] Driver handle closed")
