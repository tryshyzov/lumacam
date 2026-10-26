package com.lumalink.companion

import android.util.Log
import java.io.DataInputStream
import java.io.DataOutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import javax.net.ssl.SSLSocket
import kotlin.concurrent.thread
import org.json.JSONObject

/** How a computer reached the phone. */
internal enum class Link(val token: String) { USB("usb"), WIFI("wifi") }

/** A computer streaming from the phone; [name] is null for a version 1 computer. */
internal data class Computer(val id: String?, val name: String?, val link: Link)

/**
 * Serves the camera to computers, version 2 of docs/protocol.md: the phone
 * waits on its Wi-Fi, its USB tethering network and loopback (`adb forward`),
 * answers discovery there, and lets one computer stream at a time. Over Wi-Fi
 * a computer needs a token this phone issued after its user allowed it; over
 * USB none. Version 1 computers (`LUMAAVC1`) are served over USB only.
 */
internal class FrameServer(
    private val computers: AllowedComputers,
    private val phone: Phone,
    private val events: Events,
    /** TLS for Wi-Fi ("Encrypted Wi-Fi"), or null where the phone has no key. */
    private val secure: SecureChannel?,
    onKeyFrameNeeded: () -> Unit,
) {
    /** What the server needs to know about the phone, read when asked. */
    interface Phone {
        val id: String
        val name: String
        val version: String

        /** `lenses` for discovery: each lens token's zoom range. */
        fun lenses(): JSONObject

        /** The camera settings in use, for the `"ok"` status. */
        fun settings(): JSONObject
    }

    /** Called on the server's threads. */
    interface Events {
        /** [computer] started streaming, or null when the last one stopped. */
        fun streaming(computer: Computer?)

        /**
         * Ask the user whether [name] may use the camera, showing [code] (the
         * pairing code, null without encryption); call [answer] once.
         */
        fun askToAllow(id: String, name: String, code: String?, answer: (Boolean) -> Unit)

        /** The computer that was being asked about gave up or timed out. */
        fun askingCancelled(id: String)

        /** A version 1 LumaLink Studio answered on Wi-Fi, which cannot reach this phone. */
        fun oldStudioFound()

        fun control(command: JSONObject)
    }

    private val frames = LatestFrame(onKeyFrameNeeded)
    @Volatile private var running = false
    private var listener: ServerSocket? = null
    private var discovery: DatagramSocket? = null
    private val workers = Executors.newCachedThreadPool()

    private val lock = Object()
    private var session: Session? = null
    private var asking: String? = null
    private val refusedAt = mutableMapOf<String, Long>()

    private class Session(val socket: Socket, val computer: Computer)

    /** Whether a computer is streaming now; read on the camera threads. */
    @Volatile var streaming = false
        private set

    fun start() {
        check(!running) { "Сервер камеры уже запущен" }
        val any = InetAddress.getByName("0.0.0.0")
        val server = ServerSocket().apply {
            reuseAddress = true
            bind(InetSocketAddress(any, PORT))
        }
        val responder = try {
            DatagramSocket(null).apply {
                reuseAddress = true
                broadcast = true
                bind(InetSocketAddress(any, DISCOVERY_PORT))
            }
        } catch (error: Exception) {
            server.close()
            throw error
        }
        listener = server
        discovery = responder
        running = true
        thread(name = "LumaLink server", isDaemon = true) { acceptLoop(server) }
        thread(name = "LumaLink discovery", isDaemon = true) { answerDiscovery(responder) }
        thread(name = "LumaLink old Studio", isDaemon = true) { lookForOldStudio() }
    }

    fun publishFrame(frame: EncodedFrame) {
        frames.publish(frame)
    }

    fun stop() {
        if (!running) return
        running = false
        closeQuietly(listener)
        listener = null
        discovery?.close()
        discovery = null
        synchronized(lock) {
            session?.let { closeQuietly(it.socket) }
            session = null
        }
        workers.shutdownNow()
        frames.close()
        streaming = false
        events.streaming(null)
    }

    private fun acceptLoop(server: ServerSocket) {
        try {
            while (running) {
                val client = server.accept()
                val link = Network.linkOf(client.inetAddress)
                if (link == null) {
                    Log.w(TAG, "Rejected a client on another network: ${client.inetAddress}")
                    closeQuietly(client)
                    continue
                }
                workers.execute { serve(client, link) }
            }
        } catch (error: Exception) {
            if (running) Log.e(TAG, "Camera listener failed", error)
        }
    }

    private fun serve(client: Socket, link: Link) {
        var socket = client
        try {
            client.tcpNoDelay = true
            client.soTimeout = ACK_TIMEOUT_MS
            var input = DataInputStream(client.getInputStream())
            var output = DataOutputStream(client.getOutputStream())
            var magic = ByteArray(MAGIC_V2.size).also(input::readFully)
            var encrypted = false
            if (magic.contentEquals(MAGIC_TLS)) {
                if (link != Link.WIFI || secure == null) return
                val tls = secure.socketFactory
                    .createSocket(client, client.inetAddress.hostAddress, client.port, true) as SSLSocket
                tls.useClientMode = false
                tls.soTimeout = ACK_TIMEOUT_MS
                tls.startHandshake()
                socket = tls
                input = DataInputStream(tls.inputStream)
                output = DataOutputStream(tls.outputStream)
                magic = ByteArray(MAGIC_V2.size).also(input::readFully)
                encrypted = true
            } else if (link == Link.WIFI && secure != null) {
                // Over Wi-Fi this phone takes only encrypted connections.
                if (magic.contentEquals(MAGIC_V2)) {
                    send(output, JSONObject().put("status", "denied").put("reason", "unencrypted"))
                }
                return
            }
            val computer = when {
                magic.contentEquals(MAGIC_V1) && link == Link.USB -> Computer(null, null, link)
                magic.contentEquals(MAGIC_V2) -> handshake(socket, input, output, link, encrypted) ?: return
                else -> return
            }
            if (!begin(socket, computer, output)) return
            stream(socket, input, output)
        } catch (_: Exception) {
            // A computer that disconnects only ends its own session.
        } finally {
            finish(socket)
        }
    }

    /** Read the hello and decide; the computer, or null after a refusal. */
    private fun handshake(
        client: Socket,
        input: DataInputStream,
        output: DataOutputStream,
        link: Link,
        encrypted: Boolean,
    ): Computer? {
        val hello = readJson(input)
        val id = hello.optString("id").ifBlank { return null }
        val name = hello.optString("name").ifBlank { "Computer" }.take(MAX_NAME)
        val computer = Computer(id, name, link)
        if (busyFor(id)) {
            send(output, JSONObject().put("status", "busy"))
            return null
        }
        if (link == Link.USB || computers.accepts(id, hello.optString("token"))) return computer
        val code = if (encrypted && secure != null) PairingCode.of(secure.certificate, id) else null
        return when (ask(client, id, name, code, output)) {
            null -> null
            else -> computer
        }
    }

    /**
     * Ask the user about computer [id] while it waits; the issued token, or
     * null after "denied" was sent.
     */
    private fun ask(client: Socket, id: String, name: String, code: String?, output: DataOutputStream): String? {
        val refusedRecently = synchronized(lock) {
            val at = refusedAt[id]
            at != null && System.currentTimeMillis() - at < COOLDOWN_MS
        }
        val othersPending = synchronized(lock) {
            if (asking != null) true else false.also { asking = id }
        }
        if (refusedRecently || othersPending) {
            if (!othersPending) synchronized(lock) { asking = null }
            send(output, JSONObject().put("status", "denied").put("reason", "cooldown"))
            return null
        }
        try {
            send(output, JSONObject().put("status", "waiting"))
            val answer = AtomicReference<Boolean?>(null)
            val answered = CountDownLatch(1)
            events.askToAllow(id, name, code) { allowed ->
                if (answer.compareAndSet(null, allowed)) answered.countDown()
            }
            // Watch for the computer giving up while the user decides.
            val deadline = System.currentTimeMillis() + ASK_TIMEOUT_MS
            while (!answered.await(500, TimeUnit.MILLISECONDS)) {
                if (!running || client.isClosed || System.currentTimeMillis() > deadline) break
            }
            return when (answer.get()) {
                true -> computers.allow(id, name).also { pendingToken.set(it) }
                false -> {
                    synchronized(lock) { refusedAt[id] = System.currentTimeMillis() }
                    send(output, JSONObject().put("status", "denied").put("reason", "refused"))
                    null
                }
                null -> {
                    events.askingCancelled(id)
                    send(output, JSONObject().put("status", "denied").put("reason", "timeout"))
                    null
                }
            }
        } finally {
            synchronized(lock) { asking = null }
        }
    }

    // The token issued by the last allowed request, handed to begin() on the same thread.
    private val pendingToken = ThreadLocal<String?>()

    private fun busyFor(id: String?): Boolean = synchronized(lock) {
        val current = session ?: return false
        current.computer.id == null || current.computer.id != id
    }

    /** Make [client] the session, replacing the same computer's older one; false when busy. */
    private fun begin(client: Socket, computer: Computer, output: DataOutputStream): Boolean {
        synchronized(lock) {
            val current = session
            if (current != null) {
                if (computer.id != null && current.computer.id == computer.id) {
                    closeQuietly(current.socket)
                } else {
                    if (computer.id != null) send(output, JSONObject().put("status", "busy"))
                    return false
                }
            }
            session = Session(client, computer)
        }
        if (computer.id != null) {
            val ok = JSONObject()
                .put("status", "ok")
                .put("camera", describe(computer.link))
                .put("settings", phone.settings())
            pendingToken.get()?.let { ok.put("token", it) }
            pendingToken.remove()
            send(output, ok)
        }
        streaming = true
        events.streaming(computer)
        return true
    }

    private fun stream(client: Socket, input: DataInputStream, output: DataOutputStream) {
        val reader = frames.Reader()
        while (!client.isClosed) {
            val frame = reader.next { running && isSession(client) } ?: break
            output.writeInt(frame.size)
            output.write(frame)
            output.flush()
            check(input.readUnsignedByte() == FRAME_ACK) { "Компьютер прислал неверное подтверждение кадра" }
            val length = input.readInt()
            check(length in 0..MAX_JSON) { "Недопустимый размер команды камеры" }
            if (length > 0) {
                val payload = ByteArray(length).also(input::readFully)
                events.control(JSONObject(payload.toString(Charsets.UTF_8)))
            }
        }
    }

    private fun isSession(client: Socket): Boolean = synchronized(lock) { session?.socket === client }

    private fun finish(client: Socket) {
        closeQuietly(client)
        val ended = synchronized(lock) {
            if (session?.socket === client) {
                session = null
                true
            } else {
                false
            }
        }
        if (ended && running) {
            streaming = false
            events.streaming(null)
        }
    }

    /** The phone as discovery and the `"ok"` status describe it. */
    private fun describe(link: Link): JSONObject =
        JSONObject()
            .put("id", phone.id)
            .put("name", phone.name)
            .put("link", link.token)
            .put("version", phone.version)
            .put("lenses", phone.lenses())
            .apply { if (secure != null) put("tls", true) }

    private fun answerDiscovery(responder: DatagramSocket) {
        val buffer = ByteArray(MAX_PROBE)
        while (running) {
            try {
                val packet = DatagramPacket(buffer, buffer.size)
                responder.receive(packet)
                val text = String(buffer, 0, packet.length, Charsets.UTF_8)
                val link = Network.linkOf(packet.address) ?: continue
                val reply = when {
                    text == DISCOVER_V1 && link == Link.USB && !packet.address.isLoopbackAddress -> CAMERA_V1
                    text == DISCOVER_V2 || text.startsWith("$DISCOVER_V2 ") -> "$CAMERA_V2 ${describe(link)}"
                    else -> continue
                }
                val bytes = reply.toByteArray(Charsets.UTF_8)
                responder.send(DatagramPacket(bytes, bytes.size, packet.address, packet.port))
            } catch (error: Exception) {
                if (running) Log.w(TAG, "Discovery responder failed", error)
                return
            }
        }
    }

    /**
     * While no computer streams, ask the Wi-Fi network for a version 1
     * LumaLink Studio every few seconds: one that answers cannot reach this
     * phone, and its user should update it.
     */
    private fun lookForOldStudio() {
        val probe = FIND_STUDIO_V1.toByteArray(Charsets.US_ASCII)
        try {
            DatagramSocket().use { socket ->
                socket.broadcast = true
                socket.soTimeout = 1_000
                val buffer = ByteArray(64)
                while (running) {
                    if (!streaming) {
                        for (target in Network.wifiBroadcastAddresses()) {
                            try {
                                socket.send(DatagramPacket(probe, probe.size, target, STUDIO_V1_PORT))
                            } catch (_: Exception) {
                                // That network is gone; the others may still work.
                            }
                        }
                        val until = System.currentTimeMillis() + OLD_STUDIO_INTERVAL_MS
                        while (running && System.currentTimeMillis() < until) {
                            try {
                                val packet = DatagramPacket(buffer, buffer.size)
                                socket.receive(packet)
                                if (String(buffer, 0, packet.length, Charsets.US_ASCII) == STUDIO_V1) {
                                    events.oldStudioFound()
                                }
                            } catch (_: SocketTimeoutException) {
                                // Keep listening until the next probe.
                            }
                        }
                    } else {
                        Thread.sleep(OLD_STUDIO_INTERVAL_MS)
                    }
                }
            }
        } catch (error: Exception) {
            if (running) Log.w(TAG, "Looking for an old LumaLink Studio failed", error)
        }
    }

    private fun readJson(input: DataInputStream): JSONObject {
        val length = input.readInt()
        check(length in 1..MAX_JSON) { "Недопустимый размер приветствия" }
        return JSONObject(ByteArray(length).also(input::readFully).toString(Charsets.UTF_8))
    }

    private fun send(output: DataOutputStream, status: JSONObject) {
        val bytes = status.toString().toByteArray(Charsets.UTF_8)
        synchronized(output) {
            output.writeInt(bytes.size)
            output.write(bytes)
            output.flush()
        }
    }

    private fun closeQuietly(closeable: AutoCloseable?) {
        try {
            closeable?.close()
        } catch (_: Exception) {
            // Already closed.
        }
    }

    companion object {
        const val PORT = 8765
        const val DISCOVERY_PORT = 8766
        const val STUDIO_V1_PORT = 8767
        private val MAGIC_V1 = "LUMAAVC1".toByteArray(Charsets.US_ASCII)
        private val MAGIC_V2 = "LUMAAVC2".toByteArray(Charsets.US_ASCII)
        private val MAGIC_TLS = "LUMATLS1".toByteArray(Charsets.US_ASCII)
        private const val DISCOVER_V1 = "LUMALINK-DISCOVER-1"
        private const val CAMERA_V1 = "LUMALINK-CAMERA-1"
        private const val DISCOVER_V2 = "LUMALINK-DISCOVER-2"
        private const val CAMERA_V2 = "LUMALINK-CAMERA-2"
        private const val FIND_STUDIO_V1 = "LUMALINK-FIND-STUDIO-1"
        private const val STUDIO_V1 = "LUMALINK-STUDIO-1"
        private const val ACK_TIMEOUT_MS = 5_000
        private const val ASK_TIMEOUT_MS = 60_000L
        private const val COOLDOWN_MS = 30_000L
        private const val OLD_STUDIO_INTERVAL_MS = 5_000L
        private const val FRAME_ACK = 0x06
        private const val MAX_JSON = 16_384
        private const val MAX_PROBE = 2_048
        private const val MAX_NAME = 64
        private const val TAG = "LumaLinkServer"
    }
}

/** Which of the phone's networks a peer is on (docs/protocol.md, version 2). */
internal object Network {
    private val USB_PREFIXES = listOf("rndis", "usb", "ncm")
    private val MOBILE_PREFIXES = listOf("rmnet", "ccmni", "pdp", "clat", "v4-", "tun", "ppp", "ipsec", "dummy")

    /** USB for loopback and the tethering network, Wi-Fi for a private IPv4 peer on another local network, else null. */
    fun linkOf(peer: InetAddress): Link? {
        if (peer !is Inet4Address) return null
        if (peer.isLoopbackAddress) return Link.USB
        for (network in interfaces()) {
            val name = network.name
            if (MOBILE_PREFIXES.any { name.startsWith(it) }) continue
            val local = network.interfaceAddresses.firstOrNull { address ->
                val bytes = address.address
                bytes is Inet4Address && sharesPrefix(bytes.address, peer.address, address.networkPrefixLength.toInt())
            } ?: continue
            if (USB_PREFIXES.any { name.startsWith(it) }) return Link.USB
            return if (peer.isSiteLocalAddress && local.address.isSiteLocalAddress) Link.WIFI else null
        }
        return null
    }

    /** The broadcast addresses of the phone's Wi-Fi networks. */
    fun wifiBroadcastAddresses(): List<InetAddress> =
        interfaces()
            .filter { network ->
                (USB_PREFIXES + MOBILE_PREFIXES).none { network.name.startsWith(it) } && !network.isLoopback
            }
            .flatMap { network -> network.interfaceAddresses.mapNotNull { it.broadcast } }
            .filter { it is Inet4Address }

    private fun interfaces(): List<NetworkInterface> =
        try {
            NetworkInterface.getNetworkInterfaces()?.toList().orEmpty().filter { it.isUp }
        } catch (_: Exception) {
            emptyList()
        }

    fun sharesPrefix(first: ByteArray, second: ByteArray, prefixLength: Int): Boolean {
        if (first.size != second.size) return false
        for (bit in 0 until prefixLength.coerceAtMost(first.size * 8)) {
            val mask = 0x80 shr (bit % 8)
            if ((first[bit / 8].toInt() xor second[bit / 8].toInt()) and mask != 0) return false
        }
        return true
    }
}
