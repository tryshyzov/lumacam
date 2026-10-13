"""Interface strings in Russian and English."""

LANGUAGES = ("ru", "en")
LANGUAGE_NAMES = {"ru": "Русский", "en": "English"}

STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        "pause": "Пауза",
        "resume": "Продолжить",
        "settings": "Настройки",
        "language": "Язык",
        "profile": "Профиль",
        "fps_unit": "кадров/с",
        "minimize": "Свернуть",
        "maximize": "Развернуть",
        "restore": "Восстановить",
        "close": "Закрыть",
        "connection": "Подключение",
        "connect": "Подключить камеру",
        "disconnect": "Отключить камеру",
        "stop_waiting": "Остановить ожидание",
        "status_on": "Камера подключена по {link}",
        "status_off": "Камера не подключена",
        "status_usb": "Ищем телефон по USB…",
        "status_wifi": "Ждём телефон в сети Wi-Fi…",
        "hint_usb": "Включите на телефоне «USB-модем» и нажмите «Начать трансляцию» в LumaLink Companion.",
        "hint_usb_vpn": (
            "VPN на этом компьютере блокирует локальную сеть, поэтому «USB-модем» не сработает. "
            "Включите на телефоне «Отладку по USB» — подключение пойдёт в обход VPN. "
            "Либо добавьте LumaLink Studio в исключения VPN."
        ),
        "hint_wifi": "На телефоне выберите Wi-Fi и нажмите «Начать трансляцию». Если компьютер не найдётся, введите там IP {host}. Только доверенная сеть.",
        "hint_wifi_vpn": (
            "VPN на этом компьютере блокирует локальную сеть: телефон не сможет подключиться по Wi-Fi. "
            "Добавьте LumaLink Studio в исключения VPN или разрешите в нём доступ к локальной сети."
        ),
        "error_host": "Выберите в настройках адрес компьютера в локальной сети",
        "error_port": "Не удалось открыть порт {port}: {error}",
        "focus": "Фокус",
        "autofocus": "Автофокус",
        "manual_focus": "Ручной фокус",
        "far": "Дальше",
        "near": "Ближе",
        "image": "Изображение",
        "exposure": "Экспозиция",
        "iso": "ISO",
        "auto": "Авто",
        "white_balance": "Баланс белого",
        "wb_incandescent": "Лампа накаливания",
        "wb_fluorescent": "Люминесцентная лампа",
        "wb_daylight": "Дневной свет",
        "wb_cloudy": "Облачно",
        "lens": "Объектив",
        "rear": "Задняя",
        "front": "Фронтальная",
        "zoom": "Зум",
        "reset": "Сбросить параметры камеры",
        "vcam": "Виртуальная камера",
        "vcam_on": "{device} передаёт видео в другие программы",
        "vcam_paused": "Показывает стоп-кадр со значком паузы",
        "vcam_wait": "Включится сама, когда подключите камеру. Пока показывает заставку",
        "paused_note": "Пауза · стоп-кадр",
        "empty_title": "Камера не подключена",
        "empty_text": "Запустите трансляцию в приложении на телефоне, затем нажмите «Подключить камеру».",
        "preview": "Предпросмотр",
        "opt_grid": "Сетка кадрирования",
        "opt_info": "Сведения о потоке поверх кадра",
        "opt_mirror": "Отразить по горизонтали",
        "opt_host": "Адрес компьютера для Wi-Fi",
        "opt_adb": "Обходить VPN через отладку по USB",
    },
    "en": {
        "pause": "Pause",
        "resume": "Resume",
        "settings": "Settings",
        "language": "Language",
        "profile": "Profile",
        "fps_unit": "fps",
        "minimize": "Minimize",
        "maximize": "Maximize",
        "restore": "Restore",
        "close": "Close",
        "connection": "Connection",
        "connect": "Connect camera",
        "disconnect": "Disconnect camera",
        "stop_waiting": "Stop waiting",
        "status_on": "Camera connected over {link}",
        "status_off": "Camera not connected",
        "status_usb": "Looking for the phone over USB…",
        "status_wifi": "Waiting for the phone on Wi-Fi…",
        "hint_usb": "Turn on USB tethering on the phone and start the stream in LumaLink Companion.",
        "hint_usb_vpn": (
            "A VPN on this computer blocks the local network, so USB tethering will not work. "
            "Turn on USB debugging on the phone and the connection will bypass the VPN. "
            "Or add LumaLink Studio to the VPN exceptions."
        ),
        "hint_wifi": "Select Wi-Fi on the phone and start the stream. If the computer is not found, enter the IP {host} there. Trusted networks only.",
        "hint_wifi_vpn": (
            "A VPN on this computer blocks the local network: the phone cannot connect over Wi-Fi. "
            "Add LumaLink Studio to the VPN exceptions or allow local network access in the VPN."
        ),
        "error_host": "Choose the computer's local network address in Settings",
        "error_port": "Could not open port {port}: {error}",
        "focus": "Focus",
        "autofocus": "Autofocus",
        "manual_focus": "Manual focus",
        "far": "Far",
        "near": "Near",
        "image": "Image",
        "exposure": "Exposure",
        "iso": "ISO",
        "auto": "Auto",
        "white_balance": "White balance",
        "wb_incandescent": "Incandescent",
        "wb_fluorescent": "Fluorescent",
        "wb_daylight": "Daylight",
        "wb_cloudy": "Cloudy",
        "lens": "Lens",
        "rear": "Rear",
        "front": "Front",
        "zoom": "Zoom",
        "reset": "Reset camera settings",
        "vcam": "Virtual camera",
        "vcam_on": "{device} is sending video to other applications",
        "vcam_paused": "Showing a still frame with the pause icon",
        "vcam_wait": "Starts by itself when a camera connects. Showing the idle image for now",
        "paused_note": "Paused · still frame",
        "empty_title": "Camera not connected",
        "empty_text": "Start the stream in the phone app, then select Connect camera.",
        "preview": "Preview",
        "opt_grid": "Framing grid",
        "opt_info": "Stream info over the frame",
        "opt_mirror": "Flip horizontally",
        "opt_host": "Computer address for Wi-Fi",
        "opt_adb": "Bypass VPN through USB debugging",
    },
}


def translate(language: str, key: str, **values: object) -> str:
    text = STRINGS[language][key]
    return text.format(**values) if values else text


def format_number(language: str, value: float, digits: int) -> str:
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if language == "ru" else text
