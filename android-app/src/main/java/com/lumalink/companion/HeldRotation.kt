package com.lumalink.companion

import android.view.OrientationEventListener
import android.view.Surface

/**
 * Which way up the phone is held, so the video can follow it.
 *
 * OrientationEventListener reports how far the phone is turned clockwise from
 * upright portrait, or ORIENTATION_UNKNOWN while it lies about flat. This
 * snaps that to a quarter turn, given as the display rotation that would
 * show the phone's screen upright, the way CameraX takes target rotations.
 */
internal object HeldRotation {
    /**
     * Before the phone is first tilted: the stream's orientation from before
     * it followed the phone, which the desktop shows upright.
     */
    const val INITIAL = Surface.ROTATION_270

    /** How far past the halfway point between quarter turns a turn must go to count. */
    const val MARGIN_DEGREES = 15

    /**
     * The rotation for [degrees] from OrientationEventListener, or [current]
     * while the phone lies flat or is held close to a diagonal, so the video
     * does not flip back and forth.
     */
    fun snap(degrees: Int, current: Int): Int {
        if (degrees == OrientationEventListener.ORIENTATION_UNKNOWN) return current
        val turned = ((degrees % 360) + 360) % 360
        val quarterTurns = ((turned + 45) / 90) % 4
        val rotation = (4 - quarterTurns) % 4
        if (rotation == current) return current
        val offset = Math.abs(turned - quarterTurns * 90).let { minOf(it, 360 - it) }
        return if (offset <= 45 - MARGIN_DEGREES) rotation else current
    }

    /**
     * The target rotation that makes CameraX report the turn the stream
     * needs: half a turn from the phone's, as the desktop turns every frame
     * by 180° (stream.correct_stream_orientation).
     */
    fun streamTarget(held: Int): Int = (held + 2) % 4
}
