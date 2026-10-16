package com.lumalink.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Test

class ZoomPlanTest {
    private val main = CameraModule("0", 1f, 1f..10f)
    private val wide = CameraModule("21", 0.62f, 1f..4f)
    private val macro = CameraModule("20", 0.95f, 1f..4f)
    private val depth = CameraModule("22", 0.66f, 1f..1f)
    private val tele = CameraModule("3", 2f, 1f..8f)

    @Test
    fun picksModulesByFieldOfViewAndResolution() {
        // The Redmi's macro is barely wider than the main camera and the
        // depth module cannot deliver the stream's resolution.
        val plan = ZoomPlan.of(main, listOf(macro to true, depth to false, wide to true, tele to true))

        assertSame(wide, plan.ultraWide)
        assertSame(tele, plan.telephoto)
        assertEquals(0.62f, plan.range.start, 0.001f)
        assertEquals(16f, plan.range.endInclusive, 0.001f)
    }

    @Test
    fun offersOneToTheMainZoomWithoutOtherModules() {
        val plan = ZoomPlan.of(main, listOf(macro to true, depth to false))

        assertNull(plan.ultraWide)
        assertEquals(1f, plan.range.start, 0.001f)
        assertEquals(10f, plan.range.endInclusive, 0.001f)
    }

    @Test
    fun keepsALogicalCamerasOwnRangeBelowOne() {
        val logical = CameraModule("0", 1f, 0.6f..10f)

        assertEquals(0.6f, ZoomPlan(logical).range.start, 0.001f)
        assertEquals(0.6f, ZoomPlan(logical).moduleZoom(0.6f, logical), 0.001f)
    }

    @Test
    fun switchesModulesOnlyPastTheHysteresis() {
        val plan = ZoomPlan(main, wide, tele)

        assertSame(main, plan.moduleFor(0.97f, main))
        assertSame(wide, plan.moduleFor(0.95f, main))
        assertSame(wide, plan.moduleFor(1.03f, wide))
        assertSame(main, plan.moduleFor(1.05f, wide))
        assertSame(main, plan.moduleFor(2.05f, main))
        assertSame(tele, plan.moduleFor(2.1f, main))
        assertSame(tele, plan.moduleFor(1.95f, tele))
        assertSame(main, plan.moduleFor(1.9f, tele))
    }

    @Test
    fun zoomsEachModuleRelativeToTheMainCamera() {
        val plan = ZoomPlan(main, wide, tele)

        assertEquals(1f, plan.moduleZoom(0.62f, wide), 0.001f)
        assertEquals(1.5f, plan.moduleZoom(0.93f, wide), 0.01f)
        assertEquals(2.5f, plan.moduleZoom(5f, tele), 0.001f)
        // Below a module's own range it holds at its widest.
        assertEquals(1f, plan.moduleZoom(0.5f, wide), 0.001f)
    }
}
