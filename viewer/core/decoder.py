"""FFmpeg H.264 decoder - outputs QImage frames"""

import os
import subprocess
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

FFMPEG_PATHS = [
    os.path.expanduser("~/AppData/Local/Microsoft/WinGet/Links/ffmpeg.exe"),
    "ffmpeg",
]


def find_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    for path in FFMPEG_PATHS:
        if os.path.exists(path):
            return path
    return "ffmpeg"


class FrameDecoder(QObject):
    frame_ready = Signal(object)  # QImage - use object to avoid copy issues

    def __init__(self, parent=None):
        super().__init__(parent)
        self.width = 0
        self.height = 0
        self.running = False
        self.decoder = None
        self.frames_decoded = 0

    def start(self, width=1920, height=1080):
        self.width = width
        self.height = height
        self.frames_decoded = 0
        self.running = True

        ffmpeg = find_ffmpeg()
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-flags", "low_delay",
            "-fflags", "+nobuffer+fastseek+flush_packets",
            "-flags2", "fast",
            "-f", "h264",
            "-probesize", "1024",
            "-analyzeduration", "0",
            "-i", "pipe:0",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}",
            "-threads", "1",
            "-flush_packets", "1",
            "-avioflags", "direct",
            "pipe:1",
        ]

        self.decoder = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        threading.Thread(target=self._read_frames, daemon=True).start()
        threading.Thread(target=self._read_errors, daemon=True).start()

    def feed(self, data: bytes):
        if self.decoder and self.decoder.stdin:
            try:
                self.decoder.stdin.write(data)
                self.decoder.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def restart(self, width, height):
        self.stop()
        self.start(width, height)

    def stop(self):
        self.running = False
        if self.decoder:
            try:
                self.decoder.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                self.decoder.terminate()
                self.decoder.wait(timeout=3)
            except Exception:
                self.decoder.kill()
            self.decoder = None

    def _read_frames(self):
        frame_size = self.width * self.height * 3
        while self.running and self.decoder:
            try:
                frame_data = self.decoder.stdout.read(frame_size)
                if not frame_data or len(frame_data) < frame_size:
                    break
                qimg = QImage(
                    frame_data,
                    self.width,
                    self.height,
                    self.width * 3,
                    QImage.Format_RGB888,
                )
                self.frame_ready.emit(qimg.copy())  # copy needed - buffer reused
                self.frames_decoded += 1
                if self.frames_decoded == 1:
                    print(f"[decoder] first frame decoded ({self.width}x{self.height})")
            except Exception:
                break

    def _read_errors(self):
        while self.running and self.decoder:
            try:
                line = self.decoder.stderr.readline()
                if not line:
                    break
                print(f"[ffmpeg] {line.decode(errors='replace').rstrip()}")
            except Exception:
                break
