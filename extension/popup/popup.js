import * as storage from "../lib/storage.js";
import { toCsv, toJson, download } from "../lib/export.js";

const FIELDS = [
  "name", "description", "vendor", "model", "vendor_sku",
  "url", "qty", "unit_cost", "notes",
];

// Last successful scrape, used by the "Reset to scraped" button and by
// the follow-up flow after Add (so the user can re-add a variant quickly).
let scraped = null;

const $ = (id) => document.getElementById(id);

async function init() {
  attachHandlers();
  await renderList();
  await tryScrape();
}

async function tryScrape() {
  const status = $("capture-status");
  const form = $("capture-form");

  let tabs;
  try {
    tabs = await browser.tabs.query({ active: true, currentWindow: true });
  } catch (e) {
    status.textContent = "Couldn't read the active tab: " + e.message;
    return;
  }
  const tab = tabs && tabs[0];
  if (!tab) {
    status.textContent = "No active tab.";
    return;
  }
  if (!/^https?:\/\/[^/]*\.amazon\./i.test(tab.url || "")) {
    status.textContent = "Open an Amazon product page to capture from it.";
    return;
  }

  let resp;
  try {
    resp = await browser.tabs.sendMessage(tab.id, { type: "scrape" });
  } catch (e) {
    status.textContent =
      "Couldn't reach the content script. Try reloading the tab.";
    return;
  }
  if (!resp || !resp.ok) {
    status.textContent =
      "Scrape failed" + (resp && resp.error ? `: ${resp.error}` : ".");
    return;
  }
  scraped = resp.data;
  fillForm(scraped);
  form.hidden = false;

  const missing = FIELDS.filter((f) => {
    const v = scraped[f];
    if (f === "qty") return false; // always defaulted to 1
    if (f === "unit_cost") return !v;
    return !v;
  });
  if (missing.length) {
    status.textContent =
      "Review and edit, then click Add. Missing: " + missing.join(", ") + ".";
  } else {
    status.textContent = "Review and edit, then click Add.";
  }
}

function fillForm(data) {
  const form = $("capture-form");
  for (const f of FIELDS) {
    const el = form.elements[f];
    if (!el) continue;
    el.value = data[f] !== undefined && data[f] !== null ? data[f] : "";
  }
  $("raw-view").textContent = JSON.stringify(data, null, 2);
}

function readForm() {
  const form = $("capture-form");
  const out = {};
  for (const f of FIELDS) {
    const el = form.elements[f];
    if (!el) { out[f] = ""; continue; }
    let v = (el.value || "").trim();
    if (f === "qty") {
      const n = parseInt(v, 10);
      v = Number.isFinite(n) && n > 0 ? n : 1;
    } else if (f === "unit_cost") {
      const n = parseFloat(v);
      v = Number.isFinite(n) && n >= 0 ? n : 0;
    }
    out[f] = v;
  }
  if (scraped) {
    out.source = scraped.source;
    out.source_host = scraped.source_host;
    out.scraped_at = scraped.scraped_at;
  } else {
    out.source = "manual";
  }
  return out;
}

function attachHandlers() {
  $("capture-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const item = readForm();
    if (!item.name) {
      flash("Name is required.");
      return;
    }
    await storage.addItem(item);
    await renderList();
    flash("Added.");
  });

  $("reset-btn").addEventListener("click", () => {
    if (scraped) {
      fillForm(scraped);
      flash("Fields reset to scraped values.");
    }
  });

  const rawBtn = $("raw-btn");
  const rawView = $("raw-view");
  rawBtn.addEventListener("click", () => {
    rawView.hidden = !rawView.hidden;
    rawBtn.textContent = rawView.hidden ? "Show raw" : "Hide raw";
  });

  $("export-csv-btn").addEventListener("click", async () => {
    const items = await storage.getList();
    if (!items.length) { flash("List is empty."); return; }
    download(filename("csv"), toCsv(items), "text/csv");
  });

  $("export-json-btn").addEventListener("click", async () => {
    const items = await storage.getList();
    if (!items.length) { flash("List is empty."); return; }
    download(filename("json"), toJson(items), "application/json");
  });

  $("clear-btn").addEventListener("click", async () => {
    const items = await storage.getList();
    if (!items.length) return;
    if (!confirm(`Clear ${items.length} captured item${items.length === 1 ? "" : "s"}?`)) return;
    await storage.clearList();
    await renderList();
  });
}

function filename(ext) {
  const stamp = new Date().toISOString().slice(0, 10);
  return `hfcs-${stamp}.${ext}`;
}

async function renderList() {
  const items = await storage.getList();
  $("list-count").textContent = `(${items.length})`;
  $("list-empty").hidden = items.length > 0;

  const ol = $("list");
  ol.replaceChildren();
  for (const it of items) {
    const li = document.createElement("li");

    const main = document.createElement("div");
    main.className = "item-main";

    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = it.name || "(unnamed)";
    title.title = it.name || "";

    const meta = document.createElement("div");
    meta.className = "item-meta";
    const parts = [];
    if (it.vendor) parts.push(it.vendor);
    if (it.vendor_sku) parts.push(it.vendor_sku);
    parts.push(`qty ${it.qty}`);
    if (it.unit_cost) parts.push("$" + Number(it.unit_cost).toFixed(2));
    if (it.captured_at) parts.push(it.captured_at.slice(0, 10));
    meta.textContent = parts.join(" · ");
    meta.title = (it.url || "") + (it.captured_at ? `\nCaptured ${it.captured_at}` : "");

    main.append(title, meta);

    const rm = document.createElement("button");
    rm.textContent = "×";
    rm.title = "Remove from list";
    rm.className = "remove-btn";
    rm.addEventListener("click", async () => {
      await storage.removeItem(it.id);
      await renderList();
    });

    li.append(main, rm);
    ol.append(li);
  }
}

let flashTimer = null;
function flash(msg) {
  const s = $("capture-status");
  if (!s) return;
  s.textContent = msg;
  if (flashTimer) clearTimeout(flashTimer);
  flashTimer = setTimeout(() => {
    // Restore the contextual status (so the missing-fields hint comes back).
    if (scraped) tryRestoreStatus();
  }, 1500);
}

function tryRestoreStatus() {
  const s = $("capture-status");
  if (!s || !scraped) return;
  const missing = FIELDS.filter((f) => {
    if (f === "qty") return false;
    if (f === "unit_cost") return !scraped[f];
    return !scraped[f];
  });
  s.textContent = missing.length
    ? "Review and edit, then click Add. Missing: " + missing.join(", ") + "."
    : "Review and edit, then click Add.";
}

init();
