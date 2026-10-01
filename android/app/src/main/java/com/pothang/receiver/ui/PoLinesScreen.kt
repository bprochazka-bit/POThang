package com.pothang.receiver.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DocumentScanner
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.pothang.receiver.data.ApiClient
import com.pothang.receiver.data.ApiException
import com.pothang.receiver.data.PoLine
import com.pothang.receiver.data.PoSummary
import com.pothang.receiver.ui.theme.FgMuted
import kotlinx.coroutines.launch

/** Manual receiving for one PO: tap a line, enter qty. Also launches the scanner. */
@Composable
fun PoLinesScreen(api: ApiClient, poId: Int, onBack: () -> Unit, onScan: () -> Unit) {
    var po by remember { mutableStateOf<PoSummary?>(null) }
    var lines by remember { mutableStateOf<List<PoLine>>(emptyList()) }
    var includeReceived by remember { mutableStateOf(false) }
    var filter by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var reload by remember { mutableIntStateOf(0) }
    var receiving by remember { mutableStateOf<PoLine?>(null) }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    LaunchedEffect(poId, includeReceived, reload) {
        loading = true
        error = null
        try {
            val (p, l) = api.poLines(poId, includeReceived)
            po = p
            lines = l
        } catch (e: ApiException) {
            error = e.message
        } finally {
            loading = false
        }
    }

    val shown = lines.filter { line ->
        val f = filter.trim().lowercase()
        f.isEmpty() || listOfNotNull(line.item?.name, line.item?.model, line.item?.vendorSku,
            line.item?.description).any { it.lowercase().contains(f) }
    }

    AppScaffold(po?.let { "PO ${it.poNumber}" } ?: "PO", onBack = onBack, snackbar = snackbar) { pad ->
        Scaffold(
            modifier = Modifier.padding(pad),
            containerColor = MaterialTheme.colorScheme.background,
            floatingActionButton = {
                ExtendedFloatingActionButton(
                    onClick = onScan,
                    icon = { Icon(Icons.Filled.DocumentScanner, null) },
                    text = { Text("Scan") },
                )
            },
        ) { inner ->
            Column(Modifier.padding(inner).padding(horizontal = 12.dp)) {
                po?.let {
                    Text(
                        "${it.vendor ?: ""} · ${it.totalReceived}/${it.totalOrdered} units received · " +
                            "${it.outstandingLines} lines open",
                        color = FgMuted, style = MaterialTheme.typography.bodyMedium,
                    )
                }
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.padding(vertical = 8.dp),
                ) {
                    OutlinedTextField(
                        value = filter, onValueChange = { filter = it },
                        placeholder = { Text("Filter lines") }, singleLine = true,
                        modifier = Modifier.weight(1f),
                    )
                    FilterChip(selected = includeReceived,
                        onClick = { includeReceived = !includeReceived },
                        label = { Text("Show received") })
                }
                if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
                ErrorText(error)
                if (!loading && error == null && shown.isEmpty()) {
                    Text(if (lines.isEmpty()) "Everything on this PO has been received." else "No lines match.",
                        color = FgMuted, modifier = Modifier.padding(16.dp))
                }
                // One column on phones in portrait, two or more when wide.
                LazyVerticalGrid(
                    columns = GridCells.Adaptive(minSize = 340.dp),
                    verticalArrangement = ListSpacing,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    contentPadding = PaddingValues(bottom = 88.dp),
                    modifier = Modifier.fillMaxSize(),
                ) {
                    items(shown, key = { it.id }) { line ->
                        LineCard(api, line, onClick = { receiving = line })
                    }
                }
            }
        }
    }

    receiving?.let { line ->
        ReceiveDialog(api, line,
            onDismiss = { receiving = null },
            onReceived = { msg, _ ->
                receiving = null
                reload++
                scope.launch { snackbar.showSnackbar(msg) }
            })
    }
}
