"""Tests for the xlsx PO template renderer."""
from __future__ import annotations

import io
from pathlib import Path

from openpyxl import Workbook, load_workbook

from purchasetracker.models import Item, POLine, PurchaseOrder
from purchasetracker.services import render_po_xlsx


def _build_template_bytes() -> bytes:
    """Build a minimal in-memory template with both named cells and a loop."""
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "PO: {{po_number}}"
    ws["A2"] = "Vendor: {{vendor}}"
    ws["A3"] = "Date: {{date}}"
    # Loop region
    ws["A5"] = "{{#items}}"
    ws["A6"] = "{{item.index}}"
    ws["B6"] = "{{item.description}}"
    ws["C6"] = "{{item.qty}}"
    ws["D6"] = "{{item.unit_cost}}"
    ws["E6"] = "{{item.line_total}}"
    ws["A7"] = "{{/items}}"
    ws["A9"] = "Total: {{total}}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _seed_po(db) -> PurchaseOrder:
    item_a = Item(description="Widget A", qty=2, unit_cost=10.5)
    item_b = Item(description="Widget B", qty=3, unit_cost=2.0)
    db.session.add_all([item_a, item_b])
    db.session.flush()
    po = PurchaseOrder(po_number="PO-2026-001", vendor="Acme Co.")
    db.session.add(po)
    db.session.flush()
    db.session.add(POLine(po_id=po.id, item_id=item_a.id, qty=2, unit_cost=10.5))
    db.session.add(POLine(po_id=po.id, item_id=item_b.id, qty=3, unit_cost=2.0))
    db.session.commit()
    return po


def test_named_cells_substituted(app, db):
    po = _seed_po(db)
    rendered = render_po_xlsx(po, _build_template_bytes())
    wb = load_workbook(io.BytesIO(rendered))
    ws = wb.active
    assert ws["A1"].value == "PO: PO-2026-001"
    assert ws["A2"].value == "Vendor: Acme Co."


def test_loop_expands_to_one_row_per_line(app, db):
    po = _seed_po(db)
    rendered = render_po_xlsx(po, _build_template_bytes())
    wb = load_workbook(io.BytesIO(rendered))
    ws = wb.active

    # Open marker at row 5, single template row at 6, close marker at 7.
    # After rendering: rows 5 and 6 should be the two line items, with
    # everything below shifted appropriately.
    assert ws.cell(row=5, column=1).value == 1
    assert ws.cell(row=5, column=2).value == "Widget A"
    assert ws.cell(row=5, column=3).value == 2
    assert ws.cell(row=5, column=4).value == 10.5
    assert ws.cell(row=5, column=5).value == 21.0

    assert ws.cell(row=6, column=1).value == 2
    assert ws.cell(row=6, column=2).value == "Widget B"
    assert ws.cell(row=6, column=3).value == 3
    assert ws.cell(row=6, column=4).value == 2.0
    assert ws.cell(row=6, column=5).value == 6.0


def test_total_named_cell_substituted(app, db):
    po = _seed_po(db)
    rendered = render_po_xlsx(po, _build_template_bytes())
    wb = load_workbook(io.BytesIO(rendered))
    ws = wb.active
    # The Total row started at A9 in the template; the loop expanded by 0 net
    # rows (deleted 3 marker+template rows, inserted 2 item rows = net -1),
    # so the total is at A8 now. Easier to find it by content scan.
    found = None
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and "Total:" in c.value:
                found = c.value
                break
    assert found is not None
    assert "27" in found  # 21.0 + 6.0 = 27.0


def test_render_with_no_lines_drops_loop(app, db):
    po = PurchaseOrder(po_number="PO-empty", vendor="None")
    db.session.add(po)
    db.session.commit()
    rendered = render_po_xlsx(po, _build_template_bytes())
    wb = load_workbook(io.BytesIO(rendered))
    ws = wb.active
    # Markers and template row should all be gone, no blank line items remain.
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str):
                assert "{{#items}}" not in c.value
                assert "{{/items}}" not in c.value
                assert "{{item." not in c.value


def test_sample_template_renders(app, db):
    """Render against the bundled sample template; must not throw."""
    po = _seed_po(db)
    sample = Path(__file__).resolve().parent.parent / "sample_template" / "po_template.xlsx"
    rendered = render_po_xlsx(po, sample.read_bytes())
    wb = load_workbook(io.BytesIO(rendered))
    ws = wb.active
    # Sanity check: PO number landed somewhere.
    found_po_number = False
    for row in ws.iter_rows():
        for c in row:
            if c.value == "PO-2026-001":
                found_po_number = True
    assert found_po_number, "Rendered template missing PO number"
