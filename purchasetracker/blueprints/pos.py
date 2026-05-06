"""Purchase order CRUD, line management, receipts, and xlsx rendering."""
from __future__ import annotations

import datetime as dt
from io import BytesIO

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request,
    send_file, url_for,
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

    return render_template(
        "pos/detail.html",
        po=po,
        candidate_items=candidate_items,
        incomplete_count=incomplete_count,
        all_tags=all_tags,
        tag_filter=tag_filter,
        statuses=PO_STATUSES,
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

    line = POLine(po_id=po.id, item_id=item.id, qty=qty,
                  unit_cost=item.unit_cost)
    db.session.add(line)
    db.session.flush()
    recompute_item_state(item)
    db.session.commit()
    flash(f"Added {qty} x {item.name}.")
    return redirect(url_for("pos.detail", po_id=po.id))


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


@bp.route("/<int:po_id>/render", methods=["POST"])
@login_required
def render_xlsx(po_id: int):
    """Render this PO using the uploaded xlsx template."""
    po = db.session.get(PurchaseOrder, po_id) or abort(404)
    template = request.files.get("template")
    if not template or not template.filename:
        flash("Pick an xlsx template to render against.", "error")
        return redirect(url_for("pos.detail", po_id=po.id))
    try:
        rendered = render_po_xlsx(po, template.read())
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
