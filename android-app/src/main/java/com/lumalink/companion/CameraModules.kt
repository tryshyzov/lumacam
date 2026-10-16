package com.lumalink.companion

import android.graphics.ImageFormat
import android.graphics.SurfaceTexture
import android.hardware.camera2.CameraCharacteristics
import android.util.Size
import androidx.annotation.OptIn
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.CameraInfo
import androidx.camera.core.CameraSelector

/**
 * One camera module the app may open, as seen from the main camera of its
 * side: [ratio] is how much narrower its view is (main = 1, an ultra-wide
 * below 1, a telephoto above), [zoom] its own zoom range.
 */
internal data class CameraModule(
    val id: String,
    val ratio: Float,
    val zoom: ClosedFloatingPointRange<Float>,
)

/**
 * The modules of one side of the phone and how a requested zoom, relative to
 * the main camera, maps onto them (docs/protocol.md, "How the Android app
 * picks modules").
 */
internal class ZoomPlan(
    val main: CameraModule,
    val ultraWide: CameraModule? = null,
    val telephoto: CameraModule? = null,
) {
    /** The zoom range to offer, as ratios to the main camera. */
    val range: ClosedFloatingPointRange<Float>
        get() {
            val low = ultraWide?.let { it.ratio * it.zoom.start } ?: minOf(main.zoom.start, 1f)
            val high = telephoto?.let { it.ratio * it.zoom.endInclusive } ?: main.zoom.endInclusive
            return low..high
        }

    /** The module for [zoom] when [current] is bound, switching only past the hysteresis band. */
    fun moduleFor(zoom: Float, current: CameraModule): CameraModule {
        val wide = ultraWide
        val tele = telephoto
        return when {
            wide != null && current == wide -> if (zoom >= 1f + HYSTERESIS) main else wide
            tele != null && current == tele -> if (zoom <= tele.ratio * (1f - HYSTERESIS)) main else tele
            wide != null && zoom <= 1f - HYSTERESIS -> wide
            tele != null && zoom >= tele.ratio * (1f + HYSTERESIS) -> tele
            else -> main
        }
    }

    /** The zoom to set on [module] to show [zoom] relative to the main camera. */
    fun moduleZoom(zoom: Float, module: CameraModule): Float =
        (zoom / module.ratio).coerceIn(module.zoom.start, module.zoom.endInclusive)

    companion object {
        const val HYSTERESIS = 0.05f
        const val ULTRA_WIDE_MAX_RATIO = 0.8f
        const val TELEPHOTO_MIN_RATIO = 1.6f

        /**
         * Pick the ultra-wide and the telephoto among [others] by their
         * ratio; modules that cannot deliver the requested resolution (depth
         * and macro modules) never qualify.
         */
        fun of(main: CameraModule, others: List<Pair<CameraModule, Boolean>>): ZoomPlan {
            val usable = others.filter { (_, delivers) -> delivers }.map { it.first }
            return ZoomPlan(
                main,
                ultraWide = usable.filter { it.ratio <= ULTRA_WIDE_MAX_RATIO }.minByOrNull { it.ratio },
                telephoto = usable.filter { it.ratio >= TELEPHOTO_MIN_RATIO }.minByOrNull { it.ratio },
            )
        }
    }
}

/** Reads the modules CameraX may open on this phone. */
@OptIn(ExperimentalCamera2Interop::class)
internal object CameraModules {
    /**
     * The zoom plan of the side [lensFacing], or null if it has no camera.
     * Phones whose main camera already zooms below 1 (a logical
     * multi-camera) switch modules themselves; then the plan has the main
     * camera only.
     */
    fun plan(cameras: List<CameraInfo>, lensFacing: Int, resolution: Size): ZoomPlan? {
        val side = cameras.filter { it.lensFacing == lensFacing }
        val main = side.firstOrNull() ?: return null
        val mainModule = module(main, 1f)
        if (mainModule.zoom.start < 1f) return ZoomPlan(mainModule)
        val mainView = fieldOfView(main) ?: return ZoomPlan(mainModule)
        val others = side.drop(1).mapNotNull { info ->
            val view = fieldOfView(info) ?: return@mapNotNull null
            module(info, mainView / view) to delivers(info, resolution)
        }
        return ZoomPlan.of(mainModule, others)
    }

    /**
     * The steady rates among 30 and 60 fps that [info] delivers at
     * [resolution] (docs/protocol.md, `lenses.<lens>.fps`): the camera must
     * offer a range up to that rate, and a stream at least that large must
     * be fast enough for it. Phones often list 60 only for smaller sizes.
     */
    fun frameRates(info: CameraInfo, resolution: Size): List<Int> {
        val camera = Camera2CameraInfo.from(info)
        val ranges = camera.getCameraCharacteristic(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)
            .orEmpty()
        val map = camera.getCameraCharacteristic(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
        val longSide = maxOf(resolution.width, resolution.height)
        val shortSide = minOf(resolution.width, resolution.height)
        val sizes = map?.getOutputSizes(SurfaceTexture::class.java).orEmpty().filter {
            maxOf(it.width, it.height) >= longSide && minOf(it.width, it.height) >= shortSide
        }
        return listOf(30, 60).filter { rate ->
            ranges.any { it.upper == rate } && (
                map == null || sizes.any { size ->
                    val duration = map.getOutputMinFrameDuration(SurfaceTexture::class.java, size)
                    duration == 0L || duration <= 1_000_000_000L / rate + FRAME_DURATION_SLACK_NS
                }
            )
        }.ifEmpty { listOf(30) }
    }

    // Cameras round frame durations; 16.67 ms listed as 16.7 still makes 60 fps.
    private const val FRAME_DURATION_SLACK_NS = 200_000L

    /** A selector for exactly [module]. */
    fun selector(lensFacing: Int, module: CameraModule): CameraSelector =
        CameraSelector.Builder()
            .requireLensFacing(lensFacing)
            .addCameraFilter { infos -> infos.filter { Camera2CameraInfo.from(it).cameraId == module.id } }
            .build()

    private fun module(info: CameraInfo, ratio: Float): CameraModule {
        val zoom = info.zoomState.value
        return CameraModule(
            Camera2CameraInfo.from(info).cameraId,
            ratio,
            (zoom?.minZoomRatio ?: 1f)..(zoom?.maxZoomRatio ?: 1f),
        )
    }

    /** Sensor width over focal length: larger is wider. */
    private fun fieldOfView(info: CameraInfo): Float? {
        val camera = Camera2CameraInfo.from(info)
        val width = camera.getCameraCharacteristic(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE)?.width
        val focal = camera.getCameraCharacteristic(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)
            ?.minOrNull()
        return if (width != null && focal != null && focal > 0f) width / focal else null
    }

    private fun delivers(info: CameraInfo, resolution: Size): Boolean {
        val map = Camera2CameraInfo.from(info)
            .getCameraCharacteristic(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP) ?: return false
        val wanted = maxOf(resolution.width, resolution.height) to minOf(resolution.width, resolution.height)
        return map.getOutputSizes(ImageFormat.YUV_420_888).orEmpty().any { size ->
            maxOf(size.width, size.height) >= wanted.first && minOf(size.width, size.height) >= wanted.second
        }
    }
}
