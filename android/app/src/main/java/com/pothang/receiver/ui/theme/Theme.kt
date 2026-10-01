package com.pothang.receiver.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Mirrors the web app's dark palette (purchasetracker/static/style.css).
val Bg = Color(0xFF0F1419)
val Surface1 = Color(0xFF161B22)
val Surface2 = Color(0xFF1E252E)
val Fg = Color(0xFFE6EDF3)
val FgMuted = Color(0xFF9AA6B2)
val Accent = Color(0xFF4D9AFF)
val Success = Color(0xFF5DD39E)
val Warning = Color(0xFFF0B66B)
val Danger = Color(0xFFF47174)

private val scheme = darkColorScheme(
    primary = Accent,
    onPrimary = Color(0xFF0A0E13),
    secondary = Success,
    onSecondary = Color(0xFF0A0E13),
    tertiary = Warning,
    background = Bg,
    onBackground = Fg,
    surface = Surface1,
    onSurface = Fg,
    surfaceVariant = Surface2,
    onSurfaceVariant = FgMuted,
    surfaceContainer = Surface1,
    surfaceContainerHigh = Surface2,
    surfaceContainerHighest = Color(0xFF262D36),
    error = Danger,
    outline = Color(0xFF2D3641),
)

@Composable
fun POThangTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = scheme, content = content)
}
