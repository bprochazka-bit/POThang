package com.pothang.receiver.ui

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.PhotoLibrary
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import com.pothang.receiver.data.ApiClient
import com.pothang.receiver.data.ApiException
import com.pothang.receiver.data.ItemDraft
import com.pothang.receiver.data.ItemInfo
import com.pothang.receiver.data.UploadPhoto
import com.pothang.receiver.ocr.Ocr
import com.pothang.receiver.ocr.OcrResult
import com.pothang.receiver.ui.theme.FgMuted
import com.pothang.receiver.ui.theme.Success
import kotlinx.coroutines.launch
import java.io.File

private class SourceImage(val bitmap: Bitmap, val ocr: OcrResult)

/** Fields an OCR line can be tapped into. */
private enum class Target(val label: String) {
    NAME("Name"), DESCRIPTION("Description"), MODEL("Model"), SKU("SKU"),
    VENDOR("Vendor"), URL("URL"), PRICE("Price"), NOTES("Notes"),
}

private fun ItemDraft.assign(target: Target, text: String): ItemDraft = when (target) {
    Target.NAME -> copy(name = text)
    Target.DESCRIPTION -> copy(description = if (description.isBlank()) text else "$description $text")
    Target.MODEL -> copy(model = text)
    Target.SKU -> copy(vendorSku = text)
    Target.VENDOR -> copy(vendor = text)
    Target.URL -> copy(url = text)
    Target.PRICE -> copy(unitCost = Regex("[0-9]+(?:[.,][0-9]{1,2})?").find(text.replace(",", ""))?.value ?: unitCost)
    Target.NOTES -> copy(notes = if (notes.isBlank()) text else "$notes\n$text")
}

/**
 * Add a new item from a photo and/or screenshot. Each image is OCR'd on the
 * device; the server suggests field values from the combined text, and any
 * recognized line can be tapped into a field. Images are attached to the
 * new item.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun NewItemScreen(
    api: ApiClient,
    initialText: String?,
    sharedImage: Uri?,
    onSharedImageConsumed: () -> Unit,
    onBack: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val images = remember { mutableStateListOf<SourceImage>() }
    var draft by remember { mutableStateOf(ItemDraft()) }
    var target by remember { mutableStateOf(Target.NAME) }
    var attachPhotos by remember { mutableStateOf(true) }
    var working by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var created by remember { mutableStateOf<ItemInfo?>(null) }
    var knownTags by remember { mutableStateOf<List<String>>(emptyList()) }
    var pendingCaptureUri by remember { mutableStateOf<Uri?>(null) }

    val allOcr = OcrResult.merge(images.map { it.ocr })
    val ocrLines = allOcr.lines.map { it.first.trim() }.filter { it.length >= 2 }.distinct()

    suspend fun suggest(text: String, lines: List<Pair<String, Int>>) {
        if (text.isBlank()) return
        try {
            draft = draft.mergeSuggestions(api.parseItemText(text, lines))
        } catch (e: ApiException) {
            error = e.message
        }
    }

    fun addImage(uri: Uri) {
        scope.launch {
            working = "Reading text…"
            error = null
            try {
                val bmp = Ocr.loadBitmap(context, uri)
                val ocr = Ocr.recognize(bmp)
                images.add(SourceImage(bmp, ocr))
                if (ocr.isEmpty) error = "No text found in that image - fill the fields by hand."
                else suggest(ocr.text, ocr.lines)
            } catch (e: Exception) {
                error = "Couldn't read that image: ${e.message}"
            } finally {
                working = null
            }
        }
    }

    val pickImages = rememberLauncherForActivityResult(
        ActivityResultContracts.PickMultipleVisualMedia(maxItems = 4)
    ) { uris -> uris.forEach { addImage(it) } }

    val takePicture = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val uri = pendingCaptureUri
        if (ok && uri != null) addImage(uri)
    }

    fun launchCamera() {
        val dir = File(context.cacheDir, "photos").apply { mkdirs() }
        val file = File.createTempFile("item-", ".jpg", dir)
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", file)
        pendingCaptureUri = uri
        takePicture.launch(uri)
    }

    // The app declares CAMERA, so the system camera intent needs it granted too.
    val cameraPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> if (granted) launchCamera() else error = "Camera permission denied." }

    fun takePhoto() {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED) launchCamera()
        else cameraPermission.launch(Manifest.permission.CAMERA)
    }

    LaunchedEffect(Unit) {
        runCatching { knownTags = api.tags() }
        initialText?.let { suggest(it, emptyList()) }
    }
    LaunchedEffect(sharedImage) {
        sharedImage?.let { addImage(it); onSharedImageConsumed() }
    }

    fun save() {
        if (draft.name.isBlank()) {
            error = "Name is required."
            return
        }
        scope.launch {
            working = "Saving…"
            error = null
            try {
                val photos = if (attachPhotos) images.mapIndexed { i, img ->
                    UploadPhoto("item-photo-${i + 1}.jpg", Ocr.toJpeg(img.bitmap))
                } else emptyList()
                created = api.createItem(draft, photos)
            } catch (e: ApiException) {
                error = e.message
            } finally {
                working = null
            }
        }
    }

    fun reset() {
        images.clear()
        draft = ItemDraft(tags = draft.tags)  // keep tags: batches usually share them
        created = null
        error = null
        target = Target.NAME
    }

    AppScaffold("New item", onBack = onBack) { pad ->
        val done = created
        if (done != null) {
            Column(Modifier.padding(pad).padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("Added “${done.name}”", style = MaterialTheme.typography.titleLarge, color = Success)
                Text("Item #${done.id} is now in the requested state. Finish any missing " +
                    "details on the web app before putting it on a PO.", color = FgMuted)
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Button(onClick = { reset() }) { Text("Add another") }
                    OutlinedButton(onClick = onBack) { Text("Done") }
                }
            }
            return@AppScaffold
        }

        AdaptiveSplit(
            Modifier.padding(pad).imePadding(),
            firstWeight = 0.45f,
            first = { m ->
                Column(m.verticalScroll(rememberScrollState()).padding(12.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilledTonalButton(onClick = { takePhoto() }) {
                            Icon(Icons.Filled.PhotoCamera, null); Spacer(Modifier.width(6.dp)); Text("Photo")
                        }
                        FilledTonalButton(onClick = {
                            pickImages.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
                        }) {
                            Icon(Icons.Filled.PhotoLibrary, null); Spacer(Modifier.width(6.dp)); Text("Screenshot / image")
                        }
                    }
                    working?.let {
                        LinearProgressIndicator(Modifier.fillMaxWidth())
                        Text(it, color = FgMuted, style = MaterialTheme.typography.bodySmall)
                    }
                    if (images.isNotEmpty()) {
                        Row(Modifier.horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            images.forEachIndexed { i, img ->
                                Box {
                                    Image(img.bitmap.asImageBitmap(), null, contentScale = ContentScale.Crop,
                                        modifier = Modifier.size(96.dp).clip(RoundedCornerShape(8.dp)))
                                    IconButton(onClick = { images.removeAt(i) },
                                        modifier = Modifier.align(Alignment.TopEnd).size(28.dp)) {
                                        Icon(Icons.Filled.Close, "Remove image")
                                    }
                                }
                            }
                        }
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("Attach images to the item", modifier = Modifier.weight(1f))
                            Switch(checked = attachPhotos, onCheckedChange = { attachPhotos = it })
                        }
                    } else if (working == null) {
                        Text("Take a photo of the product or label, or pick a screenshot of a " +
                            "product page. Text is read on the phone and used to fill in the form. " +
                            "You can also share a screenshot straight to this app.",
                            color = FgMuted, style = MaterialTheme.typography.bodyMedium)
                    }
                    if (ocrLines.isNotEmpty()) {
                        SectionLabel("Tap text to fill")
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Target.entries.forEach { t ->
                                FilterChip(selected = target == t, onClick = { target = t },
                                    label = { Text(t.label) })
                            }
                        }
                        Card {
                            FlowRow(Modifier.padding(8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                ocrLines.forEach { line ->
                                    SuggestionChip(onClick = { draft = draft.assign(target, line) },
                                        label = { Text(line, maxLines = 2) })
                                }
                            }
                        }
                    }
                }
            },
            second = { m ->
                Column(m.verticalScroll(rememberScrollState()).padding(12.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Field("Name *", draft.name) { draft = draft.copy(name = it) }
                    Field("Description", draft.description, singleLine = false) { draft = draft.copy(description = it) }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Field("Model", draft.model, Modifier.weight(1f)) { draft = draft.copy(model = it) }
                        Field("Vendor SKU", draft.vendorSku, Modifier.weight(1f)) { draft = draft.copy(vendorSku = it) }
                    }
                    Field("Vendor", draft.vendor) { draft = draft.copy(vendor = it) }
                    Field("URL", draft.url, keyboard = KeyboardType.Uri) { draft = draft.copy(url = it) }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Field("Qty", draft.qty, Modifier.weight(1f), KeyboardType.Number) {
                            draft = draft.copy(qty = it.filter(Char::isDigit))
                        }
                        Field("Unit price", draft.unitCost, Modifier.weight(1f), KeyboardType.Decimal) {
                            draft = draft.copy(unitCost = it.filter { c -> c.isDigit() || c == '.' })
                        }
                    }
                    Field("Tags (comma separated)", draft.tags) { draft = draft.copy(tags = it) }
                    if (knownTags.isNotEmpty()) {
                        val current = draft.tags.split(",").map { it.trim() }.filter { it.isNotEmpty() }
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            knownTags.filter { it !in current }.take(12).forEach { tag ->
                                AssistChip(onClick = {
                                    draft = draft.copy(tags = (current + tag).joinToString(", "))
                                }, label = { Text(tag) })
                            }
                        }
                    }
                    Field("Notes", draft.notes, singleLine = false) { draft = draft.copy(notes = it) }
                    ErrorText(error)
                    Button(onClick = { save() }, enabled = working == null && draft.name.isNotBlank(),
                        modifier = Modifier.fillMaxWidth()) {
                        if (working == "Saving…") CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                        else Text("Create item", fontWeight = FontWeight.SemiBold)
                    }
                }
            },
        )
    }
}

@Composable
private fun Field(
    label: String,
    value: String,
    modifier: Modifier = Modifier.fillMaxWidth(),
    keyboard: KeyboardType = KeyboardType.Text,
    singleLine: Boolean = true,
    onChange: (String) -> Unit,
) {
    OutlinedTextField(
        value = value, onValueChange = onChange, label = { Text(label) },
        singleLine = singleLine, minLines = if (singleLine) 1 else 2,
        keyboardOptions = KeyboardOptions(keyboardType = keyboard),
        modifier = modifier,
    )
}
