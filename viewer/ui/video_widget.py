"""Video rendering widget with mouse/keyboard capture"""
import time

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Signal, Qt, QRect
from PySide6.QtGui import QPainter, QImage, QColor, QFont, QCursor

from viewer.core.input_mapper import (
    map_key_event,
    map_mouse_move,
    map_mouse_button,
    map_mouse_wheel,
)


class VideoWidget(QWidget):
    """Displays decoded video frames and captures mouse/keyboard input."""

    input_event = Signal(dict)
    capture_started = Signal()
    capture_ended = Signal()

    # Minimum interval between mouse-move events (~120 Hz)
    _MOVE_INTERVAL = 1.0 / 120.0

    def __init__(self, parent=None):
        super().__init__(parent)

        self._current_frame: QImage | None = None
        self._input_active: bool = False
        self._stream_width: int = 1920
        self._stream_height: int = 1080
        self._last_move_time: float = 0.0

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(640, 360)
        self.setStyleSheet("background-color: #000000;")

    # ── Public API ────────────────────────────────────

    def update_frame(self, qimage: QImage) -> None:
        """Store the latest decoded frame and schedule a repaint."""
        self._current_frame = qimage
        self.update()

    def set_stream_size(self, w: int, h: int) -> None:
        """Update the remote stream resolution used for coordinate mapping."""
        self._stream_width = w
        self._stream_height = h

    def is_capturing(self) -> bool:
        """Return whether input capture is currently active."""
        return self._input_active

    # ── Paint ─────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        if self._current_frame is not None:
            self._paint_frame(painter)
        else:
            self._paint_placeholder(painter)

        painter.end()

    def _paint_frame(self, painter: QPainter) -> None:
        """Scale frame to widget size while preserving aspect ratio, draw centered."""
        fw = self._current_frame.width()
        fh = self._current_frame.height()
        ww = self.width()
        wh = self.height()

        scale = min(ww / fw, wh / fh) if fw and fh else 1.0
        dw = int(fw * scale)
        dh = int(fh * scale)
        dx = (ww - dw) // 2
        dy = (wh - dh) // 2

        # Fill letterbox / pillarbox bands
        painter.fillRect(self.rect(), QColor("#000000"))
        target = QRect(dx, dy, dw, dh)
        painter.drawImage(target, self._current_frame)

    def _paint_placeholder(self, painter: QPainter) -> None:
        """Draw a dark background with centred waiting text."""
        painter.fillRect(self.rect(), QColor("#1e1e2e"))
        painter.setPen(QColor("#a6adc8"))
        font = QFont()
        font.setPixelSize(18)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, "연결 대기 중...")

    # ── Input capture helpers ─────────────────────────

    def _activate_capture(self) -> None:
        self._input_active = True
        self.setCursor(Qt.BlankCursor)
        self.setFocus()
        self.capture_started.emit()

    def _deactivate_capture(self) -> None:
        self._input_active = False
        self.setCursor(Qt.ArrowCursor)
        self.capture_ended.emit()

    # ── Mouse events ──────────────────────────────────

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self._input_active:
            self._activate_capture()
            return

        evt = map_mouse_button(event.button(), down=True)
        if evt:
            self.input_event.emit(evt)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if not self._input_active:
            return

        evt = map_mouse_button(event.button(), down=False)
        if evt:
            self.input_event.emit(evt)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._input_active:
            return

        now = time.monotonic()
        if now - self._last_move_time < self._MOVE_INTERVAL:
            return
        self._last_move_time = now

        pos = event.position()
        evt = map_mouse_move(
            pos.x(), pos.y(),
            self.width(), self.height(),
            self._stream_width, self._stream_height,
        )
        self.input_event.emit(evt)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._input_active:
            self._deactivate_capture()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self._input_active:
            return

        delta = event.angleDelta().y()
        evt = map_mouse_wheel(delta)
        self.input_event.emit(evt)

    # ── Keyboard events ───────────────────────────────

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self._input_active:
            return

        evt = map_key_event(event.key(), down=True)
        if evt:
            self.input_event.emit(evt)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if not self._input_active:
            return

        evt = map_key_event(event.key(), down=False)
        if evt:
            self.input_event.emit(evt)
