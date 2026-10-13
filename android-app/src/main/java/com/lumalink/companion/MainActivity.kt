package com.lumalink.companion

import android.Manifest
import android.content.pm.PackageManager
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CameraCharacteristics
import android.graphics.Color
import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import android.util.Range
import android.util.Size
import android.view.Gravity
import android.view.Surface
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.camera2.interop.Camera2CameraControl
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.CaptureRequestOptions
import androidx.camera.core.Camera
import androidx.camera.core.CameraControl
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import java.net.InetAddress
import java.util.concurrent.Executors
import kotlin.math.pow
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private lateinit var previewView: PreviewView
    private lateinit var statusLabel: TextView
    private lateinit var lensButton: TextView
    private lateinit var streamButton: Button
    private lateinit var streamInfo: TextView
    private lateinit var computerAddress: EditText
    private lateinit var usbModeButton: RadioButton
    private lateinit var wifiModeButton: RadioButton
    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private var cameraProvider: ProcessCameraProvider? = null
    private var activeCamera: Camera? = null
    private var lensFacing = CameraSelector.LENS_FACING_BACK
    private var requestedResolution = DEFAULT_RESOLUTION
    private var requestedFps = DEFAULT_FPS
    private var publisher: H264Publisher? = null
    private var localServer: UsbFrameServer? = null
    private var videoEncoder: H264Encoder? = null
    private var cameraReady = false
    private var lastEncodedFrameAt = 0L
    private var lastMeasuredFrameStartedAt = 0L
    private var measuredFrameCount = 0
    private val requestCameraPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) {
                startCamera()
            } else {
                statusLabel.text = "Разрешите доступ к камере в настройках приложения"
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        buildScreen()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            startCamera()
        } else {
            requestCameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    override fun onDestroy() {
        publisher?.stop()
        localServer?.stop()
        videoEncoder?.close()
        videoEncoder = null
        cameraProvider?.unbindAll()
        cameraExecutor.shutdown()
        super.onDestroy()
    }

    private fun buildScreen() {
        val root = FrameLayout(this).apply {
            setBackgroundColor(Color.rgb(12, 14, 18))
        }
        previewView = PreviewView(this).apply {
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
            scaleType = PreviewView.ScaleType.FILL_CENTER
        }
        root.addView(
            previewView,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT,
            ),
        )

        val topPanel = FrameLayout(this).apply {
            setBackgroundColor(Color.argb(205, 16, 18, 22))
            setPadding(dp(20), dp(14), dp(20), dp(14))
        }
        val brand = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val logo = ImageView(this).apply {
            setImageResource(R.mipmap.ic_launcher)
            contentDescription = "LumaLink"
            scaleType = ImageView.ScaleType.FIT_CENTER
        }
        brand.addView(
            logo,
            LinearLayout.LayoutParams(dp(36), dp(36)).apply {
                marginEnd = dp(12)
            },
        )
        val title = TextView(this).apply {
            text = "LumaLink Companion"
            setTextColor(Color.WHITE)
            textSize = 18f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        }
        brand.addView(title)
        topPanel.addView(brand)
        root.addView(
            topPanel,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP,
            ),
        )

        val bottomPanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.argb(215, 16, 18, 22))
            setPadding(dp(20), dp(14), dp(20), dp(14))
        }
        statusLabel = TextView(this).apply {
            text = "Запуск камеры…"
            setTextColor(Color.WHITE)
            textSize = 14f
        }
        bottomPanel.addView(
            statusLabel,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ),
        )
        streamInfo = TextView(this).apply {
            setTextColor(Color.rgb(190, 194, 205))
            textSize = 12f
            setPadding(0, dp(8), 0, 0)
        }
        bottomPanel.addView(
            streamInfo,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ),
        )
        usbModeButton = RadioButton(this).apply {
            id = View.generateViewId()
            text = "USB"
            setTextColor(Color.WHITE)
            setPadding(0, 0, dp(24), 0)
        }
        wifiModeButton = RadioButton(this).apply {
            id = View.generateViewId()
            text = "Wi-Fi"
            setTextColor(Color.WHITE)
        }
        val modeGroup = RadioGroup(this).apply {
            orientation = RadioGroup.HORIZONTAL
            addView(usbModeButton)
            addView(wifiModeButton)
            check(usbModeButton.id)
            setOnCheckedChangeListener { _, _ -> showModeHelp() }
        }
        bottomPanel.addView(
            modeGroup,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = dp(4)
            },
        )
        computerAddress = EditText(this).apply {
            setSingleLine(true)
            hint = "IP компьютера (необязательно)"
            textSize = 14f
            setTextColor(Color.WHITE)
            setHintTextColor(Color.LTGRAY)
            setPadding(dp(12), dp(8), dp(12), dp(8))
            setBackgroundColor(Color.rgb(45, 48, 56))
        }
        bottomPanel.addView(
            computerAddress,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = dp(8)
            },
        )
        streamButton = Button(this).apply {
            text = "Начать трансляцию"
            isEnabled = false
            setOnClickListener { toggleStreaming() }
        }
        bottomPanel.addView(
            streamButton,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = dp(8)
            },
        )
        lensButton = TextView(this).apply {
            text = "Сменить камеру"
            textSize = 15f
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(dp(18), dp(12), dp(18), dp(12))
            setBackgroundColor(Color.rgb(105, 84, 220))
            isClickable = true
            isFocusable = true
            setOnClickListener { switchCamera() }
        }
        val buttonParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply {
            topMargin = dp(12)
        }
        bottomPanel.addView(lensButton, buttonParams)
        root.addView(
            bottomPanel,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM,
            ),
        )
        ViewCompat.setOnApplyWindowInsetsListener(root) { _, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            topPanel.setPadding(dp(20), dp(14) + systemBars.top, dp(20), dp(14))
            bottomPanel.setPadding(dp(20), dp(14), dp(20), dp(14) + systemBars.bottom)
            insets
        }
        setContentView(root)
        ViewCompat.requestApplyInsets(root)
        showModeHelp()
    }

    private fun showModeHelp() {
        val wifi = wifiModeButton.isChecked
        computerAddress.visibility = if (wifi) View.VISIBLE else View.GONE
        streamInfo.text = if (wifi) {
            "В LumaLink Studio выберите Wi-Fi и нажмите «Подключить камеру», затем здесь — " +
                "«Начать трансляцию». Компьютер найдётся сам; если нет, введите его IP. " +
                "Без кода доступа — только доверенная сеть."
        } else {
            "Включите «USB-модем» в настройках телефона и нажмите «Начать трансляцию», " +
                "затем в LumaLink Studio выберите USB и нажмите «Подключить камеру»."
        }
    }

    private fun updateStreamingState() {
        val streaming = publisher != null || localServer != null
        streamButton.text = if (streaming) "Остановить трансляцию" else "Начать трансляцию"
        streamButton.isEnabled = cameraReady
        usbModeButton.isEnabled = !streaming
        wifiModeButton.isEnabled = !streaming
        // The stream stops when the screen turns off, so keep it on while streaming.
        if (streaming) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }

    private fun startCamera() {
        statusLabel.text = "Подключение к камере…"
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener(
            {
                try {
                    cameraProvider = providerFuture.get()
                    bindCamera()
                } catch (error: Exception) {
                    statusLabel.text = "Не удалось открыть камеру: ${error.localizedMessage}"
                }
            },
            ContextCompat.getMainExecutor(this),
        )
    }

    private fun bindCamera() {
        val provider = cameraProvider ?: return
        val selector = CameraSelector.Builder()
            .requireLensFacing(lensFacing)
            .build()
        if (!provider.hasCamera(selector)) {
            statusLabel.text = "Выбранная камера недоступна на устройстве"
            return
        }

        val previewBuilder = Preview.Builder()
            .setTargetResolution(Size(640, 480))
            .setTargetFrameRate(Range(requestedFps, requestedFps))
        Camera2Interop.Extender(previewBuilder).setCaptureRequestOption(
            CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE,
            Range(requestedFps, requestedFps),
        )
        val preview = previewBuilder
            .build()
            .also {
                it.surfaceProvider = previewView.surfaceProvider
            }
        val analysisBuilder = ImageAnalysis.Builder()
            .setTargetResolution(analysisSize(requestedResolution))
            .setTargetRotation(Surface.ROTATION_90)
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
        Camera2Interop.Extender(analysisBuilder).setCaptureRequestOption(
            CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE,
            Range(requestedFps, requestedFps),
        )
        val analysis = analysisBuilder
            .build()
            .also { useCase ->
                useCase.setAnalyzer(cameraExecutor, ::analyzeFrame)
            }
        try {
            provider.unbindAll()
            activeCamera = null
            activeCamera = provider.bindToLifecycle(this, selector, preview, analysis)
            cameraReady = true
            streamButton.isEnabled = true
            applyCameraSettings(JSONObject().apply {
                put("autofocus", true)
                put("focus", 0.5)
                put("exposure", 0.0)
                put("iso", "Авто")
                put("whiteBalance", "Авто")
                put("zoom", 1.0)
                put("fps", requestedFps)
                put("resolution", requestedResolution)
            })
            statusLabel.text = when (lensFacing) {
                CameraSelector.LENS_FACING_FRONT -> "Предпросмотр · фронтальная камера"
                else -> "Предпросмотр · основная камера"
            }
        } catch (error: Exception) {
            statusLabel.text = "Ошибка запуска камеры: ${error.localizedMessage}"
        }
    }

    private fun analysisSize(resolution: String): Size =
        when (resolution) {
            "480p" -> Size(640, 480)
            "1080p" -> Size(1920, 1080)
            else -> Size(1280, 720)
        }

    private fun applyCameraControl(command: JSONObject) {
        try {
            val resolution = command.optString("resolution", requestedResolution)
                .takeIf { it in setOf("480p", "720p", "1080p") }
                ?: requestedResolution
            val fps = command.optInt("fps", requestedFps).coerceIn(15, 60)
            val facing = if (command.optString("lens", "Задняя") == "Фронтальная") {
                CameraSelector.LENS_FACING_FRONT
            } else {
                CameraSelector.LENS_FACING_BACK
            }
            val needsRebind =
                resolution != requestedResolution || fps != requestedFps || facing != lensFacing
            requestedResolution = resolution
            requestedFps = fps
            if (facing != lensFacing) lensFacing = facing
            if (needsRebind) bindCamera()

            applyCameraSettings(command)
        } catch (error: Exception) {
            Log.e(TAG, "Could not apply camera settings", error)
            statusLabel.text = "Не удалось применить настройки: ${error.localizedMessage}"
        }
    }

    private fun applyCameraSettings(command: JSONObject) {
        val camera = activeCamera ?: return
        val cameraInfo = Camera2CameraInfo.from(camera.cameraInfo)
        val options = CaptureRequestOptions.Builder()
        val autofocus = command.optBoolean("autofocus", true)
        val focusDistance = command.optDouble("focus", 0.5).coerceIn(0.0, 1.0)
        val minimumFocusDistance =
            cameraInfo.getCameraCharacteristic(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE)
                ?: 0f
        val supportedAfModes =
            cameraInfo.getCameraCharacteristic(CameraCharacteristics.CONTROL_AF_AVAILABLE_MODES)
                ?.toSet().orEmpty()
        if (autofocus && CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO in supportedAfModes) {
            options.setCaptureRequestOption(
                CaptureRequest.CONTROL_AF_MODE,
                CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO,
            )
        } else if (!autofocus && minimumFocusDistance > 0f) {
            options.setCaptureRequestOption(
                CaptureRequest.CONTROL_AF_MODE,
                CaptureRequest.CONTROL_AF_MODE_OFF,
            )
            options.setCaptureRequestOption(
                CaptureRequest.LENS_FOCUS_DISTANCE,
                minimumFocusDistance * focusDistance.toFloat(),
            )
        } else if (!autofocus) {
            statusLabel.text = "Ручной фокус этой камеры не поддерживается"
        }

        val isoChoice = command.optString("iso", "Авто")
        val requestedIso = isoChoice.toIntOrNull()
        val supportedAeModes =
            cameraInfo.getCameraCharacteristic(CameraCharacteristics.CONTROL_AE_AVAILABLE_MODES)
                ?.toSet().orEmpty()
        val sensitivityRange =
            cameraInfo.getCameraCharacteristic(CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE)
        val exposureTimeRange =
            cameraInfo.getCameraCharacteristic(CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE)
        val manualExposureSupported =
            CaptureRequest.CONTROL_AE_MODE_OFF in supportedAeModes &&
                sensitivityRange != null && exposureTimeRange != null
        val manualIso = requestedIso != null && manualExposureSupported
        if (manualIso) {
            options.setCaptureRequestOption(
                CaptureRequest.CONTROL_AE_MODE,
                CaptureRequest.CONTROL_AE_MODE_OFF,
            )
            options.setCaptureRequestOption(
                CaptureRequest.SENSOR_SENSITIVITY,
                requestedIso!!.coerceIn(sensitivityRange!!.lower, sensitivityRange.upper),
            )
            val exposureEv = command.optDouble("exposure", 0.0).coerceIn(-2.0, 2.0)
            val exposureNanos =
                ((1_000_000_000.0 / requestedFps) * 0.75 * 2.0.pow(exposureEv)).toLong()
            options.setCaptureRequestOption(
                CaptureRequest.SENSOR_EXPOSURE_TIME,
                exposureNanos.coerceIn(exposureTimeRange!!.lower, exposureTimeRange.upper),
            )
        } else {
            options.setCaptureRequestOption(
                CaptureRequest.CONTROL_AE_MODE,
                CaptureRequest.CONTROL_AE_MODE_ON,
            )
        }

        val requestedAwbMode = when (command.optString("whiteBalance", "Авто")) {
            "Лампа" -> CaptureRequest.CONTROL_AWB_MODE_INCANDESCENT
            "Люминесцентный" -> CaptureRequest.CONTROL_AWB_MODE_FLUORESCENT
            "Дневной" -> CaptureRequest.CONTROL_AWB_MODE_DAYLIGHT
            "Облачно" -> CaptureRequest.CONTROL_AWB_MODE_CLOUDY_DAYLIGHT
            else -> CaptureRequest.CONTROL_AWB_MODE_AUTO
        }
        val supportedAwbModes =
            cameraInfo.getCameraCharacteristic(CameraCharacteristics.CONTROL_AWB_AVAILABLE_MODES)
                ?.toSet().orEmpty()
        val awbMode = if (requestedAwbMode in supportedAwbModes) {
            requestedAwbMode
        } else {
            CaptureRequest.CONTROL_AWB_MODE_AUTO
        }
        options.setCaptureRequestOption(CaptureRequest.CONTROL_AWB_MODE, awbMode)

        val fpsRanges =
            cameraInfo.getCameraCharacteristic(
                CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES,
            )?.toList().orEmpty()
        val fpsRange = fpsRanges
            .filter { requestedFps in it.lower..it.upper }
            .minByOrNull { it.upper - it.lower }
            ?: fpsRanges.minByOrNull {
                minOf(kotlin.math.abs(it.lower - requestedFps), kotlin.math.abs(it.upper - requestedFps))
            }
        if (fpsRange != null) {
            options.setCaptureRequestOption(
                CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE,
                fpsRange,
            )
        }

        val captureRequestFuture =
            Camera2CameraControl.from(camera.cameraControl)
                .setCaptureRequestOptions(options.build())
        captureRequestFuture.addListener(
            {
                try {
                    captureRequestFuture.get()
                    if (!manualIso && isoChoice != "Авто") {
                        statusLabel.text = "ISO вручную не поддерживается этой камерой"
                    }
                } catch (error: Exception) {
                    if (error.cause is CameraControl.OperationCanceledException) {
                        return@addListener
                    }
                    Log.e(TAG, "Camera2 rejected a camera setting", error)
                    statusLabel.text = "Камера не поддерживает один из параметров"
                }
            },
            ContextCompat.getMainExecutor(this),
        )

        val zoomState = camera.cameraInfo.zoomState.value
        if (zoomState != null) {
            val requestedZoom = command.optDouble("zoom", 1.0).toFloat()
            camera.cameraControl.setZoomRatio(
                requestedZoom.coerceIn(zoomState.minZoomRatio, zoomState.maxZoomRatio),
            )
        }
        if (!manualIso) {
            val range = camera.cameraInfo.exposureState.exposureCompensationRange
            val step = camera.cameraInfo.exposureState.exposureCompensationStep.toFloat()
            if (step > 0f) {
                val requestedExposure = command.optDouble("exposure", 0.0).toFloat()
                camera.cameraControl.setExposureCompensationIndex(
                    (requestedExposure / step).toInt().coerceIn(range.lower, range.upper),
                )
            }
        }
    }

    private fun switchCamera() {
        if (publisher != null) {
            publisher?.stop()
            publisher = null
            updateStreamingState()
        }
        lensFacing = if (lensFacing == CameraSelector.LENS_FACING_BACK) {
            CameraSelector.LENS_FACING_FRONT
        } else {
            CameraSelector.LENS_FACING_BACK
        }
        bindCamera()
    }

    private fun analyzeFrame(image: ImageProxy) {
        try {
            if (publisher == null && localServer == null) return
            val now = SystemClock.elapsedRealtime()
            if (now - lastEncodedFrameAt < 1_000L / requestedFps) return
            lastEncodedFrameAt = now
            val frameStartedAt = SystemClock.elapsedRealtimeNanos()
            val crop = image.cropRect
            val left = crop.left and -2
            val top = crop.top and -2
            val width = crop.width() and -2
            val height = crop.height() and -2
            if (width <= 0 || height <= 0) return
            val rotationDegrees = image.imageInfo.rotationDegrees
            if (
                rotationDegrees == 0 &&
                left == 0 &&
                top == 0 &&
                width == image.width &&
                height == image.height
            ) {
                val packets = ensureVideoEncoder(width, height).encode(image)
                publishEncodedPackets(packets, width, height, rotationDegrees, frameStartedAt)
                return
            }
            val planes = image.planes
            if (planes.size < 3) return
            val nv21 = ByteArray(width * height * 3 / 2)
            val yPlane = planes[0]
            val yBuffer = yPlane.buffer.duplicate()
            for (y in 0 until height) {
                val sourceRow = (top + y) * yPlane.rowStride + left * yPlane.pixelStride
                yBuffer.position(sourceRow)
                yBuffer.get(nv21, y * width, width)
            }
            val chromaOffset = width * height
            val uPlane = planes[1]
            val vPlane = planes[2]
            val uBuffer = uPlane.buffer.duplicate()
            val vBuffer = vPlane.buffer.duplicate()
            var outputIndex = chromaOffset
            for (y in 0 until height / 2) {
                val sourceY = top / 2 + y
                for (x in 0 until width / 2) {
                    val sourceX = left / 2 + x
                    val uIndex = sourceY * uPlane.rowStride + sourceX * uPlane.pixelStride
                    val vIndex = sourceY * vPlane.rowStride + sourceX * vPlane.pixelStride
                    nv21[outputIndex++] = vBuffer.get(vIndex)
                    nv21[outputIndex++] = uBuffer.get(uIndex)
                }
            }
            val (frameData, frameWidth, frameHeight) = rotateNv21(
                nv21,
                width,
                height,
                rotationDegrees,
            )
            val packets = ensureVideoEncoder(frameWidth, frameHeight).encode(frameData)
            publishEncodedPackets(packets, frameWidth, frameHeight, rotationDegrees, frameStartedAt)
        } catch (error: Exception) {
            Log.e(TAG, "Could not encode camera frame", error)
            runOnUiThread {
                statusLabel.text = "Ошибка подготовки видеокадра: ${error.localizedMessage}"
            }
        } finally {
            image.close()
        }
    }

    private fun ensureVideoEncoder(width: Int, height: Int): H264Encoder {
        val current = videoEncoder
        if (current != null && current.matches(width, height, requestedFps)) {
            return current
        }
        current?.close()
        return H264Encoder(width, height, requestedFps).also {
            videoEncoder = it
        }
    }

    private fun publishEncodedPackets(
        packets: List<ByteArray>,
        width: Int,
        height: Int,
        rotationDegrees: Int,
        frameStartedAt: Long,
    ) {
        packets.forEach { packet ->
            publisher?.publishFrame(packet)
            localServer?.publishFrame(packet)
        }
        measuredFrameCount += 1
        if (measuredFrameCount % PERFORMANCE_LOG_INTERVAL == 0) {
            val processingMs =
                (SystemClock.elapsedRealtimeNanos() - frameStartedAt) / 1_000_000
            val intervalMs =
                (frameStartedAt - lastMeasuredFrameStartedAt) / 1_000_000
            Log.i(
                TAG,
                "Camera frame ${width}x${height} rotation=$rotationDegrees: " +
                    "process=${processingMs}ms interval=${intervalMs}ms",
            )
        }
        lastMeasuredFrameStartedAt = frameStartedAt
    }

    private fun rotateNv21(
        source: ByteArray,
        width: Int,
        height: Int,
        rotationDegrees: Int,
    ): Triple<ByteArray, Int, Int> {
        val rotation = ((rotationDegrees % 360) + 360) % 360
        require(rotation == 0 || rotation == 90 || rotation == 180 || rotation == 270) {
            "Неподдерживаемый поворот кадра: $rotation°"
        }
        if (rotation == 0) return Triple(source, width, height)

        val outputWidth = if (rotation == 90 || rotation == 270) height else width
        val outputHeight = if (rotation == 90 || rotation == 270) width else height
        val output = ByteArray(source.size)
        val outputLumaSize = outputWidth * outputHeight

        for (y in 0 until height) {
            for (x in 0 until width) {
                val rotatedX: Int
                val rotatedY: Int
                when (rotation) {
                    90 -> {
                        rotatedX = height - 1 - y
                        rotatedY = x
                    }
                    180 -> {
                        rotatedX = width - 1 - x
                        rotatedY = height - 1 - y
                    }
                    else -> {
                        rotatedX = y
                        rotatedY = width - 1 - x
                    }
                }
                output[rotatedY * outputWidth + rotatedX] = source[y * width + x]
            }
        }

        val chromaOffset = width * height
        val chromaWidth = width / 2
        val chromaHeight = height / 2
        val outputChromaWidth = outputWidth / 2
        for (y in 0 until chromaHeight) {
            for (x in 0 until chromaWidth) {
                val rotatedX: Int
                val rotatedY: Int
                when (rotation) {
                    90 -> {
                        rotatedX = chromaHeight - 1 - y
                        rotatedY = x
                    }
                    180 -> {
                        rotatedX = chromaWidth - 1 - x
                        rotatedY = chromaHeight - 1 - y
                    }
                    else -> {
                        rotatedX = y
                        rotatedY = chromaWidth - 1 - x
                    }
                }
                val sourceIndex = chromaOffset + (y * chromaWidth + x) * 2
                val outputIndex =
                    outputLumaSize + (rotatedY * outputChromaWidth + rotatedX) * 2
                output[outputIndex] = source[sourceIndex]
                output[outputIndex + 1] = source[sourceIndex + 1]
            }
        }
        return Triple(output, outputWidth, outputHeight)
    }

    private fun toggleStreaming() {
        if (publisher != null || localServer != null) {
            publisher?.stop()
            publisher = null
            localServer?.stop()
            localServer = null
            statusLabel.text = "Предпросмотр · камера готова"
            updateStreamingState()
            return
        }
        if (!cameraReady) {
            statusLabel.text = "Сначала дождитесь запуска камеры"
            return
        }
        if (usbModeButton.isChecked) startUsbStream() else startWifiStream()
        updateStreamingState()
    }

    private fun startUsbStream() {
        val server = UsbFrameServer(
            onStatus = { status -> runOnUiThread { statusLabel.text = status } },
            onControl = { command ->
                runOnUiThread { applyCameraControl(command) }
            },
        )
        try {
            server.start()
            localServer = server
        } catch (error: Exception) {
            Log.e(TAG, "Could not start USB camera stream", error)
            statusLabel.text = "Не удалось открыть USB-канал: ${error.localizedMessage}"
        }
    }

    private fun startWifiStream() {
        val host = computerAddress.text.toString().trim().ifEmpty { null }
        if (host != null) {
            try {
                InetAddress.getByName(host)
            } catch (error: Exception) {
                statusLabel.text = "Введите IP-адрес компьютера в локальной сети"
                return
            }
        }
        lateinit var activePublisher: H264Publisher
        activePublisher = H264Publisher(
            host,
            onStatus = { status, isError ->
                runOnUiThread {
                    if (publisher === activePublisher) {
                        statusLabel.text = status
                        if (isError) {
                            publisher = null
                            updateStreamingState()
                        }
                    }
                }
            },
            onControl = { command ->
                runOnUiThread { applyCameraControl(command) }
            },
        )
        publisher = activePublisher
        try {
            activePublisher.start()
        } catch (error: Exception) {
            Log.e(TAG, "Could not start camera publisher", error)
            activePublisher.stop()
            publisher = null
            statusLabel.text = "Не удалось начать трансляцию: ${error.localizedMessage}"
        }
    }

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density).toInt()

    companion object {
        private const val PERFORMANCE_LOG_INTERVAL = 30
        private const val DEFAULT_FPS = 30
        private const val DEFAULT_RESOLUTION = "720p"
        private const val TAG = "LumaLink"
    }
}
