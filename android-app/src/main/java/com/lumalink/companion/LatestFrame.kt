package com.lumalink.companion

/** An encoded H.264 access unit, and whether it decodes without earlier frames. */
internal class EncodedFrame(val data: ByteArray, val keyFrame: Boolean)

/**
 * The newest encoded frame, handed to the network senders.
 *
 * A sender that is slower than the camera skips frames to keep the latency
 * low. A skipped frame breaks the ones encoded against it, which would show
 * as corrupted video until the next key frame, so after a gap, and when a
 * sender starts, it holds back frames until a key frame arrives and asks the
 * encoder for one through [onKeyFrameNeeded].
 */
internal class LatestFrame(private val onKeyFrameNeeded: () -> Unit) {
    private val lock = Object()
    private var frame: EncodedFrame? = null
    private var sequence = 0L
    private var closed = false

    fun publish(next: EncodedFrame) {
        synchronized(lock) {
            frame = next
            sequence += 1
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
                    while (running() && !closed && (frame == null || sequence == sent)) {
                        lock.wait(WAIT_MS)
                    }
                    if (!running() || closed) return null
                    (frame ?: return null) to sequence
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
    }
}
