package com.lumalink.companion

import android.content.SharedPreferences
import java.security.SecureRandom
import java.util.UUID
import org.json.JSONArray
import org.json.JSONObject

/** A computer allowed to use the camera over Wi-Fi, and the token it was given. */
internal data class AllowedComputer(val id: String, val name: String, val token: String)

/**
 * The computers this phone allowed over Wi-Fi (docs/protocol.md, "Allowing a
 * computer"), kept in the app's preferences. Thread-safe: the server checks
 * tokens on its own threads while the settings screen lists and forgets them.
 */
internal class AllowedComputers(private val preferences: SharedPreferences) {
    private val random = SecureRandom()

    @Synchronized
    fun all(): List<AllowedComputer> {
        val stored = preferences.getString(KEY, null) ?: return emptyList()
        return try {
            val array = JSONArray(stored)
            (0 until array.length()).map { index ->
                val item = array.getJSONObject(index)
                AllowedComputer(item.getString("id"), item.optString("name"), item.getString("token"))
            }
        } catch (_: Exception) {
            emptyList()
        }
    }

    /** Whether [token] is the one this phone issued to computer [id]. */
    fun accepts(id: String, token: String): Boolean =
        token.isNotEmpty() && all().any { it.id == id && it.token == token }

    /** Allow computer [id] and return the new token it must send from now on. */
    @Synchronized
    fun allow(id: String, name: String): String {
        val token = ByteArray(16).also(random::nextBytes).joinToString("") { "%02x".format(it) }
        save(all().filter { it.id != id } + AllowedComputer(id, name, token))
        return token
    }

    @Synchronized
    fun forget(id: String) {
        save(all().filter { it.id != id })
    }

    private fun save(computers: List<AllowedComputer>) {
        val array = JSONArray()
        computers.forEach { array.put(JSONObject().put("id", it.id).put("name", it.name).put("token", it.token)) }
        preferences.edit().putString(KEY, array.toString()).apply()
    }

    private companion object {
        // Tokens issued before Wi-Fi was encrypted (0.6, key "allowed_computers")
        // went in plain text; those computers are asked once more.
        const val KEY = "allowed_computers_tls"
    }
}

/** This phone's lasting identity in discovery and the handshake. */
internal object PhoneId {
    private const val KEY = "phone_id"

    fun get(preferences: SharedPreferences): String =
        preferences.getString(KEY, null) ?: UUID.randomUUID().toString().also {
            preferences.edit().putString(KEY, it).apply()
        }
}
