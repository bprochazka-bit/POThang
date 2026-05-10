# HFCS

A Firefox extension that captures product details from supported shopping
sites into a local list, then exports the list as CSV or JSON for import
into PurchaseTracker.

Currently supports: **Amazon** (all major locale TLDs).

## How it works

1. Visit an Amazon product page.
2. Click the HFCS toolbar button.
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

## Building the .xpi

The extension is packaged as a `.xpi` file — which is just a zip of this
directory with `manifest.json` at its root. Build with:

```
extension/build.sh
```

That writes `extension/dist/hfcs-<version>.xpi`.

## Installing it (three options, pick one)

### A. Temporary install for development (you)

No build required. Works on any Firefox.

1. `about:debugging#/runtime/this-firefox`
2. **Load Temporary Add-on…**
3. Select `extension/manifest.json`.

Caveat: the install is wiped when Firefox restarts. Fine for development,
not for your colleagues.

### B. Signed XPI from Mozilla, self-distributed (recommended for colleagues)

This is the way to put it on a colleague's stock Firefox without using the
public AMO listing. Mozilla still signs the file (for free), but the
listing is unlisted — only people you give the URL to can install it.

1. Run `extension/build.sh` to get a `.xpi`.
2. Go to <https://addons.mozilla.org/developers/> (free Mozilla account).
3. **Submit a New Add-on** → choose **"On your own"** when asked how you
   want to distribute. This makes it unlisted.
4. Upload the `.xpi`. Mozilla runs an automated validation and returns a
   signed `.xpi`, usually within a couple of minutes.
5. Host the signed `.xpi` anywhere (your file server, a private GitHub
   release, email attachment, internal share).
6. Colleagues open the file from disk or click an HTTPS link to it in
   Firefox; they'll get the normal "Add to Firefox" prompt.

Re-signing is required on every version bump — bump the `version` field
in `manifest.json`, rebuild, re-upload, redistribute.

### C. Unsigned XPI on Firefox ESR / Developer Edition / Nightly

If you don't want to involve Mozilla at all, you can install the unsigned
`.xpi` directly, but only on builds that allow disabling the signature
requirement:

- **Firefox Developer Edition** or **Nightly**: open `about:config`, set
  `xpinstall.signatures.required` to `false`, then drag the `.xpi` onto a
  Firefox window.
- **Firefox ESR** in a managed environment: a system admin can drop a
  `policies.json` next to the `firefox` binary with an `ExtensionSettings`
  entry that force-installs your `.xpi` from a URL. See
  <https://mozilla.github.io/policy-templates/#extensionsettings>.

This does **not** work on the regular stock Firefox release / Beta — those
builds enforce signing and the about:config flag has no effect on them.
Use option B if any of your colleagues run stock Firefox.

## Files

```
extension/
  manifest.json            # Firefox MV3 manifest
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
  build.sh                 # produces dist/hfcs-<version>.xpi
```

## Export format

CSV columns (and JSON object keys) match the aliases recognised by
`purchasetracker/import_parsers.py::PT_FIELD_ALIASES`:

```
name, description, vendor, model, vendor_sku, url, qty, unit_cost, notes
```

JSON uses the same `{"items": [...]}` envelope as PT's own JSON export, so
it round-trips through the same import endpoint.

## Adding more sites later

The scraping logic is isolated to `content/amazon.js`. To add another site:

1. Create `content/<site>.js` exporting the same `{type:"scrape"} →
   {ok, data}` message contract.
2. Add the new host patterns to `manifest.json` under both `host_permissions`
   and `content_scripts`.
3. Update the regex in `popup/popup.js::tryScrape` that gates the
   "Open an Amazon product page" message.

No changes to the popup form, storage, or export are required — the
field shape is identical regardless of source.
