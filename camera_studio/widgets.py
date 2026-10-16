"""Controls of the LumaLink Studio window that Qt does not provide as-is."""

from __future__ import annotations

import subprocess
import sys

from PIL import Image
from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QSlider,
    QWidget,
)

from camera_studio.pause_frame import PAUSE_IN_SECONDS, PAUSE_OUT_SECONDS

# The logo's palette (assets/logo/README.md): graphite surfaces, light text
# like the aperture's blades, and its opening's colour as the one accent,
# kept for what is on or primary.
CHROME = "#232323"
FRAME = "#181818"
LETTERBOX = "#000000"
TEXT = "#f4f4f2"
TEXT_SECONDARY = "#a9a9a5"
TEXT_ON_SOLID = "#1b1b1b"
BORDER_SUBTLE = "#383838"
BORDER_CONTROL = "#6b6b68"
MENU = "#2d2d2d"
MENU_HOVER = "#3b3b3b"
ACCENT = "#ff5a3c"
ACCENT_HOVER = "#ff7559"
# macOS draws its own window buttons; Windows gets the prototype's ones.
MACOS = sys.platform == "darwin"
FONT_FAMILY = ".AppleSystemUIFont" if MACOS else "Segoe UI"

ICON_PAUSE = (
    '<rect x="1" y="0.5" width="4.5" height="13" rx="1"/>'
    '<rect x="8.5" y="0.5" width="4.5" height="13" rx="1"/>'
)
ICON_PLAY = '<path d="M3 1.2v11.6a.5.5 0 0 0 .77.42l9-5.8a.5.5 0 0 0 0-.84l-9-5.8A.5.5 0 0 0 3 1.2z"/>'
ICON_GEAR = (
    '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08'
    "a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74"
    "l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 "
    "1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08"
    "a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74"
    "l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25"
    'a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>'
)
ICON_MINIMIZE = '<path d="M0 7.5h14"/>'
ICON_MAXIMIZE = '<rect x="0.5" y="0.5" width="13" height="13" rx="1.5"/>'
ICON_RESTORE = (
    '<rect x="0.5" y="3.5" width="10" height="10" rx="1.5"/>'
    '<path d="M3.5 3.5V2a1.5 1.5 0 0 1 1.5-1.5h7A1.5 1.5 0 0 1 13.5 2v7a1.5 1.5 0 0 1-1.5 1.5h-1.5"/>'
)
ICON_CLOSE = '<path d="M1 1l12 12M13 1 1 13"/>'
ICON_STATUS_ON = '<circle cx="7" cy="7" r="6"/><path d="M4.2 7.2 6.2 9.2 9.9 5"/>'
ICON_STATUS_WAIT = '<circle cx="7" cy="7" r="6"/><path d="M7 3.8V7l2.2 1.4"/>'
ICON_STATUS_OFF = '<circle cx="7" cy="7" r="6"/><path d="M4.5 7h5"/>'
ICON_STATUS_ERROR = '<circle cx="7" cy="7" r="6"/><path d="M7 3.8v3.9M7 9.9v.2"/>'


def svg_pixmap(body: str, view: float = 14, size: int = 14, stroke: float = 1.0) -> QPixmap:
    """Render white line art; four times oversized so it stays sharp on any display."""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {view} {view}" fill="none" '
        f'stroke="{TEXT}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    )
    pixmap = QPixmap(size * 4, size * 4)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(4)
    return pixmap


def svg_icon(body: str, view: float = 14, size: int = 14, stroke: float = 1.0) -> QIcon:
    return QIcon(svg_pixmap(body, view, size, stroke))


def ui_font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(FONT_FAMILY)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def _chevron(painter: QPainter, center: QPointF) -> None:
    painter.setPen(QPen(QColor(TEXT), 1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    path = QPainterPath(QPointF(center.x() - 4.5, center.y() - 2.25))
    path.lineTo(center.x(), center.y() + 2.25)
    path.lineTo(center.x() + 4.5, center.y() - 2.25)
    painter.drawPath(path)


def _mix(first: QColor, second: QColor, amount: float) -> QColor:
    return QColor(
        round(first.red() + (second.red() - first.red()) * amount),
        round(first.green() + (second.green() - first.green()) * amount),
        round(first.blue() + (second.blue() - first.blue()) * amount),
        round(first.alpha() + (second.alpha() - first.alpha()) * amount),
    )


class Switch(QAbstractButton):
    """An on/off switch: outlined when off, filled when on."""

    _MARGIN = 2  # room for the keyboard focus ring

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(36 + 2 * self._MARGIN, 20 + 2 * self._MARGIN)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._position = 0.0
        self._keyboard_focus = False
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_position)
        self.toggled.connect(self._animate)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def _set_position(self, value: float) -> None:
        self._position = float(value)
        self.update()

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._keyboard_focus = event.reason() in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
        )
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self._keyboard_focus = False
        super().focusOutEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.4)
        margin = self._MARGIN
        amount = self._position
        track = QRectF(margin + 0.5, margin + 0.5, 35, 19)
        outline = _mix(QColor(BORDER_CONTROL), QColor(ACCENT), amount)
        fill = QColor(ACCENT)
        fill.setAlphaF(amount)
        painter.setPen(QPen(outline, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(track, 9.5, 9.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_mix(QColor(BORDER_CONTROL), QColor(TEXT_ON_SOLID), amount))
        painter.drawEllipse(QPointF(margin + 10 + 16 * amount, margin + 10), 6, 6)
        if self._keyboard_focus and self.hasFocus():
            painter.setPen(QPen(QColor(TEXT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 11, 11)


class Segmented(QFrame):
    """A row of mutually exclusive options; the chosen one is filled white."""

    changed = Signal(str)

    def __init__(self, values: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("seg")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for value in values:
            button = QPushButton(value, self)
            button.setObjectName("segButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.setProperty("value", value)
            self._group.addButton(button)
            layout.addWidget(button, 1)
            self._buttons[value] = button
        self._buttons[values[0]].setChecked(True)
        self._group.buttonClicked.connect(
            lambda button: self.changed.emit(button.property("value"))
        )

    def set_labels(self, labels: dict[str, str]) -> None:
        for value, label in labels.items():
            self._buttons[value].setText(label)

    def value(self) -> str:
        return self._group.checkedButton().property("value")

    def set_value(self, value: str) -> None:
        self._buttons[value].setChecked(True)


class Select(QComboBox):
    """A combo box in the outlined style, with the prototype's chevron."""

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.4)
        _chevron(painter, QPointF(self.width() - 16, self.height() / 2))

    def wheelEvent(self, event) -> None:  # noqa: N802
        # Scrolling the panel must not change a value the pointer passes over.
        event.ignore()


class Slider(QSlider):
    def __init__(self, minimum: int, maximum: int, value: int, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(minimum, maximum)
        self.setValue(value)
        self.setFixedHeight(24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class ProfileButton(QAbstractButton):
    """The title bar's Profile box: label, resolution over frame rate, chevron."""

    _HEIGHT = 30
    _CHEVRON_WIDTH = 28

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(self._HEIGHT)
        self._label = ""
        self._resolution = ""
        self._frame_rate = ""
        self._label_font = ui_font(12)
        self._value_font = ui_font(10)

    def set_texts(self, label: str, resolution: str, frame_rate: str) -> None:
        self._label, self._resolution, self._frame_rate = label, resolution, frame_rate
        self.updateGeometry()
        self.update()

    def _value_width(self) -> int:
        metrics = QFontMetrics(self._value_font)
        return max(metrics.horizontalAdvance(self._resolution), metrics.horizontalAdvance(self._frame_rate))

    def sizeHint(self) -> QSize:  # noqa: N802
        label_width = QFontMetrics(self._label_font).horizontalAdvance(self._label)
        return QSize(11 + label_width + 12 + self._value_width() + 8 + self._CHEVRON_WIDTH, self._HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(QPen(QColor(TEXT if self.hasFocus() else BORDER_SUBTLE), 1))
        hovered = self.underMouse() or self.isDown()
        painter.setBrush(QColor(255, 255, 255, 20) if hovered else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box, 6, 6)
        divider = self.width() - self._CHEVRON_WIDTH
        painter.setPen(QPen(QColor(BORDER_SUBTLE), 1))
        painter.drawLine(QPointF(divider + 0.5, 1), QPointF(divider + 0.5, self.height() - 1))

        painter.setPen(QColor(TEXT))
        painter.setFont(self._label_font)
        label_width = QFontMetrics(self._label_font).horizontalAdvance(self._label)
        painter.drawText(QRectF(11, 0, label_width + 2, self.height()), Qt.AlignmentFlag.AlignVCenter, self._label)
        painter.setFont(self._value_font)
        line = QFontMetrics(self._value_font).height() - 1
        value_left = 11 + label_width + 12
        top = (self.height() - 2 * line) / 2
        width = self._value_width() + 2
        painter.drawText(QRectF(value_left, top, width, line), Qt.AlignmentFlag.AlignVCenter, self._resolution)
        painter.drawText(QRectF(value_left, top + line, width, line), Qt.AlignmentFlag.AlignVCenter, self._frame_rate)
        _chevron(painter, QPointF(divider + self._CHEVRON_WIDTH / 2, self.height() / 2))

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


def _to_qimage(image: Image.Image) -> tuple[QImage, bytes]:
    source = image if image.mode == "RGB" else image.convert("RGB")
    # QImage does not copy the buffer, so the caller keeps the bytes alive.
    pixels = source.tobytes()
    return (
        QImage(pixels, source.width, source.height, source.width * 3, QImage.Format.Format_RGB888),
        pixels,
    )


def animations_enabled() -> bool:
    """Whether the system is set to show animations (an accessibility setting)."""
    if MACOS:
        return not _macos_reduce_motion()
    try:
        import ctypes

        enabled = ctypes.c_int(1)
        spi_getclientareaanimation = 0x1042
        ctypes.windll.user32.SystemParametersInfoW(
            spi_getclientareaanimation, 0, ctypes.byref(enabled), 0
        )
        return bool(enabled.value)
    except (AttributeError, OSError):
        return True


def _macos_reduce_motion() -> bool:
    """System Settings → Accessibility → Display → Reduce motion."""
    try:
        result = subprocess.run(
            ["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip() == "1"


class Preview(QWidget):
    """The camera frame, letterboxed on black."""

    PAUSE_IN_MS = round(PAUSE_IN_SECONDS * 1000)
    PAUSE_OUT_MS = round(PAUSE_OUT_SECONDS * 1000)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 180)
        self._image: QImage | None = None
        self._pixels: bytes | None = None
        self._pause_image: QImage | None = None
        self._pause_pixels: bytes | None = None
        self._pause_amount = 0.0
        self._pause_animation = QVariantAnimation(self)
        self._pause_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pause_animation.valueChanged.connect(self._set_pause_amount)
        self._pause_animation.finished.connect(self._pause_animation_finished)
        self.show_grid = True
        self.mirrored = False
        self.note = ""
        self.empty_title = ""
        self.empty_text = ""

    def set_image(self, image: Image.Image | None) -> None:
        if image is None:
            self._image = None
            self._pixels = None
        else:
            self._image, self._pixels = _to_qimage(image)
        self.update()

    def set_pause(self, background: Image.Image | None, animate: bool = True) -> None:
        """Fade the blurred still frame and the pause badge in, or out with ``None``."""
        showing = background is not None
        if showing:
            self._pause_image, self._pause_pixels = _to_qimage(background)
        self._pause_animation.stop()
        target = 1.0 if showing else 0.0
        if not animate or not self.isVisible() or not animations_enabled():
            self._set_pause_amount(target)
            self._pause_animation_finished()
            return
        self._pause_animation.setDuration(self.PAUSE_IN_MS if showing else self.PAUSE_OUT_MS)
        self._pause_animation.setStartValue(self._pause_amount)
        self._pause_animation.setEndValue(target)
        self._pause_animation.start()

    def is_paused(self) -> bool:
        return self._pause_image is not None

    def _set_pause_amount(self, amount: float) -> None:
        self._pause_amount = float(amount)
        self.update()

    def _pause_animation_finished(self) -> None:
        if self._pause_amount <= 0.0:
            self._pause_image = None
            self._pause_pixels = None
            self.update()

    def has_image(self) -> bool:
        return self._image is not None

    def frame_rect(self) -> QRectF:
        ratio = self._image.width() / self._image.height() if self._image else 16 / 9
        width = min(self.width(), self.height() * ratio)
        height = width / ratio
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(LETTERBOX))
        frame = self.frame_rect()
        if self._image is None:
            painter.fillRect(frame, QColor(FRAME))
            self._paint_empty_state(painter, frame)
            return

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.mirrored:
            painter.save()
            painter.translate(frame.left() + frame.right(), 0)
            painter.scale(-1, 1)
        painter.drawImage(frame, self._image)
        pausing = self._pause_image is not None and self._pause_amount > 0
        if pausing:
            painter.setOpacity(self._pause_amount)
            painter.drawImage(frame, self._pause_image)
            painter.setOpacity(1.0)
        if self.mirrored:
            painter.restore()
        if pausing:
            self._paint_pause_badge(painter, frame)

        if self.show_grid:
            painter.setPen(QPen(QColor(255, 255, 255, 36), 1))
            for step in (1, 2):
                x = round(frame.left() + frame.width() * step / 3) + 0.5
                y = round(frame.top() + frame.height() * step / 3) + 0.5
                painter.drawLine(QPointF(x, frame.top()), QPointF(x, frame.bottom()))
                painter.drawLine(QPointF(frame.left(), y), QPointF(frame.right(), y))
        if self.note:
            self._paint_note(painter, frame)

    def _paint_pause_badge(self, painter: QPainter, frame: QRectF) -> None:
        """The badge of pause_frame.paused_frame, growing and fading in."""
        amount = self._pause_amount
        diameter = frame.height() * 88 / 720 * (0.6 + 0.4 * amount)
        unit = diameter / 88
        center = frame.center()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(amount)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 153))
        painter.drawEllipse(center, diameter / 2, diameter / 2)
        painter.setBrush(QColor(TEXT))
        for bar_start in (6.0, 17.5):
            painter.drawRoundedRect(
                QRectF(
                    center.x() + (bar_start - 15) * unit,
                    center.y() - 11 * unit,
                    6.5 * unit,
                    22 * unit,
                ),
                1.5 * unit,
                1.5 * unit,
            )
        painter.restore()

    def _paint_note(self, painter: QPainter, frame: QRectF) -> None:
        font = ui_font(12)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(self.note) + 16
        box = QRectF(frame.left() + 12, frame.bottom() - 12 - 24, width, 24)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 140))
        painter.drawRoundedRect(box, 6, 6)
        painter.setPen(QColor(TEXT))
        painter.setFont(font)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.note)

    def _paint_empty_state(self, painter: QPainter, frame: QRectF) -> None:
        text_width = min(frame.width() - 48, 380)
        if text_width < 120:
            return
        title_font, body_font = ui_font(20, QFont.Weight.DemiBold), ui_font(14)
        flags = Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap
        painter.setFont(title_font)
        probe = QRectF(0, 0, text_width, 1000)
        title_height = painter.boundingRect(probe, flags, self.empty_title).height()
        painter.setFont(body_font)
        body_height = painter.boundingRect(probe, flags, self.empty_text).height()
        left = frame.center().x() - text_width / 2
        top = frame.center().y() - (title_height + 6 + body_height) / 2
        painter.setPen(QColor(TEXT))
        painter.setFont(title_font)
        painter.drawText(QRectF(left, top, text_width, title_height), flags, self.empty_title)
        painter.setPen(QColor(TEXT_SECONDARY))
        painter.setFont(body_font)
        painter.drawText(QRectF(left, top + title_height + 6, text_width, body_height), flags, self.empty_text)


STYLE_SHEET = f"""
QWidget {{ color: {TEXT}; font-family: "{FONT_FAMILY}"; font-size: 14px; }}
QToolTip {{ color: {TEXT}; background: {MENU}; border: 1px solid {MENU_HOVER}; padding: 3px 6px; }}
#root, #titlebar, #panel, #panelContent, #panelFoot {{ background: {CHROME}; }}
#panelFoot {{ border-top: 1px solid {BORDER_SUBTLE}; }}
QLabel {{ background: transparent; }}
QLabel[role="groupTitle"] {{ color: {TEXT_SECONDARY}; font-size: 12px; font-weight: 600; }}
QLabel[role="secondary"] {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
QLabel[role="value"] {{ color: {TEXT_SECONDARY}; font-size: 13px; }}
QLabel[role="strong"] {{ font-weight: 600; }}
QLabel[role="heading"] {{ font-size: 16px; font-weight: 600; }}
QLabel:disabled {{ color: rgba(255, 255, 255, 0.4); }}

QToolButton {{ background: transparent; border: none; border-radius: 6px; }}
QToolButton:hover, QToolButton:checked {{ background: rgba(255, 255, 255, 0.08); }}
QToolButton:focus {{ border: 1px solid {TEXT}; }}
QToolButton[role="window"] {{ border-radius: 0; }}

QPushButton#action {{ min-height: 34px; padding: 0 16px; border: 1px solid {ACCENT}; border-radius: 6px;
    background: {ACCENT}; color: {TEXT_ON_SOLID}; font-weight: 600; }}
QPushButton#action:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#action[quiet="true"] {{ background: transparent; color: {TEXT}; border: 1px solid {BORDER_CONTROL}; }}
QPushButton#action[quiet="true"]:hover {{ background: rgba(255, 255, 255, 0.08); }}
QPushButton#action:focus {{ border: 2px solid {TEXT}; }}
QPushButton#textButton {{ padding: 2px 0; border: none; background: transparent; font-size: 13px;
    text-decoration: underline; text-align: left; }}
QPushButton#textButton:disabled {{ color: rgba(255, 255, 255, 0.4); }}

QFrame#seg {{ border: 1px solid {BORDER_CONTROL}; border-radius: 6px; }}
QFrame#seg:disabled {{ border-color: rgba(184, 184, 184, 0.4); }}
QPushButton#segButton {{ min-height: 24px; padding: 0 2px; border: 1px solid transparent; border-radius: 3px;
    background: transparent; font-size: 13px; }}
QPushButton#segButton:checked {{ background: {TEXT}; color: {TEXT_ON_SOLID}; font-weight: 600; }}
QPushButton#segButton:focus {{ border: 1px solid {TEXT}; }}
QPushButton#segButton:disabled {{ color: rgba(255, 255, 255, 0.4); }}
QPushButton#segButton:checked:disabled {{ background: rgba(255, 255, 255, 0.4); color: {TEXT_ON_SOLID}; }}

QComboBox {{ min-height: 30px; padding: 0 34px 0 10px; border: 1px solid {BORDER_CONTROL}; border-radius: 6px;
    background: transparent; }}
QComboBox:focus {{ border-color: {TEXT}; }}
QComboBox:disabled {{ border-color: rgba(184, 184, 184, 0.4); color: rgba(255, 255, 255, 0.4); }}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{ background: {MENU}; border: 1px solid {MENU_HOVER}; outline: none; padding: 4px;
    selection-background-color: {MENU_HOVER}; selection-color: {TEXT}; }}

QSlider::groove:horizontal {{ height: 2px; border-radius: 1px; background: {BORDER_CONTROL}; }}
QSlider::sub-page:horizontal {{ height: 2px; border-radius: 1px; background: {ACCENT}; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -7px 0; border-radius: 8px; background: {TEXT}; }}
QSlider::groove:horizontal:disabled, QSlider::sub-page:horizontal:disabled {{ background: rgba(184, 184, 184, 0.4); }}
QSlider::handle:horizontal:disabled {{ background: #9a9a9a; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ width: 8px; margin: 2px; background: transparent; }}
QScrollBar::handle:vertical {{ min-height: 28px; border-radius: 2px; background: {BORDER_CONTROL}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QMenu {{ background: {MENU}; border: 1px solid {MENU_HOVER}; border-radius: 10px; padding: 4px; }}
QMenu::item {{ padding: 7px 14px 7px 8px; border-radius: 6px; }}
QMenu::item:selected {{ background: {MENU_HOVER}; }}
QMenu::indicator {{ width: 14px; height: 14px; margin-left: 6px; }}

#settingsCard {{ background: {MENU}; border: 1px solid {MENU_HOVER}; border-radius: 10px; }}
"""
