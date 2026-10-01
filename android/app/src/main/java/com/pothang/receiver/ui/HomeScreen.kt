package com.pothang.receiver.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AddPhotoAlternate
import androidx.compose.material.icons.filled.DocumentScanner
import androidx.compose.material.icons.automirrored.filled.FactCheck
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.pothang.receiver.data.ApiClient
import com.pothang.receiver.data.ApiException
import com.pothang.receiver.data.PoSummary
import com.pothang.receiver.data.Settings
import com.pothang.receiver.ui.theme.Accent
import com.pothang.receiver.ui.theme.FgMuted
import com.pothang.receiver.ui.theme.Warning

@Composable
fun HomeScreen(
    api: ApiClient,
    settings: Settings,
    onScan: (Int?) -> Unit,
    onOpenPo: (Int) -> Unit,
    onNewItem: () -> Unit,
    onReceived: () -> Unit,
    onSettings: () -> Unit,
) {
    var pos by remember { mutableStateOf<List<PoSummary>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var reload by remember { mutableIntStateOf(0) }

    LaunchedEffect(reload, settings.serverUrl, settings.apiToken) {
        loading = true
        error = null
        try {
            pos = api.receivablePos()
        } catch (e: ApiException) {
            error = e.message
        } finally {
            loading = false
        }
    }

    AppScaffold(
        "Receiving",
        actions = {
            IconButton(onClick = { reload++ }) { Icon(Icons.Filled.Refresh, "Refresh") }
            IconButton(onClick = onSettings) { Icon(Icons.Filled.Settings, "Settings") }
        },
    ) { pad ->
        AdaptiveSplit(
            Modifier.padding(pad),
            firstWeight = 0.4f,
            first = { m ->
                Column(
                    m.verticalScroll(rememberScrollState()).padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    ActionTile(Icons.Filled.DocumentScanner, "Scan to receive",
                        "Point the camera at a label or packing slip", primary = true) { onScan(null) }
                    ActionTile(Icons.Filled.AddPhotoAlternate, "New item from photo",
                        "Screenshot or picture → prefilled item") { onNewItem() }
                    ActionTile(Icons.AutoMirrored.Filled.FactCheck, "Received shipments",
                        "Receipts by day, double-check & verify") { onReceived() }
                }
            },
            second = { m ->
                Column(m.padding(horizontal = 16.dp)) {
                    SectionLabel("Open POs")
                    if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
                    ErrorText(error)
                    val list = pos
                    if (list != null && list.isEmpty()) {
                        Text("Nothing waiting to be received.", color = FgMuted,
                            modifier = Modifier.padding(vertical = 16.dp))
                    }
                    LazyColumn(
                        verticalArrangement = ListSpacing,
                        contentPadding = PaddingValues(bottom = 24.dp),
                    ) {
                        items(list.orEmpty(), key = { it.id }) { po ->
                            PoRow(po, onClick = { onOpenPo(po.id) })
                        }
                    }
                }
            },
        )
    }
}

@Composable
private fun ActionTile(
    icon: ImageVector,
    title: String,
    subtitle: String,
    primary: Boolean = false,
    onClick: () -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
        colors = CardDefaults.cardColors(
            containerColor = if (primary) Accent.copy(alpha = 0.18f) else MaterialTheme.colorScheme.surface),
    ) {
        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, null, tint = Accent, modifier = Modifier.size(32.dp))
            Spacer(Modifier.width(16.dp))
            Column {
                Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Text(subtitle, style = MaterialTheme.typography.bodySmall, color = FgMuted)
            }
        }
    }
}

@Composable
private fun PoRow(po: PoSummary, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("PO ${po.poNumber}", fontWeight = FontWeight.SemiBold)
                    Text(listOfNotNull(po.vendor, po.status).joinToString(" · "),
                        style = MaterialTheme.typography.bodySmall, color = FgMuted)
                }
                Column(horizontalAlignment = Alignment.End) {
                    Text("${po.outstanding}", style = MaterialTheme.typography.titleLarge,
                        color = Warning, fontWeight = FontWeight.Bold)
                    Text("outstanding", style = MaterialTheme.typography.labelSmall, color = FgMuted)
                }
            }
            Spacer(Modifier.height(8.dp))
            Box {
                LinearProgressIndicator(
                    progress = { if (po.totalOrdered == 0) 0f else po.totalReceived.toFloat() / po.totalOrdered },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            Text("${po.totalReceived}/${po.totalOrdered} units · ${po.outstandingLines} of ${po.totalLines} lines open",
                style = MaterialTheme.typography.labelSmall, color = FgMuted,
                modifier = Modifier.padding(top = 4.dp))
        }
    }
}
