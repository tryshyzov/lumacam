package com.lumalink.companion

import org.junit.Assert.assertEquals
import org.junit.Test

class StringsTest {
    @Test
    fun everyLanguageHasTheSameStrings() {
        val russian = Strings.keys("ru")
        for (language in Strings.languages) {
            assertEquals(language, russian, Strings.keys(language))
        }
    }
}
