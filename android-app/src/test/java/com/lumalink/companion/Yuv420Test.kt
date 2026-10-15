package com.lumalink.companion

import java.nio.ByteBuffer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertSame
import org.junit.Test

class Yuv420Test {
    private val width = 6
    private val height = 4
    private val plane = ByteArray(width * height) { it.toByte() }

    /** The turn a sample at a time, as the copying path used to do it. */
    private fun reference(rotation: Int): ByteArray {
        val outputWidth = if (rotation == 180) width else height
        val output = ByteArray(plane.size)
        for (y in 0 until height) for (x in 0 until width) {
            val (tx, ty) = when (rotation) {
                90 -> Pair(height - 1 - y, x)
                180 -> Pair(width - 1 - x, height - 1 - y)
                else -> Pair(y, width - 1 - x)
            }
            output[ty * outputWidth + tx] = plane[y * width + x]
        }
        return output
    }

    @Test
    fun turnsLikeTheSampleBySampleVersion() {
        for (rotation in listOf(90, 180, 270)) {
            assertArrayEquals("rotation $rotation", reference(rotation), Yuv420.turn(plane, width, height, rotation))
        }
        assertSame(plane, Yuv420.turn(plane, width, height, 360))
    }

    @Test
    fun cropsInterleavedChroma() {
        // U samples at even bytes, V between them, rows padded to 10 bytes.
        val rowStride = 10
        val bytes = ByteArray(rowStride * 3) { (if (it % 2 == 0) it else -1).toByte() }
        val view = PlaneView(ByteBuffer.wrap(bytes), rowStride, 2)

        val cropped = Yuv420.crop(view, left = 1, top = 1, width = 3, height = 2)

        assertArrayEquals(byteArrayOf(12, 14, 16, 22, 24, 26), cropped)
    }
}
