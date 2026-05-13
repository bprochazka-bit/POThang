# PurchaseTracker

A small Flask application for managing purchasing for a small team. Tracks
items, groups them into purchase orders, handles partial receipts, attaches
quotes/images, and generates POs from an xlsx template.

Designed to be lightweight, debuggable with `journalctl`, and friendly to
volunteer handoff. Native Debian apt packages where available; minimal use of
pip; no Docker required.

## Features

- Items with name, description, vendor, model, URL, estimated cost, qty, tags
- States: `requested → approved → ordered → partial → received` plus `cancelled`
- **Required-fields completeness**: items must have name, description, URL, vendor, qty, and price filled in before they can go on a PO. The items list visually marks incomplete rows and supports filtering by completeness.
- **Quick add** workflow: a task-tag picker plus a single-row form for rapid stub entry (just a name + qty), with details filled in later via edit.
- Purchase orders: group items (or partial qty of items) into POs, manual PO numbering
- Partial receipt tracking with receipt history per line
- File attachments (quotes, images, datasheets) stored on disk with sha256 names
- Filtering by state, completeness, cost range, tag, PO, vendor, free-text search
- Import / export JSON (full fidelity) and CSV (flat item list, both legacy and modern formats accepted)
- **Import wizard** with field-mapping UI for arbitrary CSV / TSV / xlsx / JSON files: auto-detects header rows, suggests mappings using header-name aliases (e.g. `Manufacturer → vendor`, `List Price → unit_cost`), supports constant values across all rows, and lets you edit cells inline before commit.
- Generate a PO document from a user-supplied xlsx template
- Pluggable auth: single-user, reverse-proxy header (Authentik), or LDAP
- Built-in SQLite migration layer (no Alembic required) for in-place schema upgrades

## Requirements

Debian 13. Install runtime deps from apt:

```
sudo apt install python3-flask python3-flask-sqlalchemy python3-openpyxl \
                 python3-werkzeug python3-gunicorn
```

Optional, only if you turn on LDAP auth later:

```
sudo apt install python3-ldap3
```

No virtualenv, no pip required for the default configuration.

## Quick start (development)

```
cd purchasetracker
FLASK_APP=purchasetracker FLASK_ENV=development \
    python3 -m flask --app purchasetracker run --host 0.0.0.0 --port 5000
```

The first run creates `instance/purchasetracker.sqlite` and an empty
`uploads/` tree. Visit `http://<host>:5000/`.

## Production (LAN, multi-user)

Use the included systemd unit and run behind a reverse proxy (nginx / Caddy /
Authentik outpost). See `deploy/purchasetracker.service` and
`deploy/nginx.conf.example`.

```
# 1. Create directories, copy default config, fix ownership:
sudo bash deploy/setup.sh

# 2. Edit instance/config.py (SECRET_KEY, AUTH_MODE, etc.)

# 3. Install and start the service:
sudo cp deploy/purchasetracker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now purchasetracker
journalctl -u purchasetracker -f
```

## Configuration

All config lives in `instance/config.py` (created on first run from
`config.example.py`). Key settings:

- `AUTH_MODE` — `single_user`, `proxy_header`, or `ldap`
- `PROXY_HEADER_NAME` — header your reverse proxy / Authentik sets, default `X-Remote-User`
- `LDAP_URI`, `LDAP_BIND_DN`, etc. — only consulted if `AUTH_MODE == 'ldap'`
- `UPLOAD_DIR` — where attachments live (default `./uploads`)
- `MAX_UPLOAD_MB` — per-file upload cap (default 25)
- `ITEM_STATES` — workflow states; reorderable in config

## xlsx template format

Two ways to mark up an xlsx file you supply as a PO template:

1. **Named cells** — anywhere in the sheet, write `{{po_number}}`,
   `{{vendor}}`, `{{date}}`, `{{total}}`, `{{notes}}`, `{{ship_to}}`.
   These cells are replaced in place; surrounding formatting is preserved.

2. **Item loop region** — a row containing `{{#items}}` marks the start of
   a line-item block, and `{{/items}}` marks the end. Inside the block,
   place column placeholders like `{{item.description}}`,
   `{{item.qty}}`, `{{item.unit_cost}}`, `{{item.line_total}}`,
   `{{item.model}}`, `{{item.vendor_sku}}`, `{{item.url}}`. The block is
   repeated once per line item; rows below the block shift down.

3. **Excel formulas** — formula cells (anything starting with `=`) pass
   through the same templating, so you can wire totals, taxes, and
   per-row math directly into your template:

   - Inside the loop, use `{{row}}` for the current line's absolute row
     number — e.g. `=C{{row}}*D{{row}}` becomes `=C5*D5`, `=C6*D6`, … as
     the template row is duplicated per line.
   - Outside the loop, reference the expanded items region with
     `{{items.range.X}}` (X is a column letter) — e.g.
     `=SUM({{items.range.E}})` becomes `=SUM(E5:E10)`. Pair this with a
     tax rate cell to compute `=SUM({{items.range.E}})*0.0825`, or any
     other aggregate that depends on how many lines were added.
   - Companion tokens `{{items.first_row}}`, `{{items.last_row}}`,
     `{{items.count}}` are available for hand-rolled ranges, and
     `{{#if items}}…{{else}}…{{/if}}` lets you fall back to a literal
     when a PO has no lines. (For convenience, an empty PO collapses
     `{{items.range.X}}` to `0` so aggregates like `=SUM(...)` stay
     valid without a guard.)

A working sample is in `sample_template/po_template.xlsx`.

## Auth modes

### single_user (default)

No login screen; everyone hitting the app is treated as the configured user.
Suitable while developing, or behind a fully trusted LAN.

### proxy_header (Authentik / oauth2-proxy / nginx-auth)

The reverse proxy authenticates and passes the username in a header
(`X-Remote-User` by default). The app trusts this header **only** if the
request comes from `TRUSTED_PROXIES`. Configure Authentik to send
`X-Remote-User` and you're done.

### ldap

Direct LDAP bind on a login form. `python3-ldap3` required.

## Data import / export

- `GET /export/json` — full backup including POs, receipts, attachment
  metadata (filenames only, blobs not embedded)
- `GET /export/csv` — flat item list
- `POST /import/json` and `POST /import/csv` — upload to restore / merge

Both formats round-trip; JSON preserves PO links and receipt history.

## License

MIT.

## Required-fields completeness

Items have two layers of validation:

1. **Database-level**: only `name` and `qty` are NOT NULL. Everything else can
   be empty - this lets you create stub items via Quick Add and fill in
   details later.

2. **Workflow-level "complete"**: an item is considered complete when `name`,
   `description`, `vendor`, `url`, `qty`, and `unit_cost` (price) are all
   populated. Only complete items can be added to a purchase order. Incomplete
   items show a "missing: …" badge and an amber row tint in the items list,
   and the items list has a `?complete=no` filter to show only items that
   need attention.

The `xlsx` template renderer supports `{{item.name}}` for the title and
`{{item.description}}` for the longer description text.

## Upgrading from earlier versions

The app runs lightweight in-place migrations on every startup
(see `purchasetracker/migrations.py`). When upgrading from v4 or earlier, the
first run will detect the missing `name` column on the items table, add it,
and backfill it from the existing `description` field (which used to serve as
the title). No manual data cleanup is required, and the migration is
idempotent and safe on empty databases.
