"""Purchase order CRUD, line management, receipts, and xlsx rendering."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
from io import BytesIO
from pathlib import Path

from flask import (
    Blueprint, Response, abort, current_app, flash, jsonify, redirect,
    render_template, request, send_file, url_for,
)

from ..auth import current_user, login_required
from ..extensions import db
from ..models import (
    Attachment, Item, PODocument, POLine, PurchaseOrder, Receipt, Tag,
)
from ..services import (
    delete_po_document, move_po_line, next_po_line_no, next_po_revision,
    po_document_path, receipts_by_day, recompute_item_state, record_receipt,
    render_po_xlsx, store_po_document, sync_po_receipt_status, to_display_tz,
)

bp = Blueprint("pos", __name__)

# User-facing PO statuses. "received" is derived (set automatically when all
# lines are fully received) and is not in this list to keep the manual control
# from being confusing.
PO_STATUSES = ["draft", "submitted", "approved", "ordered", "cancelled"]
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

    # Show lines in their stable line_no order, with a deterministic tiebreaker
    # for legacy rows that share a line_no (would be 0 from the migration).
    ordered_lines = sorted(po.lines, key=lambda l: (l.line_no or 0, l.id or 0))
    documents = sorted(po.documents, key=lambda d: d.revision, reverse=True)

    return render_template(
        "pos/detail.html",
        po=po,
        ordered_lines=ordered_lines,
        documents=documents,
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
                  unit_cost=item.unit_cost,
                  line_no=next_po_line_no(po.id))
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
                          unit_cost=item.unit_cost,
                          line_no=next_po_line_no(po.id))
            db.session.add(line)
            db.session.flush()  # so the next next_po_line_no sees this row
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


@bp.route("/lines/<int:line_id>/move", methods=["POST"])
@login_required
def move_line(line_id: int):
    """Shift this line up (-1) or down (+1) one slot in line_no order."""
    line = db.session.get(POLine, line_id) or abort(404)
    raw = request.form.get("dir", "")
    if raw not in ("up", "down"):
        abort(400)
    direction = -1 if raw == "up" else 1
    if move_po_line(line, direction):
        db.session.commit()
    return redirect(url_for("pos.detail", po_id=line.po_id))


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
    try:
        receipt = record_receipt(line, qty, notes=request.form.get("notes"),
                                 received_by=_user_name())
    except ValueError as e:
        return _receive_error(line.po_id, str(e))
    qty = receipt.qty

    db.session.commit()
    msg = f"Recorded receipt of {qty}."
    if _wants_json():
        return jsonify({"ok": True, "message": msg,
                        "line": _line_receiving_state(line),
                        "po": _po_receiving_summary(line.po),
                        "tag_summary": _receiving_tag_summary(line.po)})
    flash(msg)
    return redirect(url_for("pos.detail", po_id=line.po_id))


@bp.route("/receipts/<int:receipt_id>/update", methods=["POST"])
@login_required
def update_receipt(receipt_id: int):
    """Change the qty on an existing receipt.

    The new qty must be positive and must not push the line over its ordered
    qty (accounting for the other receipts on the same line).
    """
    receipt = db.session.get(Receipt, receipt_id) or abort(404)
    line = receipt.line
    po_id = line.po_id if line else None
    new_qty = request.form.get("qty", type=int)
    if new_qty is None or new_qty <= 0:
        return _receive_error(po_id, "Receipt quantity must be positive.")

    other = line.qty_received - receipt.qty
    if new_qty + other > line.qty:
        allowed = line.qty - other
        return _receive_error(
            po_id,
            f"Can receive at most {allowed} more on this line "
            f"({line.qty} ordered, {other} on other receipts).",
        )

    if new_qty != receipt.qty:
        # A verified count that changes is no longer verified.
        receipt.verified_at = None
        receipt.verified_by = None
    receipt.qty = new_qty
    db.session.flush()
    _resync_after_receipt_change(line)
    db.session.commit()
    msg = f"Updated receipt to {new_qty}."
    if _wants_json():
        return jsonify({"ok": True, "message": msg,
                        "line": _line_receiving_state(line),
                        "po": _po_receiving_summary(line.po),
                        "tag_summary": _receiving_tag_summary(line.po)})
    flash(msg)
    return redirect(url_for("pos.detail", po_id=po_id))


@bp.route("/receipts/<int:receipt_id>/delete", methods=["POST"])
@login_required
def delete_receipt(receipt_id: int):
    """Undo a receipt entirely."""
    receipt = db.session.get(Receipt, receipt_id) or abort(404)
    line = receipt.line
    po_id = line.po_id if line else None
    qty = receipt.qty
    db.session.delete(receipt)
    db.session.flush()
    _resync_after_receipt_change(line)
    db.session.commit()
    msg = f"Removed receipt of {qty}."
    if _wants_json():
        # line may still exist; reload state from the (now flushed) line.
        return jsonify({"ok": True, "message": msg,
                        "line": _line_receiving_state(line),
                        "po": _po_receiving_summary(line.po),
                        "tag_summary": _receiving_tag_summary(line.po)})
    flash(msg)
    return redirect(url_for("pos.detail", po_id=po_id))


def _resync_after_receipt_change(line: POLine) -> None:
    """Recompute item state and PO status after a receipt is edited/removed.

    A reduced or removed receipt can pull a PO back out of the auto-set
    "received" status; drop it back to "ordered" so it stays receivable.
    """
    if line is None:
        return
    if line.item is not None:
        recompute_item_state(line.item)
    sync_po_receipt_status(line.po)


def _user_name():
    return (current_user() or {}).get("name")


def _wants_json() -> bool:
    """True when the caller expects a JSON reply (AJAX) rather than a redirect."""
    if request.args.get("format") == "json":
        return True
    if request.headers.get("X-Requested-With", "").lower() == "xmlhttprequest":
        return True
    accept = request.accept_mimetypes
    return bool(accept["application/json"]) and (
        accept["application/json"] >= accept["text/html"]
    )


def _receive_error(po_id, message: str):
    """Return a JSON error (AJAX) or flash+redirect (plain form post)."""
    if _wants_json():
        return jsonify({"ok": False, "error": message}), 400
    flash(message, "error")
    return redirect(url_for("pos.detail", po_id=po_id))


def _line_receiving_state(line: POLine) -> dict:
    """Serialize the receiving-relevant state of a single line for AJAX."""
    received = line.qty_received
    return {
        "id": line.id,
        "qty": line.qty,
        "qty_received": received,
        "outstanding": line.qty - received,
        "fully_received": received >= line.qty and line.qty > 0,
        "receipts": [
            {
                "id": r.id,
                "qty": r.qty,
                "received_at": r.received_at.strftime("%Y-%m-%d")
                               if r.received_at else "",
                "notes": r.notes or "",
            }
            for r in sorted(line.receipts,
                            key=lambda r: (r.received_at or dt.datetime.min,
                                           r.id or 0))
        ],
    }


def _po_receiving_summary(po: PurchaseOrder) -> dict:
    """Aggregate counts/quantities for a PO's receiving view."""
    lines = list(po.lines) if po else []
    total_ordered = sum(l.qty for l in lines)
    total_received = sum(l.qty_received for l in lines)
    return {
        "status": po.status if po else None,
        "total_lines": len(lines),
        "outstanding_lines": sum(1 for l in lines if l.qty - l.qty_received > 0),
        "total_ordered": total_ordered,
        "total_received": total_received,
        "outstanding": total_ordered - total_received,
        "fully_received": po.fully_received if po else False,
    }


# Receiving statuses used by the by-tag breakdown, in display order.
RECEIVING_STATUSES = ["outstanding", "partial", "received", "error", "cancelled"]


def _classify_line(line: POLine) -> str:
    """Bucket a PO line into a single receiving status.

    The item's manual terminal states (cancelled, error) take precedence over
    receipt progress; otherwise the bucket reflects how much of THIS line has
    been received.
    """
    item = line.item
    if item is not None and item.state == "cancelled":
        return "cancelled"
    if item is not None and item.state == "error":
        return "error"
    received = line.qty_received
    if line.qty > 0 and received >= line.qty:
        return "received"
    if received > 0:
        return "partial"
    return "outstanding"


def _receiving_tag_summary(po: PurchaseOrder) -> dict:
    """Count the PO's items by receiving status, grouped by tag.

    An item with several tags is counted under each of its tags, so the tag
    rows deliberately do not sum to the distinct-item totals. Untagged items
    fall into their own row, and a totals row counts each line once.
    """
    def empty():
        return {s: 0 for s in RECEIVING_STATUSES}

    tags: dict[str, dict] = {}
    untagged = empty()
    totals = empty()
    has_untagged = False

    for line in (po.lines if po else []):
        status = _classify_line(line)
        totals[status] += 1
        names = [t.name for t in line.item.tags] if line.item else []
        if names:
            for name in names:
                tags.setdefault(name, empty())[status] += 1
        else:
            has_untagged = True
            untagged[status] += 1

    def row(label, counts):
        return {"tag": label, "counts": counts, "total": sum(counts.values())}

    return {
        "statuses": RECEIVING_STATUSES,
        "rows": [row(name, tags[name]) for name in sorted(tags)],
        "untagged": row("(untagged)", untagged) if has_untagged else None,
        "totals": row("All items", totals),
    }


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
    summary = None
    tag_summary = None
    if selected_id is not None:
        selected_po = db.session.get(PurchaseOrder, selected_id)
        if selected_po is None:
            abort(404)
        summary = _po_receiving_summary(selected_po)
        tag_summary = _receiving_tag_summary(selected_po)
    return render_template(
        "pos/receiving.html",
        pos=pos,
        selected_po=selected_po,
        summary=summary,
        tag_summary=tag_summary,
    )


@bp.route("/receiving/<int:po_id>/receive", methods=["POST"])
@login_required
def receiving_receive(po_id: int):
    """Batch-record receipts on multiple lines of one PO."""
    po = db.session.get(PurchaseOrder, po_id) or abort(404)

    note = (request.form.get("notes") or "").strip() or None
    received_lines = 0
    received_units = 0

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
        receipt = record_receipt(line, qty, notes=note,
                                 received_by=_user_name())
        received_lines += 1
        received_units += receipt.qty

    if received_lines == 0:
        flash("No quantities entered - nothing received.", "error")
        return redirect(url_for("pos.receiving_index", po_id=po.id))

    db.session.commit()
    flash(
        f"Received {received_units} unit{'' if received_units == 1 else 's'} "
        f"across {received_lines} line{'' if received_lines == 1 else 's'} "
        f"on PO {po.po_number}."
    )
    return redirect(url_for("pos.receiving_index", po_id=po.id))


# ---------- Received shipments (receipts grouped by day) ----------

SHIPMENTS_DEFAULT_DAYS = 14


def _parse_date(raw):
    try:
        return dt.date.fromisoformat((raw or "").strip())
    except ValueError:
        return None


@bp.route("/receiving/shipments")
@login_required
def shipments():
    """Every receipt, grouped by day then PO, for double verification."""
    today = (to_display_tz(dt.datetime.utcnow()) or dt.datetime.now()).date()
    start = _parse_date(request.args.get("from"))
    end = _parse_date(request.args.get("to"))
    if start is None and end is None and not request.args.get("all"):
        start = today - dt.timedelta(days=SHIPMENTS_DEFAULT_DAYS - 1)
    if start and end and start > end:
        start, end = end, start
    po_id = request.args.get("po_id", type=int)
    search = (request.args.get("q") or "").strip()
    verified_arg = request.args.get("verified", "")
    verified = {"yes": True, "no": False}.get(verified_arg)

    days = receipts_by_day(start=start, end=end, po_id=po_id,
                           search=search, verified=verified)
    po_choices = (
        db.session.query(PurchaseOrder)
        .filter(PurchaseOrder.lines.any(POLine.receipts.any()))
        .order_by(PurchaseOrder.po_number)
        .all()
    )
    return render_template(
        "pos/shipments.html",
        days=days, start=start, end=end, today=today, po_id=po_id,
        search=search, verified_arg=verified_arg, po_choices=po_choices,
        show_all=bool(request.args.get("all")),
        totals={
            "receipts": sum(d["receipt_count"] for d in days),
            "units": sum(d["units"] for d in days),
            "verified": sum(d["verified_count"] for d in days),
        },
    )


def _set_verified(receipt: Receipt, verified: bool) -> None:
    if verified:
        if receipt.verified_at is None:
            receipt.verified_at = dt.datetime.utcnow()
            receipt.verified_by = _user_name()
    else:
        receipt.verified_at = None
        receipt.verified_by = None


def _receipt_verify_state(receipt: Receipt) -> dict:
    local = to_display_tz(receipt.verified_at)
    return {
        "id": receipt.id,
        "verified": receipt.is_verified,
        "verified_by": receipt.verified_by,
        "verified_at": local.strftime("%Y-%m-%d %H:%M") if local else None,
    }


@bp.route("/receipts/<int:receipt_id>/verify", methods=["POST"])
@login_required
def verify_receipt(receipt_id: int):
    """Mark (verified=1, default) or unmark (verified=0) one receipt."""
    receipt = db.session.get(Receipt, receipt_id) or abort(404)
    verified = request.form.get("verified", "1") not in ("0", "false", "")
    _set_verified(receipt, verified)
    db.session.commit()
    if _wants_json():
        return jsonify({"ok": True, "receipt": _receipt_verify_state(receipt)})
    flash("Receipt verified." if verified else "Verification cleared.")
    return redirect(request.referrer or url_for("pos.shipments"))


@bp.route("/receipts/verify-bulk", methods=["POST"])
@login_required
def verify_receipts_bulk():
    """Verify (or un-verify) a batch of receipts, e.g. a whole day or PO."""
    ids = [int(x) for x in request.form.getlist("receipt_ids") if x.isdigit()]
    verified = request.form.get("verified", "1") not in ("0", "false", "")
    receipts = (
        db.session.query(Receipt).filter(Receipt.id.in_(ids)).all()
        if ids else []
    )
    for r in receipts:
        _set_verified(r, verified)
    db.session.commit()
    if _wants_json():
        return jsonify({"ok": True,
                        "receipts": [_receipt_verify_state(r) for r in receipts]})
    n = len(receipts)
    flash(f"{'Verified' if verified else 'Cleared verification on'} "
          f"{n} receipt{'' if n == 1 else 's'}.")
    return redirect(request.referrer or url_for("pos.shipments"))


@bp.route("/<int:po_id>/render", methods=["POST"])
@login_required
def render_xlsx(po_id: int):
    """Render this PO using a saved or uploaded xlsx template.

    The rendered file is archived as a PODocument with an incrementing
    revision number, then sent back to the browser as a download. Past
    revisions remain downloadable from the PO detail page.
    """
    po = db.session.get(PurchaseOrder, po_id) or abort(404)

    template_bytes: bytes | None = None
    template_label: str | None = None

    # Uploaded file takes priority over a saved template selection.
    uploaded = request.files.get("template")
    if uploaded and uploaded.filename:
        template_bytes = uploaded.read()
        template_label = f"upload:{uploaded.filename}"
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
            template_label = template_name

    if not template_bytes:
        flash("Pick a template to render against.", "error")
        return redirect(url_for("pos.detail", po_id=po.id))

    # Render with the next revision so a {{revision}} placeholder in the
    # template reflects what the archive will record.
    next_rev = next_po_revision(po.id)
    try:
        rendered = render_po_xlsx(po, template_bytes, revision=next_rev)
    except Exception as e:
        current_app.logger.exception("PO render failed")
        flash(f"Render failed: {e}", "error")
        return redirect(url_for("pos.detail", po_id=po.id))

    user = current_user()
    doc = store_po_document(
        po, rendered,
        template_name=template_label,
        generated_by=(user or {}).get("name"),
        revision=next_rev,
    )
    db.session.commit()

    return send_file(
        BytesIO(rendered),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=doc.original_filename,
    )


@bp.route("/<int:po_id>/documents/<int:doc_id>/download")
@login_required
def download_document(po_id: int, doc_id: int):
    """Download a previously-archived PO revision."""
    doc = db.session.get(PODocument, doc_id) or abort(404)
    if doc.po_id != po_id:
        abort(404)
    path = po_document_path(doc)
    if not path.exists():
        flash("Archived document is missing from disk.", "error")
        return redirect(url_for("pos.detail", po_id=po_id))
    return send_file(
        path,
        mimetype=doc.mime_type or
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=doc.original_filename,
    )


@bp.route("/<int:po_id>/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
def delete_document(po_id: int, doc_id: int):
    """Delete an archived revision. Revision numbers above it are not renumbered."""
    doc = db.session.get(PODocument, doc_id) or abort(404)
    if doc.po_id != po_id:
        abort(404)
    rev = doc.revision
    delete_po_document(doc)
    db.session.commit()
    flash(f"Removed revision {rev}.")
    return redirect(url_for("pos.detail", po_id=po_id))


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
