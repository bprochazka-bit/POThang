"""Dashboard / index page."""
from flask import Blueprint, render_template, request
from sqlalchemy import func

from ..auth import login_required
from ..extensions import db
from ..models import Item, PurchaseOrder, Tag

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def index():
    counts_by_state = dict(
        db.session.query(Item.state, func.count(Item.id))
        .group_by(Item.state).all()
    )
    open_pos = (
        db.session.query(PurchaseOrder)
        .filter(PurchaseOrder.status.in_(["draft", "approved", "ordered"]))
        .order_by(PurchaseOrder.created_at.desc())
        .limit(20)
        .all()
    )
    open_value = sum(po.total for po in open_pos)

    # Incomplete = items not yet on a PO that are missing required fields.
    # Computed in Python because is_complete uses non-trivial rules per field.
    pending_items = (
        db.session.query(Item)
        .filter(Item.state.in_(["requested", "approved"]))
        .all()
    )
    incomplete_count = sum(1 for i in pending_items if not i.is_complete)

    return render_template(
        "index.html",
        counts_by_state=counts_by_state,
        open_pos=open_pos,
        open_value=open_value,
        incomplete_count=incomplete_count,
    )


@bp.route("/quick")
@login_required
def quick_add():
    """Task-oriented rapid item entry."""
    initial_tag = (request.args.get("tag") or "").strip()
    existing_tags = [
        t.name for t in db.session.query(Tag).order_by(Tag.name).all()
    ]
    return render_template(
        "quick.html",
        initial_tag=initial_tag,
        existing_tags=existing_tags,
    )
