package com.lumalink.companion

import android.Manifest
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CaptureRequest
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import android.util.Range
import android.util.Size
import android.view.Gravity
import android.view.OrientationEventListener
import android.view.Surface
import android.view.View
import android.view.WindowManager
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
import androidx.camera.core.CameraInfo
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.UseCaseGroup
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.pow
import org.json.JSONArray
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private lateinit var previewView: PreviewView
    private lateinit var topBar: LinearLayout
    private lateinit var statusIcon: ImageView
    private lateinit var statusText: TextView
    private lateinit var settingsButton: ImageView
    private lateinit var lensTag: TextView
    private lateinit var sheet: LinearLayout
    private lateinit var hintText: TextView
    private lateinit var lensButton: LinearLayout
    private lateinit var settingsOverlay: FrameLayout
    private lateinit var settingsPanel: LinearLayout
    private lateinit var languageControl: SegmentedControl
    private lateinit var keepAwakeToggle: ToggleView
    private lateinit var settingsBack: OnBackPressedCallback
    private lateinit var computersList: LinearLayout
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
    @Volatile private var server: FrameServer? = null
    private var multicastLock: WifiManager.MulticastLock? = null
    private val allowedComputers by lazy { AllowedComputers(preferences) }

    /** The camera settings in use, as the computer sends them (see [applyCameraControl]). */
    private val cameraSettings = JSONObject(DEFAULT_SETTINGS)
    @Volatile private var settingsSnapshot = JSONObject(DEFAULT_SETTINGS)
    @Volatile private var lensesSnapshot = JSONObject()

    /** How zoom maps onto this side's camera modules, and the module bound now. */
    private var zoomPlan: ZoomPlan? = null
    private var boundModule: CameraModule? = null
    private var videoEncoder: H264Encoder? = null
    private var gpuCameraPipeline: GpuCameraPipeline? = null
    private var gpuFailed = false

    /**
     * A photo the computer asked for (docs/protocol.md, "Photos"): the camera
     * gets a photo output only while one is being taken, so the video keeps
     * its frame rate otherwise.
     */
    private var photoWanted = false
    private var lastPhotoRequest = 0L
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

    // Frames sent per second, counted on the camera threads and shown under
    // the picture while streaming, so a missing 60 fps is visible on the phone.
    private var rateWindowStartedAt = 0L
    private var rateWindowFrames = 0
    @Volatile private var deliveredFps = 0
    private var lastRateLogAt = 0L

    /** A camera problem that keeps the phone from streaming at all. */
    private var cameraProblem: String? = null

    /** The computer streaming now, or null while the phone waits. */
    private var connected: Computer? = null
    private var serverProblem: String? = null
    private var oldStudioSeenAt = 0L
    private var askDialog: AlertDialog? = null
    private var askingId: String? = null

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
        // adb shell am start -n com.lumalink.companion.test/com.lumalink.companion.MainActivity --ez force_copy true
        gpuFailed = intent.getBooleanExtra(EXTRA_FORCE_COPY, false)
        // For checking every orientation on a phone lying flat, where the
        // sensor keeps it: --ei held_rotation 0..3 (a Surface.ROTATION_*).
        heldRotation = intent.getIntExtra(EXTRA_HELD_ROTATION, heldRotation).takeIf { it in 0..3 } ?: heldRotation
        language = preferences.getString(KEY_LANGUAGE, "ru")?.takeIf { it in Strings.languages } ?: "ru"
        // Test builds are a separate app and never take a release over them.
        if (BuildConfig.UPDATES) {
            updater = AppUpdater(this, installedVersion() ?: "0") { state ->
                if (state is UpdateState.NeedsPermission) askToInstallUpdates()
                render()
            }
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

    override fun onStart() {
        super.onStart()
        startServer()
    }

    override fun onStop() {
        stopServer()
        super.onStop()
    }

    override fun onResume() {
        super.onResume()
        if (orientationListener.canDetectOrientation()) orientationListener.enable()
        // Back from Settings with the permission: install what was downloaded.
        val waiting = updater?.state as? UpdateState.NeedsPermission
        if (waiting != null && packageManager.canRequestPackageInstalls() && !streaming()) {
            updater?.install(waiting.release)
        }
    }

    /** Open this app's "Install unknown apps" switch in Settings. */
    private fun askToInstallUpdates() {
        try {
            startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")),
            )
        } catch (error: Exception) {
            Log.w(TAG, "Could not open the install permission screen", error)
        }
    }

    override fun onPause() {
        orientationListener.disable()
        super.onPause()
    }

    override fun onDestroy() {
        updater?.close()
        stopServer()
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

        settingsPanel.addView(groupTitle("computers"), matchWidth(14))
        computersList = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        settingsPanel.addView(computersList, matchWidth(6))

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
                text = "${getString(R.string.app_name)} ${installedVersion() ?: ""}".trim()
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

    private fun streaming(): Boolean = server?.streaming == true

    /** The release the update button would install, if any. */
    private fun offeredRelease(): AppRelease? =
        when (val state = updater?.state) {
            is UpdateState.Available -> state.release
            is UpdateState.Failed -> state.release
            is UpdateState.NeedsPermission -> state.release
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
            is UpdateState.NeedsPermission -> t("updatePermission") to t("updateAllow")
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
            state is UpdateState.NeedsPermission ||
            state is UpdateState.Installing || (state is UpdateState.Failed && state.release != null)
        updateBanner.visibility = if (news && !streaming) View.VISIBLE else View.GONE
        updateBannerText.text = text
        updateBannerButton.text = action ?: ""
        updateBannerButton.visibility = updateButton.visibility
    }

    /** The computers allowed over Wi-Fi, each with a button to forget it. */
    private fun renderComputers() {
        computersList.removeAllViews()
        val computers = allowedComputers.all()
        if (computers.isEmpty()) {
            computersList.addView(label(13f, Palette.TEXT_SECONDARY).apply { text = t("computersNone") }, matchWidth())
            return
        }
        for (computer in computers) {
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                minimumHeight = dp(36)
            }
            row.addView(
                label(13f).apply { text = computer.name },
                LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f),
            )
            val forget = smallButton {
                allowedComputers.forget(computer.id)
                renderComputers()
            }.apply { text = t("forget") }
            row.addView(forget)
            computersList.addView(row, matchWidth())
        }
    }

    private fun openSettings() {
        renderComputers()
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

        val problem = cameraProblem
        val computer = connected
        val (icon, status) = when {
            problem != null -> R.drawable.ic_status_error to problem
            !cameraReady -> R.drawable.ic_status_wait to t("startingCamera")
            computer != null -> R.drawable.ic_status_live to t(
                "liveTo",
                computer.name ?: t("computer"),
                t(if (computer.link == Link.USB) "linkUsb" else "linkWifi"),
            )
            serverProblem != null -> R.drawable.ic_status_error to serverProblem!!
            else -> R.drawable.ic_status_wait to t("waiting")
        }
        statusIcon.setImageResource(icon)
        // Connected shows in the logo's accent; every other state in the text colour.
        statusIcon.setColorFilter(if (computer != null && problem == null && cameraReady) Palette.ACCENT else Palette.TEXT)
        statusText.text = status

        val oldStudio = SystemClock.elapsedRealtime() - oldStudioSeenAt < OLD_STUDIO_HINT_MS && oldStudioSeenAt > 0
        hintText.text = notice ?: when {
            computer != null -> t("hintLive")
            oldStudio -> t("oldStudio")
            else -> t("hintWaiting")
        }
        val lens = t(if (lensFacing == CameraSelector.LENS_FACING_FRONT) "front" else "rear")
        lensTag.text = if (computer != null && deliveredFps > 0) t("lensRate", lens, deliveredFps) else lens

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
        updateLenses(provider)
        val plan = CameraModules.plan(provider.availableCameraInfos, lensFacing, analysisSize(requestedResolution))
        if (plan == null) {
            cameraProblem = t("cameraUnavailable")
            render()
            return
        }
        val zoom = cameraSettings.optDouble("zoom", 1.0).toFloat()
        val current = boundModule?.takeIf { it == plan.main || it == plan.ultraWide || it == plan.telephoto }
        val module = plan.moduleFor(zoom, current ?: plan.main)
        zoomPlan = plan
        boundModule = module
        val selector = CameraModules.selector(lensFacing, module)

        val fpsRange = cameraFpsRange(selector, requestedFps)
        val rates = selector.filter(provider.availableCameraInfos).firstOrNull()
            ?.let { CameraModules.frameRates(it, analysisSize(requestedResolution)) }
        Log.i(
            TAG,
            "Camera ${module.id}: range $fpsRange for $requestedFps fps at $requestedResolution; " +
                "steady rates there: $rates",
        )
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
        val capture = if (photoWanted) {
            ImageCapture.Builder()
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
                .setTargetRotation(heldRotation)
                .build()
        } else {
            null
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
                    .apply { capture?.let { addUseCase(it) } }
                    .addEffect(gpu.effect)
                    .build()
                provider.bindToLifecycle(this, selector, group)
            } else {
                val info = selector.filter(provider.availableCameraInfos).firstOrNull()
                val useCases = listOfNotNull(preview, copyingAnalysis(fpsRange, info), capture)
                provider.bindToLifecycle(this, selector, *useCases.toTypedArray())
            }
            cameraReady = true
            applyCameraSettings(cameraSettings)
            cameraProblem = null
            render()
            // Let focus and exposure settle on the new session before the shot,
            // or some phones take it dark or blurred.
            capture?.let { previewView.postDelayed({ if (photoWanted) takePhoto(it) }, PHOTO_SETTLE_MS) }
        } catch (error: Exception) {
            if (capture != null) {
                // This camera cannot add a photo output: say so, and go back to video only.
                Log.w(TAG, "Could not add the photo output", error)
                finishPhoto(ByteArray(0))
                return
            }
            if (gpu != null) {
                fallBackToCopying(error)
                return
            }
            cameraProblem = t("cameraStartFailed", error.localizedMessage)
            render()
        }
    }

    private fun takePhoto(capture: ImageCapture) {
        capture.takePicture(
            cameraExecutor,
            object : ImageCapture.OnImageCapturedCallback() {
                override fun onCaptureSuccess(image: ImageProxy) {
                    val jpeg = try {
                        val buffer = image.planes[0].buffer
                        ByteArray(buffer.remaining()).also { buffer.get(it) }
                    } catch (error: Exception) {
                        Log.w(TAG, "Could not read the photo", error)
                        ByteArray(0)
                    } finally {
                        image.close()
                    }
                    runOnUiThread { finishPhoto(jpeg) }
                }

                override fun onError(error: ImageCaptureException) {
                    Log.w(TAG, "Could not take the photo", error)
                    runOnUiThread { finishPhoto(ByteArray(0)) }
                }
            },
        )
    }

    /** Hand [jpeg] (empty if it failed) to the computer and go back to video only. */
    private fun finishPhoto(jpeg: ByteArray) {
        server?.publishPhoto(jpeg)
        photoWanted = false
        bindCamera()
    }

    /** The CPU path: camera frames copied into the encoder (see [analyzeFrame]). */
    private fun copyingAnalysis(fpsRange: Range<Int>, info: CameraInfo?): ImageAnalysis {
        val target = HeldRotation.streamTarget(heldRotation)
        // CameraX reads the resolution in the target rotation's orientation:
        // given landscape for a portrait stream, it picked a 4:3 size instead.
        val size = analysisSize(requestedResolution)
        val portrait = (info?.getSensorRotationDegrees(target) ?: 0) % 180 != 0
        val analysisBuilder = ImageAnalysis.Builder()
            .setTargetResolution(if (portrait) Size(size.height, size.width) else size)
            .setTargetRotation(target)
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
            if (server?.streaming != true) return null
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
            val photo = command.optLong("photo", 0L)
            command.remove("photo")
            if (photo > lastPhotoRequest) {
                lastPhotoRequest = photo
                if (!photoWanted) {
                    photoWanted = true
                    previewView.post { bindCamera() }
                }
            }
            for (key in command.keys()) cameraSettings.put(key, command.get(key))
            settingsSnapshot = JSONObject(cameraSettings.toString())
            val resolution = command.optString("resolution", requestedResolution)
                .takeIf { it in RESOLUTIONS }
                ?: requestedResolution
            val fps = command.optInt("fps", requestedFps).coerceIn(15, 60)
            val facing = if (command.optString("lens", "Задняя") == "Фронтальная") {
                CameraSelector.LENS_FACING_FRONT
            } else {
                CameraSelector.LENS_FACING_BACK
            }
            var needsRebind =
                resolution != requestedResolution || fps != requestedFps || facing != lensFacing
            if (facing != lensFacing) boundModule = null
            requestedResolution = resolution
            requestedFps = fps
            lensFacing = facing
            val plan = zoomPlan
            val bound = boundModule
            if (!needsRebind && plan != null && bound != null) {
                val zoom = cameraSettings.optDouble("zoom", 1.0).toFloat()
                // Crossing into another module's range rebinds the camera.
                needsRebind = plan.moduleFor(zoom, bound) != bound
            }
            if (needsRebind) bindCamera() else applyCameraSettings(cameraSettings)
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

        val plan = zoomPlan
        val module = boundModule
        if (plan != null && module != null) {
            val requestedZoom = command.optDouble("zoom", 1.0).toFloat()
            camera.cameraControl.setZoomRatio(plan.moduleZoom(requestedZoom, module))
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
        val front = lensFacing == CameraSelector.LENS_FACING_BACK
        applyCameraControl(JSONObject().put("lens", if (front) "Фронтальная" else "Задняя").put("zoom", 1.0))
    }

    /** Each lens's zoom range, for discovery and the handshake. */
    /** Keep [deliveredFps] up to date; log it every few seconds against what was asked. */
    private fun countDeliveredFrame(now: Long) {
        rateWindowFrames += 1
        if (rateWindowStartedAt == 0L) rateWindowStartedAt = now
        val elapsed = now - rateWindowStartedAt
        if (elapsed < 1_000_000_000L) return
        val rate = Math.round(rateWindowFrames * 1_000_000_000.0 / elapsed).toInt()
        rateWindowFrames = 0
        rateWindowStartedAt = now
        if (now - lastRateLogAt >= RATE_LOG_INTERVAL_NS) {
            lastRateLogAt = now
            Log.i(TAG, "Sending $rate fps (asked for $requestedFps at $requestedResolution)")
        }
        if (rate != deliveredFps) {
            deliveredFps = rate
            runOnUiThread { render() }
        }
    }

    private fun updateLenses(provider: ProcessCameraProvider) {
        val lenses = JSONObject()
        val size = analysisSize(requestedResolution)
        for ((token, facing) in listOf(
            "Задняя" to CameraSelector.LENS_FACING_BACK,
            "Фронтальная" to CameraSelector.LENS_FACING_FRONT,
        )) {
            val range = CameraModules.plan(provider.availableCameraInfos, facing, size)?.range ?: continue
            val main = provider.availableCameraInfos.first { it.lensFacing == facing }
            lenses.put(
                token,
                JSONObject()
                    .put("zoom", JSONArray().put(round2(range.start)).put(round2(range.endInclusive)))
                    // Every resolution at once: discovery and "ok" are not sent
                    // again when the computer switches resolution.
                    .put("fps", JSONObject().apply {
                        for (resolution in RESOLUTIONS) {
                            put(resolution, JSONArray(CameraModules.frameRates(main, analysisSize(resolution))))
                        }
                    }),
            )
        }
        lensesSnapshot = lenses
    }

    private fun round2(value: Float): Double = Math.round(value * 100.0) / 100.0

    private fun analyzeFrame(image: ImageProxy) {
        try {
            if (server?.streaming != true) return
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
            // Crop and turn each plane on its own, a row at a time, rather
            // than building an NV21 frame a sample at a time (see Yuv420).
            val chromaWidth = width / 2
            val chromaHeight = height / 2
            val y = Yuv420.crop(PlaneView.of(planes[0]), left, top, width, height)
            val u = Yuv420.crop(PlaneView.of(planes[1]), left / 2, top / 2, chromaWidth, chromaHeight)
            val v = Yuv420.crop(PlaneView.of(planes[2]), left / 2, top / 2, chromaWidth, chromaHeight)
            val quarterTurn = rotationDegrees % 180 != 0
            val frameWidth = if (quarterTurn) height else width
            val frameHeight = if (quarterTurn) width else height
            val encoder = ensureVideoEncoder(frameWidth, frameHeight)
            requestKeyFrameIfWanted(encoder)
            val packets = encoder.encode(
                Yuv420.turn(y, width, height, rotationDegrees),
                Yuv420.turn(u, chromaWidth, chromaHeight, rotationDegrees),
                Yuv420.turn(v, chromaWidth, chromaHeight, rotationDegrees),
            )
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
            server?.publishFrame(packet)
        }
        measuredFrameCount += 1
        countDeliveredFrame(frameStartedAt)
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

    /** Wait for computers on every network the phone may use (docs/protocol.md, version 2). */
    private fun startServer() {
        if (server != null) return
        val next = FrameServer(allowedComputers, phoneDescription, serverEvents, secureChannel) { keyFrameWanted.set(true) }
        try {
            next.start()
            server = next
            serverProblem = null
            multicastLock = (applicationContext.getSystemService(WIFI_SERVICE) as? WifiManager)
                ?.createMulticastLock("LumaLink discovery")
                ?.apply {
                    setReferenceCounted(false)
                    acquire()
                }
        } catch (error: Exception) {
            Log.e(TAG, "Could not wait for computers", error)
            serverProblem = t("serverFailed", error.localizedMessage)
        }
        render()
    }

    private fun stopServer() {
        val running = server ?: return
        server = null
        running.stop()
        multicastLock?.release()
        multicastLock = null
        askDialog?.dismiss()
        askDialog = null
        connected = null
    }

    /** The phone's TLS key for Wi-Fi, made once; null where the Keystore cannot. */
    private val secureChannel: SecureChannel? by lazy { SecureChannel.load() }

    private val phoneDescription = object : FrameServer.Phone {
        override val id: String by lazy { PhoneId.get(preferences) }
        override val name: String by lazy {
            Settings.Global.getString(contentResolver, Settings.Global.DEVICE_NAME)?.takeIf { it.isNotBlank() }
                ?: Build.MODEL
        }
        override val version: String by lazy { installedVersion() ?: "0" }

        override fun lenses(): JSONObject = lensesSnapshot

        override fun settings(): JSONObject = settingsSnapshot
    }

    private val serverEvents = object : FrameServer.Events {
        override fun streaming(computer: Computer?) {
            runOnUiThread {
                connected = computer
                lastPhotoRequest = 0L // photo numbers count per connection
                if (computer == null) {
                    deliveredFps = 0
                    rateWindowStartedAt = 0L
                    rateWindowFrames = 0
                }
                notice = null
                render()
            }
        }

        override fun askToAllow(id: String, name: String, code: String?, answer: (Boolean) -> Unit) {
            runOnUiThread {
                askDialog?.dismiss()
                askingId = id
                val message = t("allowMessage", name) + if (code != null) "\n\n" + t("allowCode", code) else ""
                askDialog = AlertDialog.Builder(this@MainActivity)
                    .setTitle(t("allowTitle"))
                    .setMessage(message)
                    .setPositiveButton(t("allow")) { _, _ -> answer(true) }
                    .setNegativeButton(t("deny")) { _, _ -> answer(false) }
                    .setOnCancelListener { answer(false) }
                    .show()
            }
        }

        override fun askingCancelled(id: String) {
            runOnUiThread {
                if (askingId == id) {
                    askDialog?.dismiss()
                    askDialog = null
                }
            }
        }

        override fun oldStudioFound() {
            runOnUiThread {
                oldStudioSeenAt = SystemClock.elapsedRealtime()
                render()
            }
        }

        override fun control(command: JSONObject) {
            runOnUiThread { applyCameraControl(command) }
        }
    }

    companion object {
        private const val PERFORMANCE_LOG_INTERVAL = 30
        private const val KEY_FRAME_REQUEST_INTERVAL_MS = 300L
        private const val PHOTO_SETTLE_MS = 500L
        private const val FRAME_INTERVAL_TOLERANCE = 0.75
        private const val EXTRA_FORCE_COPY = "force_copy"
        private const val EXTRA_HELD_ROTATION = "held_rotation"
        private const val DEFAULT_FPS = 30
        private const val DEFAULT_RESOLUTION = "720p"
        private const val OLD_STUDIO_HINT_MS = 15_000L
        private const val RATE_LOG_INTERVAL_NS = 5_000_000_000L
        private val RESOLUTIONS = listOf("480p", "720p", "1080p")
        private val DEFAULT_SETTINGS = JSONObject()
            .put("autofocus", true)
            .put("focus", 0.5)
            .put("exposure", 0.0)
            .put("iso", "Авто")
            .put("whiteBalance", "Авто")
            .put("lens", "Задняя")
            .put("zoom", 1.0)
            .put("fps", DEFAULT_FPS)
            .put("resolution", DEFAULT_RESOLUTION)
            .toString()
        private const val PREFERENCES = "settings"
        private const val KEY_LANGUAGE = "language"
        private const val KEY_KEEP_AWAKE = "keep_awake"
        private const val TAG = "LumaLink"
    }
}
