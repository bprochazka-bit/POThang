package com.pothang.receiver.data

import org.json.JSONArray
import org.json.JSONObject

data class ItemInfo(
    val id: Int,
    val name: String,
    val description: String?,
    val model: String?,
    val vendor: String?,
    val vendorSku: String?,
    val url: String?,
    val qty: Int,
    val state: String?,
    val tags: List<String>,
    val imageUrl: String?,
    val webUrl: String?,
    val score: Double? = null,
    val reasons: List<String> = emptyList(),
) {
    /** "Model WH-1000XM5 · SKU B0..." for compact display. */
    val identifiers: String
        get() = listOfNotNull(
            model?.takeIf { it.isNotBlank() }?.let { "Model $it" },
            vendorSku?.takeIf { it.isNotBlank() }?.let { "SKU $it" },
        ).joinToString(" · ")

    companion object {
        fun from(o: JSONObject) = ItemInfo(
            id = o.getInt("id"),
            name = o.optString("name"),
            description = o.str("description"),
            model = o.str("model"),
            vendor = o.str("vendor"),
            vendorSku = o.str("vendor_sku"),
            url = o.str("url"),
            qty = o.optInt("qty", 1),
            state = o.str("state"),
            tags = o.optJSONArray("tags").strings(),
            imageUrl = o.str("image_url"),
            webUrl = o.str("web_url"),
            score = if (o.has("score")) o.optDouble("score") else null,
            reasons = o.optJSONArray("reasons").strings(),
        )
    }
}

data class PoLine(
    val id: Int,
    val lineNo: Int,
    val poId: Int,
    val poNumber: String,
    val poVendor: String?,
    val qty: Int,
    val qtyReceived: Int,
    val outstanding: Int,
    val item: ItemInfo?,
    val score: Double? = null,
    val reasons: List<String> = emptyList(),
) {
    companion object {
        fun from(o: JSONObject) = PoLine(
            id = o.getInt("id"),
            lineNo = o.optInt("line_no"),
            poId = o.optInt("po_id"),
            poNumber = o.optString("po_number"),
            poVendor = o.str("po_vendor"),
            qty = o.optInt("qty"),
            qtyReceived = o.optInt("qty_received"),
            outstanding = o.optInt("outstanding"),
            item = o.optJSONObject("item")?.let { ItemInfo.from(it) },
            score = if (o.has("score")) o.optDouble("score") else null,
            reasons = o.optJSONArray("reasons").strings(),
        )
    }
}

data class PoSummary(
    val id: Int,
    val poNumber: String,
    val vendor: String?,
    val status: String,
    val totalLines: Int,
    val outstandingLines: Int,
    val totalOrdered: Int,
    val totalReceived: Int,
    val outstanding: Int,
) {
    companion object {
        fun from(o: JSONObject) = PoSummary(
            id = o.getInt("id"),
            poNumber = o.optString("po_number"),
            vendor = o.str("vendor"),
            status = o.optString("status"),
            totalLines = o.optInt("total_lines"),
            outstandingLines = o.optInt("outstanding_lines"),
            totalOrdered = o.optInt("total_ordered"),
            totalReceived = o.optInt("total_received"),
            outstanding = o.optInt("outstanding"),
        )
    }
}

data class MatchResult(val matches: List<PoLine>, val otherItems: List<ItemInfo>)

data class ReceiptInfo(
    val id: Int,
    val qty: Int,
    val receivedAt: String?,
    val receivedBy: String?,
    val notes: String?,
    val verified: Boolean,
    val verifiedBy: String?,
    val lineNo: Int?,
    val lineQty: Int?,
    val itemName: String?,
    val itemModel: String?,
) {
    /** "14:05" from "2026-09-30T14:05". */
    val time: String get() = receivedAt?.substringAfter('T', "")?.take(5) ?: ""

    companion object {
        fun from(o: JSONObject) = ReceiptInfo(
            id = o.getInt("id"),
            qty = o.optInt("qty"),
            receivedAt = o.str("received_at"),
            receivedBy = o.str("received_by"),
            notes = o.str("notes"),
            verified = o.optBoolean("verified"),
            verifiedBy = o.str("verified_by"),
            lineNo = if (o.has("line_no") && !o.isNull("line_no")) o.optInt("line_no") else null,
            lineQty = if (o.has("line_qty") && !o.isNull("line_qty")) o.optInt("line_qty") else null,
            itemName = o.str("item_name"),
            itemModel = o.str("item_model"),
        )
    }
}

data class DayPoGroup(
    val poId: Int?,
    val poNumber: String?,
    val vendor: String?,
    val units: Int,
    val receipts: List<ReceiptInfo>,
)

data class ReceiptDay(
    val date: String,
    val receiptCount: Int,
    val units: Int,
    val itemCount: Int,
    val verifiedCount: Int,
    val pos: List<DayPoGroup>,
) {
    companion object {
        fun from(o: JSONObject) = ReceiptDay(
            date = o.optString("date"),
            receiptCount = o.optInt("receipt_count"),
            units = o.optInt("units"),
            itemCount = o.optInt("item_count"),
            verifiedCount = o.optInt("verified_count"),
            pos = o.optJSONArray("pos").objects().map { g ->
                DayPoGroup(
                    poId = if (g.isNull("po_id")) null else g.optInt("po_id"),
                    poNumber = g.str("po_number"),
                    vendor = g.str("vendor"),
                    units = g.optInt("units"),
                    receipts = g.optJSONArray("receipts").objects().map { ReceiptInfo.from(it) },
                )
            },
        )
    }
}

/** Editable fields for a new item; also what /items/parse suggests. */
data class ItemDraft(
    val name: String = "",
    val description: String = "",
    val model: String = "",
    val vendor: String = "",
    val vendorSku: String = "",
    val url: String = "",
    val qty: String = "1",
    val unitCost: String = "",
    val tags: String = "",
    val notes: String = "",
) {
    /** Fill only the blanks from server suggestions; never clobber user edits. */
    fun mergeSuggestions(o: JSONObject): ItemDraft = copy(
        name = name.ifBlank { o.str("name") ?: "" },
        description = description.ifBlank { o.str("description") ?: "" },
        model = model.ifBlank { o.str("model") ?: "" },
        vendor = vendor.ifBlank { o.str("vendor") ?: "" },
        vendorSku = vendorSku.ifBlank { o.str("vendor_sku") ?: "" },
        url = url.ifBlank { o.str("url") ?: "" },
        qty = if (qty.isBlank() || qty == "1") (o.str("qty") ?: qty) else qty,
        unitCost = unitCost.ifBlank {
            if (o.has("unit_cost")) "%.2f".format(o.optDouble("unit_cost")) else ""
        },
    )
}

// ---- org.json helpers ----

internal fun JSONObject.str(key: String): String? =
    if (!has(key) || isNull(key)) null else optString(key).takeIf { it.isNotEmpty() }

internal fun JSONArray?.strings(): List<String> =
    if (this == null) emptyList() else (0 until length()).map { optString(it) }

internal fun JSONArray?.objects(): List<JSONObject> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }
