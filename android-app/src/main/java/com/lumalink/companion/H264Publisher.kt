package com.lumalink.companion

import android.util.Log
import java.io.DataInputStream
import java.io.DataOutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread
import org.json.JSONObject

internal class H264Publisher(
    private val host: String,
    private val onStatus: (String, Boolean) -> Unit,
    private val onControl: (JSONObject) -> Unit,
) {
    private val frameLock = Object()
    private val running = AtomicBoolean(false)
    @Volatile private var latestFrame: ByteArray? = null
    @Volatile private var socket: Socket? = null

    fun start() {
        check(running.compareAndSet(false, true)) { "Трансляция уже запущена" }
        thread(name = "LumaLink camera publisher", isDaemon = true) {
            publish()
        }
    }

    fun publishFrame(frame: ByteArray) {
        latestFrame = frame
        synchronized(frameLock) {
            frameLock.notifyAll()
        }
    }

    fun stop() {
        running.set(false)
        try {
            socket?.close()
        } catch (_: Exception) {
            // Closing the socket also interrupts an in-progress network write.
        }
        synchronized(frameLock) {
            frameLock.notifyAll()
        }
    }

    private fun publish() {
        try {
            val connection = Socket()
            socket = connection
            connection.connect(InetSocketAddress(host, PORT), CONNECT_TIMEOUT_MS)
            connection.soTimeout = CONNECT_TIMEOUT_MS

            val output = DataOutputStream(connection.getOutputStream())
            output.write(
                ("POST /publish HTTP/1.1\r\n" +
                    "Host: $host:$PORT\r\n" +
                    "Content-Type: application/x-lumalink-h264\r\n" +
                    "Content-Length: 0\r\n" +
                    "Connection: keep-alive\r\n\r\n").toByteArray(Charsets.US_ASCII),
            )
            output.flush()

            val input = DataInputStream(connection.getInputStream())
            val responseLine = readHttpLine(input)
            val statusCode = responseLine.split(' ').getOrNull(1)?.toIntOrNull()
            var responseContentType: String? = null
            while (true) {
                val header = readHttpLine(input)
                if (header.isEmpty()) break
                val separator = header.indexOf(':')
                if (
                    separator >= 0 &&
                    header.substring(0, separator).equals("Content-Type", ignoreCase = true)
                ) {
                    responseContentType = header.substring(separator + 1).trim()
                }
            }
            if (statusCode != 200) {
                throw IllegalStateException(
                    if (responseLine.isBlank()) "Компьютер не ответил"
                    else "Компьютер отклонил поток: $responseLine"
                )
            }
            if (responseContentType != "application/x-lumalink-h264") {
                throw IllegalStateException("Компьютер не поддерживает H.264-поток")
            }

            connection.soTimeout = 0
            onStatus("Подключено к компьютеру · трансляция активна", false)
            var sentFrame: ByteArray? = null
            while (running.get() && !connection.isClosed) {
                val frame = synchronized(frameLock) {
                    while (running.get() && latestFrame === sentFrame) {
                        frameLock.wait(500)
                    }
                    latestFrame
                } ?: continue
                if (frame === sentFrame) continue

                output.writeInt(frame.size)
                output.write(frame)
                output.flush()
                check(input.readUnsignedByte() == FRAME_ACK) {
                    "Компьютер прислал неверное подтверждение видеокадра"
                }
                val controlLength = input.readInt()
                check(controlLength in 0..MAX_CONTROL_SIZE) {
                    "Недопустимый размер команды камеры"
                }
                if (controlLength > 0) {
                    val payload = ByteArray(controlLength)
                    input.readFully(payload)
                    onControl(JSONObject(payload.toString(Charsets.UTF_8)))
                }
                sentFrame = frame
            }
        } catch (error: Exception) {
            if (running.get()) {
                Log.e(TAG, "Camera stream connection failed for $host:$PORT", error)
                onStatus("Ошибка подключения: ${error.localizedMessage}", true)
            }
        } finally {
            running.set(false)
            try {
                socket?.close()
            } catch (_: Exception) {
                // The connection can already have been closed by stop().
            }
            socket = null
        }
    }

    private fun readHttpLine(input: DataInputStream): String {
        val line = StringBuilder()
        while (true) {
            val value = input.read()
            if (value < 0 || value == '\n'.code) break
            if (value != '\r'.code) line.append(value.toChar())
        }
        return line.toString()
    }

    companion object {
        private const val PORT = 8765
        private const val CONNECT_TIMEOUT_MS = 5_000
        private const val MAX_CONTROL_SIZE = 16_384
        private const val FRAME_ACK = 0x06
        private const val TAG = "LumaLink"
    }
}
