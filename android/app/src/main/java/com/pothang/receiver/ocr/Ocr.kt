package com.pothang.receiver.ocr

import android.content.Context
import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.net.Uri
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.Text
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream

/** Recognized text plus each line's pixel height (tall lines = headlines). */
data class OcrResult(val text: String, val lines: List<Pair<String, Int>>) {
    val isEmpty get() = text.isBlank()

    companion object {
        val EMPTY = OcrResult("", emptyList())

        fun from(t: Text): OcrResult {
            val lines = t.textBlocks.flatMap { b ->
                b.lines.map { it.text to (it.boundingBox?.height() ?: 0) }
            }
            return OcrResult(t.text, lines)
        }

        fun merge(results: List<OcrResult>) = OcrResult(
            results.joinToString("\n") { it.text }.trim(),
            results.flatMap { it.lines },
        )
    }
}

object Ocr {
    /** Bundled Latin model: on-device, works without network or Play downloads. */
    val recognizer by lazy { TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS) }

    suspend fun recognize(bitmap: Bitmap): OcrResult =
        OcrResult.from(recognizer.process(InputImage.fromBitmap(bitmap, 0)).await())

    /**
     * Decodes an image Uri (camera capture, gallery pick, shared screenshot),
     * honouring EXIF rotation and downscaling so the long edge <= [maxPx].
     */
    suspend fun loadBitmap(context: Context, uri: Uri, maxPx: Int = 2000): Bitmap =
        withContext(Dispatchers.IO) {
            val source = ImageDecoder.createSource(context.contentResolver, uri)
            ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
                val w = info.size.width
                val h = info.size.height
                val scale = maxPx.toFloat() / maxOf(w, h)
                if (scale < 1f) decoder.setTargetSize((w * scale).toInt(), (h * scale).toInt())
                // Software bitmaps so ML Kit and JPEG encoding can read pixels.
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            }
        }

    fun toJpeg(bitmap: Bitmap, maxPx: Int = 1600, quality: Int = 85): ByteArray {
        val scale = maxPx.toFloat() / maxOf(bitmap.width, bitmap.height)
        val scaled = if (scale < 1f) {
            Bitmap.createScaledBitmap(bitmap, (bitmap.width * scale).toInt(),
                (bitmap.height * scale).toInt(), true)
        } else bitmap
        return ByteArrayOutputStream().use {
            scaled.compress(Bitmap.CompressFormat.JPEG, quality, it)
            it.toByteArray()
        }
    }
}

/**
 * CameraX analyzer that OCRs frames continuously, at most once per
 * [intervalMs], and reports results on ML Kit's callback thread.
 */
class TextAnalyzer(
    private val intervalMs: Long = 600,
    private val onResult: (OcrResult) -> Unit,
) : ImageAnalysis.Analyzer {
    private var lastRun = 0L
    @Volatile var paused = false

    @androidx.annotation.OptIn(androidx.camera.core.ExperimentalGetImage::class)
    override fun analyze(proxy: ImageProxy) {
        val now = System.currentTimeMillis()
        val media = proxy.image
        if (paused || media == null || now - lastRun < intervalMs) {
            proxy.close()
            return
        }
        lastRun = now
        val input = InputImage.fromMediaImage(media, proxy.imageInfo.rotationDegrees)
        Ocr.recognizer.process(input)
            .addOnSuccessListener { onResult(OcrResult.from(it)) }
            .addOnCompleteListener { proxy.close() }
    }
}
