package com.lumalink.companion

import android.media.Image
import androidx.camera.core.ImageProxy
import java.nio.ByteBuffer

/** One plane of a YUV 4:2:0 frame: where it starts, its row and sample spacing. */
internal data class PlaneView(
    val buffer: ByteBuffer,
    val rowStride: Int,
    val pixelStride: Int,
) {
    companion object {
        fun of(plane: Image.Plane) = PlaneView(plane.buffer, plane.rowStride, plane.pixelStride)

        fun of(plane: ImageProxy.PlaneProxy) = PlaneView(plane.buffer, plane.rowStride, plane.pixelStride)

        /** A tightly packed plane, [width] samples a row. */
        fun packed(samples: ByteArray, width: Int) = PlaneView(ByteBuffer.wrap(samples), width, 1)
    }
}

/**
 * Copy [rows] rows of [rowBytes] samples from [source] to [destination],
 * starting at each buffer's position.
 */
internal fun copyPlane(
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
    // Interleaved chroma (U and V sharing memory, pixel stride 2): reading
    // and writing a sample at a time through the buffers cost about 25 ns
    // per sample, most of a frame's time at 1080p. Move whole rows instead
    // and place the samples in arrays. The destination row is read first,
    // so the other plane's samples between ours survive whatever the U/V
    // order, and the span stops at the last sample, which the plane's
    // buffer may end right after.
    val sourceSpan = (rowBytes - 1) * source.pixelStride + 1
    val destinationSpan = (rowBytes - 1) * destination.pixelStride + 1
    val sourceRow = ByteArray(sourceSpan)
    val destinationRow = ByteArray(destinationSpan)
    for (row in 0 until rows) {
        sourceBuffer.position(sourceStart + row * source.rowStride)
        sourceBuffer.get(sourceRow)
        val destinationOffset = destinationStart + row * destination.rowStride
        destinationBuffer.position(destinationOffset)
        destinationBuffer.get(destinationRow)
        for (column in 0 until rowBytes) {
            destinationRow[column * destination.pixelStride] = sourceRow[column * source.pixelStride]
        }
        destinationBuffer.position(destinationOffset)
        destinationBuffer.put(destinationRow)
    }
}

/**
 * Turning planes on the CPU, for the copying path.
 *
 * Since the video follows the phone, three of four ways to hold it need a
 * turn. Building an NV21 frame and writing it into the encoder a sample at a
 * time held 1080p to 10 fps; the planes are now copied a row at a time and
 * turned here with the turn decided once.
 */
internal object Yuv420 {
    /** [width] x [height] samples of [plane] from column [left], row [top], tightly packed. */
    fun crop(plane: PlaneView, left: Int, top: Int, width: Int, height: Int): ByteArray {
        val samples = ByteArray(width * height)
        val start = plane.buffer.duplicate()
        start.position(start.position() + top * plane.rowStride + left * plane.pixelStride)
        copyPlane(plane.copy(buffer = start), PlaneView.packed(samples, width), width, height)
        return samples
    }

    /** A packed [width] x [height] plane turned clockwise by [rotationDegrees], a quarter-turn multiple. */
    fun turn(plane: ByteArray, width: Int, height: Int, rotationDegrees: Int): ByteArray {
        val rotation = ((rotationDegrees % 360) + 360) % 360
        require(rotation % 90 == 0) { "Неподдерживаемый поворот кадра: $rotation°" }
        if (rotation == 0) return plane
        val output = ByteArray(plane.size)
        when (rotation) {
            180 -> {
                val last = plane.size - 1
                for (i in plane.indices) output[last - i] = plane[i]
            }
            90 -> {
                // Output row r is source column r, read from the bottom up.
                var to = 0
                for (column in 0 until width) {
                    var from = (height - 1) * width + column
                    for (row in 0 until height) {
                        output[to++] = plane[from]
                        from -= width
                    }
                }
            }
            else -> {
                // 270: output row r is source column width-1-r, read top down.
                var to = 0
                for (column in width - 1 downTo 0) {
                    var from = column
                    for (row in 0 until height) {
                        output[to++] = plane[from]
                        from += width
                    }
                }
            }
        }
        return output
    }
}
