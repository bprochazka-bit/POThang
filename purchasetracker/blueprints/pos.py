"""Purchase order CRUD, line management, receipts, and xlsx rendering."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
from io import BytesIO
from pathlib import Path

from flask import (
    Blueprint, Response, abort, current_app, flash, redirect,
    render_template, request, send_file, url_for,
)

from ..auth import login_required
from ..extensions import db
from ..models import Attachment, Item, POLine, PurchaseOrder, Receipt, Tag
from ..services import recompute_item_state, render_po_xlsx

bp = Blueprint("pos", __name__)

# User-facing PO statuses. "received" is derived (set automatically when all
# lines are fully received) and is not in this list to keep the manual control
# from being confusing.
PO_STATUSES = ["draft", "approved", "ordered", "cancelled"]
DERIVED_PO_STATUSES = ["received"]
ALL_PO_STATUSES = PO_STATUSES + DERIVED_PO_STATUSES


@bp.route("/")
@login_required
def list_pos():
    status = request.args.get("status")
    q = db.session.query(PurchaseOrder)
    if status:
        q = q.filter(PurchaseOrder.status == status)
    pos = q.order_by(PurchaseOrder.created_at.desc()).all()
    return render_template("pos/list.html", pos=pos, status=status,
                           statuses=ALL_PO_STATUSES)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        po_number = request.form.get("po_number", "").strip()
        if not po_number:
            flash("PO number is required.", "error")
            return redirect(url_for("pos.create"))
        if db.session.query(PurchaseOrder).filter_by(po_number=po_number).first():
            flash(f"PO number {po_number} already exists.", "error")
            return redirect(url_for("pos.create"))

        po = PurchaseOrder(
            po_number=po_number,
            vendor=request.form.get("vendor", "").strip() or None,
            ship_to=request.form.get("ship_to", "").strip() or None,
            notes=request.form.get("notes", "").strip() or None,
        )
        db.session.add(po)
        db.session.commit()
        flash(f"Created PO {po.po_number}.")
        return redirect(url_for("pos.detail", po_id=po.id))
    return render_template("pos/edit.html", po=None)


@bp.route("/<int:po_id>")
@login_required
def detail(po_id: int):
    po = db.session.get(PurchaseOrder, po_id) or abort(404)

    tag_filter = request.args.get("tag", "").strip()

    q = (
        db.session.query(Item)
        .filter(Item.state.in_(["requested", "approved", "ordered", "partial"]))
    )
    if tag_filter:
        q = q.join(Item.tags).filter(Tag.name == tag_filter)
    available = q.order_by(Item.name).all()
    available = [i for i in available if i.qty_unallocated > 0]

    # Split: only complete items can actually be added.
    candidate_items = [i for i in available if i.is_complete]
    incomplete_count = sum(1 for i in available if not i.is_complete)

    all_tags = [t.name for t in db.session.query(Tag).order_by(Tag.name).all()]

    tpl_dir = Path(current_app.config["PO_TEMPLATES_DIR_RESOLVED"])
    po_templates = sorted(p.name for p in tpl_dir.glob("*.xlsx"))

    return render_template(
        "pos/detail.html",
        po=po,
        candidate_items=candidate_items,
        incomplete_count=incomplete_count,
        all_tags=all_tags,
        tag_filter=tag_filter,
        statuses=PO_STATUSES,
        po_templates=po_templates,
    )


@bp.route("/<int:po_id>/edit", methods=["GET", "POST"])
@login_required
def edit(po_id: int):
    po = db.session.get(PurchaseOrder, po_id) or abort(404)
    if request.method == "POST":
        po.vendor = request.form.get("vendor", "").strip() or None
        po.ship_to = request.form.get("ship_to", "").strip() or None
        po.notes = request.form.get("notes", "").strip() or None
        new_status = request.form.get("status", po.status)
        if new_status in ALL_PO_STATUSES:
            old_status = po.status
            po.status = new_status
            # Stamp ordered_at when transitioning to ordered for the first time.
            if new_status == "ordered" and not po.ordered_at:
                po.ordered_at = dt.datetime.utcnow()
            if old_status != new_status:
                for line in po.lines:
                    recompute_item_state(line.item)
        else:
            flash(f"Unknown status: {new_status}", "error")
        db.session.commit()
        flash("Saved.")
        return redirect(url_for("pos.detail", po_id=po.id))
    return render_template("pos/edit.html", po=po, statuses=PO_STATUSES)


@bp.route("/<int:po_id>/lines/add", methods=["POST"])
@login_required
def add_line(po_id: int):
    po = db.session.get(PurchaseOrder, po_id) or abort(404)
    item_id = request.form.get("item_id", type=int)
    qty = request.form.get("qty", type=int) or 1
    item = db.session.get(Item, item_id) or abort(404)

    # Items must have all required fields filled before they can go on a PO.
    if not item.is_complete:
        flash(
            f"Cannot add \"{item.name}\" to a PO yet - missing: "
            f"{', '.join(item.missing_fields)}. "
            f"Edit the item to fill in the required fields first.",
            "error",
        )
        return redirect(url_for("pos.detail", po_id=po.id))

    qty = max(1, qty)
    qty = min(qty, item.qty_unallocated or qty)
    if qty <= 0:
        flash("Item is fully allocated to other POs.", "error")
        return redirect(url_for("pos.detail", po_id=po.id))

    # Merge into any existing line on this PO for the same item, instead of
    # creating a duplicate row.
    existing = (
        db.session.query(POLine)
        .filter_by(po_id=po.id, item_id=item.id)
        .first()
    )
    if existing is not None:
        existing.qty += qty
        db.session.flush()
        recompute_item_state(item)
        db.session.commit()
        flash(f"Updated {item.name}: +{qty} (now {existing.qty}).")
        return redirect(url_for("pos.detail", po_id=po.id))

    line = POLine(po_id=po.id, item_id=item.id, qty=qty,
                  unit_cost=item.unit_cost)
    db.session.add(line)
    db.session.flush()
    recompute_item_state(item)
    db.session.commit()
    flash(f"Added {qty} x {item.name}.")
    return redirect(url_for("pos.detail", po_id=po.id))


@bp.route("/lines/<int:line_id>/qty", methods=["POST"])
@login_required
def update_line_qty(line_id: int):
    """Adjust the qty on an existing PO line.

    The new qty must be at least the qty already received and may not exceed
    what the item still has unallocated (plus this line's current allocation).
    """
    line = db.session.get(POLine, line_id) or abort(404)
    new_qty = request.form.get("qty", type=int)
    if new_qty is None or new_qty < 1:
        flash("Quantity must be a positive integer.", "error")
        return redirect(url_for("pos.detail", po_id=line.po_id))

    received = line.qty_received
    if new_qty < received:
        flash(
            f"Cannot set qty below the {received} already received. "
            f"Adjust receipts first.",
            "error",
        )
        return redirect(url_for("pos.detail", po_id=line.po_id))

    item = line.item
    # Item.qty_unallocated already excludes this line's current qty, so the
    # cap on the new qty is line.qty + qty_unallocated.
    if item is not None:
        max_qty = line.qty + (item.qty_unallocated or 0)
        if new_qty > max_qty:
            flash(
                f"Only {max_qty} of {item.name} available "
                f"(item qty {item.qty}, allocations on other POs subtracted).",
                "error",
            )
            return redirect(url_for("pos.detail", po_id=line.po_id))

    old_qty = line.qty
    line.qty = new_qty
    db.session.flush()
    if item is not None:
        recompute_item_state(item)
    # If the new qty is now fully covered by receipts, propagate to PO status.
    if line.po and line.po.fully_received and line.po.status != "received":
        line.po.status = "received"
    db.session.commit()
    flash(f"Updated qty: {old_qty} → {new_qty}.")
    return redirect(url_for("pos.detail", po_id=line.po_id))


@bp.route("/<int:po_id>/lines/add-all", methods=["POST"])
@login_required
def add_all(po_id: int):
    po = db.session.get(PurchaseOrder, po_id) or abort(404)
    tag_filter = request.form.get("tag", "").strip()

    q = (
        db.session.query(Item)
        .filter(Item.state.in_(["requested", "approved", "ordered", "partial"]))
    )
    if tag_filter:
        q = q.join(Item.tags).filter(Tag.name == tag_filter)
    available = [i for i in q.order_by(Item.name).all() if i.qty_unallocated > 0]
    candidates = [i for i in available if i.is_complete]

    if not candidates:
        flash("No eligible items to add.", "error")
        return redirect(url_for("pos.detail", po_id=po.id,
                                **{"tag": tag_filter} if tag_filter else {}))

    added = 0
    merged = 0
    for item in candidates:
        qty = item.qty_unallocated
        existing = (
            db.session.query(POLine)
            .filter_by(po_id=po.id, item_id=item.id)
            .first()
        )
        if existing is not None:
            existing.qty += qty
            merged += 1
        else:
            line = POLine(po_id=po.id, item_id=item.id, qty=qty,
                          unit_cost=item.unit_cost)
            db.session.add(line)
            added += 1
        db.session.flush()
        recompute_item_state(item)

    db.session.commit()
    if merged:
        flash(f"Added {added} item{'' if added == 1 else 's'}; "
              f"merged into {merged} existing line{'' if merged == 1 else 's'}.")
    else:
        flash(f"Added {added} item{'' if added == 1 else 's'} to PO.")
    return redirect(url_for("pos.detail", po_id=po.id,
                            **{"tag": tag_filter} if tag_filter else {}))


@bp.route("/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_line(line_id: int):
    line = db.session.get(POLine, line_id) or abort(404)
    po_id = line.po_id
    item = line.item
    db.session.delete(line)
    db.session.flush()
    if item:
        recompute_item_state(item)
    db.session.commit()
    flash("Removed line.")
    return redirect(url_for("pos.detail", po_id=po_id))


@bp.route("/lines/<int:line_id>/receive", methods=["POST"])
@login_required
def receive_line(line_id: int):
    line = db.session.get(POLine, line_id) or abort(404)
    qty = request.form.get("qty", type=int) or 0
    if qty <= 0:
        flash("Receipt quantity must be positive.", "error")
        return redirect(url_for("pos.detail", po_id=line.po_id))

    receipt = Receipt(
        line_id=line.id, qty=qty,
        notes=request.form.get("notes", "").strip() or None,
    )
    db.session.add(receipt)
    db.session.flush()
    recompute_item_state(line.item)

    # If everything on the PO is received, move PO to received.
    if line.po and line.po.fully_received and line.po.status != "received":
        line.po.status = "received"

    db.session.commit()
    flash(f"Recorded receipt of {qty}.")
    return redirect(url_for("pos.detail", po_id=line.po_id))


# ---------- Receiving workflow ----------

def _receivable_pos():
    """POs that have at least one line with outstanding qty.

    Excludes draft and cancelled POs - you should only be receiving against
    POs that have actually been placed (approved / ordered / partial).
    """
    pos = (
        db.session.query(PurchaseOrder)
        .filter(PurchaseOrder.status.in_(["approved", "ordered", "partial",
                                          "received"]))
        .order_by(PurchaseOrder.created_at.desc())
        .all()
    )
    return [p for p in pos if any(l.qty - l.qty_received > 0 for l in p.lines)]


@bp.route("/receiving/")
@login_required
def receiving_index():
    """Pick a PO to receive against."""
    selected_id = request.args.get("po_id", type=int)
    pos = _receivable_pos()
    selected_po = None
    if selected_id is not None:
        selected_po = db.session.get(PurchaseOrder, selected_id)
        if selected_po is None:
            abort(404)
    return render_template(
        "pos/receiving.html",
        pos=pos,
        selected_po=selected_po,
    )


@bp.route("/receiving/<int:po_id>/receive", methods=["POST"])
@login_required
def receiving_receive(po_id: int):
    """Batch-record receipts on multiple lines of one PO."""
    po = db.session.get(PurchaseOrder, po_id) or abort(404)

    note = (request.form.get("notes") or "").strip() or None
    received_lines = 0
    received_units = 0
    touched_items: set[int] = set()

    for line in po.lines:
        outstanding = line.qty - line.qty_received
        if outstanding <= 0:
            continue
        raw = request.form.get(f"qty_{line.id}", "").strip()
        if not raw:
            continue
        try:
            qty = int(raw)
        except ValueError:
            continue
        if qty <= 0:
            continue
        if qty > outstanding:
            qty = outstanding
        receipt = Receipt(qty=qty, notes=note)
        # Append via the relationship so line.receipts reflects the new row
        # immediately (otherwise fully_received computes off stale state).
        line.receipts.append(receipt)
        received_lines += 1
        received_units += qty
        if line.item_id is not None:
            touched_items.add(line.item_id)

    if received_lines == 0:
        flash("No quantities entered - nothing received.", "error")
        return redirect(url_for("pos.receiving_index", po_id=po.id))

    db.session.flush()
    for item_id in touched_items:
        item = db.session.get(Item, item_id)
        if item is not None:
            recompute_item_state(item)

    if po.fully_received and po.status != "received":
        po.status = "received"

    db.session.commit()
    flash(
        f"Received {received_units} unit{'' if received_units == 1 else 's'} "
        f"across {received_lines} line{'' if received_lines == 1 else 's'} "
        f"on PO {po.po_number}."
    )
    return redirect(url_for("pos.receiving_index", po_id=po.id))


@bp.route("/<int:po_id>/render", methods=["POST"])
@login_required
def render_xlsx(po_id: int):
    """Render this PO using a saved or uploaded xlsx template."""
    po = db.session.get(PurchaseOrder, po_id) or abort(404)

    template_bytes: bytes | None = None

    # Uploaded file takes priority over a saved template selection.
    uploaded = request.files.get("template")
    if uploaded and uploaded.filename:
        template_bytes = uploaded.read()
    else:
        template_name = (request.form.get("template_name") or "").strip()
        if template_name and template_name != "__upload__":
            tpl_dir = Path(current_app.config["PO_TEMPLATES_DIR_RESOLVED"])
            tpl_path = (tpl_dir / template_name).resolve()
            # Guard against path traversal.
            if tpl_dir.resolve() not in tpl_path.parents:
                abort(400)
            if not tpl_path.exists():
                flash("Template not found.", "error")
                return redirect(url_for("pos.detail", po_id=po.id))
            template_bytes = tpl_path.read_bytes()

    if not template_bytes:
        flash("Pick a template to render against.", "error")
        return redirect(url_for("pos.detail", po_id=po.id))

    try:
        rendered = render_po_xlsx(po, template_bytes)
    except Exception as e:
        current_app.logger.exception("PO render failed")
        flash(f"Render failed: {e}", "error")
        return redirect(url_for("pos.detail", po_id=po.id))

    fname = f"{po.po_number}.xlsx"
    return send_file(
        BytesIO(rendered),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=fname,
    )


@bp.route("/<int:po_id>/export/json")
@login_required
def export_json(po_id: int):
    """Export this PO and its line items as JSON."""
    po = db.session.get(PurchaseOrder, po_id) or abort(404)
    payload = {
        "exported_at": dt.datetime.utcnow().isoformat(),
        "purchase_order": po.to_dict(),
    }
    body = json.dumps(payload, indent=2)
    safe_num = po.po_number.replace("/", "-").replace("\\", "-")
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition":
                 f'attachment; filename="po-{safe_num}.json"'},
    )


@bp.route("/<int:po_id>/export/csv")
@login_required
def export_csv(po_id: int):
    """Export the line items of this PO as CSV."""
    po = db.session.get(PurchaseOrder, po_id) or abort(404)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "po_number", "item_id", "name", "description", "vendor", "model",
        "vendor_sku", "url", "qty_ordered", "qty_received", "unit_cost",
        "line_total", "state", "tags", "notes",
    ])
    for line in po.lines:
        item = line.item
        writer.writerow([
            po.po_number,
            item.id if item else "",
            item.name if item else "",
            (item.description or "").replace("\n", " ") if item else "",
            item.vendor or "" if item else "",
            item.model or "" if item else "",
            item.vendor_sku or "" if item else "",
            item.url or "" if item else "",
            line.qty,
            line.qty_received,
            line.unit_cost,
            line.line_total,
            item.state if item else "",
            ";".join(t.name for t in item.tags) if item else "",
            (item.notes or "").replace("\n", " ") if item else "",
        ])
    safe_num = po.po_number.replace("/", "-").replace("\\", "-")
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="po-{safe_num}.csv"'},
    )
