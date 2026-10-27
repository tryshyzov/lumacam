package com.lumalink.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class PairingCodeTest {
    @Test
    fun matchesTheDesktopsCode() {
        // The same vector as tests/test_handshake_v2.py in LumaLink Studio.
        assertEquals("300 879", PairingCode.of("certificate".toByteArray(), "computer-1"))
    }

    @Test
    fun differsPerComputer() {
        assertNotEquals(
            PairingCode.of("certificate".toByteArray(), "computer-1"),
            PairingCode.of("certificate".toByteArray(), "computer-2"),
        )
    }
}
