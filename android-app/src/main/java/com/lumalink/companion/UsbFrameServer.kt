package com.lumalink.companion

import android.util.Log
import java.io.DataInputStream
import java.io.DataOutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import org.json.JSONObject
import java.util.Collections
import java.util.concurrent.Executors
import kotlin.concurrent.thread

/**
 * Serves the camera stream to a computer connected through USB tethering.
 *
 * The computer broadcasts a discovery probe on the tethering network, learns
 * the phone address from the reply and opens the TCP stream itself, so USB
 * debugging is not required. When a VPN blocks that network, the computer
 * falls back to `adb forward`, which reaches the same listener over loopback.
 * Any other peer is rejected.
 */
internal class UsbFrameServer(
    private val onStatus: (String) -> Unit,
    private val onControl: (JSONObject) -> Unit,
) {
    private val frameLock = Object()
    private val clients = Collections.synchronizedSet(mutableSetOf<Socket>())
    @Volatile private var latestFrame: ByteArray? = null
    @Volatile private var running = false
    private var listener: ServerSocket? = null
    private var discovery: DatagramSocket? = null
    private val clientExecutor = Executors.newFixedThreadPool(2)

    fun start() {
        check(!running) { "USB-трансляция уже запущена" }
        val server = ServerSocket().apply {
            reuseAddress = true
            bind(InetSocketAddress(PORT))
        }
        val responder = try {
            DatagramSocket(null).apply {
                reuseAddress = true
                soTimeout = TETHER_POLL_MS
                bind(InetSocketAddress(DISCOVERY_PORT))
            }
        } catch (error: Exception) {
            server.close()
            throw error
        }
        listener = server
        discovery = responder
        running = true
        thread(name = "LumaLink USB stream", isDaemon = true) {
            try {
                while (running) {
                    val client = server.accept()
                    // Loopback peers arrive through `adb forward`, the VPN-proof fallback.
                    if (
                        !client.inetAddress.isLoopbackAddress &&
                        !isUsbTetherPeer(client.inetAddress)
                    ) {
                        Log.w(TAG, "Rejected non-USB client ${client.inetAddress}")
                        client.close()
                        continue
                    }
                    clients.add(client)
                    clientExecutor.execute { serve(client) }
                }
            } catch (error: Exception) {
                if (running) {
                    Log.e(TAG, "USB camera listener failed", error)
                    onStatus("Ошибка USB-подключения: ${error.localizedMessage}")
                    stop()
                }
            }
        }
        thread(name = "LumaLink USB discovery", isDaemon = true) {
            answerDiscovery(responder)
        }
        onStatus(waitingStatus(usbTetherInterfaces().isNotEmpty()))
    }

    fun publishFrame(frame: ByteArray) {
        latestFrame = frame
        synchronized(frameLock) {
            frameLock.notifyAll()
        }
    }

    fun stop() {
        if (!running && listener == null) return
        running = false
        try {
            listener?.close()
        } catch (_: Exception) {
            // The accept loop may already have closed its socket.
        }
        listener = null
        discovery?.close()
        discovery = null
        synchronized(clients) {
            clients.forEach { client ->
                try {
                    client.close()
                } catch (_: Exception) {
                    // Continue closing the remaining clients.
                }
            }
            clients.clear()
        }
        clientExecutor.shutdownNow()
        synchronized(frameLock) {
            frameLock.notifyAll()
        }
        onStatus("USB-трансляция остановлена")
    }

    private fun answerDiscovery(responder: DatagramSocket) {
        val buffer = ByteArray(64)
        var tetherActive = usbTetherInterfaces().isNotEmpty()
        while (running) {
            try {
                val packet = DatagramPacket(buffer, buffer.size)
                responder.receive(packet)
                val request = buffer.copyOf(packet.length)
                if (request.contentEquals(DISCOVERY_REQUEST) && isUsbTetherPeer(packet.address)) {
                    responder.send(
                        DatagramPacket(
                            DISCOVERY_REPLY,
                            DISCOVERY_REPLY.size,
                            packet.address,
                            packet.port,
                        ),
                    )
                }
            } catch (_: SocketTimeoutException) {
                val active = usbTetherInterfaces().isNotEmpty()
                if (active != tetherActive && running && clients.isEmpty()) {
                    onStatus(waitingStatus(active))
                }
                tetherActive = active
            } catch (error: Exception) {
                if (running) Log.w(TAG, "USB discovery responder failed", error)
                return
            }
        }
    }

    private fun waitingStatus(tetherActive: Boolean): String =
        if (tetherActive) {
            "USB-модем включён · ожидаем компьютер"
        } else {
            "Включите «USB-модем» (Точка доступа и модем). " +
                "Если на компьютере VPN — включите «Отладку по USB»"
        }

    private fun usbTetherInterfaces(): List<NetworkInterface> =
        try {
            NetworkInterface.getNetworkInterfaces()?.toList().orEmpty().filter { network ->
                USB_INTERFACE_PREFIXES.any { network.name.startsWith(it) } && network.isUp
            }
        } catch (_: Exception) {
            emptyList()
        }

    private fun isUsbTetherPeer(peer: InetAddress): Boolean {
        val peerBytes = peer.address
        return usbTetherInterfaces().any { network ->
            network.interfaceAddresses.any { local ->
                val localBytes = local.address.address
                localBytes.size == peerBytes.size &&
                    sharesPrefix(localBytes, peerBytes, local.networkPrefixLength.toInt())
            }
        }
    }

    private fun sharesPrefix(first: ByteArray, second: ByteArray, prefixLength: Int): Boolean {
        for (bit in 0 until prefixLength.coerceAtMost(first.size * 8)) {
            val mask = 0x80 shr (bit % 8)
            if ((first[bit / 8].toInt() xor second[bit / 8].toInt()) and mask != 0) return false
        }
        return true
    }

    private fun serve(client: Socket) {
        client.use { socket ->
            var connected = false
            try {
                socket.tcpNoDelay = true
                val input = DataInputStream(socket.getInputStream())
                val handshake = ByteArray(PROTOCOL_MAGIC.size)
                input.readFully(handshake)
                if (!handshake.contentEquals(PROTOCOL_MAGIC)) {
                    return
                }

                val output = DataOutputStream(socket.getOutputStream())
                connected = true
                onStatus("Компьютер подключён по USB")
                var sentFrame: ByteArray? = null
                while (running && !socket.isClosed) {
                    val frame = synchronized(frameLock) {
                        while (running && latestFrame === sentFrame) {
                            frameLock.wait(500)
                        }
                        latestFrame
                    } ?: continue
                    if (frame === sentFrame) continue
                    output.writeInt(frame.size)
                    output.write(frame)
                    output.flush()
                    check(input.readUnsignedByte() == FRAME_ACK) {
                        "Компьютер прислал неверное подтверждение кадра"
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
            } catch (_: Exception) {
                // Disconnecting the desktop only terminates its stream session.
            } finally {
                clients.remove(client)
                if (connected && running) {
                    onStatus(waitingStatus(usbTetherInterfaces().isNotEmpty()))
                }
            }
        }
    }

    companion object {
        const val PORT = 8765
        const val DISCOVERY_PORT = 8766
        private val PROTOCOL_MAGIC = "LUMAAVC1".toByteArray(Charsets.US_ASCII)
        private val DISCOVERY_REQUEST = "LUMALINK-DISCOVER-1".toByteArray(Charsets.US_ASCII)
        private val DISCOVERY_REPLY = "LUMALINK-CAMERA-1".toByteArray(Charsets.US_ASCII)
        private val USB_INTERFACE_PREFIXES = listOf("rndis", "usb", "ncm")
        private const val TETHER_POLL_MS = 1_000
        private const val FRAME_ACK = 0x06
        private const val MAX_CONTROL_SIZE = 16_384
        private const val TAG = "LumaLinkUsb"
    }
}
