package com.lumalink.companion

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Log
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.spec.ECGenParameterSpec
import java.util.Calendar
import java.util.Date
import java.util.Locale
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.security.auth.x500.X500Principal

/**
 * TLS for Wi-Fi connections (docs/protocol.md, "Encrypted Wi-Fi"): the
 * phone's own ECDSA P-256 key, made once in the Android Keystore, and the
 * self-signed certificate the Keystore keeps with it. Computers recognise
 * the phone by that certificate's fingerprint.
 */
internal class SecureChannel private constructor(
    val socketFactory: SSLSocketFactory,
    /** The certificate in DER, which the pairing code and the fingerprint cover. */
    val certificate: ByteArray,
) {
    companion object {
        private const val TAG = "LumaLinkTls"
        private const val ALIAS = "lumalink_tls"

        /** The phone's channel, or null where the Keystore cannot make one (then Wi-Fi stays unencrypted). */
        fun load(): SecureChannel? =
            try {
                val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
                if (!keyStore.containsAlias(ALIAS)) makeKey()
                val managers = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
                    .apply { init(keyStore, null) }
                    .keyManagers
                val context = SSLContext.getInstance("TLS").apply { init(managers, null, null) }
                SecureChannel(context.socketFactory, keyStore.getCertificate(ALIAS).encoded)
            } catch (error: Exception) {
                Log.e(TAG, "No key for encrypted Wi-Fi; Wi-Fi stays unencrypted", error)
                null
            }

        private fun makeKey() {
            val until = Calendar.getInstance().apply { add(Calendar.YEAR, 50) }.time
            val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
            generator.initialize(
                KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                    .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                    // TLS 1.2 and 1.3 sign with SHA-256 and up; NONE lets the
                    // TLS stack hash itself.
                    .setDigests(
                        KeyProperties.DIGEST_NONE,
                        KeyProperties.DIGEST_SHA256,
                        KeyProperties.DIGEST_SHA384,
                        KeyProperties.DIGEST_SHA512,
                    )
                    .setCertificateSubject(X500Principal("CN=LumaLink phone"))
                    .setCertificateSerialNumber(BigInteger.ONE)
                    .setCertificateNotBefore(Date())
                    .setCertificateNotAfter(until)
                    .build(),
            )
            generator.generateKeyPair()
        }
    }
}

/** The six digits the phone and the computer both show while the user allows it. */
internal object PairingCode {
    fun of(certificate: ByteArray, computerId: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(certificate + computerId.toByteArray(Charsets.UTF_8))
        var number = 0L
        for (index in 0 until 4) number = (number shl 8) or (digest[index].toLong() and 0xff)
        number %= 1_000_000
        return String.format(Locale.ROOT, "%03d %03d", number / 1000, number % 1000)
    }
}
