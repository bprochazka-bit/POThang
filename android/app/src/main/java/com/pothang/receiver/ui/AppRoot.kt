package com.pothang.receiver.ui

import android.net.Uri
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.pothang.receiver.data.ApiClient
import com.pothang.receiver.data.Settings

object Routes {
    const val HOME = "home"
    const val SETTINGS = "settings"
    const val SCAN = "scan?poId={poId}"
    const val PO = "po/{poId}"
    const val NEW_ITEM = "new-item"
    const val RECEIVED = "received"

    fun scan(poId: Int? = null) = if (poId == null) "scan" else "scan?poId=$poId"
    fun po(poId: Int) = "po/$poId"
}

/** Hands OCR text from the scanner to the new-item screen ("not found? add it"). */
class PendingDraft { var text: String? = null }

@Composable
fun AppRoot(
    settings: Settings,
    api: ApiClient,
    sharedImage: Uri?,
    onSharedImageConsumed: () -> Unit,
) {
    val nav = rememberNavController()
    val pending = remember { PendingDraft() }
    val start = if (settings.isConfigured) Routes.HOME else Routes.SETTINGS

    // A shared screenshot jumps straight to "new item"; the screen picks the
    // image up from sharedImage.
    LaunchedEffect(sharedImage) {
        if (sharedImage != null && settings.isConfigured) nav.navigate(Routes.NEW_ITEM)
    }

    NavHost(navController = nav, startDestination = start) {
        composable(Routes.HOME) {
            HomeScreen(
                api = api, settings = settings,
                onScan = { nav.navigate(Routes.scan(it)) },
                onOpenPo = { nav.navigate(Routes.po(it)) },
                onNewItem = { nav.navigate(Routes.NEW_ITEM) },
                onReceived = { nav.navigate(Routes.RECEIVED) },
                onSettings = { nav.navigate(Routes.SETTINGS) },
            )
        }
        composable(Routes.SETTINGS) {
            SettingsScreen(
                settings = settings, api = api,
                canGoBack = nav.previousBackStackEntry != null,
                onBack = { nav.popBackStack() },
                onDone = {
                    if (!nav.popBackStack()) {
                        nav.navigate(Routes.HOME) { popUpTo(Routes.SETTINGS) { inclusive = true } }
                    }
                },
            )
        }
        composable(
            Routes.SCAN,
            arguments = listOf(navArgument("poId") {
                type = NavType.StringType; nullable = true; defaultValue = null
            }),
        ) { entry ->
            ScanScreen(
                api = api, settings = settings,
                poId = entry.arguments?.getString("poId")?.toIntOrNull(),
                onBack = { nav.popBackStack() },
                onAddNew = { text ->
                    pending.text = text
                    nav.navigate(Routes.NEW_ITEM)
                },
            )
        }
        composable(Routes.PO, arguments = listOf(navArgument("poId") { type = NavType.IntType })) { entry ->
            val poId = entry.arguments!!.getInt("poId")
            PoLinesScreen(
                api = api, poId = poId,
                onBack = { nav.popBackStack() },
                onScan = { nav.navigate(Routes.scan(poId)) },
            )
        }
        composable(Routes.NEW_ITEM) {
            NewItemScreen(
                api = api,
                initialText = remember { pending.text.also { pending.text = null } },
                sharedImage = sharedImage,
                onSharedImageConsumed = onSharedImageConsumed,
                onBack = { nav.popBackStack() },
            )
        }
        composable(Routes.RECEIVED) {
            ReceivedScreen(api = api, onBack = { nav.popBackStack() })
        }
    }
}
