package com.pothang.receiver.ui

import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Inventory2
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.RowScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.pothang.receiver.data.ApiClient
import com.pothang.receiver.data.PoLine
import com.pothang.receiver.ui.theme.Danger
import com.pothang.receiver.ui.theme.FgMuted
import com.pothang.receiver.ui.theme.Success
import com.pothang.receiver.ui.theme.Warning

/** True when there's more width than height (phone landscape, most tablets). */
fun isWide(maxWidth: Dp, maxHeight: Dp) = maxWidth > maxHeight && maxWidth >= 560.dp

/**
 * Two panes side-by-side in landscape, stacked in portrait. [firstWeight] is
 * the share of space the first pane gets in either orientation.
 */
@Composable
fun AdaptiveSplit(
    modifier: Modifier = Modifier,
    firstWeight: Float = 0.5f,
    first: @Composable (Modifier) -> Unit,
    second: @Composable (Modifier) -> Unit,
) {
    BoxWithConstraints(modifier.fillMaxSize()) {
        if (isWide(maxWidth, maxHeight)) {
            Row(Modifier.fillMaxSize()) {
                first(Modifier.weight(firstWeight).fillMaxHeight())
                second(Modifier.weight(1f - firstWeight).fillMaxHeight())
            }
        } else {
            Column(Modifier.fillMaxSize()) {
                first(Modifier.weight(firstWeight).fillMaxWidth())
                second(Modifier.weight(1f - firstWeight).fillMaxWidth())
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppScaffold(
    title: String,
    onBack: (() -> Unit)? = null,
    snackbar: SnackbarHostState? = null,
    actions: @Composable RowScope.() -> Unit = {},
    content: @Composable (PaddingValues) -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                navigationIcon = {
                    if (onBack != null) {
                        IconButton(onClick = onBack) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                        }
                    }
                },
                actions = actions,
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background),
            )
        },
        snackbarHost = { if (snackbar != null) SnackbarHost(snackbar) },
        containerColor = MaterialTheme.colorScheme.background,
        content = content,
    )
}

@Composable
fun ErrorText(message: String?, modifier: Modifier = Modifier) {
    if (message.isNullOrBlank()) return
    Text(
        message,
        color = Danger,
        style = MaterialTheme.typography.bodyMedium,
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(Danger.copy(alpha = 0.12f))
            .padding(10.dp),
    )
}

/** Small in-memory cache so thumbnails survive recomposition and rotation. */
private val thumbCache = mutableStateMapOf<String, Bitmap>()

@Composable
fun ItemThumb(api: ApiClient, path: String?, size: Dp = 56.dp) {
    var bmp by remember(path) { mutableStateOf(path?.let { thumbCache[it] }) }
    LaunchedEffect(path) {
        if (path != null && bmp == null) {
            api.image(path)?.let { thumbCache[path] = it; bmp = it }
        }
    }
    Box(
        Modifier
            .size(size)
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant),
        contentAlignment = Alignment.Center,
    ) {
        val b = bmp
        if (b != null) {
            Image(b.asImageBitmap(), null, contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize())
        } else {
            Icon(Icons.Filled.Inventory2, null, tint = FgMuted)
        }
    }
}

/** A PO line: thumbnail, name, identifiers, PO / qty progress. */
@Composable
fun LineCard(
    api: ApiClient,
    line: PoLine,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    highlight: Boolean = false,
    footer: @Composable ColumnScope.() -> Unit = {},
) {
    val item = line.item
    Card(
        modifier = modifier.fillMaxWidth().clickable(onClick = onClick),
        colors = CardDefaults.cardColors(
            containerColor = if (highlight) MaterialTheme.colorScheme.surfaceContainerHighest
            else MaterialTheme.colorScheme.surface),
    ) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            ItemThumb(api, item?.imageUrl)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(item?.name ?: "(unknown item)", fontWeight = FontWeight.SemiBold,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
                val ids = item?.identifiers.orEmpty()
                if (ids.isNotEmpty()) {
                    Text(ids, style = MaterialTheme.typography.bodySmall, color = FgMuted)
                }
                Text(
                    "PO ${line.poNumber} · line ${line.lineNo}" +
                        (line.poVendor?.let { " · $it" } ?: ""),
                    style = MaterialTheme.typography.bodySmall, color = FgMuted,
                )
                footer()
            }
            Spacer(Modifier.width(8.dp))
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    if (line.outstanding > 0) "${line.outstanding}" else "✓",
                    style = MaterialTheme.typography.headlineSmall,
                    color = if (line.outstanding > 0) Warning else Success,
                    fontWeight = FontWeight.Bold,
                )
                Text("${line.qtyReceived}/${line.qty} rcvd",
                    style = MaterialTheme.typography.labelSmall, color = FgMuted)
            }
        }
    }
}

@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(text.uppercase(), style = MaterialTheme.typography.labelMedium, color = FgMuted,
        modifier = modifier.padding(top = 8.dp, bottom = 4.dp))
}

val ListSpacing = Arrangement.spacedBy(8.dp)
