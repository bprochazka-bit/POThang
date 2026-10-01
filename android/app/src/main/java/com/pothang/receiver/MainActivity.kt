package com.pothang.receiver

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.mutableStateOf
import androidx.core.content.IntentCompat
import com.pothang.receiver.data.ApiClient
import com.pothang.receiver.data.Settings
import com.pothang.receiver.ui.AppRoot
import com.pothang.receiver.ui.theme.POThangTheme

class MainActivity : ComponentActivity() {
    /** Image shared into the app ("Share > POThang Receiver"), pending use. */
    private val sharedImage = mutableStateOf<Uri?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val settings = Settings(applicationContext)
        val api = ApiClient(settings)
        handleIntent(intent)
        setContent {
            POThangTheme {
                AppRoot(
                    settings = settings,
                    api = api,
                    sharedImage = sharedImage.value,
                    onSharedImageConsumed = { sharedImage.value = null },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.action == Intent.ACTION_SEND && intent.type?.startsWith("image/") == true) {
            sharedImage.value = IntentCompat.getParcelableExtra(
                intent, Intent.EXTRA_STREAM, Uri::class.java)
        }
    }
}
