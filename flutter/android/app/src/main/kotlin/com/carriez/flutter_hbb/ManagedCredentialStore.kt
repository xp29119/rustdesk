package com.carriez.flutter_hbb

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Called only by the native credential owner; not exposed through exported components. */
class ManagedCredentialStore(context: Context) {
    private val record = AtomicFile(File(context.noBackupFilesDir, "managed-auth-v1"))
    private val alias = "rustdesk-internal-auth-v1"

    private fun key(create: Boolean): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val existing = store.getKey(alias, null)
        if (existing != null) return existing as SecretKey
        check(create) { "Protected device key is unavailable" }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(KeyGenParameterSpec.Builder(
                alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            ).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setRandomizedEncryptionRequired(true)
                .build())
            generateKey()
        }
    }

    @Synchronized
    fun load(): ByteArray? {
        // AtomicFile restores an interrupted write before reading.
        val input = try { record.openRead() } catch (_: java.io.FileNotFoundException) {
            return null
        }
        val sealed = input.use {
            check(it.channel.size() in 29..8192) { "Invalid protected login record" }
            it.readBytes()
        }
        check(sealed[0] == 1.toByte()) { "Unsupported protected login record" }
        return Cipher.getInstance("AES/GCM/NoPadding").run {
            init(Cipher.DECRYPT_MODE, key(false), GCMParameterSpec(128, sealed.copyOfRange(1, 13)))
            updateAAD(alias.toByteArray(Charsets.UTF_8))
            doFinal(sealed, 13, sealed.size - 13)
        }
    }

    @Synchronized
    fun save(plain: ByteArray) {
        check(plain.size <= 4096) { "Login record is too large" }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key(true))
        cipher.updateAAD(alias.toByteArray(Charsets.UTF_8))
        val sealed = byteArrayOf(1) + cipher.iv + cipher.doFinal(plain)
        val output = record.startWrite()
        try {
            output.write(sealed)
            record.finishWrite(output)
        } catch (error: Exception) {
            record.failWrite(output)
            throw error
        }
    }
}
