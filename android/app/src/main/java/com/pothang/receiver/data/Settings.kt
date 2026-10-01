package com.pothang.receiver.data

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/** Connection settings, persisted in SharedPreferences and observable from Compose. */
class Settings(context: Context) {
    private val prefs = context.getSharedPreferences("pothang", Context.MODE_PRIVATE)

    var serverUrl by mutableStateOf(prefs.getString(KEY_URL, "") ?: "")
        private set
    var apiToken by mutableStateOf(prefs.getString(KEY_TOKEN, "") ?: "")
        private set
    /** Scan screen: look up automatically once the camera text settles. */
    var autoLookup by mutableStateOf(prefs.getBoolean(KEY_AUTO, true))
        private set

    val isConfigured: Boolean get() = serverUrl.isNotBlank()

    fun save(url: String, token: String) {
        serverUrl = normalizeUrl(url)
        apiToken = token.trim()
        prefs.edit().putString(KEY_URL, serverUrl).putString(KEY_TOKEN, apiToken).apply()
    }

    fun updateAutoLookup(value: Boolean) {
        autoLookup = value
        prefs.edit().putBoolean(KEY_AUTO, value).apply()
    }

    companion object {
        private const val KEY_URL = "server_url"
        private const val KEY_TOKEN = "api_token"
        private const val KEY_AUTO = "auto_lookup"

        /** "192.168.1.5:5000/" -> "http://192.168.1.5:5000" */
        fun normalizeUrl(raw: String): String {
            var u = raw.trim().trimEnd('/')
            if (u.isEmpty()) return u
            if (!u.startsWith("http://", true) && !u.startsWith("https://", true)) {
                u = "http://$u"
            }
            return u
        }
    }
}
