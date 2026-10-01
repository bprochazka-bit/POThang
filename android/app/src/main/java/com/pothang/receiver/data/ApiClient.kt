package com.pothang.receiver.data

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

class ApiException(message: String, val status: Int = 0) : Exception(message)

/** A photo to upload with a new item (already JPEG-encoded). */
class UploadPhoto(val fileName: String, val jpeg: ByteArray)

/**
 * Thin client for PurchaseTracker's /api/v1 endpoints.
 *
 * Reads the server URL and token from [Settings] on every call, so changing
 * them in the settings screen takes effect immediately.
 */
class ApiClient(private val settings: Settings) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    private val jsonType = "application/json; charset=utf-8".toMediaType()

    // ---- endpoints ----

    suspend fun ping(): String =
        get("/api/v1/ping").optString("user", "?")

    suspend fun receivablePos(): List<PoSummary> =
        get("/api/v1/receiving/pos").optJSONArray("pos").objects().map { PoSummary.from(it) }

    suspend fun poLines(poId: Int, includeReceived: Boolean): Pair<PoSummary, List<PoLine>> {
        val o = get("/api/v1/pos/$poId/lines" + if (includeReceived) "?include_received=1" else "")
        return PoSummary.from(o.getJSONObject("po")) to
            o.optJSONArray("lines").objects().map { PoLine.from(it) }
    }

    suspend fun match(text: String, poId: Int?): MatchResult {
        val body = JSONObject().put("text", text).put("limit", 8)
        if (poId != null) body.put("po_id", poId)
        val o = post("/api/v1/receiving/match", body)
        return MatchResult(
            matches = o.optJSONArray("matches").objects().map { PoLine.from(it) },
            otherItems = o.optJSONArray("other_items").objects().map { ItemInfo.from(it) },
        )
    }

    /** Records a receipt; returns the server's message and the updated line. */
    suspend fun receive(lineId: Int, qty: Int, notes: String?): Pair<String, PoLine> {
        val body = JSONObject().put("qty", qty)
        if (!notes.isNullOrBlank()) body.put("notes", notes)
        val o = post("/api/v1/lines/$lineId/receive", body)
        return o.optString("message") to PoLine.from(o.getJSONObject("line"))
    }

    suspend fun parseItemText(text: String, lines: List<Pair<String, Int>>): JSONObject {
        val arr = JSONArray()
        lines.forEach { (t, h) -> arr.put(JSONObject().put("text", t).put("height", h)) }
        return post("/api/v1/items/parse", JSONObject().put("text", text).put("lines", arr))
            .optJSONObject("fields") ?: JSONObject()
    }

    suspend fun createItem(draft: ItemDraft, photos: List<UploadPhoto>): ItemInfo {
        val form = MultipartBody.Builder().setType(MultipartBody.FORM)
        fun field(k: String, v: String) { if (v.isNotBlank()) form.addFormDataPart(k, v.trim()) }
        field("name", draft.name)
        field("description", draft.description)
        field("model", draft.model)
        field("vendor", draft.vendor)
        field("vendor_sku", draft.vendorSku)
        field("url", draft.url)
        field("qty", draft.qty)
        field("unit_cost", draft.unitCost.removePrefix("$"))
        field("tags", draft.tags)
        field("notes", draft.notes)
        photos.forEach {
            form.addFormDataPart("photo", it.fileName,
                it.jpeg.toRequestBody("image/jpeg".toMediaType()))
        }
        return ItemInfo.from(send("POST", "/api/v1/items", form.build()).getJSONObject("item"))
    }

    suspend fun tags(): List<String> = get("/api/v1/tags").optJSONArray("tags").strings()

    suspend fun receiptsByDay(from: String?, to: String?): List<ReceiptDay> {
        val q = buildList {
            from?.let { add("from=$it") }
            to?.let { add("to=$it") }
        }.joinToString("&")
        return get("/api/v1/receipts/by-day" + if (q.isEmpty()) "" else "?$q")
            .optJSONArray("days").objects().map { ReceiptDay.from(it) }
    }

    suspend fun setVerified(receiptId: Int, verified: Boolean): ReceiptInfo =
        ReceiptInfo.from(
            post("/api/v1/receipts/$receiptId/verify", JSONObject().put("verified", verified))
                .getJSONObject("receipt")
        )

    /** Fetches a server-relative image (e.g. an item photo) with auth. */
    suspend fun image(path: String, maxPx: Int = 400): Bitmap? = withContext(Dispatchers.IO) {
        try {
            http.newCall(request(path).get().build()).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val bytes = resp.body?.bytes() ?: return@withContext null
                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                var sample = 1
                while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= maxPx) sample *= 2
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size,
                    BitmapFactory.Options().apply { inSampleSize = sample })
            }
        } catch (e: Exception) {
            null  // a missing thumbnail is never worth an error message
        }
    }

    // ---- plumbing ----

    private fun request(path: String): Request.Builder {
        if (!settings.isConfigured) throw ApiException("Set the server address in Settings first.")
        val b = Request.Builder().url(settings.serverUrl + path)
            .header("Accept", "application/json")
        if (settings.apiToken.isNotBlank()) {
            b.header("Authorization", "Bearer ${settings.apiToken}")
        }
        return b
    }

    private suspend fun get(path: String) = send("GET", path, null)

    private suspend fun post(path: String, body: JSONObject) =
        send("POST", path, body.toString().toRequestBody(jsonType))

    private suspend fun send(method: String, path: String, body: RequestBody?): JSONObject =
        withContext(Dispatchers.IO) {
            val req = try {
                request(path).method(method, body).build()
            } catch (e: IllegalArgumentException) {
                throw ApiException("Invalid server address: ${settings.serverUrl}")
            }
            val resp = try {
                http.newCall(req).execute()
            } catch (e: IOException) {
                throw ApiException("Can't reach ${settings.serverUrl}: ${e.message ?: e.javaClass.simpleName}")
            }
            resp.use {
                val text = it.body?.string().orEmpty()
                val json = try {
                    JSONObject(text)
                } catch (e: JSONException) {
                    // An HTML login page or proxy error instead of JSON.
                    throw ApiException(
                        if (it.isSuccessful || it.code == 302)
                            "Server didn't return JSON - is this the PurchaseTracker address, " +
                                "and is /api/ allowed past your auth proxy?"
                        else "HTTP ${it.code} from server", it.code)
                }
                if (!it.isSuccessful || !json.optBoolean("ok", true)) {
                    throw ApiException(json.optString("error").ifBlank { "HTTP ${it.code}" }, it.code)
                }
                json
            }
        }
}
