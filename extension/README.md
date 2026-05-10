# PurchaseTracker Capture (browser extension)

A small Firefox extension for collecting product details from supported
shopping sites into a local list, then exporting that list as CSV or JSON
for import into the main PurchaseTracker app.

Currently supports: **Amazon** (all major locale TLDs).

## How it works

1. Visit an Amazon product page.
2. Click the toolbar button.
3. The popup opens with the scraped fields pre-filled in an editable form:
   - **Name** (`#productTitle`)
   - **Description** (feature bullets, joined and trimmed to ~800 chars)
   - **Vendor / Brand** (byline or Brand row in the details table)
   - **Model** (Item model number)
   - **Vendor SKU / ASIN** (from the URL or `data-asin`)
   - **URL** (canonicalised to `https://<host>/dp/<ASIN>`, stripping all
     `?tag=…`, `?ref=…`, and other referral / tracking parameters)
   - **Qty** (defaults to 1)
   - **Unit cost** (parsed from the buy-box price)
   - **Notes** (empty for you to fill in)
4. Correct anything wrong, then click **Add to list**.
5. Repeat on other pages. The captured list is shown at the bottom of the
   popup and persisted in `browser.storage.local`.
6. When you're ready, click **Export CSV** or **Export JSON** and import
   the resulting file via PurchaseTracker's import wizard. The column names
   line up with PT's auto-mapper, so no field-by-field mapping is required.

## Install (temporary, for testing)

1. Open Firefox.
2. Visit `about:debugging#/runtime/this-firefox`.
3. Click **Load Temporary Add-on…**.
4. Select `extension/manifest.json` from this repository.

The extension stays loaded until Firefox restarts. To install permanently
you'd need to sign it through AMO or use an unbranded / developer build.

## Files

```
extension/
  manifest.json            # Firefox MV3 manifest, amazon host_permissions
  background.js            # toolbar badge count
  content/amazon.js        # injected on amazon.*; handles "scrape" messages
  popup/
    popup.html             # review form + captured-list UI
    popup.css
    popup.js               # talks to content script, writes storage, exports
  lib/
    storage.js             # browser.storage.local wrapper
    export.js              # CSV / JSON serializers matching PT import columns
  icons/icon.svg
```

## Export format

CSV columns (and JSON object keys) match the aliases recognised by
`purchasetracker/import_parsers.py::PT_FIELD_ALIASES`:

```
name, description, vendor, model, vendor_sku, url, qty, unit_cost, notes
```

JSON uses the same `{"items": [...]}` envelope as PT's own JSON export,
so it round-trips through the same import endpoint.

## Adding more sites later

The scraping logic is isolated to `content/amazon.js`. To add another site:

1. Create `content/<site>.js` exporting the same `{type:"scrape"} →
   {ok, data}` message contract.
2. Add the new host patterns to `manifest.json` under both `host_permissions`
   and `content_scripts`.
3. Update the regex in `popup/popup.js::tryScrape` that gates the
   "Open an Amazon product page" message.

No changes to the popup form, storage, or export are required - the
field shape is identical regardless of source.
