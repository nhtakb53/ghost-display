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
        self._mouse_pos = None  # 캡처 중 마우스 위치
        self._video_rect = None  # 영상 실제 렌더링 영역 (dx, dy, dw, dh)

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(640, 360)
        self.setStyleSheet("background-color: #000000;")

        # 커서 이미지 생성
        self._cursor_image = self._create_cursor_image()

    # ── Public API ────────────────────────────────────

    def update_frame(self, qimage: QImage) -> None:
        """Store the latest decoded frame and schedule a repaint."""
        self._current_frame = qimage
        self._status_text = None
        self.update()

    def set_status_text(self, text: str) -> None:
        """Show status text on the placeholder screen."""
        self._status_text = text
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

        # 캡처 중이면 커서 그리기
        if self._input_active and self._mouse_pos is not None:
            painter.drawImage(self._mouse_pos[0], self._mouse_pos[1], self._cursor_image)

        painter.end()

    def _paint_frame(self, painter: QPainter) -> None:
        """Scale frame to fill widget completely (no letterbox)."""
        fw = self._current_frame.width()
        fh = self._current_frame.height()
        ww = self.width()
        wh = self.height()

        # 위젯 전체를 채움 (비율 유지, 넘치는 부분 잘림)
        scale = max(ww / fw, wh / fh) if fw and fh else 1.0
        dw = int(fw * scale)
        dh = int(fh * scale)
        dx = (ww - dw) // 2
        dy = (wh - dh) // 2

        target = QRect(dx, dy, dw, dh)
        painter.drawImage(target, self._current_frame)

        # 영상 렌더링 영역 저장 (마우스 좌표 변환용)
        self._video_rect = (dx, dy, dw, dh)

    def _paint_placeholder(self, painter: QPainter) -> None:
        """Draw a dark background with centred waiting text."""
        painter.fillRect(self.rect(), QColor("#1e1e2e"))
        painter.setPen(QColor("#a6adc8"))
        font = QFont()
        font.setPixelSize(18)
        painter.setFont(font)
        text = getattr(self, '_status_text', None) or "연결 대기 중..."
        painter.drawText(self.rect(), Qt.AlignCenter, text)

    # ── Cursor ──────────────────────────────────────────

    def _create_cursor_image(self) -> QImage:
        """흰색 화살표 커서 이미지 생성 (24x24)"""
        from PySide6.QtGui import QPainterPath, QPen, QBrush
        size = 24
        img = QImage(size, size, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)

        path = QPainterPath()
        path.moveTo(0, 0)
        path.lineTo(0, 18)
        path.lineTo(4, 14)
        path.lineTo(8, 22)
        path.lineTo(11, 20)
        path.lineTo(7, 13)
        path.lineTo(12, 13)
        path.closeSubpath()

        p.setPen(QPen(QColor("#000000"), 1.5))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawPath(path)
        p.end()
        return img

    # ── Input capture helpers ─────────────────────────

    def _activate_capture(self) -> None:
        self._input_active = True
        self._mouse_pos = self.mapFromGlobal(QCursor.pos())
        self._mouse_pos = (self._mouse_pos.x(), self._mouse_pos.y())
        self.setCursor(Qt.BlankCursor)
        self.setFocus()
        self.capture_started.emit()

    def _deactivate_capture(self) -> None:
        self._input_active = False
        self._mouse_pos = None
        self.setCursor(Qt.ArrowCursor)
        self.update()
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

    def _widget_to_stream(self, wx, wy):
        """위젯 좌표 → 스트림 좌표 (영상 렌더링 영역 기준)"""
        if self._video_rect:
            dx, dy, dw, dh = self._video_rect
        else:
            dx, dy, dw, dh = 0, 0, self.width(), self.height()

        # 영상 영역 내 상대 좌표
        rx = wx - dx
        ry = wy - dy

        # 클램프
        rx = max(0, min(rx, dw))
        ry = max(0, min(ry, dh))

        # 스트림 좌표로 변환
        sx = int(rx * self._stream_width / dw) if dw else 0
        sy = int(ry * self._stream_height / dh) if dh else 0
        return sx, sy

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        if self._input_active:
            self._mouse_pos = (int(pos.x()), int(pos.y()))
            self.update()  # 커서 다시 그리기

            now = time.monotonic()
            if now - self._last_move_time < self._MOVE_INTERVAL:
                return
            self._last_move_time = now

            sx, sy = self._widget_to_stream(pos.x(), pos.y())
            self.input_event.emit({"type": "mouse_move", "x": sx, "y": sy})

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
