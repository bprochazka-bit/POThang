"""
Service-layer helpers. Anything that's more than a CRUD operation lives here.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
from copy import copy
from pathlib import Path
from typing import BinaryIO, Iterable, Optional, Tuple

from flask import current_app
from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .extensions import db
from .models import Attachment, Item, POLine, PurchaseOrder, Receipt, Tag


# ---------- State recomputation ----------

def recompute_item_state(item: Item) -> None:
    """Recompute and assign item.state from current allocations and receipts.

    Manual states (cancelled, approved) are preserved if they still make
    sense; otherwise we fall through to the derived states.
    """
    if item.state == "cancelled":
        return  # terminal, user-set

    qty_allocated = item.qty_on_active_pos
    qty_received = item.qty_received
    qty_total = item.qty or 0

    if qty_received >= qty_total and qty_total > 0:
        item.state = "received"
    elif qty_received > 0:
        item.state = "partial"
    elif qty_allocated >= qty_total and qty_total > 0:
        item.state = "ordered"
    elif item.state == "approved":
        # Keep user's explicit approval until the item gets onto a PO.
        return
    else:
        item.state = "requested"


def set_item_state(item: Item, new_state: str) -> None:
    """Manual override (used for approved / cancelled / un-cancelling)."""
    states = current_app.config["ITEM_STATES"]
    if new_state not in states:
        raise ValueError(f"Unknown state: {new_state}")
    item.state = new_state


# ---------- Attachment storage ----------

def _upload_root() -> Path:
    return Path(current_app.config["UPLOAD_DIR_RESOLVED"])


def store_attachment(stream: BinaryIO, original_filename: str,
                     mime_type: Optional[str] = None,
                     kind: str = "other",
                     uploaded_by: Optional[str] = None,
                     item_id: Optional[int] = None,
                     po_id: Optional[int] = None) -> Attachment:
    """Persist file content under uploads/<aa>/<bb>/<sha256> and return the
    Attachment row. Caller is responsible for db.session.commit()."""
    h = hashlib.sha256()
    # Buffer to disk while hashing so we don't load huge files into memory.
    tmp = io.BytesIO()
    while True:
        chunk = stream.read(1024 * 64)
        if not chunk:
            break
        h.update(chunk)
        tmp.write(chunk)
    sha = h.hexdigest()
    size = tmp.tell()

    sub = _upload_root() / sha[0:2] / sha[2:4]
    sub.mkdir(parents=True, exist_ok=True)
    final = sub / sha
    if not final.exists():
        with open(final, "wb") as f:
            tmp.seek(0)
            shutil.copyfileobj(tmp, f)

    att = Attachment(
        sha256=sha,
        original_filename=original_filename,
        mime_type=mime_type,
        size_bytes=size,
        kind=kind,
        uploaded_by=uploaded_by,
        item_id=item_id,
        po_id=po_id,
    )
    db.session.add(att)
    return att


def attachment_path(att: Attachment) -> Path:
    sha = att.sha256
    return _upload_root() / sha[0:2] / sha[2:4] / sha


def delete_attachment(att: Attachment) -> None:
    """Remove DB row and the on-disk blob if no other rows reference it."""
    sha = att.sha256
    path = attachment_path(att)
    db.session.delete(att)
    db.session.flush()
    still_referenced = (
        db.session.query(Attachment.id).filter_by(sha256=sha).first() is not None
    )
    if not still_referenced and path.exists():
        try:
            path.unlink()
        except OSError:
            pass  # leave orphaned blob; better than crashing


# ---------- Tags ----------

def get_or_create_tag(name: str) -> Tag:
    name = name.strip()
    if not name:
        raise ValueError("Empty tag")
    tag = db.session.query(Tag).filter_by(name=name).first()
    if tag is None:
        tag = Tag(name=name)
        db.session.add(tag)
        db.session.flush()
    return tag


def apply_tags(item: Item, names: Iterable[str]) -> None:
    cleaned = [n.strip() for n in names if n and n.strip()]
    item.tags = [get_or_create_tag(n) for n in cleaned]


# ---------- xlsx PO template rendering ----------

# Patterns we recognise inside a cell's text.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
_LOOP_OPEN_RE = re.compile(r"\{\{\s*#\s*items\s*\}\}")
_LOOP_CLOSE_RE = re.compile(r"\{\{\s*/\s*items\s*\}\}")


def render_po_xlsx(po: PurchaseOrder, template_bytes: bytes) -> bytes:
    """Render a PO into a copy of the supplied xlsx template.

    Two replacement modes coexist:

    1. Named cells: any cell whose text contains {{name}} is substituted
       in place. Surrounding formatting on that cell is preserved.

    2. Loop region: rows from the row containing {{#items}} through the
       row containing {{/items}} (exclusive of marker rows) are treated
       as a per-line template. They are duplicated once per PO line
       and the rows that originally followed are shifted down. The
       marker rows themselves are deleted.

    Returns the rendered xlsx as bytes.
    """
    wb = load_workbook(io.BytesIO(template_bytes))

    context = _po_context(po)

    for ws in wb.worksheets:
        _render_loop_region(ws, po)
        _render_named_cells(ws, context)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _po_context(po: PurchaseOrder) -> dict:
    return {
        "po_number": po.po_number or "",
        "vendor": po.vendor or "",
        "ship_to": po.ship_to or "",
        "notes": po.notes or "",
        "date": (po.ordered_at or po.created_at).strftime("%Y-%m-%d")
                 if (po.ordered_at or po.created_at) else "",
        "total": po.total,
    }


def _line_context(line: POLine, idx: int) -> dict:
    item = line.item
    return {
        "item.name": item.name if item else "",
        "item.description": (item.description if item else "") or "",
        "item.model": (item.model if item else "") or "",
        "item.vendor": (item.vendor if item else "") or "",
        "item.vendor_sku": (item.vendor_sku if item else "") or "",
        "item.url": (item.url if item else "") or "",
        "item.qty": line.qty,
        "item.unit_cost": line.unit_cost,
        "item.line_total": line.line_total,
        "item.index": idx + 1,
        "item.notes": line.notes or "",
    }


def _substitute(text: str, ctx: dict) -> str:
    def repl(m):
        key = m.group(1)
        val = ctx.get(key)
        if val is None:
            return m.group(0)  # leave unknown placeholders untouched
        return str(val)
    return _PLACEHOLDER_RE.sub(repl, text)


def _cell_substitute(cell: Cell, ctx: dict) -> None:
    if not isinstance(cell.value, str):
        return
    new = _substitute(cell.value, ctx)
    if new != cell.value:
        # If the new value is a number and the placeholder was the only
        # content, coerce to a number so the spreadsheet can sum it.
        stripped = new.strip()
        if _looks_numeric(stripped):
            try:
                cell.value = float(stripped) if "." in stripped else int(stripped)
                return
            except ValueError:
                pass
        cell.value = new


def _looks_numeric(s: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", s))


def _render_named_cells(ws: Worksheet, ctx: dict) -> None:
    for row in ws.iter_rows():
        for cell in row:
            _cell_substitute(cell, ctx)


def _find_loop_region(ws: Worksheet) -> Optional[Tuple[int, int]]:
    """Return (open_row, close_row) of the first {{#items}}/{{/items}} pair,
    or None if absent. Both are 1-indexed Excel row numbers."""
    open_row = None
    for row in ws.iter_rows():
        for cell in row:
            if not isinstance(cell.value, str):
                continue
            if open_row is None and _LOOP_OPEN_RE.search(cell.value):
                open_row = cell.row
            elif open_row is not None and _LOOP_CLOSE_RE.search(cell.value):
                return open_row, cell.row
    return None


def _render_loop_region(ws: Worksheet, po: PurchaseOrder) -> None:
    region = _find_loop_region(ws)
    if region is None:
        return
    open_row, close_row = region
    template_rows = list(range(open_row + 1, close_row))
    template_height = len(template_rows)

    # Capture template row data (values + styles) before we mutate the sheet.
    max_col = ws.max_column
    template = []
    for r in template_rows:
        row_snapshot = []
        for c in range(1, max_col + 1):
            cell = ws.cell(row=r, column=c)
            row_snapshot.append({
                "value": cell.value,
                "font": copy(cell.font),
                "fill": copy(cell.fill),
                "border": copy(cell.border),
                "alignment": copy(cell.alignment),
                "number_format": cell.number_format,
            })
        template.append(row_snapshot)

    lines = list(po.lines)

    # Strategy:
    #   1. Delete the loop markers and template rows.
    #   2. Insert (template_height * len(lines)) rows starting at open_row.
    #   3. Fill them in, applying the template per line.
    # openpyxl's insert_rows shifts cells down; we need to be careful with
    # row counts after each mutation.

    rows_to_remove = (close_row - open_row + 1)  # inclusive of both markers
    ws.delete_rows(open_row, rows_to_remove)

    if not lines:
        return  # nothing to render; markers are gone, leave the rest alone

    insert_count = template_height * len(lines)
    if insert_count > 0:
        ws.insert_rows(open_row, amount=insert_count)

    write_row = open_row
    for idx, line in enumerate(lines):
        ctx = _line_context(line, idx)
        for tmpl_row in template:
            for col_idx, src in enumerate(tmpl_row, start=1):
                cell = ws.cell(row=write_row, column=col_idx)
                value = src["value"]
                if isinstance(value, str):
                    value = _substitute(value, ctx)
                    if _looks_numeric(value.strip()):
                        try:
                            value = (float(value) if "." in value
                                     else int(value))
                        except ValueError:
                            pass
                cell.value = value
                cell.font = copy(src["font"])
                cell.fill = copy(src["fill"])
                cell.border = copy(src["border"])
                cell.alignment = copy(src["alignment"])
                cell.number_format = src["number_format"]
            write_row += 1
