package com.pothang.receiver.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.pothang.receiver.data.ApiClient
import com.pothang.receiver.data.ApiException
import com.pothang.receiver.data.PoLine
import com.pothang.receiver.ui.theme.FgMuted
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Asks how many of [line] arrived and records the receipt.
 *
 * Defaults to everything outstanding (the common case: the whole line is in
 * the box) with quick "1" / "All" chips and a stepper for partial counts.
 * The qty field is focused with its text selected so typing replaces it.
 */
@Composable
fun ReceiveDialog(
    api: ApiClient,
    line: PoLine,
    onDismiss: () -> Unit,
    onReceived: (message: String, updated: PoLine) -> Unit,
) {
    val outstanding = line.outstanding.coerceAtLeast(0)
    var qty by remember(line.id) {
        val s = outstanding.toString()
        mutableStateOf(TextFieldValue(s, TextRange(0, s.length)))
    }
    var notes by rememberSaveable(line.id) { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val focus = remember { FocusRequester() }
    val qtyInt = qty.text.toIntOrNull()
    val valid = qtyInt != null && qtyInt in 1..outstanding

    fun setQty(n: Int) {
        val s = n.coerceIn(1, maxOf(1, outstanding)).toString()
        qty = TextFieldValue(s, TextRange(s.length))
    }

    fun submit() {
        if (!valid || busy) return
        busy = true
        error = null
        scope.launch {
            try {
                val (msg, updated) = api.receive(line.id, qtyInt!!, notes)
                onReceived(msg, updated)
            } catch (e: ApiException) {
                error = e.message
            } finally {
                busy = false
            }
        }
    }

    LaunchedEffect(Unit) {
        // The dialog's window attaches a frame later; focusing too early throws.
        delay(150)
        if (outstanding > 0) runCatching { focus.requestFocus() }
    }

    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text("Receive item") },
        text = {
            Column(
                Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    ItemThumb(api, line.item?.imageUrl, size = 64.dp)
                    Spacer(Modifier.width(12.dp))
                    Column {
                        Text(line.item?.name ?: "(unknown)", fontWeight = FontWeight.SemiBold)
                        line.item?.identifiers?.takeIf { it.isNotEmpty() }?.let {
                            Text(it, style = MaterialTheme.typography.bodySmall, color = FgMuted)
                        }
                        Text("PO ${line.poNumber} · line ${line.lineNo}",
                            style = MaterialTheme.typography.bodySmall, color = FgMuted)
                    }
                }
                Text(
                    "Ordered ${line.qty} · received ${line.qtyReceived} · " +
                        "outstanding $outstanding",
                    style = MaterialTheme.typography.bodyMedium,
                )
                if (outstanding <= 0) {
                    ErrorText("This line is already fully received.")
                } else {
                    Row(verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilledTonalIconButton(onClick = { setQty((qtyInt ?: 1) - 1) }) {
                            Icon(Icons.Filled.Remove, "Fewer")
                        }
                        OutlinedTextField(
                            value = qty,
                            onValueChange = { v -> qty = v.copy(text = v.text.filter(Char::isDigit).take(5)) },
                            label = { Text("Qty received") },
                            singleLine = true,
                            isError = qty.text.isNotEmpty() && !valid,
                            textStyle = MaterialTheme.typography.headlineSmall.copy(textAlign = TextAlign.Center),
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number,
                                imeAction = ImeAction.Done),
                            keyboardActions = KeyboardActions(onDone = { submit() }),
                            modifier = Modifier.width(140.dp).focusRequester(focus),
                        )
                        FilledTonalIconButton(onClick = { setQty((qtyInt ?: 0) + 1) }) {
                            Icon(Icons.Filled.Add, "More")
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        AssistChip(onClick = { setQty(1) }, label = { Text("1") })
                        AssistChip(onClick = { setQty(outstanding) }, label = { Text("All ($outstanding)") })
                    }
                    if (qtyInt != null && qtyInt > outstanding) {
                        Text("Only $outstanding outstanding on this line.",
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall)
                    }
                    OutlinedTextField(
                        value = notes, onValueChange = { notes = it.take(255) },
                        label = { Text("Note (optional)") }, singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                ErrorText(error)
            }
        },
        confirmButton = {
            Button(onClick = { submit() }, enabled = valid && !busy) {
                if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                else Text(if (valid) "Receive $qtyInt" else "Receive")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, enabled = !busy) { Text("Cancel") }
        },
    )
}
