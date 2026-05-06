"""Item CRUD and filtering."""
from __future__ import annotations

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, url_for,
)
from sqlalchemy import or_

from ..auth import current_user, login_required
from ..extensions import db
from ..models import Item, PurchaseOrder, Tag
from ..services import apply_tags, get_or_create_tag, recompute_item_state, set_item_state

bp = Blueprint("items", __name__)


@bp.route("/")
@login_required
def list_items():
    q = db.session.query(Item)

    state = request.args.get("state")
    if state:
        q = q.filter(Item.state == state)

    vendor = request.args.get("vendor", "").strip()
    if vendor:
        q = q.filter(Item.vendor.ilike(f"%{vendor}%"))

    tag = request.args.get("tag", "").strip()
    if tag:
        q = q.join(Item.tags).filter(Tag.name == tag)

    cost_min = request.args.get("cost_min")
    cost_max = request.args.get("cost_max")
    if cost_min:
        try:
            q = q.filter(Item.unit_cost >= float(cost_min))
        except ValueError:
            pass
    if cost_max:
        try:
            q = q.filter(Item.unit_cost <= float(cost_max))
        except ValueError:
            pass

    po_number = request.args.get("po", "").strip()
    if po_number:
        q = q.join(Item.lines).join(PurchaseOrder).filter(
            PurchaseOrder.po_number == po_number
        )

    search = request.args.get("q", "").strip()
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            Item.name.ilike(like),
            Item.description.ilike(like),
            Item.model.ilike(like),
            Item.vendor_sku.ilike(like),
            Item.notes.ilike(like),
        ))

    items = q.order_by(Item.created_at.desc()).all()

    # Completeness filter is applied in Python so it can use the model's
    # is_complete property (which has nuance for qty / unit_cost zero values).
    completeness = request.args.get("complete", "").strip()
    if completeness == "no":
        items = [i for i in items if not i.is_complete]
    elif completeness == "yes":
        items = [i for i in items if i.is_complete]

    all_tags = [t.name for t in db.session.query(Tag).order_by(Tag.name).all()]
    all_pos = [
        po.po_number for po in
        db.session.query(PurchaseOrder).order_by(PurchaseOrder.po_number).all()
    ]

    return render_template(
        "items/list.html",
        items=items,
        states=current_app.config["ITEM_STATES"],
        all_tags=all_tags,
        all_pos=all_pos,
        completeness=completeness,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        item = _populate_from_form(Item(), request.form)
        db.session.add(item)
        db.session.flush()
        apply_tags(item, request.form.get("tags", "").split(","))
        recompute_item_state(item)
        db.session.commit()
        flash(f"Added item: {item.description}")
        return redirect(url_for("items.detail", item_id=item.id))
    all_tags = [t.name for t in db.session.query(Tag).order_by(Tag.name).all()]
    return render_template("items/edit.html", item=None,
                           states=current_app.config["ITEM_STATES"],
                           all_tags=all_tags)


@bp.route("/<int:item_id>")
@login_required
def detail(item_id: int):
    item = db.session.get(Item, item_id) or abort(404)
    return render_template("items/detail.html", item=item,
                           states=current_app.config["ITEM_STATES"])


@bp.route("/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit(item_id: int):
    item = db.session.get(Item, item_id) or abort(404)
    if request.method == "POST":
        _populate_from_form(item, request.form)
        apply_tags(item, request.form.get("tags", "").split(","))
        recompute_item_state(item)
        db.session.commit()
        flash("Saved.")
        return redirect(url_for("items.detail", item_id=item.id))
    all_tags = [t.name for t in db.session.query(Tag).order_by(Tag.name).all()]
    return render_template("items/edit.html", item=item,
                           states=current_app.config["ITEM_STATES"],
                           all_tags=all_tags)


@bp.route("/<int:item_id>/state", methods=["POST"])
@login_required
def change_state(item_id: int):
    item = db.session.get(Item, item_id) or abort(404)
    new_state = request.form.get("state")
    try:
        set_item_state(item, new_state)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("items.detail", item_id=item.id))
    db.session.commit()
    flash(f"State set to {new_state}.")
    return redirect(url_for("items.detail", item_id=item.id))


@bp.route("/<int:item_id>/delete", methods=["POST"])
@login_required
def delete(item_id: int):
    item = db.session.get(Item, item_id) or abort(404)
    if item.lines:
        flash("Item is on a purchase order; remove it from the PO first.",
              "error")
        return redirect(url_for("items.detail", item_id=item.id))
    db.session.delete(item)
    db.session.commit()
    flash("Deleted.")
    return redirect(url_for("items.list_items"))


# ---------- internals ----------

def _populate_from_form(item: Item, form) -> Item:
    item.name = form.get("name", "").strip()
    item.description = form.get("description", "").strip() or None
    item.model = form.get("model", "").strip() or None
    item.vendor = form.get("vendor", "").strip() or None
    item.vendor_sku = form.get("vendor_sku", "").strip() or None
    item.url = form.get("url", "").strip() or None
    item.notes = form.get("notes", "").strip() or None
    try:
        item.qty = max(1, int(form.get("qty", "1")))
    except ValueError:
        item.qty = 1
    try:
        item.unit_cost = max(0.0, float(form.get("unit_cost", "0") or "0"))
    except ValueError:
        item.unit_cost = 0.0
    if not item.name:
        # Fallback so we never persist a fully blank name (NOT NULL column).
        item.name = "(unnamed)"
    return item


# ---------- JSON API for quick-add ----------

@bp.route("/api/quick-add", methods=["POST"])
@login_required
def api_quick_add():
    """Create a stub item with just a name, qty, and a tag.

    Request JSON: {"name": "...", "qty": 1, "tag": "Camp 2026"}

    Backward compatibility: if "name" is missing but "description" is present,
    we treat description as the name (this was the v3 contract). Description
    is left empty in that case for the user to fill in later.

    Response JSON: serialized item.
    """
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or data.get("description") or "").strip()
    if not name:
        return jsonify({"error": "Name is required."}), 400

    try:
        qty = max(1, int(data.get("qty") or 1))
    except (TypeError, ValueError):
        qty = 1

    tag_name = (data.get("tag") or "").strip()

    item = Item(
        name=name,
        qty=qty,
        unit_cost=0.0,
        state="requested",
    )
    db.session.add(item)
    db.session.flush()
    if tag_name:
        item.tags = [get_or_create_tag(tag_name)]
    recompute_item_state(item)
    db.session.commit()

    payload = item.to_dict(include_lines=False)
    payload["edit_url"] = url_for("items.edit", item_id=item.id)
    payload["detail_url"] = url_for("items.detail", item_id=item.id)
    return jsonify(payload), 201


@bp.route("/api/by-tag")
@login_required
def api_by_tag():
    """List items carrying a given tag, most recent first.

    Query: /items/api/by-tag?tag=Camp%202026
    """
    tag_name = (request.args.get("tag") or "").strip()
    if not tag_name:
        return jsonify({"items": []})
    items = (
        db.session.query(Item)
        .join(Item.tags)
        .filter(Tag.name == tag_name)
        .order_by(Item.created_at.desc())
        .all()
    )
    out = []
    for item in items:
        d = item.to_dict(include_lines=False)
        d["edit_url"] = url_for("items.edit", item_id=item.id)
        d["detail_url"] = url_for("items.detail", item_id=item.id)
        out.append(d)
    return jsonify({"items": out, "count": len(out)})


@bp.route("/api/tags")
@login_required
def api_tags():
    """List existing tags - feeds the datalist on the quick-add page."""
    tags = [t.name for t in db.session.query(Tag).order_by(Tag.name).all()]
    return jsonify({"tags": tags})
