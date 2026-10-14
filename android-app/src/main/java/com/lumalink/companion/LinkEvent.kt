package com.lumalink.companion

/** What the USB server and the Wi-Fi publisher report to the screen. */
internal enum class LinkEvent {
    /** USB: tethering is on and the phone waits for the computer. */
    WAITING,

    /** USB: no tethering interface is up yet. */
    NEED_TETHERING,

    /** Wi-Fi: looking for LumaLink Studio on the network. */
    SEARCHING,

    /** Wi-Fi: connecting to the address in the detail. */
    CONNECTING,

    CONNECTED,
    STOPPED,

    /** Wi-Fi: no LumaLink Studio answered the discovery probe. */
    NOT_FOUND,

    /** The connection failed; the detail holds the reason, if any. */
    FAILED,
}

internal typealias LinkListener = (event: LinkEvent, detail: String?) -> Unit

/** Thrown by the publisher when no computer answers on the network. */
internal class ComputerNotFoundException : Exception("No LumaLink Studio answered")
