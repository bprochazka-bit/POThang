"""Import/export of the data set as JSON (full) and CSV (item list)."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Iterable

from flask import (
    Blueprint, Response, abort, current_app, flash, redirect, render_template,
    request, send_file, url_for,
)

from ..auth import login_required
from ..extensions import db
from .. import import_parsers, import_staging
from ..models import (
    Attachment, Item, POLine, PurchaseOrder, Receipt, Tag,
)
from ..services import apply_tags, get_or_create_tag, recompute_item_state

bp = Blueprint("io", __name__)


# ---------- Export ----------

@bp.route("/export/json")
@login_required
def export_json():
    payload = {
        "version": 1,
        "exported_at": datetime.utcnow().isoformat(),
        "items": [item.to_dict(include_lines=True)
                  for item in db.session.query(Item).all()],
        "purchase_orders": [po.to_dict()
                            for po in db.session.query(PurchaseOrder).all()],
        "attachments": [att.to_dict()
                        for att in db.session.query(Attachment).all()],
    }
    body = json.dumps(payload, indent=2)
    return Response(
        body,
        mimetype="application/json",
        headers={
            "Content-Disposition":
                f'attachment; filename="purchasetracker-{_today()}.json"'
        },
    )


@bp.route("/export/csv")
@login_required
def export_csv():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "name", "description", "model", "vendor", "vendor_sku", "url",
        "qty", "unit_cost", "estimated_total", "state", "tags", "notes",
        "is_complete", "missing_fields", "po_numbers",
    ])
    for item in db.session.query(Item).order_by(Item.id).all():
        po_numbers = sorted({
            line.po.po_number for line in item.lines
            if line.po and line.po.status != "cancelled"
        })
        writer.writerow([
            item.id, item.name, (item.description or "").replace("\n", " "),
            item.model or "", item.vendor or "",
            item.vendor_sku or "", item.url or "", item.qty, item.unit_cost,
            item.estimated_total, item.state,
            ";".join(t.name for t in item.tags),
            (item.notes or "").replace("\n", " "),
            "yes" if item.is_complete else "no",
            ";".join(item.missing_fields),
            ";".join(po_numbers),
        ])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                f'attachment; filename="items-{_today()}.csv"'
        },
    )


# ---------- Import ----------

@bp.route("/import", methods=["GET", "POST"])
@login_required
def import_form():
    if request.method == "POST":
        kind = request.form.get("kind", "json")
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Pick a file to import.", "error")
            return redirect(url_for("io.import_form"))
        try:
            if kind == "json":
                added, updated = _import_json(f.read())
            else:
                text = f.read().decode("utf-8-sig")
                added, updated = _import_csv(text)
        except Exception as e:
            current_app.logger.exception("Import failed")
            flash(f"Import failed: {e}", "error")
            return redirect(url_for("io.import_form"))

        db.session.commit()
        flash(f"Imported {added} new, updated {updated}.")
        return redirect(url_for("items.list_items"))
    return render_template("io/import.html")


def _import_json(blob: bytes) -> tuple[int, int]:
    """Replace-or-append by primary key. Conservative: existing items are
    matched by (description, vendor, model) when no id is provided."""
    data = json.loads(blob)
    added = 0
    updated = 0

    # Items
    item_id_map: dict[int, int] = {}  # incoming id -> local id
    for src in data.get("items", []):
        item = None
        incoming_id = src.get("id")
        # Backward compatibility: old exports had only `description`.
        # New exports have both `name` and `description`.
        src_name = (src.get("name") or src.get("description") or "").strip()
        if incoming_id is not None:
            item = db.session.get(Item, incoming_id)
        if item is None:
            item = (db.session.query(Item)
                    .filter_by(name=src_name,
                               vendor=src.get("vendor"),
                               model=src.get("model")).first())
        if item is None:
            item = Item()
            db.session.add(item)
            added += 1
        else:
            updated += 1
        item.name = src_name or "(unnamed)"
        # If both name and description came through, use both. If only the
        # legacy field existed, leave description blank (we used it as name).
        if "name" in src and "description" in src:
            item.description = src.get("description")
        elif "name" in src:
            item.description = src.get("description")  # likely None
        else:
            # Old-format export: description was the title; we have no
            # separate long-text content yet.
            item.description = None
        item.model = src.get("model")
        item.vendor = src.get("vendor")
        item.vendor_sku = src.get("vendor_sku")
        item.url = src.get("url")
        item.qty = int(src.get("qty") or 1)
        item.unit_cost = float(src.get("unit_cost") or 0)
        item.notes = src.get("notes")
        item.state = src.get("state") or "requested"
        apply_tags(item, src.get("tags") or [])
        db.session.flush()
        if incoming_id is not None:
            item_id_map[incoming_id] = item.id

    # Purchase orders
    po_id_map: dict[int, int] = {}
    for src in data.get("purchase_orders", []):
        po = None
        if src.get("po_number"):
            po = (db.session.query(PurchaseOrder)
                  .filter_by(po_number=src["po_number"]).first())
        if po is None:
            po = PurchaseOrder(po_number=src.get("po_number") or "?")
            db.session.add(po)
            added += 1
        else:
            updated += 1
        po.vendor = src.get("vendor")
        po.ship_to = src.get("ship_to")
        po.notes = src.get("notes")
        po.status = src.get("status") or "open"
        if src.get("ordered_at"):
            try:
                po.ordered_at = datetime.fromisoformat(src["ordered_at"])
            except ValueError:
                pass
        db.session.flush()
        if src.get("id") is not None:
            po_id_map[src["id"]] = po.id

        # Replace lines wholesale to keep import deterministic.
        for old_line in list(po.lines):
            db.session.delete(old_line)
        db.session.flush()

        for line_src in src.get("lines", []):
            inc_item_id = line_src.get("item_id")
            local_item_id = item_id_map.get(inc_item_id, inc_item_id)
            if not local_item_id:
                continue
            line = POLine(
                po_id=po.id,
                item_id=local_item_id,
                qty=int(line_src.get("qty") or 1),
                unit_cost=float(line_src.get("unit_cost") or 0),
                notes=line_src.get("notes"),
            )
            db.session.add(line)
            db.session.flush()
            for r_src in line_src.get("receipts", []):
                receipt = Receipt(
                    line_id=line.id,
                    qty=int(r_src.get("qty") or 0),
                    received_by=r_src.get("received_by"),
                    notes=r_src.get("notes"),
                )
                if r_src.get("received_at"):
                    try:
                        receipt.received_at = datetime.fromisoformat(
                            r_src["received_at"])
                    except ValueError:
                        pass
                db.session.add(receipt)

    # Recompute item states post-import.
    for item in db.session.query(Item).all():
        recompute_item_state(item)

    return added, updated


def _import_csv(text: str) -> tuple[int, int]:
    """CSV import: items only. Existing rows matched by (name, vendor, model)
    tuple. For backward compatibility, if the CSV has no 'name' column we
    treat 'description' as the name."""
    reader = csv.DictReader(io.StringIO(text))
    added = 0
    updated = 0
    for row in reader:
        name = (row.get("name") or row.get("description") or "").strip()
        if not name:
            continue
        # If both columns are present, name is the title and description is
        # the long text. If only description was present, it's the name.
        has_separate_desc = "name" in row and "description" in row
        long_desc = row.get("description") if has_separate_desc else None

        item = (db.session.query(Item)
                .filter_by(name=name,
                           vendor=row.get("vendor") or None,
                           model=row.get("model") or None).first())
        if item is None:
            item = Item()
            db.session.add(item)
            added += 1
        else:
            updated += 1
        item.name = name
        item.description = (long_desc or None)
        item.model = row.get("model") or None
        item.vendor = row.get("vendor") or None
        item.vendor_sku = row.get("vendor_sku") or None
        item.url = row.get("url") or None
        item.notes = row.get("notes") or None
        try:
            item.qty = max(1, int(row.get("qty") or 1))
        except ValueError:
            item.qty = 1
        try:
            item.unit_cost = max(0.0, float(row.get("unit_cost") or 0))
        except ValueError:
            item.unit_cost = 0.0
        if row.get("state"):
            item.state = row["state"]
        if row.get("tags"):
            apply_tags(item, [t for t in row["tags"].split(";") if t.strip()])
    return added, updated


def _today() -> str:
    return datetime.utcnow().strftime("%Y%m%d")


# ---------- Import wizard (CSV / TSV / xlsx / JSON with field mapping) ----------

# The PT fields the wizard can target. Order matters: it's the row order in
# the mapping UI.
WIZARD_FIELDS = [
    ("name",        "Name",         True),   # (field, label, required-for-completeness)
    ("description", "Description",  True),
    ("vendor",      "Vendor",       True),
    ("url",         "URL",          True),
    ("qty",         "Quantity",     True),
    ("unit_cost",   "Unit cost",    True),
    ("model",       "Model",        False),
    ("vendor_sku",  "Vendor SKU",   False),
    ("tags",        "Tags",         False),
    ("notes",       "Notes",        False),
    ("state",       "State",        False),
]


@bp.route("/import-wizard", methods=["GET", "POST"])
@login_required
def wizard_upload():
    """Step 1: upload a source file."""
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Pick a file to import.", "error")
            return redirect(url_for("io.wizard_upload"))
        try:
            blob = f.read()
            try:
                header_row = max(1, int(request.form.get("header_row", 1)))
            except (TypeError, ValueError):
                header_row = 1
            parsed = import_parsers.parse_upload(f.filename, blob,
                                                 header_row=header_row)
        except Exception as e:
            current_app.logger.exception("Parse failed")
            flash(f"Could not parse file: {e}", "error")
            return redirect(url_for("io.wizard_upload"))
        if not parsed["headers"] or not parsed["rows"]:
            flash("File appears to be empty or unreadable.", "error")
            return redirect(url_for("io.wizard_upload"))
        sid = import_staging.create(parsed, f.filename)
        flash(f"Loaded {len(parsed['rows'])} rows from {f.filename}.")
        return redirect(url_for("io.wizard_map", sid=sid))
    return render_template("io/wizard_upload.html")


@bp.route("/import-wizard/<sid>/map", methods=["GET", "POST"])
@login_required
def wizard_map(sid: str):
    """Step 2: map source columns to PT fields."""
    staging = import_staging.load(sid)
    if staging is None:
        flash("Import session expired or not found. Please re-upload.", "error")
        return redirect(url_for("io.wizard_upload"))

    if request.method == "POST":
        # Form fields:
        #   map_<pt_field>     = "" | source_header | "__constant__"
        #   const_<pt_field>   = constant value (only used if map_*  == "__constant__")
        mapping: dict[str, str] = {}
        constants: dict[str, str] = {}
        for pt_field, _label, _required in WIZARD_FIELDS:
            choice = (request.form.get(f"map_{pt_field}") or "").strip()
            if not choice:
                continue
            if choice == "__constant__":
                value = (request.form.get(f"const_{pt_field}") or "").strip()
                if value:
                    constants[pt_field] = value
            else:
                if choice in staging["headers"]:
                    mapping[pt_field] = choice

        # Name is the only field with a hard model-level NOT NULL constraint.
        if "name" not in mapping and "name" not in constants:
            flash("Map a source column to Name (or set a constant) - "
                  "name is required.", "error")
            return redirect(url_for("io.wizard_map", sid=sid))

        staging["mapping"] = mapping
        staging["constants"] = constants
        staging["edits"] = {}  # reset any prior edits since mapping changed
        import_staging.save(sid, staging)
        return redirect(url_for("io.wizard_review", sid=sid))

    suggested = import_parsers.suggest_mapping(staging["headers"])
    # Existing mapping (if user came back to this step) wins over the suggestion.
    current = {**suggested, **staging.get("mapping", {})}
    return render_template(
        "io/wizard_map.html",
        sid=sid,
        staging=staging,
        fields=WIZARD_FIELDS,
        current_mapping=current,
        constants=staging.get("constants", {}),
    )


@bp.route("/import-wizard/<sid>/review", methods=["GET", "POST"])
@login_required
def wizard_review(sid: str):
    """Step 3: review mapped rows, edit inline, then commit."""
    staging = import_staging.load(sid)
    if staging is None:
        flash("Import session expired or not found.", "error")
        return redirect(url_for("io.wizard_upload"))
    if not staging.get("mapping") and not staging.get("constants"):
        return redirect(url_for("io.wizard_map", sid=sid))

    if request.method == "POST":
        # Two POST modes: "save_edits" (just persist edits, stay on review) or
        # "commit" (run the import).
        mode = request.form.get("mode", "commit")
        edits = _collect_edits(request.form, staging)
        staging["edits"] = edits
        staging["skipped_rows"] = _collect_skipped(request.form,
                                                    len(staging["rows"]))
        import_staging.save(sid, staging)

        if mode == "save_edits":
            flash("Edits saved.")
            return redirect(url_for("io.wizard_review", sid=sid))

        # commit
        try:
            added, updated, skipped = _commit_wizard_import(staging)
        except Exception as e:
            current_app.logger.exception("Wizard commit failed")
            flash(f"Import failed: {e}", "error")
            return redirect(url_for("io.wizard_review", sid=sid))
        db.session.commit()
        import_staging.discard(sid)
        flash(f"Imported {added} new, updated {updated}, skipped {skipped}.")
        return redirect(url_for("items.list_items"))

    # Build a preview that shows the mapped result for each row, with any
    # edits already applied.
    preview = _build_preview(staging)
    return render_template(
        "io/wizard_review.html",
        sid=sid,
        staging=staging,
        fields=WIZARD_FIELDS,
        preview=preview,
        complete_count=sum(1 for r in preview if r["complete"] and not r["skipped"]),
        total=len(preview),
        included_count=sum(1 for r in preview if not r["skipped"]),
    )


@bp.route("/import-wizard/<sid>/cancel", methods=["POST"])
@login_required
def wizard_cancel(sid: str):
    import_staging.discard(sid)
    flash("Import cancelled.")
    return redirect(url_for("io.import_form"))


# ---------- Wizard internals ----------

def _build_preview(staging: dict) -> list[dict]:
    """Apply mapping + constants + per-row edits, return preview rows."""
    mapping = staging.get("mapping", {})
    constants = staging.get("constants", {})
    edits = staging.get("edits", {})

    skipped_set = set(staging.get("skipped_rows", []))
    out = []
    for idx, src in enumerate(staging["rows"]):
        record = {}
        for pt_field, _label, _req in WIZARD_FIELDS:
            value = ""
            if pt_field in constants:
                value = constants[pt_field]
            if pt_field in mapping:
                value = src.get(mapping[pt_field], "") or value
            record[pt_field] = value
        # Per-row edits override.
        row_edits = edits.get(str(idx), {})
        for k, v in row_edits.items():
            record[k] = v

        # Coerce numeric fields for display
        record["qty"] = _coerce_int(record.get("qty"), default=1)
        record["unit_cost"] = _coerce_float(record.get("unit_cost"), default=0.0)

        record["skipped"] = idx in skipped_set
        record["complete"] = _is_record_complete(record)
        record["missing"] = _missing_fields(record)
        out.append(record)
    return out


def _collect_edits(form, staging: dict) -> dict:
    """Read edit inputs from the review form into the edits dict."""
    edits: dict = {}
    n_rows = len(staging["rows"])
    for idx in range(n_rows):
        row_edits = {}
        for pt_field, _label, _req in WIZARD_FIELDS:
            key = f"edit_{idx}_{pt_field}"
            if key in form:
                row_edits[pt_field] = form.get(key, "").strip()
        if row_edits:
            edits[str(idx)] = row_edits
    return edits


def _collect_skipped(form, n_rows: int) -> list[int]:
    """Return indices of rows the user unchecked in the review table."""
    return [i for i in range(n_rows) if not form.get(f"include_{i}")]


def _coerce_int(v, default=1) -> int:
    if v is None or v == "":
        return default
    try:
        return max(1, int(float(str(v).strip())))
    except (ValueError, TypeError):
        return default


def _coerce_float(v, default=0.0) -> float:
    if v is None or v == "":
        return default
    s = str(v).strip().lstrip("$").replace(",", "")
    try:
        return max(0.0, float(s))
    except (ValueError, TypeError):
        return default


def _is_record_complete(record: dict) -> bool:
    return not _missing_fields(record)


def _missing_fields(record: dict) -> list[str]:
    """Same definition the Item model uses, but operating on a dict."""
    missing = []
    for pt_field, label, required in WIZARD_FIELDS:
        if not required:
            continue
        v = record.get(pt_field)
        if pt_field == "qty":
            if not v or int(v) < 1:
                missing.append(label)
        elif pt_field == "unit_cost":
            if not v or float(v) <= 0:
                missing.append(label)
        else:
            if v is None or (isinstance(v, str) and not v.strip()):
                missing.append(label)
    return missing


def _commit_wizard_import(staging: dict) -> tuple[int, int, int]:
    """Walk the preview and create/update items. Returns (added, updated, skipped)."""
    added = 0
    updated = 0
    skipped = 0

    preview = _build_preview(staging)
    skipped_rows = set(staging.get("skipped_rows", []))

    for idx, record in enumerate(preview):
        if idx in skipped_rows:
            skipped += 1
            continue
        name = (record.get("name") or "").strip()
        if not name:
            skipped += 1
            continue

        # Match existing item by (name, vendor, model) to avoid duplicates on
        # repeat imports.
        item = (db.session.query(Item)
                .filter_by(name=name,
                           vendor=record.get("vendor") or None,
                           model=record.get("model") or None).first())
        if item is None:
            item = Item()
            db.session.add(item)
            added += 1
        else:
            updated += 1

        item.name = name
        item.description = record.get("description") or None
        item.model = record.get("model") or None
        item.vendor = record.get("vendor") or None
        item.vendor_sku = record.get("vendor_sku") or None
        item.url = record.get("url") or None
        item.notes = record.get("notes") or None
        item.qty = record.get("qty") or 1
        item.unit_cost = record.get("unit_cost") or 0.0
        if record.get("state"):
            item.state = record["state"]

        # Tags: split comma- or semicolon-separated list.
        tags_raw = (record.get("tags") or "").strip()
        if tags_raw:
            tag_names = [t.strip() for t in tags_raw.replace(";", ",").split(",")
                         if t.strip()]
            apply_tags(item, tag_names)

        db.session.flush()
        recompute_item_state(item)

    return added, updated, skipped
