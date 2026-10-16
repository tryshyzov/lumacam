package com.lumalink.companion

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageInstaller
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.content.ContextCompat
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import org.json.JSONObject

/** A published version of the phone app. */
internal data class AppRelease(val version: String, val apkUrl: String, val size: Long)

/** Where the in-app update stands; [AppUpdater] reports every change on the main thread. */
internal sealed class UpdateState {
    object Idle : UpdateState()
    object Checking : UpdateState()
    object UpToDate : UpdateState()
    data class Available(val release: AppRelease) : UpdateState()
    data class Downloading(val release: AppRelease, val percent: Int) : UpdateState()
    data class Installing(val release: AppRelease) : UpdateState()

    /** Downloaded, waiting for the user to let this app install updates. */
    data class NeedsPermission(val release: AppRelease) : UpdateState()
    data class Failed(val release: AppRelease?, val reason: String?) : UpdateState()
}

/**
 * Finds the newest release on GitHub, downloads its APK and hands it to the
 * system installer. Android checks that the new APK is signed with the same
 * key as the installed app, and always asks the user to confirm.
 */
internal class AppUpdater(
    private val context: Context,
    private val currentVersion: String,
    private val onChange: (UpdateState) -> Unit,
) {
    private val worker = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val action = "${context.packageName}.UPDATE_INSTALLED"
    private var receiver: BroadcastReceiver? = null

    var state: UpdateState = UpdateState.Idle
        private set

    private fun report(next: UpdateState) {
        main.post {
            state = next
            onChange(next)
        }
    }

    fun check() {
        if (state is UpdateState.Checking || state is UpdateState.Downloading || state is UpdateState.Installing) {
            return
        }
        state = UpdateState.Checking
        onChange(state)
        worker.execute {
            try {
                val release = parseLatestRelease(fetch(LATEST_RELEASE_URL))
                report(
                    if (release != null && isNewer(release.version, currentVersion)) {
                        UpdateState.Available(release)
                    } else {
                        UpdateState.UpToDate
                    },
                )
            } catch (error: Exception) {
                Log.w(TAG, "Could not check for updates", error)
                report(UpdateState.Failed(null, error.localizedMessage))
            }
        }
    }

    /**
     * Download [release] and ask the system to install it. Asks for the
     * permission to install first: the system's own prompt sends the user to
     * Settings and drops the install on the way, so the download would be lost.
     */
    fun install(release: AppRelease) {
        state = UpdateState.Downloading(release, 0)
        onChange(state)
        worker.execute {
            try {
                val apk = downloaded(release) ?: download(release)
                if (!context.packageManager.canRequestPackageInstalls()) {
                    report(UpdateState.NeedsPermission(release))
                    return@execute
                }
                report(UpdateState.Installing(release))
                commit(apk)
            } catch (error: Exception) {
                Log.w(TAG, "Could not install the update", error)
                report(UpdateState.Failed(release, error.localizedMessage))
            }
        }
    }

    fun close() {
        receiver?.let { context.unregisterReceiver(it) }
        receiver = null
        worker.shutdownNow()
    }

    private fun fetch(url: String): String {
        val connection = open(url)
        try {
            connection.setRequestProperty("Accept", "application/vnd.github+json")
            check(connection.responseCode == HttpURLConnection.HTTP_OK) { "HTTP ${connection.responseCode}" }
            return connection.inputStream.bufferedReader().use { it.readText() }
        } finally {
            connection.disconnect()
        }
    }

    private fun apkFile(release: AppRelease): File =
        File(File(context.cacheDir, "updates"), "LumaLinkCompanion-${release.version}.apk")

    /** [release]'s APK if an earlier attempt downloaded all of it. */
    private fun downloaded(release: AppRelease): File? =
        apkFile(release).takeIf { it.isFile && (release.size <= 0 || it.length() == release.size) }

    private fun download(release: AppRelease): File {
        val apk = apkFile(release)
        val folder = apk.parentFile!!.apply { mkdirs() }
        folder.listFiles()?.forEach { it.delete() }
        val partial = File(folder, apk.name + ".part")
        val connection = open(release.apkUrl)
        try {
            check(connection.responseCode == HttpURLConnection.HTTP_OK) { "HTTP ${connection.responseCode}" }
            val total = connection.contentLengthLong.takeIf { it > 0 } ?: release.size
            var copied = 0L
            var shown = 0
            connection.inputStream.use { input ->
                partial.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        copied += read
                        val percent = if (total > 0) (copied * 100 / total).toInt().coerceIn(0, 100) else 0
                        if (percent != shown) {
                            shown = percent
                            report(UpdateState.Downloading(release, percent))
                        }
                    }
                }
            }
            check(release.size <= 0 || copied == release.size) { "download incomplete" }
            check(partial.renameTo(apk)) { "could not keep the download" }
        } finally {
            connection.disconnect()
        }
        return apk
    }

    private fun commit(apk: File) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
        }
        val sessionId = installer.createSession(params)
        installer.openSession(sessionId).use { session ->
            apk.inputStream().use { input ->
                session.openWrite("LumaLinkCompanion.apk", 0, apk.length()).use { output ->
                    input.copyTo(output)
                    session.fsync(output)
                }
            }
            main.post { listenForResult() }
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0
            val result = PendingIntent.getBroadcast(
                context,
                sessionId,
                Intent(action).setPackage(context.packageName),
                flags,
            )
            session.commit(result.intentSender)
        }
    }

    /** The installer's answers: a confirmation screen to show, then success or a failure. */
    private fun listenForResult() {
        if (receiver != null) return
        val listener = object : BroadcastReceiver() {
            override fun onReceive(received: Context, intent: Intent) {
                val release = (state as? UpdateState.Installing)?.release
                when (val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
                    PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                        @Suppress("DEPRECATION")
                        val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                        confirm?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)?.let { context.startActivity(it) }
                    }
                    // On success Android replaces and stops this process, so
                    // this is rarely seen.
                    PackageInstaller.STATUS_SUCCESS -> report(UpdateState.UpToDate)
                    else -> {
                        val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
                        Log.w(TAG, "Install finished with status $status: $message")
                        report(
                            UpdateState.Failed(
                                release,
                                when {
                                    // Google Play Protect stops APKs it has not reviewed;
                                    // its "OK" arrives as an abort with this message.
                                    status == PackageInstaller.STATUS_FAILURE_BLOCKED ||
                                        message?.contains("VERIFICATION_FAILURE") == true -> BLOCKED
                                    status == PackageInstaller.STATUS_FAILURE_ABORTED -> null
                                    // The public statuses lump a key mismatch in with
                                    // other conflicts, such as an older version code.
                                    message?.contains("UPDATE_INCOMPATIBLE") == true -> SIGNATURE_MISMATCH
                                    else -> message ?: "status $status"
                                },
                            ),
                        )
                    }
                }
            }
        }
        ContextCompat.registerReceiver(context, listener, IntentFilter(action), ContextCompat.RECEIVER_NOT_EXPORTED)
        receiver = listener
    }

    private fun open(url: String): HttpURLConnection =
        (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = TIMEOUT_MS
            readTimeout = TIMEOUT_MS
            instanceFollowRedirects = true
            setRequestProperty("User-Agent", "LumaLink-Companion/$currentVersion")
        }

    companion object {
        private const val TAG = "LumaLinkUpdate"
        private const val TIMEOUT_MS = 15_000
        const val LATEST_RELEASE_URL = "https://api.github.com/repos/tryshyzov/lumacam/releases/latest"
        const val APK_NAME = "LumaLinkCompanion.apk"

        /** Reason given when the installed app was signed with another key. */
        const val SIGNATURE_MISMATCH = "signature"

        /** Reason given when the phone (usually Play Protect) blocked the install. */
        const val BLOCKED = "blocked"

        /** The release described by GitHub's `releases/latest` answer, if it carries the phone app. */
        fun parseLatestRelease(json: String): AppRelease? {
            val release = JSONObject(json)
            val version = release.optString("tag_name").removePrefix("v").ifBlank { return null }
            val assets = release.optJSONArray("assets") ?: return null
            for (index in 0 until assets.length()) {
                val asset = assets.getJSONObject(index)
                if (asset.optString("name") == APK_NAME) {
                    val url = asset.optString("browser_download_url").ifBlank { return null }
                    return AppRelease(version, url, asset.optLong("size", 0))
                }
            }
            return null
        }

        /** Whether [candidate] is a later version than [current], comparing numbers part by part. */
        fun isNewer(candidate: String, current: String): Boolean {
            val a = numbers(candidate) ?: return false
            val b = numbers(current) ?: return true
            for (index in 0 until maxOf(a.size, b.size)) {
                val left = a.getOrElse(index) { 0 }
                val right = b.getOrElse(index) { 0 }
                if (left != right) return left > right
            }
            return false
        }

        private fun numbers(version: String): List<Int>? =
            version.trim().removePrefix("v").substringBefore('-').split('.')
                .map { it.toIntOrNull() ?: return null }
    }
}
