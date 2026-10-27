package com.lumalink.companion

/**
 * Where a click on the computer's picture lands in the camera buffer, for
 * focusing there ("meter", docs/protocol.md).
 *
 * The computer sends the point in the picture it shows: upright, unmirrored,
 * (0, 0) at the top left, (1, 1) at the bottom right. That picture is the
 * camera buffer turned clockwise by the stream's rotation and then by half a
 * turn more on the computer (HeldRotation.streamTarget); this turns it back.
 */
internal object MeterPoint {
    /** The buffer point for ([x], [y]) shown after a clockwise turn by [shownDegrees]. */
    fun inBuffer(x: Float, y: Float, shownDegrees: Int): Pair<Float, Float> {
        val cx = x.coerceIn(0f, 1f)
        val cy = y.coerceIn(0f, 1f)
        return when (((shownDegrees % 360) + 360) % 360) {
            90 -> cy to 1f - cx
            180 -> 1f - cx to 1f - cy
            270 -> 1f - cy to cx
            else -> cx to cy
        }
    }

    /** The turn from the buffer to the computer's picture, for a stream turned by [streamDegrees]. */
    fun shownDegrees(streamDegrees: Int): Int = (streamDegrees + 180) % 360
}
