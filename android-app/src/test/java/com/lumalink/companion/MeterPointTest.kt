package com.lumalink.companion

import org.junit.Assert.assertEquals
import org.junit.Test

class MeterPointTest {
    /** Turn a buffer point clockwise by [degrees], the way the picture is turned. */
    private fun shown(u: Float, v: Float, degrees: Int): Pair<Float, Float> {
        var point = u to v
        repeat(degrees / 90) { point = 1f - point.second to point.first }
        return point
    }

    @Test
    fun turnsTheShownPointBackIntoTheBuffer() {
        for (degrees in listOf(0, 90, 180, 270)) {
            val (x, y) = shown(0.2f, 0.7f, degrees)
            val (u, v) = MeterPoint.inBuffer(x, y, degrees)
            assertEquals("u at $degrees", 0.2f, u, 1e-6f)
            assertEquals("v at $degrees", 0.7f, v, 1e-6f)
        }
    }

    @Test
    fun theComputerAddsHalfATurn() {
        assertEquals(180, MeterPoint.shownDegrees(0))
        assertEquals(0, MeterPoint.shownDegrees(180))
        assertEquals(90, MeterPoint.shownDegrees(270))
    }

    @Test
    fun keepsPointsInsideThePicture() {
        assertEquals(1f to 0f, MeterPoint.inBuffer(1.5f, -0.2f, 0))
    }
}
