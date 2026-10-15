package com.lumalink.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AppUpdaterTest {
    @Test
    fun comparesVersionsNumberByNumber() {
        assertTrue(AppUpdater.isNewer("0.4.0", "0.3.0"))
        assertTrue(AppUpdater.isNewer("0.10.0", "0.9.9"))
        assertTrue(AppUpdater.isNewer("1.0", "0.99.1"))
        assertTrue(AppUpdater.isNewer("v0.4.1", "0.4"))
        assertFalse(AppUpdater.isNewer("0.4.0", "0.4.0"))
        assertFalse(AppUpdater.isNewer("0.4", "0.4.0"))
        assertFalse(AppUpdater.isNewer("0.3.9", "0.4.0"))
    }

    @Test
    fun neverOffersAVersionItCannotRead() {
        assertFalse(AppUpdater.isNewer("nightly", "0.4.0"))
        assertTrue(AppUpdater.isNewer("0.4.0", "local"))
    }

    @Test
    fun takesThePhoneAppFromTheLatestRelease() {
        val release = AppUpdater.parseLatestRelease(
            """
            {"tag_name": "v0.4.0", "assets": [
              {"name": "LumaLinkStudio-Setup-0.4.0.exe", "size": 9, "browser_download_url": "https://x/setup.exe"},
              {"name": "LumaLinkCompanion-debug.apk", "size": 5, "browser_download_url": "https://x/debug.apk"},
              {"name": "LumaLinkCompanion.apk", "size": 3612651, "browser_download_url": "https://x/app.apk"}
            ]}
            """,
        )

        assertEquals(AppRelease("0.4.0", "https://x/app.apk", 3612651), release)
    }

    @Test
    fun ignoresAReleaseWithoutThePhoneApp() {
        assertNull(
            AppUpdater.parseLatestRelease(
                """{"tag_name": "v0.5.0", "assets": [{"name": "LumaLinkStudio.exe", "size": 1}]}""",
            ),
        )
    }
}
