import ipaddress
import queue
import socket
import shutil
import subprocess
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageTk

from camera_studio.models import ConnectionMode, DeviceSession, SessionStatus
from camera_studio.stream import (
    CameraStreamServer,
    H264Decoder,
    H264FrameReceiver,
    correct_stream_orientation,
)
from camera_studio.virtual_camera import VirtualCameraOutput


STREAM_PORT = 8765


COLORS = {
    "background": "#101216",
    "panel": "#171a20",
    "panel_alt": "#1d2129",
    "border": "#2b303a",
    "text": "#f1f3f6",
    "muted": "#969eab",
    "accent": "#8d7aff",
    "accent_hover": "#a295ff",
    "green": "#58d6a0",
    "warning": "#f0b766",
}


class CameraStudioApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.session = DeviceSession()
        self.mode = tk.StringVar(value=self.session.mode.value)
        self.frames: queue.Queue[Image.Image] = queue.Queue(maxsize=1)
        self.status_messages: queue.Queue[tuple[str, bool]] = queue.Queue()
        self.stream_server: CameraStreamServer | None = None
        self.receiver: H264FrameReceiver | None = None
        self.virtual_camera: VirtualCameraOutput | None = None
        self.decoder = H264Decoder()
        self.usb_forward: tuple[Path, str] | None = None
        self.preview_image: ImageTk.PhotoImage | None = None
        logo_path = Path(__file__).resolve().parents[1] / "assets" / "LumaCam.png"
        self.logo_image = ImageTk.PhotoImage(
            Image.open(logo_path).resize((32, 32), Image.Resampling.LANCZOS)
        )
        self.window_icon_image = ImageTk.PhotoImage(
            Image.open(logo_path).resize((64, 64), Image.Resampling.LANCZOS)
        )
        self.frame_timestamps: deque[float] = deque(maxlen=30)
        self.virtual_camera_status_messages: queue.Queue[tuple[str, bool]] = queue.Queue()
        self.streaming = False
        self.camera_connected = False
        self.control_after_id: str | None = None
        self.control_widgets: list[tuple[tk.Widget, str]] = []
        self.focus_slider: ttk.Scale | None = None
        self.autofocus_var = tk.BooleanVar(value=True)
        self.focus_var = tk.DoubleVar(value=0.5)
        self.exposure_var = tk.DoubleVar(value=0.0)
        self.iso_var = tk.StringVar(value="Авто")
        self.white_balance_var = tk.StringVar(value="Авто")
        self.lens_var = tk.StringVar(value="Задняя")
        self.zoom_var = tk.DoubleVar(value=1.0)
        self.fps_var = tk.StringVar(value="30")
        self.resolution_var = tk.StringVar(value="720p")

        root.title("LumaLink Studio")
        root.iconphoto(True, self.window_icon_image)
        root.geometry("1240x800")
        root.minsize(1020, 680)
        root.configure(background=COLORS["background"])

        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self._configure_styles()
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(16, self._poll_stream)

    def _configure_styles(self) -> None:
        self.style.configure(
            "TCombobox",
            fieldbackground=COLORS["panel_alt"],
            background=COLORS["panel_alt"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["muted"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=8,
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("disabled", COLORS["panel"])],
            foreground=[("disabled", COLORS["muted"])],
        )
        self.style.configure(
            "TScale",
            background=COLORS["panel"],
            troughcolor=COLORS["panel_alt"],
            sliderthickness=12,
        )
        self.style.configure(
            "TCheckbutton",
            background=COLORS["panel"],
            foreground=COLORS["muted"],
        )
        self.style.map(
            "TCheckbutton",
            background=[("active", COLORS["panel"])],
            foreground=[("disabled", COLORS["muted"])],
        )

    def _build_layout(self) -> None:
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)
        self._build_topbar()

        body = tk.Frame(self.root, background=COLORS["background"])
        body.grid(row=1, column=0, sticky="nsew", padx=28, pady=(18, 24))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_rowconfigure(1, weight=1)

        self._build_heading(body)
        self._build_preview(body)
        self._build_controls(body)

    def _build_topbar(self) -> None:
        bar = tk.Frame(
            self.root,
            background=COLORS["panel"],
            height=64,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(1, weight=1)

        brand = tk.Frame(bar, background=COLORS["panel"])
        brand.grid(row=0, column=0, sticky="w", padx=28)
        mark = tk.Label(
            brand,
            image=self.logo_image,
            bg=COLORS["panel"],
        )
        mark.pack(side="left", padx=(0, 10))
        tk.Label(
            brand,
            text="LumaLink",
            font=("Segoe UI", 13, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel"],
        ).pack(side="left")
        tk.Label(
            brand,
            text="STUDIO",
            font=("Segoe UI", 8, "bold"),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(side="left", padx=(7, 0), pady=(4, 0))

        tk.Label(
            bar,
            text="ДЕСКТОП · WINDOWS",
            font=("Segoe UI", 9, "bold"),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).grid(row=0, column=2, padx=28)

    def _build_heading(self, parent: tk.Frame) -> None:
        heading = tk.Frame(parent, background=COLORS["background"])
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        heading.grid_columnconfigure(0, weight=1)

        tk.Label(
            heading,
            text="Камера",
            font=("Segoe UI", 22, "bold"),
            fg=COLORS["text"],
            bg=COLORS["background"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            heading,
            text="Подключите телефон и настройте изображение для трансляции.",
            font=("Segoe UI", 10),
            fg=COLORS["muted"],
            bg=COLORS["background"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        status = tk.Frame(heading, background=COLORS["panel"], padx=11, pady=7)
        status.grid(row=0, column=2, rowspan=2, sticky="e")
        self.status_dot = tk.Canvas(
            status,
            width=8,
            height=8,
            bg=COLORS["panel"],
            highlightthickness=0,
        )
        self.status_dot.create_oval(1, 1, 7, 7, fill=COLORS["warning"], outline="")
        self.status_dot.pack(side="left", padx=(0, 7))
        self.status_label = tk.Label(
            status,
            text="Телефон не подключён",
            font=("Segoe UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        )
        self.status_label.pack(side="left")
        self.virtual_camera_button = tk.Button(
            heading,
            text="Запустить виртуальную камеру",
            command=self._toggle_virtual_camera,
            font=("Segoe UI", 9, "bold"),
            fg=COLORS["text"],
            bg=COLORS["accent"],
            activeforeground=COLORS["text"],
            activebackground=COLORS["accent_hover"],
            relief="flat",
            padx=12,
            pady=8,
            cursor="hand2",
        )
        self.virtual_camera_button.grid(
            row=0,
            column=1,
            rowspan=2,
            sticky="e",
            padx=(12, 16),
        )

    def _build_preview(self, parent: tk.Frame) -> None:
        area = tk.Frame(parent, background=COLORS["background"])
        area.grid(row=1, column=0, sticky="nsew", padx=(0, 20))
        area.grid_columnconfigure(0, weight=1)
        area.grid_rowconfigure(0, weight=1)

        preview = tk.Frame(
            area,
            background="#0c0e12",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        self.preview_panel = preview
        preview.grid(row=0, column=0, sticky="nsew")

        center = tk.Frame(preview, background="#0c0e12")
        self.preview_placeholder = center
        center.place(relx=0.5, rely=0.48, anchor="center")
        icon = tk.Canvas(
            center,
            width=54,
            height=54,
            background=COLORS["panel_alt"],
            highlightthickness=0,
        )
        icon.create_rectangle(14, 17, 40, 37, outline=COLORS["muted"], width=2)
        icon.create_polygon(40, 22, 48, 18, 48, 36, 40, 32, outline=COLORS["muted"], fill="")
        icon.pack(pady=(0, 17))
        tk.Label(
            center,
            text="Предпросмотр появится после подключения",
            font=("Segoe UI", 13, "bold"),
            fg=COLORS["text"],
            bg="#0c0e12",
        ).pack()
        tk.Label(
            center,
            text="Сначала установите приложение-компаньон на телефон.",
            font=("Segoe UI", 10),
            fg=COLORS["muted"],
            bg="#0c0e12",
        ).pack(pady=(7, 0))
        self.preview_label = tk.Label(preview, background="#0c0e12")
        self.preview_label.place(relx=0.5, rely=0.5, anchor="center")

        preview_footer = tk.Frame(preview, background="#0c0e12")
        preview_footer.place(relx=0.03, rely=0.95, anchor="sw")
        self.preview_signal_label = tk.Label(
            preview_footer,
            text="НЕТ ВХОДЯЩЕГО СИГНАЛА",
            font=("Segoe UI", 8, "bold"),
            fg=COLORS["muted"],
            bg="#0c0e12",
        )
        self.preview_signal_label.pack(side="left")
        self.preview_resolution_label = tk.Label(
            preview_footer,
            text="  ·  ожидание потока",
            font=("Segoe UI", 8),
            fg="#68707d",
            bg="#0c0e12",
        )
        self.preview_resolution_label.pack(side="left")

        self._build_connection_card(area)

    def _build_connection_card(self, parent: tk.Frame) -> None:
        card = self._card(parent)
        card.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        card.grid_columnconfigure(0, weight=1)

        tk.Label(
            card,
            text="Подключение",
            font=("Segoe UI", 11, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            card,
            text="Выберите способ связи с телефоном",
            font=("Segoe UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 12))

        mode_row = tk.Frame(card, background=COLORS["panel"])
        mode_row.grid(row=2, column=0, sticky="ew")
        for value in (ConnectionMode.WIFI.value, ConnectionMode.USB.value):
            button = tk.Radiobutton(
                mode_row,
                text=value,
                value=value,
                variable=self.mode,
                command=self._select_mode,
                indicatoron=False,
                font=("Segoe UI", 9, "bold"),
                fg=COLORS["muted"],
                selectcolor=COLORS["panel_alt"],
                activebackground=COLORS["panel_alt"],
                activeforeground=COLORS["text"],
                background=COLORS["panel"],
                padx=15,
                pady=8,
                relief="flat",
                borderwidth=0,
            )
            button.pack(side="left", padx=(0, 8))

        self.connect_button = tk.Button(
            card,
            text="Подключить камеру",
            command=self._toggle_connection,
            font=("Segoe UI", 9, "bold"),
            fg="white",
            bg=COLORS["accent"],
            activebackground=COLORS["accent_hover"],
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=9,
        )
        self.connect_button.grid(row=3, column=0, sticky="ew", pady=(14, 0))

        connection_fields = tk.Frame(card, background=COLORS["panel"])
        connection_fields.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        connection_fields.grid_columnconfigure(1, weight=1)
        tk.Label(
            connection_fields,
            text="IP компьютера",
            font=("Segoe UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.host_entry = tk.Entry(
            connection_fields,
            textvariable=tk.StringVar(value=self._local_ipv4_addresses()[0]),
            font=("Segoe UI", 9),
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            bg=COLORS["panel_alt"],
            relief="flat",
            width=18,
        )
        self.host_entry.grid(row=0, column=1, sticky="ew", pady=3)
        self.connection_hint = tk.Label(
            card,
            text="Wi-Fi без кода доступа: любой участник этой сети сможет видеть камеру. Используйте только доверенную сеть.",
            wraplength=395,
            justify="left",
            font=("Segoe UI", 8),
            fg="#747d8a",
            bg=COLORS["panel"],
        )
        self.connection_hint.grid(row=5, column=0, sticky="w", pady=(8, 0))

    def _build_controls(self, parent: tk.Frame) -> None:
        card = self._card(parent)
        card.grid(row=1, column=1, sticky="ns")
        card.configure(width=300)
        card.grid_propagate(False)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)
        controls_canvas = tk.Canvas(
            card,
            background=COLORS["panel"],
            highlightthickness=0,
            borderwidth=0,
        )
        controls_canvas.grid(row=0, column=0, sticky="nsew")
        controls_scrollbar = ttk.Scrollbar(
            card,
            orient="vertical",
            command=controls_canvas.yview,
        )
        controls_scrollbar.grid(row=0, column=1, sticky="ns")
        controls_canvas.configure(yscrollcommand=controls_scrollbar.set)
        controls_content = tk.Frame(controls_canvas, background=COLORS["panel"])
        controls_window = controls_canvas.create_window(
            (0, 0),
            window=controls_content,
            anchor="nw",
        )
        controls_content.bind(
            "<Configure>",
            lambda _event: controls_canvas.configure(
                scrollregion=controls_canvas.bbox("all")
            ),
        )
        controls_canvas.bind(
            "<Configure>",
            lambda event: controls_canvas.itemconfigure(
                controls_window,
                width=event.width,
            ),
        )

        tk.Label(
            controls_content,
            text="Настройки камеры",
            font=("Segoe UI", 12, "bold"),
            fg=COLORS["text"],
            bg=COLORS["panel"],
        ).grid(row=0, column=0, sticky="w")
        self.control_status_label = tk.Label(
            controls_content,
            text="Подключите камеру, чтобы управлять параметрами",
            font=("Segoe UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        )
        self.control_status_label.grid(row=1, column=0, sticky="w", pady=(4, 17))

        self._section_label(controls_content, "ФОКУС", 2)
        autofocus = ttk.Checkbutton(
            controls_content,
            text="Автофокус",
            variable=self.autofocus_var,
            command=self._schedule_camera_control_update,
        )
        autofocus.grid(row=3, column=0, sticky="w", pady=(0, 5))
        self.control_widgets.append((autofocus, "normal"))
        self.focus_slider = self._slider(
            controls_content, "Ручной фокус", "ДАЛЬШЕ", "БЛИЖЕ", 4,
            self.focus_var, 0, 1,
        )

        self._section_label(controls_content, "ИЗОБРАЖЕНИЕ", 5)
        self._slider(
            controls_content, "Экспозиция", "−2 EV", "+2 EV", 6,
            self.exposure_var, -2, 2,
        )
        self._select(
            controls_content, "ISO", ("Авто", "100", "200", "400", "800", "1600"),
            7, self.iso_var,
        )
        self._select(
            controls_content,
            "Баланс белого",
            ("Авто", "Лампа", "Люминесцентный", "Дневной", "Облачно"),
            8,
            self.white_balance_var,
        )

        self._section_label(controls_content, "ОПТИКА И ВЫХОД", 9)
        self._select(
            controls_content, "Камера", ("Задняя", "Фронтальная"), 10, self.lens_var,
        )
        self._slider(
            controls_content, "Зум", "1×", "10×", 11, self.zoom_var, 1, 10,
        )
        self._select(
            controls_content, "Частота кадров", ("15", "24", "30", "60"), 12,
            self.fps_var,
        )
        self._select(
            controls_content, "Разрешение потока", ("480p", "720p", "1080p"), 13,
            self.resolution_var,
        )
        tk.Label(
            controls_content,
            text="Доступные ISO, фокус и FPS зависят от камеры телефона.",
            wraplength=260,
            justify="left",
            font=("Segoe UI", 8),
            fg="#747d8a",
            bg=COLORS["panel"],
        ).grid(row=14, column=0, sticky="w", pady=(8, 0))
        self._set_camera_controls_enabled(False)

    def _card(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(
            parent,
            background=COLORS["panel"],
            padx=18,
            pady=18,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )

    def _section_label(self, parent: tk.Frame, text: str, row: int) -> None:
        tk.Label(
            parent,
            text=text,
            font=("Segoe UI", 8, "bold"),
            fg=COLORS["accent"],
            bg=COLORS["panel"],
        ).grid(row=row, column=0, sticky="w", pady=(13, 6))

    def _slider(
        self,
        parent: tk.Frame,
        title: str,
        low_label: str,
        high_label: str,
        row: int,
        variable: tk.DoubleVar,
        minimum: float,
        maximum: float,
    ) -> ttk.Scale:
        block = tk.Frame(parent, background=COLORS["panel"])
        block.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        block.grid_columnconfigure(0, weight=1)
        tk.Label(
            block,
            text=title,
            font=("Segoe UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).grid(row=0, column=0, sticky="w")
        scale = ttk.Scale(
            block,
            from_=minimum,
            to=maximum,
            variable=variable,
            command=lambda _value: self._schedule_camera_control_update(),
        )
        scale.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self.control_widgets.append((scale, "normal"))
        ticks = tk.Frame(block, background=COLORS["panel"])
        ticks.grid(row=2, column=0, sticky="ew")
        ticks.grid_columnconfigure(0, weight=1)
        for column, label in enumerate((low_label, high_label)):
            tk.Label(
                ticks,
                text=label,
                font=("Segoe UI", 8),
                fg="#747d8a",
                bg=COLORS["panel"],
            ).grid(row=0, column=column, sticky="w" if column == 0 else "e")
        return scale

    def _select(
        self,
        parent: tk.Frame,
        title: str,
        options: tuple[str, ...],
        row: int,
        variable: tk.StringVar,
    ) -> None:
        block = tk.Frame(parent, background=COLORS["panel"])
        block.grid(row=row, column=0, sticky="ew", pady=(0, 7))
        block.grid_columnconfigure(0, weight=1)
        tk.Label(
            block,
            text=title,
            font=("Segoe UI", 9),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        selector = ttk.Combobox(
            block,
            values=options,
            state="readonly",
            textvariable=variable,
        )
        selector.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._schedule_camera_control_update(),
        )
        selector.grid(row=1, column=0, sticky="ew")
        self.control_widgets.append((selector, "readonly"))

    def _set_camera_controls_enabled(self, enabled: bool) -> None:
        for widget, active_state in self.control_widgets:
            widget.configure(state=active_state if enabled else "disabled")
        if self.focus_slider is not None:
            self.focus_slider.configure(
                state="normal" if enabled and not self.autofocus_var.get() else "disabled"
            )
        self.control_status_label.configure(
            text=(
                "Управление камерой подключено"
                if enabled
                else "Подключите камеру, чтобы управлять параметрами"
            ),
            fg=COLORS["green"] if enabled else COLORS["muted"],
        )

    def _schedule_camera_control_update(self) -> None:
        if not self.camera_connected:
            return
        if self.focus_slider is not None:
            self.focus_slider.configure(
                state="normal" if not self.autofocus_var.get() else "disabled"
            )
        if self.control_after_id is not None:
            self.root.after_cancel(self.control_after_id)
        self.control_after_id = self.root.after(
            120, self._send_camera_control
        )

    def _send_camera_control(self) -> None:
        self.control_after_id = None
        command: dict[str, object] = {
            "autofocus": self.autofocus_var.get(),
            "focus": self.focus_var.get(),
            "exposure": self.exposure_var.get(),
            "iso": self.iso_var.get(),
            "whiteBalance": self.white_balance_var.get(),
            "lens": self.lens_var.get(),
            "zoom": self.zoom_var.get(),
            "fps": int(self.fps_var.get()),
            "resolution": self.resolution_var.get(),
        }
        try:
            if self.receiver is not None:
                self.receiver.send_control(command)
            elif self.stream_server is not None:
                self.stream_server.send_control(command)
        except (OSError, ConnectionError) as error:
            self._receive_status(f"Не удалось отправить настройки камеры: {error}", True)

    def _select_mode(self) -> None:
        self.session = DeviceSession(
            status=SessionStatus.DISCONNECTED,
            mode=ConnectionMode(self.mode.get()),
        )
        self.connection_hint.configure(
            text=(
                "USB: подключение через подтверждённую ADB-отладку. "
                "Wi-Fi без пароля: подключайтесь только к доверенной сети."
            )
        )

    def _toggle_connection(self) -> None:
        if self.stream_server is not None or self.receiver is not None:
            if self.stream_server is not None:
                self.stream_server.stop()
                self.stream_server = None
            if self.receiver is not None:
                self.receiver.stop()
                self.receiver = None
            self.streaming = False
            forward_error = self._remove_usb_forward()
            self.connect_button.configure(text="Подключить камеру")
            self._set_status(
                forward_error or "Ожидание телефона остановлено",
                forward_error is not None,
            )
            return

        self.decoder = H264Decoder()
        if self.mode.get() == ConnectionMode.USB.value:
            try:
                self._prepare_usb_connection()
            except (OSError, subprocess.SubprocessError, ConnectionError) as error:
                self._set_status(str(error), True)
                return
            self.receiver = H264FrameReceiver(
                "127.0.0.1",
                STREAM_PORT,
                self._receive_frame,
                self._receive_status,
                usb=True,
            )
            self.receiver.start()
            self.connection_hint.configure(
                text=(
                    "USB forward настроен. На телефоне укажите 127.0.0.1 "
                    "и включите трансляцию. Используется быстрый бинарный USB-протокол."
                )
            )
        else:
            host = self.host_entry.get().strip()
            try:
                address = ipaddress.IPv4Address(host)
            except ipaddress.AddressValueError:
                self._set_status("Введите корректный IPv4-адрес компьютера", True)
                return
            if not address.is_private or address.is_loopback:
                self._set_status(
                    "Для камеры выберите приватный IPv4-адрес локальной сети",
                    True,
                )
                return

            self.stream_server = CameraStreamServer(
                self._receive_frame,
                self._receive_status,
                host=host,
            )
            try:
                self.stream_server.start()
            except OSError as error:
                self.stream_server = None
                self._set_status(f"Не удалось открыть порт {STREAM_PORT}: {error}", True)
                return
        self.streaming = True
        self.connect_button.configure(
            text="Отключить камеру" if self.receiver is not None else "Остановить ожидание"
        )
        self._set_status(
            "Подключаемся к локальной камере через ADB…" if self.receiver is not None
            else "Запущено ожидание телефона…",
            False,
        )
        if self.mode.get() == ConnectionMode.WIFI.value:
            self.connection_hint.configure(
                text=(
                    f"На телефоне введите IP компьютера {host}:{STREAM_PORT} "
                    "и нажмите «Начать трансляцию». Без кода доступа — только доверенная сеть."
                )
            )

    def _prepare_usb_connection(self) -> None:
        adb = self._find_adb()
        if adb is None:
            raise ConnectionError(
                "ADB не найден. Установите Android Platform-Tools и добавьте ADB в PATH."
            )
        result = subprocess.run(
            [str(adb), "devices"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        devices = [
            line.split()[0]
            for line in result.stdout.splitlines()[1:]
            if len(line.split()) >= 2 and line.split()[1] == "device"
        ]
        if not devices:
            raise ConnectionError(
                "Телефон не найден через ADB. Подключите USB и подтвердите отладку."
            )
        result = subprocess.run(
            [
                str(adb),
                "-s",
                devices[0],
                "forward",
                f"tcp:{STREAM_PORT}",
                "localabstract:lumalink_camera_stream",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise ConnectionError(result.stderr.strip() or "Не удалось настроить USB forward")
        self.usb_forward = (adb, devices[0])

    def _remove_usb_forward(self) -> str | None:
        if self.usb_forward is None:
            return None
        adb, serial = self.usb_forward
        self.usb_forward = None
        try:
            subprocess.run(
                [
                    str(adb),
                    "-s",
                    serial,
                    "forward",
                    "--remove",
                    f"tcp:{STREAM_PORT}",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return f"Не удалось закрыть USB-forward: {error}"
        return None

    @staticmethod
    def _find_adb() -> Path | None:
        adb_path = shutil.which("adb")
        if adb_path:
            return Path(adb_path)

        local_app_data = Path.home() / "AppData" / "Local"
        package_root = local_app_data / "Microsoft" / "WinGet" / "Packages"
        for package in package_root.glob("Google.PlatformTools*"):
            executable = package / "platform-tools" / "adb.exe"
            if executable.is_file():
                return executable
        return None

    @staticmethod
    def _local_ipv4_addresses() -> list[str]:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
            if not ipaddress.IPv4Address(result[4][0]).is_loopback
        }
        return sorted(
            addresses,
            key=lambda address: (
                not address.startswith("192.168."),
                not address.startswith("10."),
                address,
            ),
        ) or ["127.0.0.1"]

    def _receive_frame(self, frame: bytes) -> None:
        for image in self.decoder.decode(frame):
            image = correct_stream_orientation(image)
            if self.virtual_camera is not None:
                self.virtual_camera.submit(image)
            try:
                self.frames.put_nowait(image)
            except queue.Full:
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass
                self.frames.put_nowait(image)

    def _receive_status(self, message: str, is_error: bool) -> None:
        self.status_messages.put((message, is_error))

    def _receive_virtual_camera_status(self, message: str, is_error: bool) -> None:
        self.virtual_camera_status_messages.put((message, is_error))

    def _toggle_virtual_camera(self) -> None:
        if self.virtual_camera is not None:
            output = self.virtual_camera
            self.virtual_camera = None
            output.stop()
            self.virtual_camera_button.configure(text="Запустить виртуальную камеру")
            self._set_status(
                "Камера подключена" if self.camera_connected else "Виртуальная камера остановлена",
                False,
                connected=self.camera_connected,
            )
            return

        output = VirtualCameraOutput(self._receive_virtual_camera_status)
        self.virtual_camera = output
        self.virtual_camera_button.configure(
            text="Запуск виртуальной камеры…",
            state="disabled",
        )
        output.start()

    def _poll_stream(self) -> None:
        while True:
            try:
                message, is_error = self.virtual_camera_status_messages.get_nowait()
            except queue.Empty:
                break
            if message.startswith("Виртуальная камера запущена"):
                self.virtual_camera_button.configure(
                    text="Остановить виртуальную камеру",
                    state="normal",
                )
                self._set_status(
                    message,
                    False,
                    connected=self.camera_connected,
                )
            elif message.startswith("Виртуальная камера остановлена"):
                self.virtual_camera_button.configure(
                    text="Запустить виртуальную камеру",
                    state="normal",
                )
                if self.virtual_camera is not None:
                    self.virtual_camera = None
                self._set_status(
                    "Камера подключена" if self.camera_connected else message,
                    False,
                    connected=self.camera_connected,
                )
            elif is_error:
                if self.virtual_camera is not None:
                    self.virtual_camera.stop()
                    self.virtual_camera = None
                self.virtual_camera_button.configure(
                    text="Запустить виртуальную камеру",
                    state="normal",
                )
                self._set_status(message, True)

        while True:
            try:
                message, is_error = self.status_messages.get_nowait()
            except queue.Empty:
                break
            self._set_status(
                message,
                is_error,
                connected=not is_error and message.startswith("Получаем H.264-видео"),
            )
            if is_error:
                self.camera_connected = False
                self._set_camera_controls_enabled(False)
            if is_error and self.receiver is not None:
                self.receiver.stop()
                self.receiver = None
                self.streaming = False
                self._remove_usb_forward()
                self.connect_button.configure(text="Подключить камеру")

        try:
            frame = self.frames.get_nowait()
        except queue.Empty:
            frame = None
        if frame is not None:
            try:
                image = frame.copy()
                source_width, source_height = image.size
                self.frame_timestamps.append(time.monotonic())
                measured_fps = 0.0
                if len(self.frame_timestamps) > 1:
                    duration = self.frame_timestamps[-1] - self.frame_timestamps[0]
                    if duration > 0:
                        measured_fps = (len(self.frame_timestamps) - 1) / duration
                width = max(self.preview_panel.winfo_width(), 1)
                height = max(self.preview_panel.winfo_height(), 1)
                image.thumbnail((width, height), Image.Resampling.LANCZOS)
                self.preview_image = ImageTk.PhotoImage(image)
                self.preview_label.configure(image=self.preview_image)
                self.preview_label.place(relx=0.5, rely=0.5, anchor="center")
                self.preview_placeholder.place_forget()
                self.preview_signal_label.configure(text="ВХОДЯЩИЙ СИГНАЛ")
                self.preview_resolution_label.configure(
                    text=(
                        f"  ·  {source_width}×{source_height}"
                        + (f" / {measured_fps:.0f} FPS" if measured_fps else "")
                    )
                )
                if not self.camera_connected:
                    self.camera_connected = True
                    self._set_camera_controls_enabled(True)
                    self._send_camera_control()
                self._set_status("Камера подключена", False, connected=True)
            except (OSError, tk.TclError) as error:
                self._set_status(f"Не удалось отобразить кадр: {error}", True)

        self.root.after(16, self._poll_stream)

    def _set_status(
        self,
        message: str,
        is_error: bool,
        connected: bool = False,
    ) -> None:
        self.status_label.configure(
            text=message,
            fg=COLORS["warning"] if is_error else (
                COLORS["green"] if connected else COLORS["muted"]
            ),
        )
        color = COLORS["warning"] if is_error else (
            COLORS["green"] if connected else COLORS["muted"]
        )
        self.status_dot.itemconfigure(1, fill=color)

    def _close(self) -> None:
        if self.stream_server is not None:
            self.stream_server.stop()
        if self.receiver is not None:
            self.receiver.stop()
        if self.virtual_camera is not None:
            self.virtual_camera.stop()
            self.virtual_camera = None
        self._remove_usb_forward()
        self.root.destroy()


def run() -> None:
    root = tk.Tk()
    CameraStudioApp(root)
    root.mainloop()
