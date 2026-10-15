package com.lumalink.companion

import android.view.Surface
import org.junit.Assert.assertEquals
import org.junit.Test

class HeldRotationTest {
    @Test
    fun snapsToTheNearestQuarterTurn() {
        // Turned clockwise: the screen's top goes right, as with ROTATION_270.
        assertEquals(Surface.ROTATION_0, HeldRotation.snap(5, Surface.ROTATION_90))
        assertEquals(Surface.ROTATION_270, HeldRotation.snap(88, Surface.ROTATION_0))
        assertEquals(Surface.ROTATION_180, HeldRotation.snap(181, Surface.ROTATION_0))
        assertEquals(Surface.ROTATION_90, HeldRotation.snap(272, Surface.ROTATION_0))
        assertEquals(Surface.ROTATION_0, HeldRotation.snap(355, Surface.ROTATION_270))
    }

    @Test
    fun keepsTheRotationWhileFlatOrNearADiagonal() {
        assertEquals(Surface.ROTATION_90, HeldRotation.snap(-1, Surface.ROTATION_90))
        // 50° is past halfway to a quarter turn, but not by the margin.
        assertEquals(Surface.ROTATION_0, HeldRotation.snap(50, Surface.ROTATION_0))
        assertEquals(Surface.ROTATION_270, HeldRotation.snap(40, Surface.ROTATION_270))
        assertEquals(Surface.ROTATION_270, HeldRotation.snap(60, Surface.ROTATION_0))
    }

    @Test
    fun theStreamTurnsHalfAWayFromThePhone() {
        // Before the phone is tilted the stream keeps its old orientation.
        assertEquals(Surface.ROTATION_90, HeldRotation.streamTarget(HeldRotation.INITIAL))
        assertEquals(Surface.ROTATION_180, HeldRotation.streamTarget(Surface.ROTATION_0))
        assertEquals(Surface.ROTATION_0, HeldRotation.streamTarget(Surface.ROTATION_180))
    }
}
