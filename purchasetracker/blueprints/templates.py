"""Template management: list / create / edit / preview .xlsx PO templates.

The editor uses x-spreadsheet (vanilla JS, loaded from a CDN by the edit page)
on the client side. Server-side we just shuttle xlsx <-> x-spreadsheet JSON
through the converters in services.py.
"""
from __future__ import annotations

import re
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect,
    render_template, request, url_for,
)

from ..auth import login_required
from ..services import (
    render_po_xlsx, sample_po_for_preview,
    xlsx_bytes_to_xspreadsheet, xspreadsheet_to_xlsx_bytes,
)

bp = Blueprint("templates", __name__)

# Filename safety: letters, digits, dash, underscore, dot, space.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9 _.\-]+$")


def _tpl_dir() -> Path:
    return Path(current_app.config["PO_TEMPLATES_DIR_RESOLVED"])


def _resolve_template(name: str) -> Path:
    """Validate `name` and return its absolute path under the templates dir.

    Aborts 400 on path-traversal attempts or unsafe characters; 404 if the
    file doesn't exist.
    """
    if not name or not _SAFE_NAME.match(name) or not name.lower().endswith(".xlsx"):
        abort(400)
    base = _tpl_dir().resolve()
    path = (base / name).resolve()
    if base not in path.parents:
        abort(400)
    if not path.exists():
        abort(404)
    return path


def _normalize_save_name(raw: str) -> str:
    """Coerce a user-supplied name into a safe .xlsx filename or raise."""
    name = (raw or "").strip()
    if not name:
        raise ValueError("Template name is required.")
    if not name.lower().endswith(".xlsx"):
        name = name + ".xlsx"
    if not _SAFE_NAME.match(name):
        raise ValueError(
            "Use letters, digits, spaces, dot, dash, or underscore only."
        )
    return name


@bp.route("/")
@login_required
def list_templates():
    files = sorted(_tpl_dir().glob("*.xlsx"))
    entries = [
        {"name": p.name, "size": p.stat().st_size}
        for p in files
    ]
    return render_template("templates/list.html", templates=entries)


@bp.route("/new")
@login_required
def new():
    """Editor with an empty workbook."""
    return render_template(
        "templates/edit.html",
        mode="new",
        original_name="",
        initial_name="",
    )


@bp.route("/<name>/edit")
@login_required
def edit(name: str):
    """Editor pre-loaded with an existing template."""
    _resolve_template(name)  # validates; data is loaded via /<name>/data
    return render_template(
        "templates/edit.html",
        mode="edit",
        original_name=name,
        initial_name=name,
    )


@bp.route("/<name>/data")
@login_required
def get_data(name: str):
    """Return the named template as x-spreadsheet JSON."""
    path = _resolve_template(name)
    data = xlsx_bytes_to_xspreadsheet(path.read_bytes())
    return jsonify(data)


@bp.route("/save", methods=["POST"])
@login_required
def save():
    """Save the editor payload as an .xlsx in PO_TEMPLATES_DIR.

    Form fields:
      data         - x-spreadsheet JSON (stringified)
      name         - target filename (with or without .xlsx)
      original     - the file we were editing (empty for new); if present
                     and differs from `name`, the original is deleted (rename).
    """
    payload = request.get_json(silent=True) or {}
    name_raw = (payload.get("name") or "").strip()
    original = (payload.get("original") or "").strip()
    data = payload.get("data")

    try:
        name = _normalize_save_name(name_raw)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if not isinstance(data, dict):
        return jsonify({"error": "Missing spreadsheet data."}), 400

    tpl_dir = _tpl_dir()
    target = (tpl_dir / name).resolve()
    if tpl_dir.resolve() not in target.parents:
        return jsonify({"error": "Invalid path."}), 400

    # Block overwriting a different existing file unless the client explicitly
    # confirmed (overwrite=True) so the user can't silently clobber another
    # template by typing its name.
    if target.exists() and target.name != original and not payload.get("overwrite"):
        return jsonify({
            "error": f"A template named {name!r} already exists.",
            "conflict": True,
        }), 409

    try:
        xlsx_bytes = xspreadsheet_to_xlsx_bytes(data)
    except Exception as e:  # noqa: BLE001 - surface the message to the editor
        current_app.logger.exception("Template save: xlsx encode failed")
        return jsonify({"error": f"Failed to encode xlsx: {e}"}), 400

    target.write_bytes(xlsx_bytes)

    # Handle rename: drop the previous file if the name changed.
    if original and original != name:
        try:
            old = _resolve_template(original)
            if old != target:
                old.unlink()
        except Exception:  # 404 / 400 are fine — nothing to remove
            pass

    return jsonify({"ok": True, "name": name,
                    "redirect": url_for("templates.edit", name=name)})


@bp.route("/preview", methods=["POST"])
@login_required
def preview():
    """Render the current editor contents with sample data, return as JSON.

    Body: { "data": <x-spreadsheet JSON> }
    Returns: x-spreadsheet JSON of the rendered template (read-only display).
    """
    payload = request.get_json(silent=True) or {}
    data = payload.get("data")
    if not isinstance(data, dict):
        return jsonify({"error": "Missing spreadsheet data."}), 400

    try:
        template_bytes = xspreadsheet_to_xlsx_bytes(data)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("Preview: xlsx encode failed")
        return jsonify({"error": f"Encode failed: {e}"}), 400

    try:
        rendered = render_po_xlsx(sample_po_for_preview(),
                                  template_bytes, revision=1)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("Preview: render failed")
        return jsonify({"error": f"Render failed: {e}"}), 400

    try:
        preview_json = xlsx_bytes_to_xspreadsheet(rendered)
    except Exception as e:  # noqa: BLE001
        current_app.logger.exception("Preview: decode failed")
        return jsonify({"error": f"Decode failed: {e}"}), 400

    return jsonify(preview_json)


@bp.route("/<name>/delete", methods=["POST"])
@login_required
def delete(name: str):
    path = _resolve_template(name)
    path.unlink()
    flash(f"Deleted template {name}.")
    return redirect(url_for("templates.list_templates"))
