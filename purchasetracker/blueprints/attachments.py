"""File attachments to items or POs."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, redirect, request, send_file,
    url_for,
)
from werkzeug.utils import secure_filename

from ..auth import current_user, login_required
from ..extensions import db
from ..models import Attachment, Item, PurchaseOrder
from ..services import attachment_path, delete_attachment, store_attachment

bp = Blueprint("attachments", __name__)


def _allowed_ext(filename: str) -> bool:
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in current_app.config["ALLOWED_UPLOAD_EXTENSIONS"]


@bp.route("/upload", methods=["POST"])
@login_required
def upload():
    target = request.form.get("target")  # "item" or "po"
    target_id = request.form.get("target_id", type=int)
    kind = request.form.get("kind", "other")
    f = request.files.get("file")
    if not f or not f.filename:
        flash("No file selected.", "error")
        return _back(target, target_id)
    if not _allowed_ext(f.filename):
        flash("File type not allowed.", "error")
        return _back(target, target_id)

    user = current_user() or {}
    safe_name = secure_filename(f.filename) or "upload"
    mime = f.mimetype or mimetypes.guess_type(safe_name)[0]

    item_id = target_id if target == "item" else None
    po_id = target_id if target == "po" else None

    store_attachment(
        f.stream, original_filename=safe_name,
        mime_type=mime, kind=kind,
        uploaded_by=user.get("name"),
        item_id=item_id, po_id=po_id,
    )
    db.session.commit()
    flash(f"Uploaded {safe_name}.")
    return _back(target, target_id)


@bp.route("/<int:att_id>")
@login_required
def download(att_id: int):
    att = db.session.get(Attachment, att_id) or abort(404)
    path = attachment_path(att)
    if not path.exists():
        abort(404)
    return send_file(
        path,
        mimetype=att.mime_type or "application/octet-stream",
        as_attachment=False,
        download_name=att.original_filename,
    )


@bp.route("/<int:att_id>/delete", methods=["POST"])
@login_required
def delete(att_id: int):
    att = db.session.get(Attachment, att_id) or abort(404)
    target = "item" if att.item_id else "po"
    target_id = att.item_id or att.po_id
    delete_attachment(att)
    db.session.commit()
    flash("Attachment deleted.")
    return _back(target, target_id)


def _back(target: str, target_id: int):
    if target == "item":
        return redirect(url_for("items.detail", item_id=target_id))
    if target == "po":
        return redirect(url_for("pos.detail", po_id=target_id))
    return redirect(url_for("main.index"))
