// Content script for Amazon product pages.
//
// Listens for a {type:"scrape"} message from the popup and replies with a
// structured object whose keys line up with PurchaseTracker's import-wizard
// field aliases (name, description, vendor, model, vendor_sku, url, qty,
// unit_cost, notes). Missing fields come back as empty strings / 0 rather
// than throwing - the popup lets the user fill in or correct anything.

(() => {
  if (window.__ptCaptureAmazonLoaded) return;
  window.__ptCaptureAmazonLoaded = true;

  const ASIN_RE = /^[A-Z0-9]{10}$/;

  function squish(s) {
    return (s || "").replace(/\s+/g, " ").trim();
  }

  function clip(s, max) {
    s = squish(s);
    if (s.length <= max) return s;
    return s.slice(0, max - 1).trimEnd() + "…";
  }

  function textOf(el) {
    return el ? squish(el.textContent) : "";
  }

  function firstText(selectors) {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      const t = textOf(el);
      if (t) return t;
    }
    return "";
  }

  function extractAsin() {
    const m = location.pathname.match(
      /\/(?:dp|gp\/product|product-reviews)\/([A-Z0-9]{10})(?:[/?]|$)/
    );
    if (m) return m[1];
    const inp = document.querySelector("input#ASIN, input[name='ASIN']");
    if (inp && ASIN_RE.test(inp.value || "")) return inp.value;
    const node = document.querySelector("[data-asin]");
    if (node) {
      const a = node.getAttribute("data-asin") || "";
      if (ASIN_RE.test(a)) return a;
    }
    return "";
  }

  function canonicalUrl(asin) {
    if (asin) return `https://${location.hostname}/dp/${asin}`;
    // Fallback: strip query+hash from current URL.
    return location.origin + location.pathname;
  }

  function extractName() {
    return clip(firstText(["#productTitle", "h1#title #productTitle", "h1#title"]), 255);
  }

  function parsePriceText(t) {
    const m = t.match(/[\d.,]+/);
    if (!m) return 0;
    let raw = m[0];
    if (raw.includes(",") && raw.includes(".")) {
      // Mixed: rightmost separator is the decimal mark.
      if (raw.lastIndexOf(",") > raw.lastIndexOf(".")) {
        raw = raw.replace(/\./g, "").replace(",", ".");
      } else {
        raw = raw.replace(/,/g, "");
      }
    } else if (raw.includes(",") && !raw.includes(".")) {
      const parts = raw.split(",");
      // EU style "12,99" - exactly two trailing digits after the comma.
      if (parts.length === 2 && parts[1].length === 2) {
        raw = parts[0] + "." + parts[1];
      } else {
        raw = raw.replace(/,/g, "");
      }
    }
    const f = parseFloat(raw);
    return Number.isFinite(f) ? f : 0;
  }

  function extractPrice() {
    const selectors = [
      "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
      "#corePrice_feature_div .a-price .a-offscreen",
      "#apex_desktop .a-price .a-offscreen",
      "#price_inside_buybox",
      "#newBuyBoxPrice",
      "#priceblock_ourprice",
      "#priceblock_dealprice",
      "#priceblock_saleprice",
      "span.a-price .a-offscreen",
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      const t = textOf(el);
      if (!t) continue;
      const p = parsePriceText(t);
      if (p > 0) return p;
    }
    return 0;
  }

  // Look up a labelled value in the various detail tables/bullets Amazon uses.
  function detailLookup(labels) {
    const wanted = labels.map((l) => l.toLowerCase());

    // a) Product overview table (modern layout)
    const overviewRows = document.querySelectorAll(
      "#productOverview_feature_div tr, #poExpander tr"
    );
    for (const r of overviewRows) {
      const cells = r.querySelectorAll("td");
      if (cells.length < 2) continue;
      const key = textOf(cells[0]).toLowerCase();
      if (wanted.includes(key)) {
        const v = textOf(cells[1]);
        if (v) return v;
      }
    }

    // b) Detail bullets ("Brand : X", "Item model number : Y" etc.)
    const bullets = document.querySelectorAll(
      "#detailBullets_feature_div li, #detailBulletsWrapper_feature_div li"
    );
    for (const li of bullets) {
      // The label and value usually live in adjacent spans.
      const spans = li.querySelectorAll("span");
      if (spans.length >= 2) {
        const key = textOf(spans[0]).replace(/[\s:‎‏]+$/g, "").toLowerCase();
        if (wanted.includes(key)) {
          const v = textOf(spans[1]).replace(/^[\s:‎‏]+/g, "");
          if (v) return v;
        }
      }
      // Fallback: parse the whole bullet's text as "Label : Value".
      const text = textOf(li);
      for (const label of labels) {
        const re = new RegExp(
          "^" + label.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&") + "\\s*[:‎‏]+\\s*(.+)$",
          "i"
        );
        const m = text.match(re);
        if (m) return squish(m[1]);
      }
    }

    // c) Tech-spec / product details tables
    const rows = document.querySelectorAll(
      "#productDetails_techSpec_section_1 tr, #productDetails_detailBullets_sections1 tr, table.prodDetTable tr, table.a-keyvalue tr"
    );
    for (const r of rows) {
      const th = r.querySelector("th");
      const td = r.querySelector("td");
      if (!th || !td) continue;
      const key = textOf(th).toLowerCase();
      if (wanted.includes(key)) {
        const v = textOf(td);
        if (v) return v;
      }
    }
    return "";
  }

  function extractBrand() {
    const byline = textOf(document.querySelector("#bylineInfo"));
    if (byline) {
      let m = byline.match(/Visit the (.+?) Store/i);
      if (m) return squish(m[1]);
      m = byline.match(/^Brand\s*[:：]\s*(.+)$/i);
      if (m) return squish(m[1]);
      // Sometimes byline is just "by BRAND" or the brand name on its own.
      const cleaned = byline.replace(/^by\s+/i, "").trim();
      if (cleaned && cleaned.length < 80 && !/visit/i.test(cleaned)) {
        return cleaned;
      }
    }
    return detailLookup(["Brand", "Manufacturer"]);
  }

  function extractModel() {
    return detailLookup(["Model", "Item model number", "Model Number", "Model number"]);
  }

  function extractDescription() {
    const bullets = document.querySelectorAll(
      "#feature-bullets ul li:not(.aok-hidden) span.a-list-item"
    );
    const lines = [];
    for (const b of bullets) {
      const t = squish(b.textContent);
      if (t && t.length > 1) lines.push("• " + t);
    }
    if (lines.length) return clip(lines.join("\n"), 800);
    const desc = textOf(document.querySelector("#productDescription"));
    if (desc) return clip(desc, 800);
    return "";
  }

  function scrape() {
    const asin = extractAsin();
    return {
      source: "amazon",
      source_host: location.hostname,
      scraped_at: new Date().toISOString(),
      name: extractName(),
      description: extractDescription(),
      vendor: extractBrand(),
      model: extractModel(),
      vendor_sku: asin,
      url: canonicalUrl(asin),
      qty: 1,
      unit_cost: extractPrice(),
      notes: "",
    };
  }

  browser.runtime.onMessage.addListener((msg) => {
    if (!msg || msg.type !== "scrape") return;
    try {
      return Promise.resolve({ ok: true, data: scrape() });
    } catch (e) {
      return Promise.resolve({ ok: false, error: String(e && e.message || e) });
    }
  });
})();
