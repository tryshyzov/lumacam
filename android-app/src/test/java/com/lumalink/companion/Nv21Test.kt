package com.lumalink.companion

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class Nv21Test {
    private val width = 6
    private val height = 4
    private val frame = ByteArray(width * height * 3 / 2) { it.toByte() }

    /** The turn a pixel at a time, as the copying path used to do it. */
    private fun reference(rotation: Int): ByteArray {
        val outputWidth = if (rotation % 180 == 0) width else height
        val output = ByteArray(frame.size)
        fun place(x: Int, y: Int, w: Int, h: Int): Pair<Int, Int> = when (rotation) {
            90 -> Pair(h - 1 - y, x)
            180 -> Pair(w - 1 - x, h - 1 - y)
            else -> Pair(y, w - 1 - x)
        }
        for (y in 0 until height) for (x in 0 until width) {
            val (tx, ty) = place(x, y, width, height)
            output[ty * outputWidth + tx] = frame[y * width + x]
        }
        val offset = width * height
        for (y in 0 until height / 2) for (x in 0 until width / 2) {
            val (tx, ty) = place(x, y, width / 2, height / 2)
            val to = offset + (ty * (outputWidth / 2) + tx) * 2
            val from = offset + (y * (width / 2) + x) * 2
            output[to] = frame[from]
            output[to + 1] = frame[from + 1]
        }
        return output
    }

    @Test
    fun turnsLikeThePixelByPixelVersion() {
        for (rotation in listOf(90, 180, 270)) {
            val (data, w, h) = Nv21.rotate(frame, width, height, rotation)
            assertArrayEquals("rotation $rotation", reference(rotation), data)
            assertEquals(if (rotation == 180) width else height, w)
            assertEquals(if (rotation == 180) height else width, h)
        }
    }

    @Test
    fun noTurnKeepsTheFrame() {
        val (data, w, h) = Nv21.rotate(frame, width, height, 360)
        assertEquals(frame, data)
        assertEquals(width, w)
        assertEquals(height, h)
    }
}
