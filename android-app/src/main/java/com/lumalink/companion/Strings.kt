package com.lumalink.companion

/** Interface strings in Russian and English, as in the desktop app. */
internal object Strings {
    val languages = listOf("ru", "en")
    val languageNames = mapOf("ru" to "Русский", "en" to "English")

    private val ru = mapOf(
        "settings" to "Настройки",
        "close" to "Закрыть",
        "language" to "Язык",
        "screen" to "Экран",
        "keepAwake" to "Не гасить экран во время трансляции",
        "modeLabel" to "Способ подключения",
        "hostLabel" to "IP компьютера (необязательно)",
        "switchLens" to "Сменить камеру",
        "start" to "Начать трансляцию",
        "stop" to "Остановить трансляцию",
        "startingCamera" to "Запуск камеры…",
        "ready" to "Камера готова",
        "waitUsb" to "Ждём компьютер по USB",
        "needTethering" to "Включите «USB-модем» в настройках телефона",
        "waitWifi" to "Ищем компьютер в сети Wi-Fi…",
        "connecting" to "Подключение к %s…",
        "live" to "Компьютер подключён по %s",
        "failed" to "Ошибка подключения: %s",
        "closed" to "соединение закрыто",
        "notFound" to "компьютер не найден",
        "hintUsb" to "Включите «USB-модем» в настройках телефона, затем в LumaLink Studio выберите USB и нажмите «Подключить камеру». Если на компьютере VPN, включите «Отладку по USB».",
        "hintWifi" to "В LumaLink Studio выберите Wi-Fi и нажмите «Подключить камеру». Компьютер найдётся сам; если нет, введите его IP. Только доверенная сеть.",
        "hintLive" to "Трансляция идёт. Управлять фокусом, зумом и качеством можно с компьютера.",
        "hintNotFound" to "Нажмите «Подключить камеру» в LumaLink Studio с выбранным Wi-Fi или введите IP компьютера.",
        "rear" to "Задняя камера",
        "front" to "Фронтальная камера",
        "cameraPermission" to "Разрешите доступ к камере в настройках приложения",
        "cameraOpenFailed" to "Не удалось открыть камеру: %s",
        "cameraUnavailable" to "Выбранная камера недоступна на этом телефоне",
        "cameraStartFailed" to "Ошибка запуска камеры: %s",
        "settingsFailed" to "Не удалось применить настройки: %s",
        "manualFocusUnsupported" to "Ручной фокус этой камеры не поддерживается",
        "manualIsoUnsupported" to "ISO вручную не поддерживается этой камерой",
        "settingUnsupported" to "Камера не поддерживает один из параметров",
        "frameFailed" to "Ошибка подготовки видеокадра: %s",
        "usbFailed" to "Не удалось открыть USB-канал: %s",
        "badHost" to "Введите IP-адрес компьютера в локальной сети",
        "startFailed" to "Не удалось начать трансляцию: %s",
    )

    private val en = mapOf(
        "settings" to "Settings",
        "close" to "Close",
        "language" to "Language",
        "screen" to "Screen",
        "keepAwake" to "Keep the screen on while streaming",
        "modeLabel" to "Connection type",
        "hostLabel" to "Computer IP (optional)",
        "switchLens" to "Switch camera",
        "start" to "Start streaming",
        "stop" to "Stop streaming",
        "startingCamera" to "Starting the camera…",
        "ready" to "Camera ready",
        "waitUsb" to "Waiting for the computer over USB",
        "needTethering" to "Turn on USB tethering in the phone settings",
        "waitWifi" to "Looking for the computer on Wi-Fi…",
        "connecting" to "Connecting to %s…",
        "live" to "Computer connected over %s",
        "failed" to "Connection failed: %s",
        "closed" to "the connection was closed",
        "notFound" to "the computer was not found",
        "hintUsb" to "Turn on USB tethering in the phone settings, then select USB in LumaLink Studio and choose Connect camera. If the computer runs a VPN, turn on USB debugging.",
        "hintWifi" to "Select Wi-Fi in LumaLink Studio and choose Connect camera. The computer is found automatically; if not, enter its IP. Trusted networks only.",
        "hintLive" to "Streaming. Focus, zoom and quality are controlled from the computer.",
        "hintNotFound" to "Choose Connect camera in LumaLink Studio with Wi-Fi selected, or enter the computer's IP.",
        "rear" to "Rear camera",
        "front" to "Front camera",
        "cameraPermission" to "Allow camera access in the app settings",
        "cameraOpenFailed" to "Could not open the camera: %s",
        "cameraUnavailable" to "The selected camera is not available on this phone",
        "cameraStartFailed" to "Could not start the camera: %s",
        "settingsFailed" to "Could not apply the settings: %s",
        "manualFocusUnsupported" to "This camera does not support manual focus",
        "manualIsoUnsupported" to "This camera does not support manual ISO",
        "settingUnsupported" to "The camera does not support one of the settings",
        "frameFailed" to "Could not prepare a video frame: %s",
        "usbFailed" to "Could not open the USB channel: %s",
        "badHost" to "Enter the computer's local network IP address",
        "startFailed" to "Could not start streaming: %s",
    )

    private val tables = mapOf("ru" to ru, "en" to en)

    fun get(language: String, key: String, vararg values: Any?): String {
        val text = tables[language]?.get(key) ?: ru.getValue(key)
        return if (values.isEmpty()) text else String.format(text, *values)
    }

    /** Both tables must define the same keys; checked by a unit test. */
    fun keys(language: String): Set<String> = tables.getValue(language).keys
}
