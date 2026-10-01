package com.pothang.receiver.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.pothang.receiver.data.ApiClient
import com.pothang.receiver.data.ApiException
import com.pothang.receiver.data.Settings
import com.pothang.receiver.ui.theme.FgMuted
import com.pothang.receiver.ui.theme.Success
import kotlinx.coroutines.launch

@Composable
fun SettingsScreen(
    settings: Settings,
    api: ApiClient,
    canGoBack: Boolean,
    onBack: () -> Unit,
    onDone: () -> Unit,
) {
    var url by rememberSaveable { mutableStateOf(settings.serverUrl) }
    var token by rememberSaveable { mutableStateOf(settings.apiToken) }
    var status by rememberSaveable { mutableStateOf<String?>(null) }
    var error by rememberSaveable { mutableStateOf<String?>(null) }
    var testing by rememberSaveable { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    fun test(thenDone: Boolean) {
        settings.save(url, token)
        url = settings.serverUrl
        testing = true
        status = null
        error = null
        scope.launch {
            try {
                val user = api.ping()
                status = "Connected as $user."
                if (thenDone) onDone()
            } catch (e: ApiException) {
                error = e.message
            } finally {
                testing = false
            }
        }
    }

    AppScaffold("Settings", onBack = if (canGoBack) onBack else null) { pad ->
        Column(
            Modifier
                .padding(pad)
                .fillMaxSize()
                .imePadding()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Column(
                Modifier.widthIn(max = 560.dp).fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text("Connect to PurchaseTracker", style = MaterialTheme.typography.titleLarge)
                Text(
                    "Use the address you open the web app with, e.g. " +
                        "http://192.168.1.20:5000. On installs that use LDAP or an " +
                        "auth proxy, add an API token from API_TOKENS in instance/config.py.",
                    color = FgMuted, style = MaterialTheme.typography.bodyMedium,
                )
                OutlinedTextField(
                    value = url, onValueChange = { url = it.trim() },
                    label = { Text("Server address") }, singleLine = true,
                    placeholder = { Text("http://192.168.1.20:5000") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = token, onValueChange = { token = it.trim() },
                    label = { Text("API token (optional)") }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Auto look-up while scanning")
                        Text("Search as soon as the camera reads steady text. " +
                            "Off = tap Look up yourself.",
                            color = FgMuted, style = MaterialTheme.typography.bodySmall)
                    }
                    Switch(checked = settings.autoLookup, onCheckedChange = settings::updateAutoLookup)
                }
                status?.let { Text(it, color = Success) }
                ErrorText(error)
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    OutlinedButton(onClick = { test(false) }, enabled = url.isNotBlank() && !testing) {
                        Text(if (testing) "Testing…" else "Test connection")
                    }
                    Button(onClick = { test(true) }, enabled = url.isNotBlank() && !testing) {
                        Text("Save")
                    }
                }
            }
        }
    }
}
