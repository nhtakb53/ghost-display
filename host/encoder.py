"""
FFmpeg NVENC H.264 인코더 모듈
캡처된 BGRA 프레임 → H.264 NAL units 변환

NAL 단위로 파싱하여 전달 — SPS/PPS/IDR/P-frame 구분
"""

import subprocess
import threading
import time
import os


def find_ffmpeg():
    """FFmpeg 바이너리 경로 탐색 (내장 모듈 → 시스템 PATH 순)"""
    # 1. imageio-ffmpeg 내장 바이너리
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass

    # 2. 시스템 경로 (SYSTEM 권한에서도 찾을 수 있도록 모든 사용자 폴더 탐색)
    candidates = [
        os.path.expanduser("~/AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe"),
    ]
    # SYSTEM 권한 실행 시 ~ 가 다른 경로이므로 Users 폴더 전체 탐색
    users_dir = os.path.join(os.environ.get("SystemDrive", "C:"), os.sep, "Users")
    if os.path.exists(users_dir):
        try:
            for user in os.listdir(users_dir):
                winget_ffmpeg = os.path.join(users_dir, user, "AppData", "Local", "Microsoft", "WinGet", "Links", "ffmpeg.exe")
                if winget_ffmpeg not in candidates:
                    candidates.append(winget_ffmpeg)
        except:
            pass
    candidates += ["ffmpeg", "ffmpeg.exe"]
    for path in candidates:
        if os.path.exists(path):
            return path

    return "ffmpeg"


# H.264 NAL unit types
NAL_SLICE = 1       # P-frame (non-IDR slice)
NAL_IDR = 5         # IDR 키프레임
NAL_SEI = 6         # Supplemental Enhancement Info
NAL_SPS = 7         # Sequence Parameter Set
NAL_PPS = 8         # Picture Parameter Set


def parse_nal_type(nal_bytes):
    """NAL unit의 타입 번호 추출 (첫 바이트 하위 5비트)"""
    if len(nal_bytes) < 1:
        return -1
    return nal_bytes[0] & 0x1F


class H264Encoder:
    def __init__(self, width, height, fps=60, bitrate="8M", use_nvenc=True,
                 input_width=0, input_height=0):
        self.width = width
        self.height = height
        self.input_width = input_width or width
        self.input_height = input_height or height
        self.fps = fps
        self.bitrate = bitrate
        self.use_nvenc = use_nvenc
        self.process = None
        self.running = False
        self.on_nal = None       # 콜백: (nal_data_with_startcode, nal_type, is_keyframe)
        self._read_thread = None
        self.encode_count = 0
        self.start_time = 0

        # SPS/PPS 캐시 (Viewer 접속 시 먼저 전송)
        self.sps = None
        self.pps = None

    @staticmethod
    def _parse_bitrate(bitrate_str):
        """'20M' → 20000000, '5000K' → 5000000"""
        s = str(bitrate_str).strip().upper()
        if s.endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        elif s.endswith("K"):
            return int(float(s[:-1]) * 1_000)
        return int(s)

    def start(self):
        ffmpeg = find_ffmpeg()
        encoder = "h264_nvenc" if self.use_nvenc else "libx264"
        needs_scale = (self.input_width != self.width or
                       self.input_height != self.height)

        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-f", "rawvideo",
            "-pix_fmt", "bgra",
            "-s", f"{self.input_width}x{self.input_height}",
            "-r", str(self.fps),
            "-i", "pipe:0",
        ]

        if needs_scale:
            cmd += ["-vf", f"scale={self.width}:{self.height}:flags=fast_bilinear"]

        cmd += ["-c:v", encoder]

        if self.use_nvenc:
            cmd += [
                "-preset", "p4",
                "-tune", "ull",
                "-profile:v", "high",
                "-b:v", self.bitrate,
                "-maxrate", str(int(self._parse_bitrate(self.bitrate) * 1.5)),
                "-bufsize", self.bitrate,
                "-zerolatency", "1",
                "-forced_idr", "1",
                "-g", str(self.fps),  # 1초마다 키프레임
                "-bf", "0",
                "-rc", "cbr",
            ]
        else:
            cmd += [
                "-preset", "superfast",
                "-tune", "zerolatency",
                "-profile:v", "high",
                "-b:v", self.bitrate,
                "-maxrate", str(int(self._parse_bitrate(self.bitrate) * 1.5)),
                "-bufsize", self.bitrate,
                "-g", str(self.fps),  # 1초마다 키프레임
                "-bf", "0",
            ]

        # 매 키프레임에 SPS/PPS 반복 + raw Annex-B 출력
        cmd += [
            "-bsf:v", "dump_extra=freq=keyframe",
            "-f", "h264",
            "pipe:1",
        ]

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

        self.running = True
        self.start_time = time.time()
        self.encode_count = 0

        self._read_thread = threading.Thread(target=self._read_and_parse, daemon=True)
        self._read_thread.start()

        self._err_thread = threading.Thread(target=self._read_errors, daemon=True)
        self._err_thread.start()

        scale_info = ""
        if needs_scale:
            scale_info = f", scale {self.input_width}x{self.input_height}->{self.width}x{self.height}"
        print(f"  [Encoder] Started ({encoder}, {self.width}x{self.height}, {self.bitrate}{scale_info})")

    def _read_and_parse(self):
        """FFmpeg stdout에서 H.264 Annex-B 스트림을 NAL 단위로 파싱"""
        buf = bytearray()
        CHUNK = 65536

        try:
            while self.running:
                data = self.process.stdout.read(CHUNK)
                if not data:
                    break
                buf.extend(data)
                self._extract_nals(buf)
        except Exception as e:
            if self.running:
                print(f"  [Encoder] Read error: {e}")

        # 남은 데이터 플러시
        if buf and self.running:
            self._emit_nal(bytes(buf))

    def _extract_nals(self, buf):
        """버퍼에서 NAL unit 경계(00 00 00 01 또는 00 00 01) 찾아서 분리"""
        while True:
            # 첫 번째 start code 찾기
            pos = self._find_start_code(buf, 0)
            if pos < 0:
                break

            # 다음 start code 찾기
            sc_len = 4 if buf[pos:pos+4] == b'\x00\x00\x00\x01' else 3
            next_pos = self._find_start_code(buf, pos + sc_len)

            if next_pos < 0:
                # 아직 다음 NAL이 안 왔음 → 현재 NAL 유지하고 대기
                if pos > 0:
                    del buf[:pos]
                break

            # NAL 추출 (start code 포함)
            nal = bytes(buf[pos:next_pos])
            del buf[:next_pos]
            self._emit_nal(nal)

    def _find_start_code(self, buf, offset):
        """Annex-B start code (00 00 00 01 또는 00 00 01) 위치 찾기"""
        i = offset
        blen = len(buf)
        while i < blen - 3:
            if buf[i] == 0 and buf[i+1] == 0:
                if buf[i+2] == 1:
                    return i
                if buf[i+2] == 0 and i + 3 < blen and buf[i+3] == 1:
                    return i
            i += 1
        return -1

    def _emit_nal(self, nal_data):
        """NAL unit 처리 — 타입 식별 + 캐싱 + 콜백"""
        # start code 건너뛰고 NAL 헤더 읽기
        if nal_data[:4] == b'\x00\x00\x00\x01':
            nal_body = nal_data[4:]
        elif nal_data[:3] == b'\x00\x00\x01':
            nal_body = nal_data[3:]
        else:
            nal_body = nal_data

        nal_type = parse_nal_type(nal_body)

        # SPS/PPS 캐싱
        if nal_type == NAL_SPS:
            self.sps = nal_data
        elif nal_type == NAL_PPS:
            self.pps = nal_data

        is_keyframe = nal_type == NAL_IDR

        if self.on_nal:
            self.on_nal(nal_data, nal_type, is_keyframe)

    def _read_errors(self):
        try:
            for line in self.process.stderr:
                if self.running:
                    msg = line.decode(errors="replace").strip()
                    if msg:
                        print(f"  [Encoder/FFmpeg] {msg}")
        except:
            pass

    def request_keyframe(self):
        """다음 프레임을 키프레임으로 강제 (Viewer 접속 시 즉시 화면 표시)"""
        self._force_keyframe = True

    def encode_frame(self, bgra_frame):
        if not self.running or not self.process:
            return False
        try:
            # 키프레임 강제 요청이 있으면 인코더 재시작 없이 처리
            # FFmpeg stdin 파이프로는 직접 키프레임 요청이 불가하므로
            # _force_keyframe 플래그로 메인 루프에서 처리
            self.process.stdin.write(bgra_frame.tobytes())
            self.encode_count += 1
            return True
        except (BrokenPipeError, OSError) as e:
            if self.running:
                print(f"  [Encoder] Write error: {e}")
            self.running = False
            return False

    def get_sps_pps(self):
        """캐시된 SPS + PPS 반환 (둘 다 있을 때만 반환)"""
        if self.sps and self.pps:
            return self.sps + self.pps
        return None

    def get_fps(self):
        elapsed = time.time() - self.start_time
        if elapsed > 0:
            return self.encode_count / elapsed
        return 0

    def stop(self):
        self.running = False
        if self.process:
            try:
                self.process.stdin.close()
            except:
                pass
            try:
                self.process.stdout.close()
            except:
                pass
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except:
                try:
                    self.process.kill()
                except:
                    pass
            self.process = None
        # _read_and_parse 스레드 종료 대기
        if self._read_thread and self._read_thread.is_alive():
            self._read_thread.join(timeout=3)
        print(f"  [Encoder] Stopped (avg {self.get_fps():.1f} fps)")
