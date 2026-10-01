package com.pothang.receiver.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.pothang.receiver.data.ApiClient
import com.pothang.receiver.data.ApiException
import com.pothang.receiver.data.ReceiptDay
import com.pothang.receiver.data.ReceiptInfo
import com.pothang.receiver.ui.theme.FgMuted
import com.pothang.receiver.ui.theme.Success
import kotlinx.coroutines.launch
import java.time.LocalDate
import java.time.format.DateTimeFormatter

/** Receipts grouped by day then PO, with a verify tick per receipt. */
@Composable
fun ReceivedScreen(api: ApiClient, onBack: () -> Unit) {
    var rangeDays by remember { mutableIntStateOf(7) }
    var days by remember { mutableStateOf<List<ReceiptDay>>(emptyList()) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    // Local overrides after toggling, so the tick flips without a refetch.
    val verified = remember { mutableStateMapOf<Int, Boolean>() }
    val scope = rememberCoroutineScope()

    LaunchedEffect(rangeDays, reload) {
        loading = true
        error = null
        try {
            val today = LocalDate.now()
            days = api.receiptsByDay(today.minusDays(rangeDays - 1L).toString(), null)
            verified.clear()
        } catch (e: ApiException) {
            error = e.message
        } finally {
            loading = false
        }
    }

    fun toggle(r: ReceiptInfo) {
        val now = !(verified[r.id] ?: r.verified)
        verified[r.id] = now
        scope.launch {
            try {
                api.setVerified(r.id, now)
            } catch (e: ApiException) {
                verified[r.id] = !now
                error = e.message
            }
        }
    }

    AppScaffold("Received shipments", onBack = onBack, actions = {
        IconButton(onClick = { reload++ }) { Icon(Icons.Filled.Refresh, "Refresh") }
    }) { pad ->
        Column(Modifier.padding(pad).fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally) {
            Column(Modifier.widthIn(max = 900.dp).padding(horizontal = 12.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(1 to "Today", 7 to "7 days", 30 to "30 days").forEach { (n, label) ->
                        FilterChip(selected = rangeDays == n, onClick = { rangeDays = n },
                            label = { Text(label) })
                    }
                }
                if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
                ErrorText(error)
                if (!loading && error == null && days.isEmpty()) {
                    Text("No receipts in this range.", color = FgMuted, modifier = Modifier.padding(16.dp))
                }
                LazyColumn(
                    verticalArrangement = ListSpacing,
                    contentPadding = PaddingValues(vertical = 8.dp),
                ) {
                    items(days, key = { it.date }) { day ->
                        DayCard(day, isVerified = { verified[it.id] ?: it.verified }, onToggle = { toggle(it) })
                    }
                }
            }
        }
    }
}

@Composable
private fun DayCard(day: ReceiptDay, isVerified: (ReceiptInfo) -> Boolean, onToggle: (ReceiptInfo) -> Unit) {
    val all = day.pos.flatMap { it.receipts }
    val done = all.count(isVerified)
    val title = runCatching {
        LocalDate.parse(day.date).format(DateTimeFormatter.ofPattern("EEEE, MMM d"))
    }.getOrDefault(day.date)
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(title, style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                Text("verified $done/${all.size}",
                    color = if (done == all.size) Success else FgMuted,
                    style = MaterialTheme.typography.labelMedium)
            }
            Text("${day.receiptCount} receipts · ${day.units} units · ${day.itemCount} items",
                color = FgMuted, style = MaterialTheme.typography.bodySmall)
            day.pos.forEach { g ->
                HorizontalDivider(Modifier.padding(vertical = 8.dp))
                Text("PO ${g.poNumber ?: "?"}" + (g.vendor?.let { " · $it" } ?: ""),
                    fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
                g.receipts.forEach { r ->
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.fillMaxWidth().clickable { onToggle(r) },
                    ) {
                        Checkbox(checked = isVerified(r), onCheckedChange = { onToggle(r) })
                        Column(Modifier.weight(1f)) {
                            Text(r.itemName ?: "(unknown)", style = MaterialTheme.typography.bodyMedium)
                            Text(
                                listOfNotNull(
                                    r.time.takeIf { it.isNotEmpty() },
                                    r.lineNo?.let { "line $it" },
                                    r.itemModel,
                                    r.receivedBy,
                                    r.notes,
                                ).joinToString(" · "),
                                color = FgMuted, style = MaterialTheme.typography.bodySmall,
                            )
                        }
                        Text("×${r.qty}", fontWeight = FontWeight.Bold,
                            modifier = Modifier.padding(start = 8.dp))
                    }
                }
            }
        }
    }
}
