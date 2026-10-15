package com.lumalink.companion

import kotlin.concurrent.thread
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class LatestFrameTest {
    private var requests = 0
    private val frames = LatestFrame { requests++ }
    private val reader = frames.Reader()

    private fun key(n: Int) = EncodedFrame(byteArrayOf(n.toByte()), keyFrame = true)
    private fun delta(n: Int) = EncodedFrame(byteArrayOf(n.toByte()), keyFrame = false)
    private fun next(): Int? = reader.next { true }?.get(0)?.toInt()

    /** Publish the frames from another thread while the reader waits. */
    private fun later(vararg next: EncodedFrame) {
        thread {
            for (frame in next) {
                Thread.sleep(50)
                frames.publish(frame)
            }
        }
    }

    @Test
    fun aSenderJoiningMidStreamStartsAtAKeyFrame() {
        frames.publish(delta(1))
        later(delta(2), key(3))

        assertEquals(3, next())
        assertEquals(1, requests)
    }

    @Test
    fun aSenderThatKeepsUpGetsEveryFrameInOrder() {
        frames.publish(key(1))
        assertEquals(1, next())

        val sent = (2..4).map { frames.publish(delta(it)); next() }

        assertEquals(listOf(2, 3, 4), sent)
        assertEquals(0, requests)
    }

    @Test
    fun aSkippedFrameHoldsBackTheRestUntilAKeyFrame() {
        frames.publish(key(1))
        next()
        frames.publish(delta(2))
        frames.publish(delta(3)) // frame 2 is never sent
        later(delta(4), key(5))

        assertEquals(5, next())
        assertEquals(1, requests)
    }

    @Test
    fun aKeyFrameRightAfterAGapGoesOutAtOnce() {
        frames.publish(key(1))
        next()
        frames.publish(delta(2))
        frames.publish(key(3))

        assertEquals(3, next())
        assertEquals(0, requests)
    }

    @Test
    fun stopsWhenClosedOrNoLongerRunning() {
        thread {
            Thread.sleep(50)
            frames.close()
        }
        assertNull(reader.next { true })
        assertNull(LatestFrame {}.Reader().next { false })
    }
}
