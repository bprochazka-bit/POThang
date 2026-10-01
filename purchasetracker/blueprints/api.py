"""
JSON API for the Android receiving app (mounted at /api/v1).

Auth: every endpoint needs a resolved user - either an API bearer token
(config.API_TOKENS) or whatever the configured AUTH_MODE resolves (handy for
single_user installs and for poking at the API from a logged-in browser).
Unauthenticated calls get a JSON 401 rather than the login redirect.

Endpoints:

  GET  /ping                       server + user check (the app's "Test")
  GET  /receiving/pos              POs with something left to receive
  GET  /pos/<id>/lines             a PO's lines (?include_received=1)
  POST /receiving/match            rank PO lines against OCR text
  GET  /items/search?q=            free-text item lookup
  POST /lines/<id>/receive         record a receipt {qty, notes}
  POST /items/parse                suggest item fields from OCR text
  POST /items                      create an item (+ optional photos)
  GET  /tags                       tag names
  GET  /receipts/by-day            receipts grouped by day
  POST /receipts/<id>/verify       mark / unmark a receipt verified
"""
from __future__ import annotations

import datetime as dt
import mimetypes
from functools import wraps

from flask import Blueprint, abort, jsonify, request, url_for
from sqlalchemy import or_
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from ..auth import current_user
from ..extensions import db
from ..models import Item, POLine, PurchaseOrder, Receipt, Tag
from ..ocr_match import match_items, match_lines, suggest_item_fields
from ..services import (
    apply_tags, receipts_by_day, recompute_item_state, record_receipt,
    store_attachment, to_display_tz,
)

bp = Blueprint("api", __name__)

API_VERSION = 1
# PO statuses we receive against (mirrors pos._receivable_pos).
RECEIVABLE_PO_STATUSES = ("approved", "ordered", "partial", "received")
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}


# ---------- plumbing ----------

def api_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return _error("Not authenticated. Check the API token in the "
                          "app settings.", 401)
        return view(*args, **kwargs)
    return wrapped


def _error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


@bp.errorhandler(HTTPException)
def _http_error(e: HTTPException):
    return _error(e.description or e.name, e.code or 500)


def _user_name():
    return (current_user() or {}).get("name")


def _json_body() -> dict:
    return request.get_json(silent=True) or {}


def _first_image(item: Item):
    for att in item.attachments:
        if (att.kind == "image" or (att.mime_type or "").startswith("image/")):
            return att
    return None


def _item_json(item: Item) -> dict:
    img = _first_image(item)
    return {
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "model": item.model,
        "vendor": item.vendor,
        "vendor_sku": item.vendor_sku,
        "url": item.url,
        "qty": item.qty,
        "unit_cost": item.unit_cost,
        "state": item.state,
        "tags": [t.name for t in item.tags],
        "image_url": url_for("attachments.download", att_id=img.id) if img else None,
        "web_url": url_for("items.detail", item_id=item.id),
    }


def _line_json(line: POLine) -> dict:
    received = line.qty_received
    po = line.po
    return {
        "id": line.id,
        "line_no": line.line_no,
        "po_id": line.po_id,
        "po_number": po.po_number if po else None,
        "po_vendor": po.vendor if po else None,
        "po_status": po.status if po else None,
        "qty": line.qty,
        "qty_received": received,
        "outstanding": max(0, line.qty - received),
        "item": _item_json(line.item) if line.item else None,
    }


def _po_json(po: PurchaseOrder) -> dict:
    lines = list(po.lines)
    ordered = sum(l.qty for l in lines)
    received = sum(l.qty_received for l in lines)
    return {
        "id": po.id,
        "po_number": po.po_number,
        "vendor": po.vendor,
        "status": po.status,
        "ordered_at": po.ordered_at.isoformat() if po.ordered_at else None,
        "total_lines": len(lines),
        "outstanding_lines": sum(1 for l in lines if l.qty - l.qty_received > 0),
        "total_ordered": ordered,
        "total_received": received,
        "outstanding": ordered - received,
    }


def _receivable_pos():
    pos = (
        db.session.query(PurchaseOrder)
        .filter(PurchaseOrder.status.in_(RECEIVABLE_PO_STATUSES))
        .order_by(PurchaseOrder.created_at.desc())
        .all()
    )
    return [p for p in pos if any(l.qty - l.qty_received > 0 for l in p.lines)]


def _receipt_json(r: Receipt) -> dict:
    local = to_display_tz(r.received_at)
    vlocal = to_display_tz(r.verified_at)
    return {
        "id": r.id,
        "line_id": r.line_id,
        "qty": r.qty,
        "received_at": local.isoformat(timespec="minutes") if local else None,
        "received_by": r.received_by,
        "notes": r.notes,
        "verified": r.is_verified,
        "verified_at": vlocal.isoformat(timespec="minutes") if vlocal else None,
        "verified_by": r.verified_by,
    }


# ---------- endpoints ----------

@bp.route("/ping")
@api_auth
def ping():
    return jsonify({"ok": True, "api_version": API_VERSION,
                    "user": _user_name(),
                    "server_time": dt.datetime.utcnow().isoformat() + "Z"})


@bp.route("/receiving/pos")
@api_auth
def receiving_pos():
    return jsonify({"ok": True, "pos": [_po_json(p) for p in _receivable_pos()]})


@bp.route("/pos/<int:po_id>/lines")
@api_auth
def po_lines(po_id: int):
    po = db.session.get(PurchaseOrder, po_id) or abort(404, "PO not found.")
    include_received = request.args.get("include_received") in ("1", "true")
    lines = sorted(po.lines, key=lambda l: (l.line_no or 0, l.id))
    if not include_received:
        lines = [l for l in lines if l.qty - l.qty_received > 0]
    return jsonify({"ok": True, "po": _po_json(po),
                    "lines": [_line_json(l) for l in lines]})


@bp.route("/receiving/match", methods=["POST"])
@api_auth
def receiving_match():
    """Rank PO lines against OCR (or typed) text.

    Body: {"text": "...", "po_id": optional, "include_received": false,
           "limit": 10}
    Without po_id, every receivable PO is searched. Also returns the best
    item matches that are NOT on any receivable line, so the app can tell
    "this is item X, but it's not on an open PO" apart from "no idea".
    """
    data = _json_body()
    text = (data.get("text") or "").strip()
    if not text:
        return _error("No text to match.")
    try:
        limit = max(1, min(50, int(data.get("limit") or 10)))
    except (TypeError, ValueError):
        limit = 10
    po_id = data.get("po_id")
    include_received = bool(data.get("include_received"))

    if po_id:
        po = db.session.get(PurchaseOrder, int(po_id)) or abort(404, "PO not found.")
        lines = list(po.lines)
    else:
        lines = [l for p in _receivable_pos() for l in p.lines]
    if not include_received:
        lines = [l for l in lines if l.qty - l.qty_received > 0]

    matches = match_lines(text, lines, limit=limit)

    # Items that look right but have nothing open to receive against.
    on_lines = {m["line"].item_id for m in matches}
    candidate_ids = {l.item_id for l in lines}
    others = [m for m in match_items(
                  text, db.session.query(Item).all(), limit=limit)
              if m["item"].id not in on_lines and m["item"].id not in candidate_ids]

    return jsonify({
        "ok": True,
        "matches": [dict(_line_json(m["line"]), score=m["score"],
                         reasons=m["reasons"]) for m in matches],
        "other_items": [dict(_item_json(m["item"]), score=m["score"],
                             reasons=m["reasons"]) for m in others[:5]],
    })


@bp.route("/items/search")
@api_auth
def items_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"ok": True, "items": []})
    like = f"%{q}%"
    items = (
        db.session.query(Item)
        .filter(or_(Item.name.ilike(like), Item.model.ilike(like),
                       Item.vendor_sku.ilike(like), Item.description.ilike(like),
                       Item.vendor.ilike(like)))
        .order_by(Item.updated_at.desc())
        .limit(50)
        .all()
    )
    return jsonify({"ok": True, "items": [_item_json(i) for i in items]})


@bp.route("/lines/<int:line_id>/receive", methods=["POST"])
@api_auth
def receive(line_id: int):
    line = db.session.get(POLine, line_id) or abort(404, "PO line not found.")
    data = _json_body()
    try:
        qty = int(data.get("qty") or 0)
    except (TypeError, ValueError):
        return _error("qty must be a whole number.")
    requested = qty
    try:
        receipt = record_receipt(line, qty, notes=data.get("notes"),
                                 received_by=_user_name())
    except ValueError as e:
        return _error(str(e))
    db.session.commit()
    msg = f"Received {receipt.qty} on PO {line.po.po_number} line {line.line_no}."
    if receipt.qty < requested:
        msg += f" (Only {receipt.qty} were outstanding.)"
    return jsonify({"ok": True, "message": msg,
                    "receipt": _receipt_json(receipt),
                    "line": _line_json(line), "po": _po_json(line.po)})


@bp.route("/items/parse", methods=["POST"])
@api_auth
def items_parse():
    """Suggest item fields from OCR text: {"text": ..., "lines": [...]}."""
    data = _json_body()
    lines = data.get("lines")
    if not isinstance(lines, list):
        lines = None
    fields = suggest_item_fields(data.get("text") or "", lines)
    return jsonify({"ok": True, "fields": fields})


@bp.route("/items", methods=["POST"])
@api_auth
def create_item():
    """Create an item.

    Accepts JSON, or multipart/form-data with the same fields plus one or
    more files under "photo" (stored as image attachments). Fields: name
    (required), description, model, vendor, vendor_sku, url, notes, qty,
    unit_cost, tags (list or comma-separated string).
    """
    if request.mimetype == "multipart/form-data":
        data = request.form.to_dict()
        tags = request.form.getlist("tags")
        if len(tags) == 1:
            tags = tags[0].split(",")
    else:
        data = _json_body()
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = tags.split(",")

    name = (data.get("name") or "").strip()
    if not name:
        return _error("Name is required.")

    def text(key, limit=None):
        v = (data.get(key) or "")
        v = str(v).strip()
        return (v[:limit] if limit else v) or None

    try:
        qty = max(1, int(data.get("qty") or 1))
    except (TypeError, ValueError):
        qty = 1
    try:
        unit_cost = max(0.0, float(data.get("unit_cost") or 0))
    except (TypeError, ValueError):
        unit_cost = 0.0

    item = Item(
        name=name[:255], description=text("description"),
        model=text("model", 128), vendor=text("vendor", 128),
        vendor_sku=text("vendor_sku", 128), url=text("url", 1024),
        notes=text("notes"), qty=qty, unit_cost=unit_cost, state="requested",
    )
    db.session.add(item)
    db.session.flush()
    apply_tags(item, [str(t) for t in tags])

    stored = 0
    for f in request.files.getlist("photo"):
        if not f or not f.filename:
            continue
        safe = secure_filename(f.filename) or "photo.jpg"
        ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
        if ext not in IMAGE_EXTS:
            db.session.rollback()
            return _error(f"{safe}: only image uploads are accepted here.")
        store_attachment(
            f.stream, original_filename=safe,
            mime_type=f.mimetype or mimetypes.guess_type(safe)[0],
            kind="image", uploaded_by=_user_name(), item_id=item.id,
        )
        stored += 1

    recompute_item_state(item)
    db.session.commit()
    return jsonify({"ok": True, "item": _item_json(item),
                    "photos": stored}), 201


@bp.route("/tags")
@api_auth
def tags():
    names = [t.name for t in db.session.query(Tag).order_by(Tag.name).all()]
    return jsonify({"ok": True, "tags": names})


@bp.route("/receipts/by-day")
@api_auth
def receipts_by_day_api():
    def date_arg(key):
        try:
            return dt.date.fromisoformat(request.args.get(key) or "")
        except ValueError:
            return None
    start, end = date_arg("from"), date_arg("to")
    if start is None and end is None:
        today = to_display_tz(dt.datetime.utcnow()).date()
        start = today - dt.timedelta(days=6)
    days = receipts_by_day(start=start, end=end,
                           po_id=request.args.get("po_id", type=int),
                           search=request.args.get("q"))
    out = []
    for d in days:
        out.append({
            "date": d["date"].isoformat(),
            "receipt_count": d["receipt_count"],
            "units": d["units"],
            "item_count": d["item_count"],
            "verified_count": d["verified_count"],
            "pos": [{
                "po_id": g["po"].id if g["po"] else None,
                "po_number": g["po"].po_number if g["po"] else None,
                "vendor": g["po"].vendor if g["po"] else None,
                "units": g["units"],
                "verified_count": g["verified_count"],
                "receipts": [dict(
                    _receipt_json(e["receipt"]),
                    line_no=e["line"].line_no if e["line"] else None,
                    line_qty=e["line"].qty if e["line"] else None,
                    item_name=e["item"].name if e["item"] else None,
                    item_model=e["item"].model if e["item"] else None,
                ) for e in g["receipts"]],
            } for g in d["po_groups"]],
        })
    return jsonify({"ok": True, "days": out})


@bp.route("/receipts/<int:receipt_id>/verify", methods=["POST"])
@api_auth
def verify(receipt_id: int):
    r = db.session.get(Receipt, receipt_id) or abort(404, "Receipt not found.")
    verified = _json_body().get("verified", True)
    if verified:
        if r.verified_at is None:
            r.verified_at = dt.datetime.utcnow()
            r.verified_by = _user_name()
    else:
        r.verified_at = None
        r.verified_by = None
    db.session.commit()
    return jsonify({"ok": True, "receipt": _receipt_json(r)})
