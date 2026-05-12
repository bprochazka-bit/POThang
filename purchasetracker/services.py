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
from .models import (
    Attachment, Item, PODocument, POLine, PurchaseOrder, Receipt, Tag,
)


# ---------- State recomputation ----------

def recompute_item_state(item: Item) -> None:
    """Recompute and assign item.state from current allocations and receipts.

    The item's state mirrors the most-progressed PO it sits on:

      - received  : sum(receipts) >= item.qty
      - partial   : sum(receipts) > 0
      - ordered   : sum of qty on POs in {ordered, partial, received} >= item.qty
      - approved  : item is on at least one approved-but-not-yet-placed PO,
                    OR the user manually flagged it approved.
      - requested : everything else.

    Cancelled is terminal (user-set, never overwritten).

    Draft and submitted POs intentionally do NOT advance item state - draft
    is a working state for the buyer and submitted means the PO is awaiting
    funding approval, neither of which is a real commitment - though they
    still count toward qty_on_active_pos / qty_unallocated to prevent
    double-allocating an item to two POs at once.
    """
    if item.state == "cancelled":
        return  # terminal, user-set

    qty_total = item.qty or 0
    qty_received = item.qty_received

    # Lines on POs that have actually been placed with the vendor.
    qty_placed = sum(
        line.qty for line in item.lines
        if line.po and line.po.status in ("ordered", "partial", "received")
    )
    # Any line on an approved-but-not-yet-placed PO?
    has_approved_line = any(
        line.po is not None and line.po.status == "approved"
        for line in item.lines
    )

    if qty_received >= qty_total and qty_total > 0:
        item.state = "received"
    elif qty_received > 0:
        item.state = "partial"
    elif qty_placed >= qty_total and qty_total > 0:
        item.state = "ordered"
    elif has_approved_line or item.state == "approved":
        # On an approved PO, OR user manually flagged it approved before any
        # PO existed and we haven't progressed past that yet.
        item.state = "approved"
    else:
        item.state = "requested"


# ---------- PO line numbering ----------

def next_po_line_no(po_id: int) -> int:
    """Return the next stable line_no to assign on this PO (max + 1, or 1)."""
    current = (
        db.session.query(POLine.line_no)
        .filter(POLine.po_id == po_id)
        .order_by(POLine.line_no.desc())
        .first()
    )
    if current is None or current[0] is None:
        return 1
    return int(current[0]) + 1


def _render_sort_key(line: POLine):
    """Sort key for rendering PO lines: by stable line_no, then by id.

    line_no is 1-based for new lines; legacy rows get backfilled by migration.
    The id tiebreaker keeps things deterministic if two rows somehow share a
    line_no (shouldn't happen, but cheap insurance).
    """
    return (line.line_no or 0, line.id or 0)


def renumber_po_lines(po: PurchaseOrder) -> None:
    """Compact line_no values to 1..N preserving current line_no order."""
    ordered = sorted(po.lines, key=_render_sort_key)
    for idx, line in enumerate(ordered, start=1):
        if line.line_no != idx:
            line.line_no = idx


def move_po_line(line: POLine, direction: int) -> bool:
    """Swap a line's line_no with its neighbour above (-1) or below (+1).

    Returns True if a swap happened, False if the line was already at the
    edge. Caller is responsible for committing.
    """
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    if line.po is None:
        return False

    siblings = sorted(line.po.lines, key=_render_sort_key)
    try:
        idx = siblings.index(line)
    except ValueError:
        return False
    neighbour_idx = idx + direction
    if neighbour_idx < 0 or neighbour_idx >= len(siblings):
        return False

    neighbour = siblings[neighbour_idx]
    line.line_no, neighbour.line_no = neighbour.line_no, line.line_no
    return True


# ---------- Saved PO documents ----------

def next_po_revision(po_id: int) -> int:
    """Return the next revision number to assign for documents on this PO."""
    current = (
        db.session.query(PODocument.revision)
        .filter(PODocument.po_id == po_id)
        .order_by(PODocument.revision.desc())
        .first()
    )
    if current is None or current[0] is None:
        return 1
    return int(current[0]) + 1


def store_po_document(po: PurchaseOrder, content: bytes,
                      template_name: Optional[str] = None,
                      generated_by: Optional[str] = None,
                      revision: Optional[int] = None,
                      mime_type: str = (
                          "application/vnd.openxmlformats-"
                          "officedocument.spreadsheetml.sheet"
                      )) -> PODocument:
    """Archive a freshly-rendered PO xlsx and return the PODocument row.

    The blob is written under uploads/<aa>/<bb>/<sha256> (same hash-tree as
    Attachment) so identical renders dedupe. Caller is responsible for
    db.session.commit().

    If `revision` is omitted, the next available revision is assigned. Pass
    it explicitly when you've already used the same value while rendering
    (e.g., to fill a {{revision}} placeholder), so the printed and archived
    numbers match.
    """
    sha = hashlib.sha256(content).hexdigest()
    sub = _upload_root() / sha[0:2] / sha[2:4]
    sub.mkdir(parents=True, exist_ok=True)
    final = sub / sha
    if not final.exists():
        with open(final, "wb") as f:
            f.write(content)

    if revision is None:
        revision = next_po_revision(po.id)
    safe_num = (po.po_number or f"po-{po.id}").replace("/", "-").replace("\\", "-")
    filename = f"{safe_num}-rev{revision}.xlsx"

    doc = PODocument(
        po_id=po.id,
        revision=revision,
        sha256=sha,
        original_filename=filename,
        mime_type=mime_type,
        size_bytes=len(content),
        template_name=template_name,
        generated_by=generated_by,
    )
    db.session.add(doc)
    return doc


def po_document_path(doc: PODocument) -> Path:
    sha = doc.sha256
    return _upload_root() / sha[0:2] / sha[2:4] / sha


def delete_po_document(doc: PODocument) -> None:
    """Remove the DB row and the on-disk blob if no other rows reference it."""
    sha = doc.sha256
    path = po_document_path(doc)
    db.session.delete(doc)
    db.session.flush()
    still_referenced = (
        db.session.query(PODocument.id).filter_by(sha256=sha).first() is not None
        or db.session.query(Attachment.id).filter_by(sha256=sha).first() is not None
    )
    if not still_referenced and path.exists():
        try:
            path.unlink()
        except OSError:
            pass


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
_IF_OPEN_RE = re.compile(r"\{\{\s*#\s*if\s+([a-zA-Z0-9_.]+)\s*\}\}")
_ELSE_RE = re.compile(r"\{\{\s*else\s*\}\}")
_IF_CLOSE_RE = re.compile(r"\{\{\s*/\s*if\s*\}\}")


def render_po_xlsx(po: PurchaseOrder, template_bytes: bytes,
                   revision: Optional[int] = None) -> bytes:
    """Render a PO into a copy of the supplied xlsx template.

    Two replacement modes coexist:

    1. Named cells: any cell whose text contains {{name}} is substituted
       in place. Surrounding formatting on that cell is preserved.

    2. Loop region: rows from the row containing {{#items}} through the
       row containing {{/items}} (exclusive of marker rows) are treated
       as a per-line template. They are duplicated once per PO line
       and the rows that originally followed are shifted down. The
       marker rows themselves are deleted.

    Lines are ordered by their stable line_no so the # column in the rendered
    document matches what the user sees in the web UI.

    Returns the rendered xlsx as bytes.
    """
    wb = load_workbook(io.BytesIO(template_bytes))

    context = _po_context(po, revision=revision)

    for ws in wb.worksheets:
        _render_loop_region(ws, po)
        _render_named_cells(ws, context)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _po_context(po: PurchaseOrder, revision: Optional[int] = None) -> dict:
    return {
        "po_number": po.po_number or "",
        "vendor": po.vendor or "",
        "ship_to": po.ship_to or "",
        "notes": po.notes or "",
        "date": (po.ordered_at or po.created_at).strftime("%Y-%m-%d")
                 if (po.ordered_at or po.created_at) else "",
        "total": po.total,
        "revision": revision if revision is not None else "",
    }


def _line_context(line: POLine) -> dict:
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
        # Stable per-PO line number set when the line was added (or, on legacy
        # DBs, backfilled by migration). Matches the "#" shown in the UI.
        "item.index": line.line_no or 0,
        "item.notes": line.notes or "",
    }


def _resolve_conditionals(text: str, ctx: dict) -> str:
    """Process {{#if var}}...{{else}}...{{/if}} blocks within a cell value.

    The {{else}} branch is optional; omitting it means an empty string when
    the condition is false. Blocks do not nest.
    """
    result = []
    pos = 0
    while pos < len(text):
        m = _IF_OPEN_RE.search(text, pos)
        if m is None:
            result.append(text[pos:])
            break
        result.append(text[pos:m.start()])
        var = m.group(1)
        end_m = _IF_CLOSE_RE.search(text, m.end())
        if end_m is None:
            # Malformed block — leave the rest untouched.
            result.append(text[m.start():])
            break
        inner = text[m.end():end_m.start()]
        else_m = _ELSE_RE.search(inner)
        condition = bool(ctx.get(var))
        if else_m:
            branch = inner[:else_m.start()] if condition else inner[else_m.end():]
        else:
            branch = inner if condition else ""
        result.append(branch)
        pos = end_m.end()
    return "".join(result)


def _substitute(text: str, ctx: dict) -> str:
    text = _resolve_conditionals(text, ctx)

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

    # --- Snapshot ALL merged ranges before any mutations. ---
    # openpyxl's insert_rows/delete_rows have well-known bugs that corrupt
    # merged cell ranges. We take full control: unmerge everything first,
    # then re-apply adjusted ranges ourselves after the mutations.
    all_merges = [
        (mr.min_row, mr.max_row, mr.min_col, mr.max_col)
        for mr in ws.merged_cells.ranges
    ]
    for mr in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(mr))

    # Split merges into two buckets:
    #   template_merges — fully inside the template rows; replicated per line.
    #   external_merges — outside the loop block; row-shifted by the net delta.
    tmpl_start, tmpl_end = open_row + 1, close_row - 1
    template_merges: list[tuple[int, int, int, int]] = []  # offsets from tmpl_start
    external_merges: list[tuple[int, int, int, int]] = []
    for (min_r, max_r, min_c, max_c) in all_merges:
        if min_r >= tmpl_start and max_r <= tmpl_end:
            template_merges.append((min_r - tmpl_start, max_r - tmpl_start, min_c, max_c))
        else:
            external_merges.append((min_r, max_r, min_c, max_c))

    # Capture template row data (values + styles + row height).
    max_col = ws.max_column
    template = []
    for r in template_rows:
        rd = ws.row_dimensions.get(r)
        row_snapshot = {
            "height": rd.height if rd else None,
            "cells": [],
        }
        for c in range(1, max_col + 1):
            cell = ws.cell(row=r, column=c)
            row_snapshot["cells"].append({
                "value": cell.value,
                "font": copy(cell.font),
                "fill": copy(cell.fill),
                "border": copy(cell.border),
                "alignment": copy(cell.alignment),
                "number_format": cell.number_format,
            })
        template.append(row_snapshot)

    lines = sorted(po.lines, key=_render_sort_key)

    rows_to_remove = close_row - open_row + 1  # inclusive of both markers
    ws.delete_rows(open_row, rows_to_remove)

    insert_count = template_height * len(lines)

    if not lines:
        _reapply_external_merges(ws, external_merges, open_row, close_row, -rows_to_remove)
        return

    if insert_count > 0:
        ws.insert_rows(open_row, amount=insert_count)

    write_row = open_row
    for line in lines:
        ctx = _line_context(line)
        line_base = write_row
        for tmpl_row in template:
            if tmpl_row["height"] is not None:
                ws.row_dimensions[write_row].height = tmpl_row["height"]
            for col_idx, src in enumerate(tmpl_row["cells"], start=1):
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
        for (off_min_r, off_max_r, min_c, max_c) in template_merges:
            ws.merge_cells(
                start_row=line_base + off_min_r,
                end_row=line_base + off_max_r,
                start_column=min_c,
                end_column=max_c,
            )

    delta = insert_count - rows_to_remove
    _reapply_external_merges(ws, external_merges, open_row, close_row, delta)


def _reapply_external_merges(
    ws: Worksheet,
    external_merges: list[tuple[int, int, int, int]],
    open_row: int,
    close_row: int,
    delta: int,
) -> None:
    """Re-apply merged cell ranges that were outside the loop region.

    Ranges entirely above open_row are unchanged. Ranges that started after
    close_row are shifted by delta. Ranges that overlapped the loop block
    itself are silently dropped — they were invalid template constructs.
    """
    for (min_r, max_r, min_c, max_c) in external_merges:
        if max_r < open_row:
            pass  # entirely above — no adjustment needed
        elif min_r > close_row:
            min_r += delta
            max_r += delta
        else:
            continue  # overlapped the loop block — drop it
        ws.merge_cells(start_row=min_r, end_row=max_r, start_column=min_c, end_column=max_c)
