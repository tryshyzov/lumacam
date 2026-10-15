package com.lumalink.companion

/** An encoded H.264 access unit, and whether it decodes without earlier frames. */
internal class EncodedFrame(val data: ByteArray, val keyFrame: Boolean)

/**
 * The newest encoded frames, handed to the network senders.
 *
 * A sender gets every frame in order while it keeps up, including when the
 * encoder hands over several frames at once. One that falls more than
 * [KEEP] frames behind skips to the newest to keep the latency low. A skipped
 * frame breaks the ones encoded against it, which would show as corrupted
 * video until the next key frame, so after a gap, and when a sender starts,
 * it holds back frames until a key frame arrives and asks the encoder for one
 * through [onKeyFrameNeeded].
 */
internal class LatestFrame(private val onKeyFrameNeeded: () -> Unit) {
    private val lock = Object()
    private val recent = arrayOfNulls<EncodedFrame>(KEEP)
    private var sequence = 0L // of the newest frame; frame n is in recent[n % KEEP]
    private var closed = false

    fun publish(next: EncodedFrame) {
        synchronized(lock) {
            sequence += 1
            recent[(sequence % KEEP).toInt()] = next
            lock.notifyAll()
        }
    }

    fun close() {
        synchronized(lock) {
            closed = true
            lock.notifyAll()
        }
    }

    /** One sender's place in the stream. */
    inner class Reader {
        private var sent = -1L
        private var waitingForKeyFrame = false

        /** The next frame to send, or null once [running] is false or the stream closed. */
        fun next(running: () -> Boolean): ByteArray? {
            while (true) {
                val (candidate, number) = synchronized(lock) {
                    while (running() && !closed && sequence <= maxOf(sent, 0L)) {
                        lock.wait(WAIT_MS)
                    }
                    if (!running() || closed) return null
                    // The next frame if it is still kept, the newest otherwise.
                    val number = if (sent >= 0 && sequence - sent <= KEEP) sent + 1 else sequence
                    (recent[(number % KEEP).toInt()] ?: return null) to number
                }
                val gap = sent < 0 || number != sent + 1
                sent = number
                if (!candidate.keyFrame && (waitingForKeyFrame || gap)) {
                    if (!waitingForKeyFrame) {
                        waitingForKeyFrame = true
                        onKeyFrameNeeded()
                    }
                    continue
                }
                waitingForKeyFrame = false
                return candidate.data
            }
        }
    }

    private companion object {
        const val WAIT_MS = 500L

        // How far behind a sender may fall before it skips frames: a few
        // frames' latency at most, enough for the encoder handing over two or
        // three frames at once.
        const val KEEP = 4
    }
}
