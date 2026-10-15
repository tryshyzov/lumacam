package com.lumalink.companion

/**
 * Turning NV21 frames on the CPU, for the copying path.
 *
 * Since the video follows the phone, three of four ways to hold it need a
 * turn. Done a pixel at a time with the turn decided inside the loop, a
 * 1080p frame held the stream to 10 fps; these loops write each output row
 * in order with the turn decided once.
 */
internal object Nv21 {
    /** [source], [width] x [height], turned clockwise by [rotationDegrees]: data, width, height. */
    fun rotate(
        source: ByteArray,
        width: Int,
        height: Int,
        rotationDegrees: Int,
    ): Triple<ByteArray, Int, Int> {
        val rotation = ((rotationDegrees % 360) + 360) % 360
        require(rotation == 0 || rotation == 90 || rotation == 180 || rotation == 270) {
            "Неподдерживаемый поворот кадра: $rotation°"
        }
        if (rotation == 0) return Triple(source, width, height)
        val quarter = rotation != 180
        val outputWidth = if (quarter) height else width
        val outputHeight = if (quarter) width else height
        val output = ByteArray(source.size)
        val lumaSize = width * height
        // Luma: one byte a pixel. Chroma: one V,U pair for each 2x2 block.
        turnPlane(source, 0, output, 0, width, height, 1, rotation)
        turnPlane(source, lumaSize, output, lumaSize, width / 2, height / 2, 2, rotation)
        return Triple(output, outputWidth, outputHeight)
    }

    /** Turn a [width] x [height] plane of [bytes]-byte samples. */
    private fun turnPlane(
        source: ByteArray,
        sourceOffset: Int,
        output: ByteArray,
        outputOffset: Int,
        width: Int,
        height: Int,
        bytes: Int,
        rotation: Int,
    ) {
        when (rotation) {
            180 -> {
                val samples = width * height
                for (i in 0 until samples) {
                    val from = sourceOffset + i * bytes
                    val to = outputOffset + (samples - 1 - i) * bytes
                    for (b in 0 until bytes) output[to + b] = source[from + b]
                }
            }
            90 -> {
                // Output row r is source column r, read from the bottom up.
                var to = outputOffset
                for (column in 0 until width) {
                    var from = sourceOffset + ((height - 1) * width + column) * bytes
                    val step = width * bytes
                    for (row in 0 until height) {
                        for (b in 0 until bytes) output[to + b] = source[from + b]
                        to += bytes
                        from -= step
                    }
                }
            }
            else -> {
                // 270: output row r is source column width-1-r, read top down.
                var to = outputOffset
                for (column in width - 1 downTo 0) {
                    var from = sourceOffset + column * bytes
                    val step = width * bytes
                    for (row in 0 until height) {
                        for (b in 0 until bytes) output[to + b] = source[from + b]
                        to += bytes
                        from += step
                    }
                }
            }
        }
    }
}
