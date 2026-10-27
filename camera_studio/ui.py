"""The LumaLink Studio window."""

from __future__ import annotations

import os
import queue
import socket
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from camera_studio import __version__, updates
from camera_studio.i18n import LANGUAGE_NAMES, LANGUAGES, format_number, translate
from camera_studio.pause_frame import (
    PAUSE_IN_SECONDS,
    PAUSE_OUT_SECONDS,
    ease_out,
    paused_background,
    paused_frame,
    transition_frame,
)
from camera_studio.phones import LINKS, Phone, PhoneFinder, frame_rates, zoom_range
from camera_studio.stream import (
    H264Decoder,
    H264FrameReceiver,
    PhoneRefused,
    correct_stream_orientation,
    local_network_blocked,
)
from camera_studio.virtual_camera import VirtualCameraOutput
from camera_studio.widgets import (
    ICON_CLOSE,
    ICON_GEAR,
    ICON_MAXIMIZE,
    ICON_MINIMIZE,
    ICON_PAUSE,
    ICON_PLAY,
    ICON_RESTORE,
    ICON_STATUS_ERROR,
    ICON_STATUS_OFF,
    ICON_STATUS_ON,
    ICON_STATUS_WAIT,
    MACOS,
    STYLE_SHEET,
    Preview,
    ProfileButton,
    Segmented,
    Select,
    Slider,
    Switch,
    animations_enabled,
    svg_icon,
    svg_pixmap,
)

STREAM_PORT = 8765
LINK_NAMES = {"usb": "USB", "adb": "USB (ADB)", "wifi": "Wi-Fi"}
# After a connection drops, the next try waits this long; after the phone
# refused, it is left alone for a while unless the user picks it again.
RETRY_DELAY = 2.0
REFUSED_PAUSE = {"busy": 10.0, "denied": 30.0, "impostor": 60.0}
VPN_CHECK_INTERVAL = 10.0
PANEL_WIDTH = 360
TITLE_BAR_HEIGHT = 40
UPDATE_FIRST_CHECK_DELAY_MS = 3_000
UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000
# Room for the macOS close, minimize and zoom buttons drawn over the title bar.
MACOS_WINDOW_BUTTONS_WIDTH = 78
RESIZE_MARGIN = 5

# Resolution, frame rate, and the frame size shown in the menu.
PROFILES = (
    ("480p", 30, "640×480"),
    ("720p", 30, "1280×720"),
    ("720p", 60, "1280×720"),
    ("1080p", 30, "1920×1080"),
    ("1080p", 60, "1920×1080"),
)
DEFAULT_PROFILE = 1
ISO_VALUES = ["auto", "100", "200", "400", "800", "1600"]

# The phone app matches these exact words, whatever the interface language.
PHONE_AUTO = "Авто"
PHONE_WHITE_BALANCE = (
    ("auto", "Авто"),
    ("wb_incandescent", "Лампа"),
    ("wb_fluorescent", "Люминесцентный"),
    ("wb_daylight", "Дневной"),
    ("wb_cloudy", "Облачно"),
)
PHONE_LENS = {"rear": "Задняя", "front": "Фронтальная"}


class _TitleBar(QWidget):
    """Drag area of the frameless window; holds the tools and window buttons."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("titlebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self.gap = QWidget(self)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # The prototype leaves 257 px between the tools and the window buttons
        # at 1920 px; keep that proportion on narrower windows.
        self.gap.setFixedWidth(int(max(12, min(257, self.width() * 0.134))))
        super().resizeEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().toggle_maximized()


class _ResizeGrip(QWidget):
    """An invisible strip on a window edge that starts a native resize."""

    def __init__(self, parent: QWidget, edges: Qt.Edge, cursor: Qt.CursorShape) -> None:
        super().__init__(parent)
        self._edges = edges
        self.setCursor(cursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemResize(self._edges)


class _SettingsPopup(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame(self)
        self.card.setObjectName("settingsCard")
        self.card.setFixedWidth(320)
        outer.addWidget(self.card)
        self.content = QVBoxLayout(self.card)
        self.content.setContentsMargins(20, 20, 20, 20)
        self.content.setSpacing(24)


class MainWindow(QWidget):
    def __init__(self, settings: QSettings | None = None) -> None:
        if MACOS:
            # A native window whose content runs under a transparent title
            # bar: macOS keeps its own window buttons, resizing and snapping.
            flags = (
                Qt.WindowType.Window
                | Qt.WindowType.ExpandedClientAreaHint
                | Qt.WindowType.NoTitleBarBackgroundHint
            )
        else:
            flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        super().__init__(None, flags)
        # Our own title bar fills the area under the transparent system one.
        self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        self.setObjectName("root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.settings = settings or QSettings("LumaLink", "LumaLink Studio")
        language = str(self.settings.value("language", "ru"))
        self.language = language if language in LANGUAGES else "ru"

        self.frames: queue.Queue[Image.Image] = queue.Queue(maxsize=1)
        self.status_messages: queue.Queue[tuple[str, bool]] = queue.Queue()
        self.virtual_camera_status_messages: queue.Queue[tuple[int, str, bool]] = queue.Queue()
        self.receiver: H264FrameReceiver | None = None
        # The phones around, from the finder thread, and the one connected.
        self.phones: list[Phone] = []
        self.phone_lists: queue.Queue[list[Phone]] = queue.Queue()
        self.accepted: queue.Queue[tuple[int, dict]] = queue.Queue()
        self.refusals: queue.Queue[tuple[int, PhoneRefused]] = queue.Queue()
        self.phone: Phone | None = None
        self.camera_info: dict | None = None
        self.connection_run = 0
        # Off once the user disconnects; picking a phone or Connect turns it on.
        self.auto_connect = True
        self.retry_at = 0.0
        self.held_back: dict[str, float] = {}
        self.wait_text = ""
        self.vpn_checked_at = -VPN_CHECK_INTERVAL
        self.finder: PhoneFinder | None = None
        self._devices_shown: tuple | None = None
        self.virtual_camera: VirtualCameraOutput | None = None
        self.virtual_camera_run = 0
        self.virtual_camera_device = ""
        self.virtual_camera_failed = False
        self.decoder = H264Decoder()
        self.frame_timestamps: deque[float] = deque(maxlen=30)
        self.streaming = False
        self.camera_connected = False
        self.paused = False
        self.paused_image: Image.Image | None = None
        self.latest_image: Image.Image | None = None
        # The virtual camera's copy of the pause animation: what is fading,
        # and when it started. ``None`` when nothing is in transition.
        self.pause_fade: tuple[Image.Image, Image.Image, Image.Image, float] | None = None
        self.resume_fade: tuple[Image.Image, float] | None = None
        self._paused_background: Image.Image | None = None
        self.frame_size = (0, 0)
        self.measured_fps = 0.0
        self.link = ""
        self.error_text = ""
        self.vpn_blocks_lan = False
        self.profile_index = DEFAULT_PROFILE
        self._texts: list[tuple[Callable[[str], None], str, bool]] = []
        self.scale_labels: dict[str, tuple[QLabel, QLabel]] = {}
        # The frame rates the connected phone's lens gives, None when unknown.
        self.lens_frame_rates: dict[str, tuple[int, ...] | None] = {}
        self._ready = False
        # Updates: results from the checking and downloading threads.
        self.update_messages: queue.Queue[tuple] = queue.Queue()
        self.available_update: updates.Update | None = None
        self.update_state = "none"  # none, available, downloading, installing, failed
        self.update_detail = ""
        self.update_checked_manually = False

        self.setWindowTitle("LumaLink Studio")
        logo = Path(__file__).resolve().parents[1] / "assets" / "LumaCam.png"
        if logo.is_file():
            self.setWindowIcon(QIcon(str(logo)))
        self.setMinimumSize(980, 620)
        self.resize(1280, 760)

        self.icons = {
            "pause": svg_icon(ICON_PAUSE),
            "play": svg_icon(ICON_PLAY),
            "maximize": svg_icon(ICON_MAXIMIZE),
            "restore": svg_icon(ICON_RESTORE),
        }
        self.status_pixmaps = {
            "on": svg_pixmap(ICON_STATUS_ON, stroke=1.25),
            "wait": svg_pixmap(ICON_STATUS_WAIT, stroke=1.25),
            "off": svg_pixmap(ICON_STATUS_OFF, stroke=1.25),
            "error": svg_pixmap(ICON_STATUS_ERROR, stroke=1.25),
        }

        self.control_timer = QTimer(self)
        self.control_timer.setSingleShot(True)
        self.control_timer.setInterval(120)
        self.control_timer.timeout.connect(self._send_camera_control)
        self.pause_fade_timer = QTimer(self)
        self.pause_fade_timer.setInterval(33)  # the virtual camera runs at 30 fps
        self.pause_fade_timer.timeout.connect(self._step_pause_fade)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(16)
        self.poll_timer.timeout.connect(self._poll_stream)

        self.computer = self._computer_identity()
        self._build()
        self._load_settings()
        self._ready = True
        self.retranslate()
        self.setFocus()
        self.poll_timer.start()
        self.finder = PhoneFinder(
            self.computer,
            self.phone_lists.put,
            use_adb=lambda: self.adb_switch.isChecked(),
        )
        self.finder.start()

        self.update_timer = QTimer(self)
        self.update_timer.setInterval(UPDATE_CHECK_INTERVAL_MS)
        self.update_timer.timeout.connect(self.check_for_updates)
        if update_checks_enabled():
            self.update_timer.start()
            QTimer.singleShot(UPDATE_FIRST_CHECK_DELAY_MS, self.check_for_updates)

    # ------------------------------------------------------------------ layout

    def t(self, key: str, **values: object) -> str:
        return translate(self.language, key, **values)

    def _bind(self, setter: Callable[[str], None], key: str, upper: bool = False) -> None:
        """Remember a text so that it follows the interface language."""
        self._texts.append((setter, key, upper))

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_title_bar())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_panel())
        self.preview = Preview(self)
        body.addWidget(self.preview, 1)
        root.addLayout(body, 1)
        self._build_settings()
        self._build_profile_menu()
        if not MACOS:
            self._build_resize_grips()

    def _icon_button(self, icon: QIcon, width: int = 28, height: int = 28, icon_size: int = 14) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(icon)
        button.setIconSize(QSize(icon_size, icon_size))
        button.setFixedSize(width, height)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        return button

    def _build_title_bar(self) -> QWidget:
        bar = _TitleBar(self)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(MACOS_WINDOW_BUTTONS_WIDTH if MACOS else 0, 0, 6, 0)
        layout.setSpacing(0)
        layout.addStretch(1)

        self.pause_button = self._icon_button(self.icons["pause"])
        self.pause_button.clicked.connect(self._toggle_pause)
        layout.addWidget(self.pause_button)
        self.settings_button = self._icon_button(
            svg_icon(ICON_GEAR, view=24, size=15, stroke=1.6), icon_size=15
        )
        self.settings_button.clicked.connect(self._show_settings)
        self._bind(self.settings_button.setToolTip, "settings")
        self._bind(self.settings_button.setAccessibleName, "settings")
        layout.addWidget(self.settings_button)
        layout.addSpacing(17)
        self.profile_button = ProfileButton(self)
        self.profile_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.profile_button.clicked.connect(self._show_profile_menu)
        layout.addWidget(self.profile_button)
        layout.addWidget(bar.gap)

        self.maximize_button: QToolButton | None = None
        if MACOS:
            return bar
        for name, icon, slot in (
            ("minimize", svg_icon(ICON_MINIMIZE), self.showMinimized),
            ("maximize", self.icons["maximize"], self.toggle_maximized),
            ("close", svg_icon(ICON_CLOSE), self.close),
        ):
            button = self._icon_button(icon, width=36, height=TITLE_BAR_HEIGHT)
            button.setProperty("role", "window")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(slot)
            if name == "maximize":
                self.maximize_button = button
            else:
                self._bind(button.setToolTip, name)
                self._bind(button.setAccessibleName, name)
            layout.addWidget(button)
        return bar

    def _group(self, parent_layout: QVBoxLayout, title_key: str) -> QVBoxLayout:
        holder = QWidget(self)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title = QLabel(holder)
        title.setProperty("role", "groupTitle")
        self._bind(title.setText, title_key, upper=True)
        layout.addWidget(title)
        parent_layout.addWidget(holder)
        return layout

    def _label(self, key: str | None = None, role: str | None = None, wrap: bool = False) -> QLabel:
        label = QLabel(self)
        if role:
            label.setProperty("role", role)
        label.setWordWrap(wrap)
        if key:
            self._bind(label.setText, key)
        return label

    def _switch_row(self, layout: QVBoxLayout, key: str) -> Switch:
        row = QHBoxLayout()
        row.setSpacing(16)
        label = self._label(key, wrap=True)
        switch = Switch(self)
        self._bind(switch.setAccessibleName, key)
        row.addWidget(label, 1)
        row.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(row)
        return switch

    def _slider_field(
        self,
        layout: QVBoxLayout,
        key: str,
        slider: Slider,
        low: str | None = None,
        high: str | None = None,
        low_key: str | None = None,
        high_key: str | None = None,
    ) -> tuple[QWidget, QLabel]:
        holder = QWidget(self)
        field = QVBoxLayout(holder)
        field.setContentsMargins(0, 0, 0, 0)
        field.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(self._label(key), 1)
        value = self._label(role="value")
        head.addWidget(value)
        field.addLayout(head)
        self._bind(slider.setAccessibleName, key)
        field.addWidget(slider)
        scale = QHBoxLayout()
        low_label = self._label(low_key, role="secondary")
        high_label = self._label(high_key, role="secondary")
        self.scale_labels[key] = (low_label, high_label)
        if low is not None:
            low_label.setText(low)
        if high is not None:
            high_label.setText(high)
        scale.addWidget(low_label)
        scale.addStretch(1)
        scale.addWidget(high_label)
        field.addLayout(scale)
        layout.addWidget(holder)
        return holder, value

    def _labeled(self, layout: QVBoxLayout, key: str, widget: QWidget) -> None:
        field = QVBoxLayout()
        field.setSpacing(4)
        field.addWidget(self._label(key))
        field.addWidget(widget)
        layout.addLayout(field)

    def _build_panel(self) -> QWidget:
        panel = QWidget(self)
        panel.setObjectName("panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setFixedWidth(PANEL_WIDTH)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = _PanelContent(scroll)
        content.setObjectName("panelContent")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        groups = QVBoxLayout(content)
        groups.setContentsMargins(24, 12, 24, 24)
        groups.setSpacing(28)

        connection = self._group(groups, "connection")
        self.device_select = Select(self)
        self._bind(self.device_select.setAccessibleName, "device")
        # Only the user's choice connects; refilling the list does not.
        self.device_select.activated.connect(self._pick_phone)
        connection.addWidget(self.device_select)
        self.connect_button = QPushButton(self)
        self.connect_button.setObjectName("action")
        self.connect_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.connect_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.connect_button.clicked.connect(self._toggle_connection)
        connection.addWidget(self.connect_button)
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_icon = QLabel(self)
        self.status_icon.setFixedSize(14, 18)
        self.status_label = self._label(wrap=True)
        status_row.addWidget(self.status_icon, 0, Qt.AlignmentFlag.AlignTop)
        status_row.addWidget(self.status_label, 1)
        connection.addLayout(status_row)
        self.hint_label = self._label(role="secondary", wrap=True)
        connection.addWidget(self.hint_label)

        self.controls = QWidget(content)
        controls = QVBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(28)

        focus = self._group(controls, "focus")
        self.autofocus_switch = self._switch_row(focus, "autofocus")
        self.autofocus_switch.setChecked(True)
        self.autofocus_switch.toggled.connect(self._camera_control_changed)
        self.focus_slider = Slider(0, 100, 50, self)
        self.focus_slider.valueChanged.connect(self._camera_control_changed)
        self.focus_field, self.focus_value = self._slider_field(
            focus, "manual_focus", self.focus_slider, low_key="far", high_key="near"
        )

        image = self._group(controls, "image")
        self.exposure_slider = Slider(-20, 20, 0, self)
        self.exposure_slider.valueChanged.connect(self._camera_control_changed)
        _, self.exposure_value = self._slider_field(
            image, "exposure", self.exposure_slider, low="−2", high="+2"
        )
        self.iso_seg = Segmented(ISO_VALUES, self)
        self.iso_seg.changed.connect(self._camera_control_changed)
        self._labeled(image, "iso", self.iso_seg)
        self.white_balance_select = Select(self)
        for _ in PHONE_WHITE_BALANCE:
            self.white_balance_select.addItem("")
        self.white_balance_select.currentIndexChanged.connect(self._camera_control_changed)
        self._bind(self.white_balance_select.setAccessibleName, "white_balance")
        self._labeled(image, "white_balance", self.white_balance_select)

        lens = self._group(controls, "lens")
        self.lens_seg = Segmented(["rear", "front"], self)
        self.lens_seg.changed.connect(self._lens_changed)
        lens.addWidget(self.lens_seg)
        self.zoom_slider = Slider(10, 100, 10, self)
        self.zoom_slider.valueChanged.connect(self._camera_control_changed)
        _, self.zoom_value = self._slider_field(
            lens, "zoom", self.zoom_slider, low="1×", high="10×"
        )

        self.reset_button = QPushButton(self)
        self.reset_button.setObjectName("textButton")
        self.reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.reset_button.clicked.connect(self._reset_camera_controls)
        self._bind(self.reset_button.setText, "reset")
        controls.addWidget(self.reset_button, 0, Qt.AlignmentFlag.AlignLeft)
        groups.addWidget(self.controls)
        groups.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        foot = QWidget(panel)
        foot.setObjectName("panelFoot")
        foot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        foot_layout = QVBoxLayout(foot)
        foot_layout.setContentsMargins(24, 16, 24, 20)
        foot_layout.setSpacing(2)

        self.update_banner = QWidget(foot)
        banner = QVBoxLayout(self.update_banner)
        banner.setContentsMargins(0, 0, 0, 14)
        banner.setSpacing(8)
        self.update_label = self._label(role="strong", wrap=True)
        banner.addWidget(self.update_label)
        self.update_button = QPushButton(self)
        self.update_button.setObjectName("action")
        self.update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.update_button.clicked.connect(self._update_clicked)
        banner.addWidget(self.update_button)
        self.update_banner.hide()
        foot_layout.addWidget(self.update_banner)

        foot_layout.addWidget(self._label("vcam", role="strong"))
        self.virtual_camera_note = self._label(role="secondary", wrap=True)
        foot_layout.addWidget(self.virtual_camera_note)
        outer.addWidget(foot)
        return panel

    def _build_settings(self) -> None:
        self.settings_popup = _SettingsPopup(self)
        layout = self.settings_popup.content
        layout.addWidget(self._label("settings", role="heading"))

        language = self._group(layout, "language")
        self.language_seg = Segmented(list(LANGUAGES), self)
        self.language_seg.set_labels(LANGUAGE_NAMES)
        self.language_seg.changed.connect(self._select_language)
        language.addWidget(self.language_seg)

        preview = self._group(layout, "preview")
        self.grid_switch = self._switch_row(preview, "opt_grid")
        self.info_switch = self._switch_row(preview, "opt_info")
        self.mirror_switch = self._switch_row(preview, "opt_mirror")

        connection = self._group(layout, "connection")
        self.adb_switch = self._switch_row(connection, "opt_adb")

        about = self._label(role="secondary")
        about.setText(f"LumaLink Studio {__version__}")
        layout.addWidget(about)
        self.check_updates_button = QPushButton(self)
        self.check_updates_button.setObjectName("textButton")
        self.check_updates_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_updates_button.clicked.connect(lambda: self.check_for_updates(manual=True))
        self._bind(self.check_updates_button.setText, "update_check")
        layout.addWidget(self.check_updates_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.update_check_label = self._label(role="secondary", wrap=True)
        self.update_check_label.hide()
        layout.addWidget(self.update_check_label)

    def _connect_settings(self) -> None:
        for switch, name in (
            (self.grid_switch, "grid"),
            (self.info_switch, "info"),
            (self.mirror_switch, "mirror"),
            (self.adb_switch, "adb_fallback"),
        ):
            switch.toggled.connect(lambda checked, key=name: self._save_option(key, checked))

    def _build_profile_menu(self) -> None:
        self.profile_menu = QMenu(self)
        self.profile_menu.setWindowFlags(
            self.profile_menu.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.profile_menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.profile_actions: list[QAction] = []
        group = QActionGroup(self.profile_menu)
        group.setExclusive(True)
        self._check_icon = svg_icon('<path d="M2.5 7.3 5.6 10.4 11.5 3.8"/>', stroke=1.5)
        blank = QPixmap(56, 56)
        blank.fill(Qt.GlobalColor.transparent)
        self._blank_icon = QIcon(blank)
        for index in range(len(PROFILES)):
            action = QAction(self.profile_menu)
            action.triggered.connect(lambda _checked=False, chosen=index: self._select_profile(chosen))
            group.addAction(action)
            self.profile_menu.addAction(action)
            self.profile_actions.append(action)

    def _build_resize_grips(self) -> None:
        edge, shape = Qt.Edge, Qt.CursorShape
        self.grips = [
            (_ResizeGrip(self, edge.LeftEdge, shape.SizeHorCursor), "left"),
            (_ResizeGrip(self, edge.RightEdge, shape.SizeHorCursor), "right"),
            (_ResizeGrip(self, edge.TopEdge, shape.SizeVerCursor), "top"),
            (_ResizeGrip(self, edge.BottomEdge, shape.SizeVerCursor), "bottom"),
            (_ResizeGrip(self, edge.TopEdge | edge.LeftEdge, shape.SizeFDiagCursor), "top-left"),
            (_ResizeGrip(self, edge.BottomEdge | edge.RightEdge, shape.SizeFDiagCursor), "bottom-right"),
            (_ResizeGrip(self, edge.TopEdge | edge.RightEdge, shape.SizeBDiagCursor), "top-right"),
            (_ResizeGrip(self, edge.BottomEdge | edge.LeftEdge, shape.SizeBDiagCursor), "bottom-left"),
        ]

    def _place_resize_grips(self) -> None:
        if MACOS:
            return
        margin, corner = RESIZE_MARGIN, 2 * RESIZE_MARGIN
        width, height = self.width(), self.height()
        places = {
            "left": (0, corner, margin, height - 2 * corner),
            "right": (width - margin, corner, margin, height - 2 * corner),
            "top": (corner, 0, width - 2 * corner, margin),
            "bottom": (corner, height - margin, width - 2 * corner, margin),
            "top-left": (0, 0, corner, corner),
            "bottom-right": (width - corner, height - corner, corner, corner),
            "top-right": (width - corner, 0, corner, corner),
            "bottom-left": (0, height - corner, corner, corner),
        }
        resizable = not self.isMaximized()
        for grip, name in self.grips:
            grip.setGeometry(*places[name])
            grip.setVisible(resizable)
            grip.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._ready:
            self._place_resize_grips()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if self._ready and event.type() == event.Type.WindowStateChange:
            self._place_resize_grips()
            self._refresh_window_buttons()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._place_resize_grips()

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ------------------------------------------------------------ settings/i18n

    def _load_settings(self) -> None:
        self.language_seg.set_value(self.language)
        self.grid_switch.setChecked(self.settings.value("grid", True, type=bool))
        self.info_switch.setChecked(self.settings.value("info", False, type=bool))
        self.mirror_switch.setChecked(self.settings.value("mirror", False, type=bool))
        self.adb_switch.setChecked(self.settings.value("adb_fallback", True, type=bool))
        self._connect_settings()

    def _save_option(self, key: str, value: object) -> None:
        self.settings.setValue(key, value)
        self._refresh()

    def _select_language(self, language: str) -> None:
        self.language = language
        self.settings.setValue("language", language)
        self.retranslate()

    def retranslate(self) -> None:
        for setter, key, upper in self._texts:
            text = self.t(key)
            setter(text.upper() if upper else text)
        self._refresh_update()
        self.iso_seg.set_labels(
            {value: self.t("auto") if value == "auto" else value for value in ISO_VALUES}
        )
        self.lens_seg.set_labels({"rear": self.t("rear"), "front": self.t("front")})
        for index, (key, _token) in enumerate(PHONE_WHITE_BALANCE):
            self.white_balance_select.setItemText(index, self.t(key))
        for index, (resolution, fps, size) in enumerate(PROFILES):
            self.profile_actions[index].setText(
                f"{resolution}, {fps} {self.t('fps_unit')}\t{size}"
            )
        self._refresh()

    # -------------------------------------------------------------- UI state

    def _refresh_window_buttons(self) -> None:
        if self.maximize_button is None:
            return
        maximized = self.isMaximized()
        self.maximize_button.setIcon(self.icons["restore" if maximized else "maximize"])
        name = self.t("restore" if maximized else "maximize")
        self.maximize_button.setToolTip(name)
        self.maximize_button.setAccessibleName(name)

    def _refresh(self) -> None:
        """Bring every state-dependent text, icon and enabled flag up to date."""
        if not self._ready:
            return
        connected = self.camera_connected
        waiting = self.streaming and not connected
        resolution, fps, _size = PROFILES[self.profile_index]
        self.profile_button.set_texts(self.t("profile"), resolution, f"{fps}fps")
        for index, action in enumerate(self.profile_actions):
            action.setIcon(self._check_icon if index == self.profile_index else self._blank_icon)
            action.setEnabled(self._profile_usable(index))
        self._refresh_window_buttons()

        if self.error_text:
            state, text = "error", self.error_text
        elif connected:
            state, text = "on", self.t("status_on", link=self.link)
        elif waiting:
            state, text = "wait", self.wait_text or self.t("status_connecting", phone=self._phone_label(self.phone))
        elif self.auto_connect:
            state, text = "wait", self.t("status_searching")
        else:
            state, text = "off", self.t("status_off")
        self.status_icon.setPixmap(self.status_pixmaps[state])
        self.status_label.setText(text)

        active = self.receiver is not None
        if active:
            button_text = self.t("disconnect")
        elif self.auto_connect:
            button_text = self.t("stop_waiting")
        else:
            button_text = self.t("connect")
        self.connect_button.setText(button_text)
        quiet = active or self.auto_connect
        if self.connect_button.property("quiet") != quiet:
            self.connect_button.setProperty("quiet", quiet)
            self.connect_button.style().unpolish(self.connect_button)
            self.connect_button.style().polish(self.connect_button)

        if connected and self.phone is not None and self.phone.link == "wifi":
            hint = self.t("hint_wifi_encrypted" if self.phone.tls else "hint_wifi")
        elif connected:
            hint = ""
        elif self.vpn_blocks_lan:
            hint = self.t("hint_usb_vpn")
        else:
            # macOS has no driver for the RNDIS tethering most Android
            # phones use, so there the ADB route is the one that works.
            hint = self.t("hint_usb_macos" if MACOS else "hint_usb")
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))
        self._fill_devices()

        self.controls.setEnabled(connected)
        self.focus_field.setEnabled(connected and not self.autofocus_switch.isChecked())
        self.focus_value.setText(f"{self.focus_slider.value()}%")
        exposure = self.exposure_slider.value() / 10
        sign = "+" if exposure > 0 else "−" if exposure < 0 else ""
        self.exposure_value.setText(f"{sign}{format_number(self.language, abs(exposure), 1)} EV")
        self.zoom_value.setText(
            f"{format_number(self.language, self.zoom_slider.value() / 10, 1)}×"
        )

        paused = connected and self.paused
        self.pause_button.setEnabled(connected)
        self.pause_button.setIcon(self.icons["play" if paused else "pause"])
        pause_name = self.t("resume" if paused else "pause")
        self.pause_button.setToolTip(pause_name)
        self.pause_button.setAccessibleName(pause_name)

        self.preview.empty_title = self.t("empty_title")
        self.preview.empty_text = self.t("empty_text")
        self.preview.show_grid = self.grid_switch.isChecked() and not paused
        self.preview.mirrored = self.mirror_switch.isChecked()
        if paused:
            self.preview.note = self.t("paused_note")
        elif connected and self.info_switch.isChecked() and self.frame_size[0]:
            width, height = self.frame_size
            self.preview.note = (
                f"{width}×{height} · {self.measured_fps:.0f} {self.t('fps_unit')} · {self.link}"
            )
        else:
            self.preview.note = ""
        self.preview.update()

        if not connected:
            note = self.t("vcam_wait")
        elif paused:
            note = self.t("vcam_paused")
        else:
            note = self.t("vcam_on", device=self.virtual_camera_device or "LumaLink Camera")
        self.virtual_camera_note.setText(note)

    def _show_settings(self) -> None:
        popup = self.settings_popup
        popup.adjustSize()
        anchor = self.settings_button.mapToGlobal(
            QPoint(self.settings_button.width(), self.settings_button.height() + 6)
        )
        popup.move(anchor - QPoint(popup.width(), 0))
        popup.show()

    def _show_profile_menu(self) -> None:
        menu_width = self.profile_menu.sizeHint().width()
        anchor = self.profile_button.mapToGlobal(
            QPoint(self.profile_button.width() - menu_width, self.profile_button.height() + 6)
        )
        self.profile_menu.popup(anchor)

    def _select_profile(self, index: int) -> None:
        self.profile_index = index
        self._refresh()
        self._schedule_camera_control_update()

    # ------------------------------------------------------- camera controls

    def _camera_control_changed(self, *_args: object) -> None:
        self._refresh()
        self._schedule_camera_control_update()

    def _schedule_camera_control_update(self) -> None:
        if self.camera_connected:
            self.control_timer.start()

    def _reset_camera_controls(self) -> None:
        self.autofocus_switch.setChecked(True)
        self.focus_slider.setValue(50)
        self.exposure_slider.setValue(0)
        self.iso_seg.set_value("auto")
        self.white_balance_select.setCurrentIndex(0)
        self.lens_seg.set_value("rear")
        self.zoom_slider.setValue(10)
        self._camera_control_changed()

    def _lens_changed(self, *_args: object) -> None:
        self._apply_zoom_range()
        self._camera_control_changed()

    def _lens_source(self) -> Phone | None:
        """What is known about the phone's lenses: from its handshake, else from discovery."""
        if self.camera_info is not None:
            return Phone(id="", name="", link="usb", lenses=self.camera_info.get("lenses") or {})
        return self.phone

    def _profile_usable(self, index: int) -> bool:
        resolution, fps, _size = PROFILES[index]
        rates = self.lens_frame_rates.get(resolution)
        return rates is None or fps in rates

    def _apply_zoom_range(self) -> None:
        """Let the zoom go as wide and as far as the phone's lens does,
        and offer only the frame rates it really gives."""
        source = self._lens_source()
        lens = PHONE_LENS[self.lens_seg.value()]
        self.lens_frame_rates = {
            resolution: frame_rates(source, lens, resolution)
            for resolution in dict.fromkeys(r for r, _, _ in PROFILES)
        }
        resolution, fps, _size = PROFILES[self.profile_index]
        if not self._profile_usable(self.profile_index):
            # The same resolution at the best rate the lens has there.
            usable = [i for i, (r, _, _) in enumerate(PROFILES) if r == resolution and self._profile_usable(i)]
            if usable:
                self.profile_index = max(usable, key=lambda i: PROFILES[i][1])
                self._schedule_camera_control_update()
        low, high = zoom_range(source, lens)
        self.zoom_slider.setRange(round(low * 10), round(high * 10))
        low_label, high_label = self.scale_labels["zoom"]
        low_label.setText(f"{format_number(self.language, low, 1 if low % 1 else 0)}×")
        high_label.setText(f"{format_number(self.language, high, 1 if high % 1 else 0)}×")

    def camera_command(self) -> dict[str, object]:
        resolution, fps, _size = PROFILES[self.profile_index]
        iso = self.iso_seg.value()
        return {
            "autofocus": self.autofocus_switch.isChecked(),
            "focus": self.focus_slider.value() / 100,
            "exposure": self.exposure_slider.value() / 10,
            "iso": PHONE_AUTO if iso == "auto" else iso,
            "whiteBalance": PHONE_WHITE_BALANCE[self.white_balance_select.currentIndex()][1],
            "lens": PHONE_LENS[self.lens_seg.value()],
            "zoom": self.zoom_slider.value() / 10,
            "fps": fps,
            "resolution": resolution,
        }

    def _send_camera_control(self) -> None:
        if not self.camera_connected:
            return
        try:
            if self.receiver is not None:
                self.receiver.send_control(self.camera_command())
        except (OSError, ConnectionError):
            # The phone has just gone away; the stream reports that itself.
            pass

    # ------------------------------------------------------------ connection

    def _toggle_connection(self) -> None:
        if self.receiver is not None or self.auto_connect:
            # Stop connecting by itself until the user asks again.
            self.auto_connect = False
            self._disconnect()
        else:
            self.auto_connect = True
            self.retry_at = 0.0
            self.held_back.clear()
            self.error_text = ""
            self._maybe_connect()
        self._refresh()

    @staticmethod
    def _computer_name() -> str:
        name = socket.gethostname().removesuffix(".local").strip()
        return (name or "Computer")[:64]

    def _computer_identity(self) -> dict[str, str]:
        computer_id = str(self.settings.value("computer_id", ""))
        if not computer_id:
            computer_id = str(uuid.uuid4())
            self.settings.setValue("computer_id", computer_id)
        return {"id": computer_id, "name": self._computer_name()}

    def _phone_label(self, phone: Phone | None) -> str:
        if phone is None:
            return ""
        return f"{phone.name or self.t('phone_unnamed')} · {LINK_NAMES[phone.link]}"

    def _phone_id(self, phone: Phone) -> str:
        """The phone's own id; an ADB entry learns it on its first connection."""
        if phone.link == "adb":
            return str(self.settings.value(f"adb_ids/{phone.serial}", phone.id))
        return phone.id

    def _fill_devices(self) -> None:
        listed = self.phones
        if self.phone is not None and all(p.key != self.phone.key for p in listed):
            listed = [self.phone, *listed]  # connected, though no longer answering probes
        current = self.phone.key if self.phone is not None else None
        shown = ([(p.key, self._phone_label(p)) for p in listed], current, self.language)
        if shown == self._devices_shown:
            return
        self._devices_shown = shown
        select = self.device_select
        select.blockSignals(True)
        select.clear()
        if not listed:
            select.addItem(self.t("devices_none"), None)
        for phone in listed:
            select.addItem(self._phone_label(phone), phone.key)
        if current is not None:
            select.setCurrentIndex([p.key for p in listed].index(current))
        select.setEnabled(bool(listed))
        select.blockSignals(False)

    def _pick_phone(self, index: int) -> None:
        key = self.device_select.itemData(index)
        phone = next((p for p in self.phones if p.key == key), None)
        if phone is None:
            return
        self.settings.setValue("phone_id", self._phone_id(phone))
        self.held_back.pop(phone.key, None)
        self.auto_connect = True
        if self.receiver is not None and self.phone is not None and self.phone.key == phone.key:
            return
        self._disconnect()
        self._connect_to(phone)
        self._refresh()

    def _auto_target(self) -> Phone | None:
        """The phone to connect to by itself: the one chosen last, else one on USB."""
        now = time.monotonic()
        ready = [p for p in self.phones if self.held_back.get(p.key, 0.0) <= now]
        preferred = str(self.settings.value("phone_id", ""))
        mine = [p for p in ready if preferred and self._phone_id(p) == preferred]
        if mine:
            return min(mine, key=lambda p: LINKS.index(p.link))
        cabled = [p for p in ready if p.link in ("usb", "adb")]
        return min(cabled, key=lambda p: LINKS.index(p.link)) if cabled else None

    def _maybe_connect(self) -> None:
        if not self.auto_connect or self.receiver is not None:
            return
        now = time.monotonic()
        if now < self.retry_at:
            return
        if now - self.vpn_checked_at >= VPN_CHECK_INTERVAL:
            self.vpn_checked_at = now
            blocked = local_network_blocked()
            if blocked != self.vpn_blocks_lan:
                self.vpn_blocks_lan = blocked
                self._refresh()
        target = self._auto_target()
        if target is not None:
            self._connect_to(target)
            self._refresh()

    def _connect_to(self, phone: Phone) -> None:
        self.error_text = ""
        self.wait_text = ""
        self.decoder = H264Decoder()
        self.virtual_camera_failed = False
        self.phone = phone
        self.camera_info = None
        self.link = LINK_NAMES[phone.link]
        self._apply_zoom_range()
        self.connection_run += 1
        run = self.connection_run
        token = str(self.settings.value(f"tokens/{self._phone_id(phone)}", ""))
        known = str(self.settings.value(f"pins/{self._phone_id(phone)}", "")) or None
        self.receiver = H264FrameReceiver(
            None,
            STREAM_PORT,
            self._receive_frame,
            lambda message, is_error: self._receive_status(message, is_error, run),
            usb=phone.link != "wifi",
            phone=phone,
            hello={**self.computer, "token": token},
            on_accepted=lambda status: self.accepted.put((run, status)),
            on_refused=lambda error: self.refusals.put((run, error)),
            # Over Wi-Fi even when the phone offers no encryption: one known
            # to encrypt that stops is turned down (see H264FrameReceiver).
            known_fingerprint=known if phone.link == "wifi" else None,
        )
        self.streaming = True
        self.receiver.start()

    def _accepted(self, status: dict) -> None:
        """A version 2 phone let this computer in: remember it, and its lenses."""
        camera = status.get("camera") if isinstance(status.get("camera"), dict) else {}
        phone_id = str(camera.get("id") or (self.phone.id if self.phone else ""))
        if not phone_id:
            return
        self.camera_info = camera
        if self.phone is not None and self.phone.link == "adb":
            self.settings.setValue(f"adb_ids/{self.phone.serial}", phone_id)
        if isinstance(status.get("token"), str) and status["token"]:
            self.settings.setValue(f"tokens/{phone_id}", status["token"])
        if isinstance(status.get("fingerprint"), str):
            # The phone's key, to tell it from a device posing as it later.
            self.settings.setValue(f"pins/{phone_id}", status["fingerprint"])
        self.settings.setValue("phone_id", phone_id)
        self.wait_text = ""
        self._apply_zoom_range()

    def _disconnect(self) -> None:
        if self.receiver is not None:
            self.receiver.stop()
            self.receiver = None
        self.connection_run += 1  # what the old connection still reports is stale
        self.streaming = False
        self.phone = None
        self.camera_info = None
        self.wait_text = ""
        self._forget_camera()

    def _forget_camera(self) -> None:
        """Return to the state without a phone picture."""
        self.camera_connected = False
        self.paused = False
        self.paused_image = None
        self.latest_image = None
        self.pause_fade = None
        self.resume_fade = None
        self._paused_background = None
        self.pause_fade_timer.stop()
        self._stop_virtual_camera()
        self.frame_timestamps.clear()
        self.frame_size = (0, 0)
        self.measured_fps = 0.0
        try:
            self.frames.get_nowait()
        except queue.Empty:
            pass
        self.preview.set_pause(None, animate=False)
        self.preview.set_image(None)

    # ---------------------------------------------------------------- stream

    def _receive_frame(self, frame: bytes) -> None:
        """Called on the network thread for every H.264 access unit."""
        if not self.streaming:
            return
        for decoded in self.decoder.decode(frame):
            image = correct_stream_orientation(decoded.to_image())
            virtual_camera = self.virtual_camera
            if self.paused:
                # Keep decoding so the picture is intact on resume, but show
                # everyone the still frame.
                still = self.paused_image
                if virtual_camera is not None and still is not None:
                    virtual_camera.submit(still)
                continue
            self.latest_image = image
            if virtual_camera is not None:
                faded = self._fading_out(image)
                # Without an overlay the camera takes the decoded frame,
                # which it scales faster than the full-size picture.
                virtual_camera.submit(decoded if faded is image else faded)
            try:
                self.frames.put_nowait(image)
            except queue.Full:
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass
                self.frames.put_nowait(image)

    def _receive_status(self, message: str, is_error: bool, run: int | None = None) -> None:
        self.status_messages.put((self.connection_run if run is None else run, message, is_error))

    def _toggle_pause(self) -> None:
        if not self.camera_connected:
            return
        animate = animations_enabled()
        self.pause_fade_timer.stop()
        if self.paused:
            background = self.pause_fade[1] if self.pause_fade else self._paused_background
            self.paused = False
            self.paused_image = None
            self.pause_fade = None
            self.resume_fade = (background, time.monotonic()) if animate and background else None
            self.preview.set_pause(None)
        else:
            source = self.latest_image
            if source is None:
                return
            background = paused_background(source)
            final = paused_frame(source)
            self._paused_background = background
            self.resume_fade = None
            # The virtual camera steps through the same transition as the
            # preview; the network thread forwards whichever step is current.
            self.paused_image = transition_frame(source, background, 0.0) if animate else final
            self.pause_fade = (source, background, final, time.monotonic()) if animate else None
            self.paused = True
            self.preview.set_image(source)
            self.preview.set_pause(background)
            if self.virtual_camera is not None:
                self.virtual_camera.submit(self.paused_image)
            if animate:
                self.pause_fade_timer.start()
        self._refresh()

    def _step_pause_fade(self) -> None:
        """Advance the virtual camera's pause animation by one frame."""
        fade = self.pause_fade
        if fade is None or not self.paused:
            self.pause_fade_timer.stop()
            return
        source, background, final, started = fade
        progress = (time.monotonic() - started) / PAUSE_IN_SECONDS
        if progress >= 1.0:
            self.paused_image = final
            self.pause_fade = None
            self.pause_fade_timer.stop()
        else:
            self.paused_image = transition_frame(source, background, ease_out(progress))
        if self.virtual_camera is not None:
            self.virtual_camera.submit(self.paused_image)

    def _fading_out(self, image: Image.Image) -> Image.Image:
        """The live picture, with the pause overlay still fading right after a resume."""
        fade = self.resume_fade
        if fade is None:
            return image
        background, started = fade
        progress = (time.monotonic() - started) / PAUSE_OUT_SECONDS
        if progress >= 1.0:
            self.resume_fade = None
            return image
        return transition_frame(image, background, 1.0 - ease_out(progress))

    def _start_virtual_camera(self) -> None:
        """Feed the virtual camera for as long as the phone is connected.

        Without a feed the camera shows its idle image on its own, so it
        needs no manual switch.
        """
        if self.virtual_camera is not None or self.virtual_camera_failed:
            return
        self.virtual_camera_run += 1
        run = self.virtual_camera_run
        self.virtual_camera = VirtualCameraOutput(
            lambda message, is_error: self.virtual_camera_status_messages.put(
                (run, message, is_error)
            )
        )
        self.virtual_camera.start()

    def _stop_virtual_camera(self) -> None:
        output = self.virtual_camera
        self.virtual_camera = None
        self.virtual_camera_device = ""
        if output is not None:
            output.stop()

    def _poll_stream(self) -> None:
        self._poll_updates()
        changed = False
        while True:
            try:
                run, message, is_error = self.virtual_camera_status_messages.get_nowait()
            except queue.Empty:
                break
            if run != self.virtual_camera_run:
                continue
            if is_error:
                # Do not retry on every frame; the next connection tries again.
                self.virtual_camera_failed = True
                self._stop_virtual_camera()
                self.error_text = message
            elif message.startswith("Виртуальная камера запущена: "):
                self.virtual_camera_device = message.partition(": ")[2]
            changed = True

        while True:
            try:
                phones = self.phone_lists.get_nowait()
            except queue.Empty:
                break
            self.phones = phones
            changed = True
        while True:
            try:
                run, status = self.accepted.get_nowait()
            except queue.Empty:
                break
            if run == self.connection_run:
                self._accepted(status)
                changed = True
        while True:
            try:
                run, refusal = self.refusals.get_nowait()
            except queue.Empty:
                break
            if run == self.connection_run and self.phone is not None:
                pause = REFUSED_PAUSE.get(refusal.status, RETRY_DELAY)
                self.held_back[self.phone.key] = time.monotonic() + pause
        while True:
            try:
                run, message, is_error = self.status_messages.get_nowait()
            except queue.Empty:
                break
            if run != self.connection_run:
                continue
            changed = True
            if not is_error:
                if message.startswith("Разрешите"):
                    code = message.partition("Код: ")[2]
                    self.wait_text = self.t("status_allow_code", code=code) if code else self.t("status_allow")
                continue
            self.error_text = message
            self._forget_camera()
            if self.receiver is not None:
                self.receiver.stop()
                self.receiver = None
                self.streaming = False
            self.phone = None
            self.camera_info = None
            self.wait_text = ""
            self.retry_at = time.monotonic() + RETRY_DELAY
        if self.receiver is None and self.auto_connect:
            receiver_before = self.receiver
            self._maybe_connect()
            changed = changed or self.receiver is not receiver_before

        try:
            frame = self.frames.get_nowait()
        except queue.Empty:
            frame = None
        if frame is not None and self.streaming and not self.paused:
            self.frame_size = frame.size
            self.frame_timestamps.append(time.monotonic())
            if len(self.frame_timestamps) > 1:
                duration = self.frame_timestamps[-1] - self.frame_timestamps[0]
                if duration > 0:
                    self.measured_fps = (len(self.frame_timestamps) - 1) / duration
            self.preview.set_image(frame)
            if not self.camera_connected:
                self.camera_connected = True
                self.error_text = ""
                self._send_camera_control()
                self._start_virtual_camera()
                changed = True
            elif self.info_switch.isChecked():
                changed = True
        if changed:
            self._refresh()

    # ---------------------------------------------------------------- updates

    def check_for_updates(self, manual: bool = False) -> None:
        """Ask GitHub for a newer release in the background."""
        if self.update_state in ("downloading", "installing"):
            return
        self.update_checked_manually = manual
        if manual:
            self.update_check_label.setText(self.t("update_checking"))
            self.update_check_label.show()

        def work() -> None:
            try:
                self.update_messages.put(("checked", updates.check()))
            except Exception as error:  # offline, rate-limited, unexpected JSON
                self.update_messages.put(("check_failed", str(error)))

        threading.Thread(target=work, name="LumaLink update check", daemon=True).start()

    def _update_clicked(self) -> None:
        update = self.available_update
        if update is None:
            return
        if not updates.can_update_in_place():
            # Running from source: the installer would not replace this copy.
            QDesktopServices.openUrl(QUrl(update.page))
            return
        self.update_state = "downloading"
        self.update_detail = self.t("update_downloading", percent=0)
        self._refresh_update()
        cancelled = threading.Event()
        self.update_cancel = cancelled

        def work() -> None:
            try:
                path = updates.download(
                    update,
                    lambda done, total: self.update_messages.put(("progress", done, total)),
                    cancelled,
                )
                self.update_messages.put(("downloaded", path))
            except Exception as error:
                self.update_messages.put(("failed", str(error)))

        threading.Thread(target=work, name="LumaLink update download", daemon=True).start()

    def _poll_updates(self) -> None:
        while True:
            try:
                message = self.update_messages.get_nowait()
            except queue.Empty:
                return
            kind = message[0]
            if kind == "checked":
                update = message[1]
                if update is not None:
                    self.available_update = update
                    if self.update_state in ("none", "failed"):
                        self.update_state = "available"
                if self.update_checked_manually:
                    self.update_check_label.setText(
                        self.t("update_available", version=update.version)
                        if update is not None
                        else self.t("update_latest")
                    )
            elif kind == "check_failed":
                if self.update_checked_manually:
                    self.update_check_label.setText(self.t("update_check_failed"))
            elif kind == "progress":
                _, done, total = message
                percent = round(done * 100 / total) if total else 0
                self.update_detail = self.t("update_downloading", percent=percent)
            elif kind == "downloaded":
                self.update_state = "installing"
                self.update_detail = self.t("update_installing")
                self._refresh_update()
                try:
                    if updates.install(message[1]):
                        self._disconnect()
                        QApplication.quit()
                        return
                    self.update_state = "available"  # the user declined; try again later
                except Exception as error:
                    self.update_state = "failed"
                    self.update_detail = self.t("update_failed", error=error)
            elif kind == "failed":
                self.update_state = "failed"
                self.update_detail = self.t("update_failed", error=message[1])
            self._refresh_update()

    def _refresh_update(self) -> None:
        """Show the update banner for the current state."""
        update = self.available_update
        if update is None or self.update_state == "none":
            self.update_banner.hide()
            return
        busy = self.update_state in ("downloading", "installing")
        if busy:
            self.update_label.setText(self.update_detail)
        elif self.update_state == "failed":
            self.update_label.setText(self.update_detail)
        else:
            self.update_label.setText(self.t("update_available", version=update.version))
        if not updates.can_update_in_place():
            button = "update_open"
        elif self.update_state == "failed":
            button = "update_retry"
        else:
            button = "update_install"
        self.update_button.setText(self.t(button))
        self.update_button.setEnabled(not busy)
        self.update_banner.show()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.poll_timer.stop()
        if self.finder is not None:
            self.finder.stop()
            self.finder = None
        self._disconnect()
        super().closeEvent(event)


class _PanelContent(QWidget):
    """The settings panel's content, which scrolls instead of shrinking.

    Its layout reports a minimum height that counts the wrapped hints at
    another width, so in a short window the scroll area squeezed the
    segmented controls and the selects below their height; this minimum is
    the layout's height at the panel's actual width.
    """

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        hint = super().minimumSizeHint()
        layout = self.layout()
        if layout is not None and layout.hasHeightForWidth() and self.width() > 0:
            return QSize(hint.width(), max(hint.height(), layout.totalHeightForWidth(self.width())))
        if layout is not None:
            return QSize(hint.width(), max(hint.height(), layout.sizeHint().height()))
        return hint

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()  # the minimum above depends on the width


def update_checks_enabled() -> bool:
    """The packaged app checks for updates; from source only when asked to."""
    return updates.can_update_in_place() or os.environ.get("LUMALINK_CHECK_UPDATES") == "1"


def run() -> None:
    updates.hold_app_mutex()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE_SHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
