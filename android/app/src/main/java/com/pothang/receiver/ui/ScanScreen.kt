package com.pothang.receiver.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AddPhotoAlternate
import androidx.compose.material.icons.filled.FlashOff
import androidx.compose.material.icons.filled.FlashOn
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.movableContentOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.pothang.receiver.data.ApiClient
import com.pothang.receiver.data.ApiException
import com.pothang.receiver.data.ItemInfo
import com.pothang.receiver.data.MatchResult
import com.pothang.receiver.data.PoLine
import com.pothang.receiver.data.Settings
import com.pothang.receiver.ocr.OcrResult
import com.pothang.receiver.ocr.TextAnalyzer
import com.pothang.receiver.ui.theme.FgMuted
import com.pothang.receiver.ui.theme.Success
import kotlinx.coroutines.launch

// A match is "confident" (open the qty prompt straight away) when it scores
// at least this, and beats the runner-up by CONFIDENT_MARGIN.
private const val CONFIDENT_SCORE = 60.0
private const val CONFIDENT_MARGIN = 25.0
// Consecutive near-identical OCR frames before auto look-up fires.
private const val STABLE_FRAMES = 2

/** Word set used to tell "same label, slightly different read" from new text. */
private fun signature(text: String): Set<String> =
    Regex("[a-z0-9]{3,}").findAll(text.lowercase()).map { it.value }.toSet()

private fun similar(a: Set<String>, b: Set<String>): Boolean {
    if (a.isEmpty() || b.isEmpty()) return a.isEmpty() && b.isEmpty()
    val inter = a.intersect(b).size.toDouble()
    return inter / a.union(b).size >= 0.6
}

/**
 * Receiving mode: the camera reads text continuously; when it settles (or the
 * user taps Look up) the text is matched against open PO lines on the server.
 * A confident match opens the qty prompt immediately; otherwise the ranked
 * candidates are listed to pick from.
 */
@Composable
fun ScanScreen(
    api: ApiClient,
    settings: Settings,
    poId: Int?,
    onBack: () -> Unit,
    onAddNew: (String) -> Unit,
) {
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }

    var live by remember { mutableStateOf(OcrResult.EMPTY) }
    var stableCount by remember { mutableStateOf(0) }
    var lastLookupSig by remember { mutableStateOf<Set<String>>(emptySet()) }
    var query by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<MatchResult?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var receiving by remember { mutableStateOf<PoLine?>(null) }
    var paused by remember { mutableStateOf(false) }
    var torch by remember { mutableStateOf(false) }
    var receivedCount by remember { mutableStateOf(0) }

    val analyzer = remember {
        TextAnalyzer { r ->
            val prev = signature(live.text)
            val sig = signature(r.text)
            stableCount = if (sig.isNotEmpty() && similar(prev, sig)) stableCount + 1 else 0
            live = r
        }
    }
    // Stop reading frames while a dialog is up or the user paused.
    SideEffect { analyzer.paused = paused || receiving != null }

    fun lookup(text: String, auto: Boolean) {
        val t = text.trim()
        if (t.isEmpty() || busy) return
        busy = true
        error = null
        query = t
        lastLookupSig = signature(t)
        scope.launch {
            try {
                val r = api.match(t, poId)
                result = r
                val top = r.matches.firstOrNull()
                val runnerUp = r.matches.getOrNull(1)?.score ?: 0.0
                if (top != null && top.outstanding > 0 && (top.score ?: 0.0) >= CONFIDENT_SCORE &&
                    (top.score ?: 0.0) - runnerUp >= CONFIDENT_MARGIN
                ) {
                    receiving = top
                } else if (!auto && r.matches.isEmpty() && r.otherItems.isEmpty()) {
                    snackbar.showSnackbar("No match. Try another side of the label, or edit the text.")
                }
            } catch (e: ApiException) {
                error = e.message
            } finally {
                busy = false
            }
        }
    }

    // Auto look-up once the camera text has settled on something new.
    LaunchedEffect(stableCount) {
        if (settings.autoLookup && stableCount >= STABLE_FRAMES && !busy && receiving == null &&
            !similar(lastLookupSig, signature(live.text))
        ) {
            lookup(live.text, auto = true)
        }
    }

    val camera = remember {
        movableContentOf { m: Modifier ->
            Box(m.background(Color.Black)) {
                WithCameraPermission {
                    CameraPreview(analyzer, torchOn = torch, modifier = Modifier.fillMaxSize())
                }
                // Live read-out so the user can see what the camera is getting.
                if (live.text.isNotBlank()) {
                    Text(
                        live.text.lines().filter { it.isNotBlank() }.take(4).joinToString("\n"),
                        color = Color.White, style = MaterialTheme.typography.bodySmall,
                        maxLines = 4, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier
                            .align(Alignment.BottomStart)
                            .padding(8.dp)
                            .clip(RoundedCornerShape(6.dp))
                            .background(Color.Black.copy(alpha = 0.6f))
                            .padding(6.dp),
                    )
                }
                Row(Modifier.align(Alignment.TopEnd).padding(8.dp)) {
                    FilledTonalIconButton(onClick = { torch = !torch }) {
                        Icon(if (torch) Icons.Filled.FlashOn else Icons.Filled.FlashOff, "Torch")
                    }
                    FilledTonalIconButton(onClick = { paused = !paused }) {
                        Icon(if (paused) Icons.Filled.PlayArrow else Icons.Filled.Pause,
                            if (paused) "Resume" else "Pause")
                    }
                }
                if (paused) {
                    Text("Paused", color = Color.White,
                        modifier = Modifier.align(Alignment.Center)
                            .background(Color.Black.copy(alpha = 0.6f)).padding(8.dp))
                }
            }
        }
    }

    AppScaffold(
        if (poId == null) "Scan to receive" else "Scan · PO",
        onBack = onBack,
        snackbar = snackbar,
        actions = {
            if (receivedCount > 0) {
                Text("$receivedCount received", color = Success,
                    modifier = Modifier.padding(end = 8.dp))
            }
            FilterChip(
                selected = settings.autoLookup,
                onClick = { settings.updateAutoLookup(!settings.autoLookup) },
                label = { Text("Auto") },
                modifier = Modifier.padding(end = 8.dp),
            )
        },
    ) { pad ->
        AdaptiveSplit(
            Modifier.padding(pad),
            firstWeight = 0.45f,
            first = { m -> camera(m) },
            second = { m ->
                Column(m.padding(horizontal = 12.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.padding(top = 8.dp)) {
                        OutlinedTextField(
                            value = query,
                            onValueChange = { query = it },
                            placeholder = { Text("Model, SKU or description") },
                            singleLine = true,
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                            keyboardActions = KeyboardActions(onSearch = { lookup(query, auto = false) }),
                            trailingIcon = {
                                IconButton(onClick = { lookup(query, auto = false) }) {
                                    Icon(Icons.Filled.Search, "Search typed text")
                                }
                            },
                            modifier = Modifier.weight(1f),
                        )
                        Spacer(Modifier.width(8.dp))
                        Button(onClick = { lookup(live.text, auto = false) },
                            enabled = !busy && live.text.isNotBlank()) {
                            if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                            else Text("Look up")
                        }
                    }
                    ErrorText(error, Modifier.padding(top = 8.dp))
                    MatchList(
                        api = api, result = result,
                        onPick = { receiving = it },
                        onAddNew = { onAddNew(query) },
                    )
                }
            },
        )
    }

    receiving?.let { line ->
        ReceiveDialog(
            api = api, line = line,
            onDismiss = { receiving = null },
            onReceived = { msg, updated ->
                receiving = null
                receivedCount++
                // Replace the line in the list so its outstanding qty is current.
                result = result?.let { r ->
                    r.copy(matches = r.matches.map {
                        if (it.id == updated.id) updated.copy(score = it.score, reasons = it.reasons) else it
                    })
                }
                // Don't re-prompt for the same label still in front of the camera.
                lastLookupSig = signature(live.text).ifEmpty { lastLookupSig }
                scope.launch { snackbar.showSnackbar(msg) }
            },
        )
    }
}

@Composable
private fun MatchList(
    api: ApiClient,
    result: MatchResult?,
    onPick: (PoLine) -> Unit,
    onAddNew: () -> Unit,
) {
    LazyColumn(
        verticalArrangement = ListSpacing,
        contentPadding = PaddingValues(vertical = 8.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        if (result == null) {
            item {
                Text(
                    "Point the camera at a box label, part sticker or packing slip. " +
                        "Model and part numbers match best.",
                    color = FgMuted, modifier = Modifier.padding(8.dp),
                )
            }
            return@LazyColumn
        }
        if (result.matches.isNotEmpty()) {
            item { SectionLabel("Matches on open POs") }
        }
        items(result.matches, key = { "l${it.id}" }) { line ->
            LineCard(api, line, onClick = { onPick(line) }, highlight = line == result.matches.first()) {
                if (line.reasons.isNotEmpty()) {
                    Text("Matched: " + line.reasons.joinToString(", "),
                        style = MaterialTheme.typography.labelSmall, color = Success)
                }
            }
        }
        if (result.otherItems.isNotEmpty()) {
            item { SectionLabel("Known items with nothing open to receive") }
            items(result.otherItems, key = { "i${it.id}" }) { OtherItemRow(api, it) }
        }
        if (result.matches.isEmpty()) {
            item {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Not on any open PO.", fontWeight = FontWeight.SemiBold)
                        Text("Try the other side of the label, type a model number above, " +
                            "or add it as a new item.", color = FgMuted,
                            style = MaterialTheme.typography.bodySmall)
                        OutlinedButton(onClick = onAddNew) {
                            Icon(Icons.Filled.AddPhotoAlternate, null)
                            Spacer(Modifier.width(8.dp))
                            Text("Add as new item")
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun OtherItemRow(api: ApiClient, item: ItemInfo) {
    Card(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            ItemThumb(api, item.imageUrl, size = 44.dp)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(item.name, fontWeight = FontWeight.SemiBold, maxLines = 2,
                    overflow = TextOverflow.Ellipsis)
                Text(listOf(item.identifiers, item.state.orEmpty()).filter { it.isNotEmpty() }
                    .joinToString(" · "), style = MaterialTheme.typography.bodySmall, color = FgMuted)
            }
        }
    }
}
