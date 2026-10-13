package com.lumalink.companion

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.media.Image
import android.os.Build
import android.util.Log
import androidx.camera.core.ImageProxy
import java.nio.ByteBuffer

internal class H264Encoder(
    private val width: Int,
    private val height: Int,
    private val fps: Int,
) {
    private val codec: MediaCodec
    private var codecConfig = ByteArray(0)
    private var lastPresentationTimeUs = 0L

    init {
        require(width > 0 && height > 0 && width % 2 == 0 && height % 2 == 0) {
            "Для H.264 требуются чётные размеры кадра"
        }
        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height)
            .apply {
                setInteger(
                    MediaFormat.KEY_COLOR_FORMAT,
                    MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible,
                )
                setInteger(MediaFormat.KEY_BIT_RATE, bitrateFor(width, height))
                setInteger(MediaFormat.KEY_FRAME_RATE, fps)
                setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    setInteger(MediaFormat.KEY_MAX_B_FRAMES, 0)
                }
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    setInteger("low-latency", 1)
                }
            }
        val codecName = MediaCodecList(MediaCodecList.REGULAR_CODECS)
            .findEncoderForFormat(format)
            ?: throw IllegalStateException("В телефоне нет H.264-кодировщика для ${width}x$height")
        codec = MediaCodec.createByCodecName(codecName)
        codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        codec.start()
        Log.i(TAG, "Using H.264 encoder $codecName for ${width}x$height at $fps FPS")
    }

    fun encode(nv21: ByteArray): List<ByteArray> {
        require(nv21.size == width * height * 3 / 2) {
            "Неверный размер входного кадра H.264"
        }
        queueInput { inputImage ->
            copyNv21ToImage(nv21, inputImage.planes)
        }
        return drainOutput()
    }

    fun encode(image: ImageProxy): List<ByteArray> {
        require(image.width == width && image.height == height) {
            "Размер кадра камеры не совпадает с конфигурацией H.264"
        }
        queueInput { inputImage ->
            copyCameraPlanes(image.planes, inputImage.planes)
        }
        return drainOutput()
    }

    private fun queueInput(fillInput: (Image) -> Unit) {
        val inputIndex = codec.dequeueInputBuffer(INPUT_TIMEOUT_US)
        if (inputIndex >= 0) {
            val image = codec.getInputImage(inputIndex)
                ?: throw IllegalStateException("H.264-кодировщик не выдал YUV-вход")
            try {
                fillInput(image)
            } finally {
                image.close()
            }
            val presentationTimeUs =
                maxOf(System.nanoTime() / 1_000L, lastPresentationTimeUs + 1L)
            lastPresentationTimeUs = presentationTimeUs
            codec.queueInputBuffer(
                inputIndex,
                0,
                width * height * 3 / 2,
                presentationTimeUs,
                0,
            )
        }
    }

    private fun drainOutput(): List<ByteArray> {
        val packets = mutableListOf<ByteArray>()
        val info = MediaCodec.BufferInfo()
        while (true) {
            val outputIndex = codec.dequeueOutputBuffer(info, OUTPUT_TIMEOUT_US)
            when {
                outputIndex == MediaCodec.INFO_TRY_AGAIN_LATER -> break
                outputIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                    codecConfig = codecSpecificData(codec.outputFormat)
                }
                outputIndex >= 0 -> {
                    try {
                        val output = codec.getOutputBuffer(outputIndex)
                        if (output != null && info.size > 0) {
                            output.position(info.offset)
                            output.limit(info.offset + info.size)
                            val bytes = ByteArray(info.size)
                            output.get(bytes)
                            if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) {
                                codecConfig += bytes
                            } else {
                                packets += if (
                                    info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0 &&
                                    codecConfig.isNotEmpty()
                                ) {
                                    codecConfig + bytes
                                } else {
                                    bytes
                                }
                            }
                        }
                    } finally {
                        codec.releaseOutputBuffer(outputIndex, false)
                    }
                }
            }
        }
        return packets
    }

    fun requestKeyFrame() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            codec.setParameters(android.os.Bundle().apply {
                putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0)
            })
        }
    }

    fun matches(requestedWidth: Int, requestedHeight: Int, requestedFps: Int): Boolean =
        width == requestedWidth && height == requestedHeight && fps == requestedFps

    fun close() {
        try {
            codec.stop()
        } finally {
            codec.release()
        }
    }

    private fun copyNv21ToImage(
        source: ByteArray,
        planes: Array<Image.Plane>,
    ) {
        require(planes.size >= 3) { "H.264-кодировщик выдал неполный YUV-кадр" }
        val yPlane = planes[0]
        for (y in 0 until height) {
            val row = y * yPlane.rowStride
            for (x in 0 until width) {
                yPlane.buffer.put(row + x * yPlane.pixelStride, source[y * width + x])
            }
        }
        val chromaOffset = width * height
        val uPlane = planes[1]
        val vPlane = planes[2]
        for (y in 0 until height / 2) {
            for (x in 0 until width / 2) {
                val sourceIndex = chromaOffset + (y * width + x * 2)
                uPlane.buffer.put(
                    y * uPlane.rowStride + x * uPlane.pixelStride,
                    source[sourceIndex + 1],
                )
                vPlane.buffer.put(
                    y * vPlane.rowStride + x * vPlane.pixelStride,
                    source[sourceIndex],
                )
            }
        }
    }

    private fun copyCameraPlanes(
        source: Array<ImageProxy.PlaneProxy>,
        destination: Array<Image.Plane>,
    ) {
        require(source.size >= 3 && destination.size >= 3) {
            "Камера или H.264-кодировщик выдал неполный YUV-кадр"
        }
        val sourceViews = source.map {
            PlaneView(it.buffer, it.rowStride, it.pixelStride)
        }
        val destinationViews = destination.map {
            PlaneView(it.buffer, it.rowStride, it.pixelStride)
        }

        copyPlane(sourceViews[0], destinationViews[0], width, height)
        if (
            sourceViews[1].pixelStride == 2 &&
            sourceViews[2].pixelStride == 2 &&
            destinationViews[1].pixelStride == 2 &&
            destinationViews[2].pixelStride == 2
        ) {
            if (!copyInterleavedChroma(sourceViews[2], destinationViews[2])) {
                copyPlane(sourceViews[1], destinationViews[1], width / 2, height / 2)
                copyPlane(sourceViews[2], destinationViews[2], width / 2, height / 2)
            }
        } else {
            copyPlane(sourceViews[1], destinationViews[1], width / 2, height / 2)
            copyPlane(sourceViews[2], destinationViews[2], width / 2, height / 2)
        }
    }

    private fun copyInterleavedChroma(
        source: PlaneView,
        destination: PlaneView,
    ): Boolean {
        if (source.rowStride < width || destination.rowStride < width) return false
        val sourceBuffer = source.buffer.duplicate()
        val destinationBuffer = destination.buffer.duplicate()
        val sourceStart = source.buffer.position()
        val destinationStart = destination.buffer.position()
        for (row in 0 until height / 2) {
            val sourceRow = sourceStart + row * source.rowStride
            val destinationRow = destinationStart + row * destination.rowStride
            if (
                sourceRow + width > sourceBuffer.limit() ||
                destinationRow + width > destinationBuffer.limit()
            ) {
                return false
            }
            sourceBuffer.position(sourceRow)
            sourceBuffer.limit(sourceRow + width)
            destinationBuffer.position(destinationRow)
            destinationBuffer.put(sourceBuffer)
            sourceBuffer.limit(source.buffer.limit())
        }
        return true
    }

    private fun copyPlane(
        source: PlaneView,
        destination: PlaneView,
        rowBytes: Int,
        rows: Int,
    ) {
        val sourceBuffer = source.buffer.duplicate()
        val destinationBuffer = destination.buffer.duplicate()
        val sourceStart = source.buffer.position()
        val destinationStart = destination.buffer.position()
        if (source.pixelStride == 1 && destination.pixelStride == 1) {
            for (row in 0 until rows) {
                sourceBuffer.position(sourceStart + row * source.rowStride)
                destinationBuffer.position(destinationStart + row * destination.rowStride)
                sourceBuffer.limit(sourceBuffer.position() + rowBytes)
                destinationBuffer.put(sourceBuffer)
                sourceBuffer.limit(source.buffer.limit())
            }
            return
        }
        for (row in 0 until rows) {
            for (column in 0 until rowBytes) {
                destinationBuffer.put(
                    destinationStart + row * destination.rowStride +
                        column * destination.pixelStride,
                    sourceBuffer.get(
                        sourceStart + row * source.rowStride +
                            column * source.pixelStride,
                    ),
                )
            }
        }
    }

    private fun codecSpecificData(format: MediaFormat): ByteArray {
        val csd = mutableListOf<ByteArray>()
        for (index in 0..1) {
            val buffer: ByteBuffer = format.getByteBuffer("csd-$index") ?: continue
            val copy = buffer.duplicate()
            val bytes = ByteArray(copy.remaining())
            copy.get(bytes)
            csd += bytes
        }
        return csd.fold(ByteArray(0)) { result, bytes -> result + bytes }
    }

    private fun bitrateFor(width: Int, height: Int): Int =
        (width * height * fps * BITS_PER_PIXEL_PER_FRAME).toInt()
            .coerceIn(MIN_BITRATE, MAX_BITRATE)

    private data class PlaneView(
        val buffer: ByteBuffer,
        val rowStride: Int,
        val pixelStride: Int,
    )

    companion object {
        private const val INPUT_TIMEOUT_US = 10_000L
        private const val OUTPUT_TIMEOUT_US = 0L
        private const val BITS_PER_PIXEL_PER_FRAME = 0.08
        private const val MIN_BITRATE = 800_000
        private const val MAX_BITRATE = 8_000_000
        private const val TAG = "LumaLinkH264"
    }
}
