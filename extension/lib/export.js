// CSV / JSON serialization for the captured-items list.
//
// Column names match PurchaseTracker's import-wizard field aliases in
// purchasetracker/import_parsers.py (PT_FIELD_ALIASES), so the wizard will
// auto-map every column without user intervention.

const COLUMNS = [
  "name",
  "description",
  "vendor",
  "model",
  "vendor_sku",
  "url",
  "qty",
  "unit_cost",
  "notes",
];

function csvEscape(v) {
  if (v === null || v === undefined) return "";
  const s = String(v);
  if (/[",\n\r]/.test(s)) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

export function toCsv(items) {
  const lines = [COLUMNS.join(",")];
  for (const it of items) {
    lines.push(COLUMNS.map((c) => csvEscape(it[c])).join(","));
  }
  return lines.join("\r\n") + "\r\n";
}

export function toJson(items) {
  // Match PT's full-fidelity export shape: {"items":[{...}]}. Strip the
  // extension-only bookkeeping fields (id, captured_at, source, source_host,
  // scraped_at) so the wizard sees a clean record.
  const stripped = items.map((it) => {
    const out = {};
    for (const c of COLUMNS) out[c] = it[c] ?? "";
    return out;
  });
  return JSON.stringify({ items: stripped }, null, 2);
}

export function download(filename, content, mime) {
  const blob = new Blob([content], { type: mime + ";charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
