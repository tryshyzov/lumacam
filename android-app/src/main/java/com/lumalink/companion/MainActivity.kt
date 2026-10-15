package com.lumalink.companion

import android.Manifest
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CaptureRequest
import android.os.Bundle
import android.os.SystemClock
import android.text.InputType
import android.util.Log
import android.util.Range
import android.util.Size
import android.view.Gravity
import android.view.OrientationEventListener
import android.view.Surface
import android.view.View
import android.view.WindowManager
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.camera2.interop.Camera2CameraControl
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.camera2.interop.CaptureRequestOptions
import androidx.camera.core.Camera
import androidx.camera.core.CameraControl
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.UseCaseGroup
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import java.net.InetAddress
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.pow
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private lateinit var previewView: PreviewView
    private lateinit var topBar: LinearLayout
    private lateinit var statusIcon: ImageView
    private lateinit var statusText: TextView
    private lateinit var settingsButton: ImageView
    private lateinit var lensTag: TextView
    private lateinit var sheet: LinearLayout
    private lateinit var modeControl: SegmentedControl
    private lateinit var hostField: LinearLayout
    private lateinit var hostInput: EditText
    private lateinit var streamButton: TextView
    private lateinit var hintText: TextView
    private lateinit var lensButton: LinearLayout
    private lateinit var settingsOverlay: FrameLayout
    private lateinit var settingsPanel: LinearLayout
    private lateinit var languageControl: SegmentedControl
    private lateinit var keepAwakeToggle: ToggleView
    private lateinit var settingsBack: OnBackPressedCallback
    private lateinit var updateBanner: LinearLayout
    private lateinit var updateBannerText: TextView
    private lateinit var updateBannerButton: TextView
    private lateinit var updateStatus: TextView
    private lateinit var updateButton: TextView
    private var updater: AppUpdater? = null
    private val translated = mutableListOf<Pair<(String) -> Unit, String>>()
    private val preferences by lazy { getSharedPreferences(PREFERENCES, MODE_PRIVATE) }
    private var language = "ru"

    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private var cameraProvider: ProcessCameraProvider? = null
    private var activeCamera: Camera? = null
    private var lensFacing = CameraSelector.LENS_FACING_BACK
    private var requestedResolution = DEFAULT_RESOLUTION
    // Also read on the camera and GL threads.
    @Volatile private var requestedFps = DEFAULT_FPS
    @Volatile private var publisher: H264Publisher? = null
    @Volatile private var localServer: UsbFrameServer? = null
    private var videoEncoder: H264Encoder? = null
    private var gpuCameraPipeline: GpuCameraPipeline? = null
    private var gpuFailed = false
    private var copyingAnalysisUseCase: ImageAnalysis? = null

    /** Which way up the phone is held (see [HeldRotation]); the video follows it. */
    private var heldRotation = HeldRotation.INITIAL
    private val orientationListener by lazy {
        object : OrientationEventListener(this) {
            override fun onOrientationChanged(degrees: Int) {
                val next = HeldRotation.snap(degrees, heldRotation)
                if (next == heldRotation) return
                heldRotation = next
                applyStreamRotation()
            }
        }
    }
    // Set by the network senders when they need a key frame; the camera thread
    // asks the encoder for one, at most every KEY_FRAME_REQUEST_INTERVAL_MS
    // so a slow link does not turn every frame into a large key frame.
    private val keyFrameWanted = AtomicBoolean(false)
    private var lastKeyFrameRequestAt = 0L
    private var cameraReady = false
    private var lastEncodedFrameAt = 0L
    private var lastMeasuredFrameStartedAt = 0L
    private var measuredFrameCount = 0

    /** A camera problem that keeps the phone from streaming at all. */
    private var cameraProblem: String? = null

    /** The last link event, or null while not streaming. */
    private var linkState: LinkEvent? = null
    private var linkDetail: String? = null

    /** A passing message shown in place of the hint until the next state change. */
    private var notice: String? = null

    private val requestCameraPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) {
                startCamera()
            } else {
                cameraProblem = t("cameraPermission")
                render()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // For comparing the two paths on one phone:
        // adb shell am start -n com.lumalink.companion/.MainActivity --ez force_copy true
        gpuFailed = intent.getBooleanExtra(EXTRA_FORCE_COPY, false)
        // For checking every orientation on a phone lying flat, where the
        // sensor keeps it: --ei held_rotation 0..3 (a Surface.ROTATION_*).
        heldRotation = intent.getIntExtra(EXTRA_HELD_ROTATION, heldRotation).takeIf { it in 0..3 } ?: heldRotation
        language = preferences.getString(KEY_LANGUAGE, "ru")?.takeIf { it in Strings.languages } ?: "ru"
        // Debug builds are signed with another key and cannot take a release over them.
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE == 0) {
            updater = AppUpdater(this, installedVersion() ?: "0") { render() }
        }
        buildScreen()
        updater?.check()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            startCamera()
        } else {
            requestCameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    override fun onResume() {
        super.onResume()
        if (orientationListener.canDetectOrientation()) orientationListener.enable()
    }

    override fun onPause() {
        orientationListener.disable()
        super.onPause()
    }

    override fun onDestroy() {
        updater?.close()
        publisher?.stop()
        localServer?.stop()
        videoEncoder?.close()
        videoEncoder = null
        cameraProvider?.unbindAll()
        gpuCameraPipeline?.release { gpuFrames.close() }
        gpuCameraPipeline = null
        cameraExecutor.shutdown()
        super.onDestroy()
    }

    private fun t(key: String, vararg values: Any?): String = Strings.get(language, key, *values)

    private fun translate(key: String, setter: (String) -> Unit) {
        translated += setter to key
    }

    // ---------------------------------------------------------------- layout

    private fun label(sizeSp: Float, color: Int = Palette.TEXT, bold: Boolean = false): TextView =
        TextView(this).apply {
            textSize = sizeSp
            setTextColor(color)
            if (bold) typeface = semiBold()
        }

    private fun iconButton(icon: Int, sizeDp: Int = 36): ImageView =
        ImageView(this).apply {
            setImageResource(icon)
            scaleType = ImageView.ScaleType.CENTER
            isClickable = true
            isFocusable = true
            background = pressable(rounded(Color.TRANSPARENT, dp(6).toFloat()), Palette.PRESS_ON_DARK)
            minimumWidth = dp(sizeDp)
            minimumHeight = dp(sizeDp)
        }

    private fun matchWidth(topMarginDp: Int = 0): LinearLayout.LayoutParams =
        LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { topMargin = dp(topMarginDp) }

    private fun buildScreen() {
        val root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        val column = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(
            column,
            FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT),
        )

        // Top bar: connection state on the left, settings on the right.
        topBar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(Palette.CHROME)
            minimumHeight = dp(40)
            setPadding(dp(12), dp(2), dp(2), dp(2))
        }
        statusIcon = ImageView(this)
        topBar.addView(statusIcon, LinearLayout.LayoutParams(dp(12), dp(12)))
        statusText = label(12f).apply { maxLines = 2 }
        topBar.addView(
            statusText,
            LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(6) },
        )
        settingsButton = iconButton(R.drawable.ic_settings).apply { setOnClickListener { openSettings() } }
        translate("settings") { settingsButton.contentDescription = it }
        topBar.addView(settingsButton, LinearLayout.LayoutParams(dp(36), dp(36)))
        column.addView(topBar, matchWidth())

        // A new version, offered above the picture while not streaming.
        updateBanner = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(Palette.SHEET)
            setPadding(dp(12), dp(4), dp(8), dp(4))
            visibility = View.GONE
        }
        updateBannerText = label(12f).apply { maxLines = 2 }
        updateBanner.addView(
            updateBannerText,
            LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f),
        )
        updateBannerButton = smallButton { onUpdateButton() }
        updateBanner.addView(
            updateBannerButton,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { marginStart = dp(8) },
        )
        column.addView(updateBanner, matchWidth())

        // The camera picture on black, with the active lens in the corner.
        val stage = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        previewView = PreviewView(this).apply {
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
            scaleType = PreviewView.ScaleType.FILL_CENTER
        }
        stage.addView(
            previewView,
            FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT),
        )
        lensTag = label(12f).apply {
            setPadding(dp(7), dp(2), dp(7), dp(2))
            background = rounded(Palette.TAG, dp(5).toFloat())
        }
        stage.addView(
            lensTag,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM or Gravity.START,
            ).apply { setMargins(dp(8), dp(8), dp(8), dp(8)) },
        )
        column.addView(stage, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))

        // Control sheet under the thumb.
        sheet = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Palette.CHROME)
            setPadding(dp(12), dp(10), dp(12), dp(10))
        }
        modeControl = SegmentedControl(this, listOf(MODE_USB, MODE_WIFI)).apply {
            onChange = {
                notice = null
                linkState = null
                render()
            }
        }
        translate("modeLabel") { modeControl.contentDescription = it }
        sheet.addView(modeControl, matchWidth())

        hostField = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val hostLabel = label(12f)
        translate("hostLabel") { hostLabel.text = it }
        hostField.addView(hostLabel, matchWidth())
        hostInput = EditText(this).apply {
            setSingleLine(true)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            hint = "192.168.1.20"
            textSize = 14f
            setTextColor(Palette.TEXT)
            setHintTextColor(Palette.BORDER_CONTROL)
            minHeight = dp(34)
            setPadding(dp(10), 0, dp(10), 0)
            background = rounded(Color.TRANSPARENT, dp(6).toFloat(), Palette.BORDER_CONTROL, dp(1))
        }
        translate("hostLabel") { hostInput.contentDescription = it }
        hostField.addView(hostInput, matchWidth(4))
        sheet.addView(hostField, matchWidth(8))

        streamButton = label(13f, bold = true).apply {
            gravity = Gravity.CENTER
            minHeight = dp(36)
            isClickable = true
            isFocusable = true
            setOnClickListener { toggleStreaming() }
        }
        sheet.addView(streamButton, matchWidth(8))

        hintText = label(12f, Palette.TEXT_SECONDARY).apply { setLineSpacing(0f, 1.15f) }
        sheet.addView(hintText, matchWidth(8))

        lensButton = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            minimumHeight = dp(30)
            setPadding(dp(10), 0, dp(10), 0)
            isClickable = true
            isFocusable = true
            background = pressable(rounded(Color.TRANSPARENT, dp(6).toFloat()), Palette.PRESS_ON_DARK)
            setOnClickListener { switchCamera() }
        }
        lensButton.addView(ImageView(this).apply { setImageResource(R.drawable.ic_switch_camera) })
        val lensLabel = label(13f)
        translate("switchLens") { lensLabel.text = it }
        lensButton.addView(
            lensLabel,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { marginStart = dp(6) },
        )
        sheet.addView(
            lensButton,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply {
                gravity = Gravity.CENTER_HORIZONTAL
                topMargin = dp(4)
            },
        )
        column.addView(sheet, matchWidth())

        buildSettings(root)

        ViewCompat.setOnApplyWindowInsetsListener(root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val keyboard = insets.getInsets(WindowInsetsCompat.Type.ime())
            val bottom = maxOf(bars.bottom, keyboard.bottom)
            topBar.setPadding(dp(12) + bars.left, dp(2) + bars.top, dp(2) + bars.right, dp(2))
            updateBanner.setPadding(dp(12) + bars.left, dp(4), dp(8) + bars.right, dp(4))
            sheet.setPadding(dp(12) + bars.left, dp(10), dp(12) + bars.right, dp(10) + bottom)
            settingsPanel.setPadding(dp(12) + bars.left, dp(14), dp(12) + bars.right, dp(14) + bars.bottom)
            insets
        }
        setContentView(root)
        ViewCompat.requestApplyInsets(root)
        render()
    }

    private fun smallButton(onClick: () -> Unit): TextView =
        label(12f, bold = true).apply {
            gravity = Gravity.CENTER
            minHeight = dp(28)
            setPadding(dp(10), 0, dp(10), 0)
            isClickable = true
            isFocusable = true
            background = pressable(
                rounded(Color.TRANSPARENT, dp(6).toFloat(), Palette.BORDER_CONTROL, dp(1)),
                Palette.PRESS_ON_DARK,
            )
            setOnClickListener { onClick() }
        }

    private fun groupTitle(key: String): TextView =
        label(12f, Palette.TEXT_SECONDARY, bold = true).apply {
            letterSpacing = 0.05f
            translate(key) { text = it.uppercase() }
        }

    private fun buildSettings(root: FrameLayout) {
        settingsOverlay = FrameLayout(this).apply {
            setBackgroundColor(Palette.SCRIM)
            visibility = View.GONE
            isClickable = true
            setOnClickListener { closeSettings() }
        }
        settingsPanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            isClickable = true
            background = GradientDrawable().apply {
                setColor(Palette.SHEET)
                val corner = dp(16).toFloat()
                cornerRadii = floatArrayOf(corner, corner, corner, corner, 0f, 0f, 0f, 0f)
            }
        }
        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val title = label(15f, bold = true)
        translate("settings") { title.text = it }
        header.addView(title, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        val close = iconButton(R.drawable.ic_close).apply { setOnClickListener { closeSettings() } }
        translate("close") { close.contentDescription = it }
        header.addView(close, LinearLayout.LayoutParams(dp(36), dp(36)))
        settingsPanel.addView(header, matchWidth())

        settingsPanel.addView(groupTitle("language"), matchWidth(14))
        languageControl = SegmentedControl(this, Strings.languages).apply {
            setLabels(Strings.languageNames)
            select(language)
            onChange = { chosen ->
                language = chosen
                preferences.edit().putString(KEY_LANGUAGE, chosen).apply()
                render()
            }
        }
        translate("language") { languageControl.contentDescription = it }
        settingsPanel.addView(languageControl, matchWidth(6))

        settingsPanel.addView(groupTitle("screen"), matchWidth(14))
        val awakeRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = dp(32)
        }
        val awakeLabel = label(13f)
        translate("keepAwake") { awakeLabel.text = it }
        awakeRow.addView(awakeLabel, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        keepAwakeToggle = ToggleView(this).apply {
            isOn = preferences.getBoolean(KEY_KEEP_AWAKE, true)
            onToggle = { on ->
                preferences.edit().putBoolean(KEY_KEEP_AWAKE, on).apply()
                render()
            }
        }
        translate("keepAwake") { keepAwakeToggle.contentDescription = it }
        awakeRow.addView(keepAwakeToggle)
        settingsPanel.addView(awakeRow, matchWidth(6))

        if (updater != null) {
            settingsPanel.addView(groupTitle("updates"), matchWidth(14))
            val updateRow = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                minimumHeight = dp(32)
            }
            updateStatus = label(13f)
            updateRow.addView(updateStatus, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            updateButton = smallButton { onUpdateButton() }
            updateRow.addView(
                updateButton,
                LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { marginStart = dp(8) },
            )
            settingsPanel.addView(updateRow, matchWidth(6))
        }

        settingsPanel.addView(
            label(12f, Palette.TEXT_SECONDARY).apply {
                text = "LumaLink Companion ${installedVersion() ?: ""}".trim()
            },
            matchWidth(14),
        )
        settingsOverlay.addView(
            settingsPanel,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM,
            ),
        )
        root.addView(
            settingsOverlay,
            FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT),
        )
        settingsBack = object : OnBackPressedCallback(false) {
            override fun handleOnBackPressed() = closeSettings()
        }
        onBackPressedDispatcher.addCallback(this, settingsBack)
    }

    private fun installedVersion(): String? =
        try {
            packageManager.getPackageInfo(packageName, 0).versionName
        } catch (_: Exception) {
            null
        }

    private fun streaming(): Boolean = publisher != null || localServer != null

    /** The release the update button would install, if any. */
    private fun offeredRelease(): AppRelease? =
        when (val state = updater?.state) {
            is UpdateState.Available -> state.release
            is UpdateState.Failed -> state.release
            else -> null
        }

    private fun onUpdateButton() {
        val release = offeredRelease()
        when {
            release == null -> updater?.check()
            // Installing replaces and stops this app, so never mid-stream.
            streaming() -> showNotice(t("updateAfterStream"))
            else -> updater?.install(release)
        }
    }

    /** The update's state as one line, and the action its button offers (null: no button). */
    private fun describeUpdate(state: UpdateState): Pair<String, String?> =
        when (state) {
            UpdateState.Idle, UpdateState.UpToDate -> t("updateLatest") to t("updateCheck")
            UpdateState.Checking -> t("updateChecking") to null
            is UpdateState.Available -> t("updateAvailable", state.release.version) to t("updateNow")
            is UpdateState.Downloading -> t("updateDownloading", state.release.version, state.percent) to null
            is UpdateState.Installing -> t("updateInstalling", state.release.version) to null
            is UpdateState.Failed -> when {
                state.release == null -> t("updateCheckFailed") to t("updateRetry")
                state.reason == AppUpdater.SIGNATURE_MISMATCH -> t("updateSignature") to null
                state.reason == AppUpdater.BLOCKED -> t("updateBlocked") to t("updateNow")
                state.reason == null -> t("updateCancelled") to t("updateNow")
                else -> t("updateFailed", state.reason) to t("updateRetry")
            }
        }

    private fun renderUpdate(streaming: Boolean) {
        val state = updater?.state ?: return
        val (text, action) = describeUpdate(state)
        updateStatus.text = text
        updateButton.text = action ?: ""
        updateButton.visibility = if (action == null) View.GONE else View.VISIBLE
        // The banner carries only news: an offer, its progress, or why it failed.
        val news = state is UpdateState.Available || state is UpdateState.Downloading ||
            state is UpdateState.Installing || (state is UpdateState.Failed && state.release != null)
        updateBanner.visibility = if (news && !streaming) View.VISIBLE else View.GONE
        updateBannerText.text = text
        updateBannerButton.text = action ?: ""
        updateBannerButton.visibility = updateButton.visibility
    }

    private fun openSettings() {
        settingsOverlay.visibility = View.VISIBLE
        settingsBack.isEnabled = true
    }

    private fun closeSettings() {
        settingsOverlay.visibility = View.GONE
        settingsBack.isEnabled = false
    }

    /** Bring every state-dependent text, icon and enabled flag up to date. */
    private fun render() {
        if (!::settingsPanel.isInitialized) return
        for ((setter, key) in translated) setter(t(key))
        val streaming = streaming()
        renderUpdate(streaming)
        val wifi = modeControl.value == MODE_WIFI

        val problem = cameraProblem
        val (icon, status) = when {
            problem != null -> R.drawable.ic_status_error to problem
            !cameraReady -> R.drawable.ic_status_wait to t("startingCamera")
            linkState == LinkEvent.CONNECTED -> R.drawable.ic_status_live to t("live", modeControl.value)
            linkState == LinkEvent.FAILED -> R.drawable.ic_status_error to t("failed", linkDetail ?: t("closed"))
            linkState == LinkEvent.NOT_FOUND -> R.drawable.ic_status_error to t("failed", t("notFound"))
            linkState == LinkEvent.NO_ANSWER -> R.drawable.ic_status_error to t("failed", t("noAnswer"))
            linkState == LinkEvent.NEED_TETHERING -> R.drawable.ic_status_wait to t("needTethering")
            linkState == LinkEvent.WAITING -> R.drawable.ic_status_wait to t("waitUsb")
            linkState == LinkEvent.SEARCHING -> R.drawable.ic_status_wait to t("waitWifi")
            linkState == LinkEvent.CONNECTING -> R.drawable.ic_status_wait to t("connecting", linkDetail ?: "")
            else -> R.drawable.ic_status_ready to t("ready")
        }
        statusIcon.setImageResource(icon)
        statusText.text = status

        streamButton.text = t(if (streaming) "stop" else "start")
        val radius = dp(6).toFloat()
        streamButton.background = if (streaming) {
            pressable(rounded(Color.TRANSPARENT, radius, Palette.BORDER_CONTROL, dp(1)), Palette.PRESS_ON_DARK)
        } else {
            pressable(rounded(Palette.TEXT, radius), Palette.PRESS_ON_LIGHT)
        }
        streamButton.setTextColor(if (streaming) Palette.TEXT else Palette.TEXT_ON_SOLID)
        streamButton.isEnabled = cameraReady
        streamButton.alpha = if (cameraReady) 1f else 0.45f

        modeControl.isEnabled = !streaming
        hostField.visibility = if (wifi && !streaming) View.VISIBLE else View.GONE
        hintText.text = notice ?: when {
            linkState == LinkEvent.CONNECTED -> t("hintLive")
            linkState == LinkEvent.NOT_FOUND -> t("hintNotFound")
            wifi -> t("hintWifi")
            else -> t("hintUsb")
        }
        lensTag.text = t(if (lensFacing == CameraSelector.LENS_FACING_FRONT) "front" else "rear")

        // The stream stops when the screen turns off, so keep it on while streaming.
        if (streaming && keepAwakeToggle.isOn) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }

    private fun showNotice(message: String) {
        notice = message
        render()
    }

    private fun startCamera() {
        cameraProblem = null
        render()
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener(
            {
                try {
                    cameraProvider = providerFuture.get()
                    bindCamera()
                } catch (error: Exception) {
                    cameraProblem = t("cameraOpenFailed", error.localizedMessage)
                    render()
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
            cameraProblem = t("cameraUnavailable")
            render()
            return
        }

        val fpsRange = cameraFpsRange(selector, requestedFps)
        val previewBuilder = Preview.Builder()
            .setTargetResolution(Size(640, 480))
            .setTargetFrameRate(fpsRange)
        Camera2Interop.Extender(previewBuilder).setCaptureRequestOption(
            CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE,
            fpsRange,
        )
        // The GPU path encodes what the preview draws, so the preview runs at
        // the streaming resolution; the copying path keeps a small preview.
        val gpu = if (gpuFailed) null else gpuPipeline()
        if (gpu != null) {
            previewBuilder.setTargetResolution(displayOriented(analysisSize(requestedResolution)))
        }
        val preview = previewBuilder
            .build()
            .also {
                it.surfaceProvider = previewView.surfaceProvider
            }
        try {
            provider.unbindAll()
            activeCamera = null
            copyingAnalysisUseCase = null
            activeCamera = if (gpu != null) {
                selector.filter(provider.availableCameraInfos).firstOrNull()?.let { info ->
                    gpu.setCamera(
                        info.sensorRotationDegrees,
                        lensFacing == CameraSelector.LENS_FACING_FRONT,
                        analysisSize(requestedResolution),
                    )
                    gpu.setStreamRotation(
                        info.getSensorRotationDegrees(HeldRotation.streamTarget(heldRotation)),
                    )
                }
                val group = UseCaseGroup.Builder()
                    .addUseCase(preview)
                    .addEffect(gpu.effect)
                    .build()
                provider.bindToLifecycle(this, selector, group)
            } else {
                provider.bindToLifecycle(this, selector, preview, copyingAnalysis(fpsRange))
            }
            cameraReady = true
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
            cameraProblem = null
            render()
        } catch (error: Exception) {
            if (gpu != null) {
                fallBackToCopying(error)
                return
            }
            cameraProblem = t("cameraStartFailed", error.localizedMessage)
            render()
        }
    }

    /** The CPU path: camera frames copied into the encoder (see [analyzeFrame]). */
    private fun copyingAnalysis(fpsRange: Range<Int>): ImageAnalysis {
        val analysisBuilder = ImageAnalysis.Builder()
            .setTargetResolution(analysisSize(requestedResolution))
            .setTargetRotation(HeldRotation.streamTarget(heldRotation))
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
        Camera2Interop.Extender(analysisBuilder).setCaptureRequestOption(
            CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE,
            fpsRange,
        )
        return analysisBuilder
            .build()
            .also { useCase ->
                useCase.setAnalyzer(cameraExecutor, ::analyzeFrame)
                copyingAnalysisUseCase = useCase
            }
    }

    /**
     * Turn the video to the way the phone is now held. CameraX reports the
     * turn for the copying path (see [analyzeFrame]); the GPU path is told it.
     * A quarter turn changes the video's shape, so the next frame starts a
     * new encoder.
     */
    private fun applyStreamRotation() {
        val target = HeldRotation.streamTarget(heldRotation)
        copyingAnalysisUseCase?.targetRotation = target
        val info = activeCamera?.cameraInfo ?: return
        if (!gpuFailed) gpuCameraPipeline?.setStreamRotation(info.getSensorRotationDegrees(target))
    }

    private fun gpuPipeline(): GpuCameraPipeline =
        gpuCameraPipeline ?: GpuCameraPipeline(
            gpuFrames,
            onError = { error -> runOnUiThread { fallBackToCopying(error) } },
        ).also { gpuCameraPipeline = it }

    /** The GPU path failed on this phone: copy frames on the CPU from now on. */
    private fun fallBackToCopying(error: Throwable) {
        if (gpuFailed) return
        Log.w(TAG, "GPU camera path failed; copying frames instead", error)
        gpuFailed = true
        // Unbind first so CameraX releases its surfaces before the GL thread stops.
        cameraProvider?.unbindAll()
        gpuCameraPipeline?.release { gpuFrames.close() }
        gpuCameraPipeline = null
        bindCamera()
    }

    /** [size], a landscape camera size, in the display's orientation, as CameraX expects. */
    private fun displayOriented(size: Size): Size {
        val rotation = previewView.display?.rotation ?: Surface.ROTATION_0
        val portrait = rotation == Surface.ROTATION_0 || rotation == Surface.ROTATION_180
        return if (portrait) Size(size.height, size.width) else size
    }

    /** The GPU path's side of the encoder; called on its GL thread only. */
    private val gpuFrames = object : GpuCameraPipeline.Frames {
        private var encoder: H264Encoder? = null
        private val replaced = mutableListOf<H264Encoder>()
        private var lastFrameAt = 0L
        private var size = Size(0, 0)

        override fun encoderFor(size: Size): H264Encoder? {
            if (publisher == null && localServer == null) return null
            this.size = size
            val current = encoder
            if (current != null && current.matches(size.width, size.height, requestedFps)) {
                return current
            }
            current?.let { replaced += it }
            encoder = null
            return H264Encoder(size.width, size.height, requestedFps, surfaceInput = true)
                .also { encoder = it }
        }

        override fun closeReplacedEncoders() {
            replaced.forEach { it.close() }
            replaced.clear()
        }

        override fun wantsFrame(timestampNs: Long): Boolean {
            if (timestampNs - lastFrameAt < FRAME_INTERVAL_TOLERANCE * 1_000_000_000L / requestedFps) {
                return false
            }
            lastFrameAt = timestampNs
            return true
        }

        override fun frameEncoded(encoder: H264Encoder) {
            val started = SystemClock.elapsedRealtimeNanos()
            requestKeyFrameIfWanted(encoder)
            publishEncodedPackets(encoder.drain(), size.width, size.height, 0, started)
        }

        fun close() {
            closeReplacedEncoders()
            encoder?.close()
            encoder = null
        }
    }

    /**
     * The frame rate range to run the camera at: the steady one closest to
     * [requested] without going over it. A camera that cannot do the requested
     * rate (many phones stop at 30 fps) runs at its best steady rate instead of
     * a wide range, which lets it drop to half that in indoor light.
     */
    private fun cameraFpsRange(ranges: List<Range<Int>>, requested: Int): Range<Int>? {
        val allowed = ranges.filter { it.upper <= requested }
        return allowed.filter { it.lower == it.upper }.maxByOrNull { it.upper }
            ?: allowed.maxWithOrNull(compareBy<Range<Int>> { it.upper }.thenBy { it.lower })
            ?: ranges.minByOrNull { it.upper }
    }

    private fun cameraFpsRange(selector: CameraSelector, requested: Int): Range<Int> {
        val info = cameraProvider?.let { selector.filter(it.availableCameraInfos).firstOrNull() }
        val ranges = info?.let {
            Camera2CameraInfo.from(it)
                .getCameraCharacteristic(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)
                ?.toList()
        }.orEmpty()
        return cameraFpsRange(ranges, requested) ?: Range(requested, requested)
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
            showNotice(t("settingsFailed", error.localizedMessage))
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
            showNotice(t("manualFocusUnsupported"))
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
        val fpsRange = cameraFpsRange(fpsRanges, requestedFps)
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
                        showNotice(t("manualIsoUnsupported"))
                    }
                } catch (error: Exception) {
                    if (error.cause is CameraControl.OperationCanceledException) {
                        return@addListener
                    }
                    Log.e(TAG, "Camera2 rejected a camera setting", error)
                    showNotice(t("settingUnsupported"))
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
            // Camera frames arrive about one interval apart, a little early
            // or late; a strict limit dropped every early one, and with it
            // a third of the frames at 30 fps.
            val now = SystemClock.elapsedRealtimeNanos()
            if (now - lastEncodedFrameAt < FRAME_INTERVAL_TOLERANCE * 1_000_000_000L / requestedFps) return
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
                val encoder = ensureVideoEncoder(width, height)
                requestKeyFrameIfWanted(encoder)
                val packets = encoder.encode(image)
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
            val encoder = ensureVideoEncoder(frameWidth, frameHeight)
            requestKeyFrameIfWanted(encoder)
            val packets = encoder.encode(frameData)
            publishEncodedPackets(packets, frameWidth, frameHeight, rotationDegrees, frameStartedAt)
        } catch (error: Exception) {
            Log.e(TAG, "Could not encode camera frame", error)
            runOnUiThread { showNotice(t("frameFailed", error.localizedMessage)) }
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

    private fun requestKeyFrameIfWanted(encoder: H264Encoder) {
        if (!keyFrameWanted.get()) return
        val now = SystemClock.elapsedRealtime()
        if (now - lastKeyFrameRequestAt < KEY_FRAME_REQUEST_INTERVAL_MS) return
        keyFrameWanted.set(false)
        lastKeyFrameRequestAt = now
        encoder.requestKeyFrame()
    }

    private fun publishEncodedPackets(
        packets: List<EncodedFrame>,
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
            val wifiPublisher = publisher
            val usbServer = localServer
            publisher = null
            localServer = null
            wifiPublisher?.stop()
            usbServer?.stop()
            linkState = null
            linkDetail = null
            notice = null
            render()
            return
        }
        if (!cameraReady) return
        linkState = null
        linkDetail = null
        notice = null
        if (modeControl.value == MODE_USB) startUsbStream() else startWifiStream()
        render()
    }

    /** Apply a link event from [source]; events from a stream already replaced are ignored. */
    private fun onLinkEvent(source: Any, event: LinkEvent, detail: String?) {
        if (source !== publisher && source !== localServer) return
        when (event) {
            LinkEvent.STOPPED -> linkState = null
            LinkEvent.FAILED, LinkEvent.NOT_FOUND, LinkEvent.NO_ANSWER -> {
                linkState = event
                linkDetail = detail
                if (source === publisher) publisher = null
                if (source === localServer) localServer = null
            }
            else -> {
                linkState = event
                linkDetail = detail
            }
        }
        notice = null
        render()
    }

    private fun startUsbStream() {
        lateinit var server: UsbFrameServer
        server = UsbFrameServer(
            onStatus = { event, detail -> runOnUiThread { onLinkEvent(server, event, detail) } },
            onControl = { command ->
                runOnUiThread { applyCameraControl(command) }
            },
            onKeyFrameNeeded = { keyFrameWanted.set(true) },
        )
        localServer = server
        try {
            server.start()
        } catch (error: Exception) {
            Log.e(TAG, "Could not start USB camera stream", error)
            localServer = null
            notice = t("usbFailed", error.localizedMessage)
        }
    }

    private fun startWifiStream() {
        val host = hostInput.text.toString().trim().ifEmpty { null }
        if (host != null) {
            try {
                InetAddress.getByName(host)
            } catch (error: Exception) {
                notice = t("badHost")
                return
            }
        }
        lateinit var activePublisher: H264Publisher
        activePublisher = H264Publisher(
            host,
            onStatus = { event, detail -> runOnUiThread { onLinkEvent(activePublisher, event, detail) } },
            onControl = { command ->
                runOnUiThread { applyCameraControl(command) }
            },
            onKeyFrameNeeded = { keyFrameWanted.set(true) },
        )
        publisher = activePublisher
        try {
            activePublisher.start()
        } catch (error: Exception) {
            Log.e(TAG, "Could not start camera publisher", error)
            activePublisher.stop()
            publisher = null
            notice = t("startFailed", error.localizedMessage)
        }
    }

    companion object {
        private const val PERFORMANCE_LOG_INTERVAL = 30
        private const val KEY_FRAME_REQUEST_INTERVAL_MS = 300L
        private const val FRAME_INTERVAL_TOLERANCE = 0.75
        private const val EXTRA_FORCE_COPY = "force_copy"
        private const val EXTRA_HELD_ROTATION = "held_rotation"
        private const val DEFAULT_FPS = 30
        private const val DEFAULT_RESOLUTION = "720p"
        private const val MODE_USB = "USB"
        private const val MODE_WIFI = "Wi-Fi"
        private const val PREFERENCES = "settings"
        private const val KEY_LANGUAGE = "language"
        private const val KEY_KEEP_AWAKE = "keep_awake"
        private const val TAG = "LumaLink"
    }
}
