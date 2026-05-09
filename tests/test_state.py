"""Tests for item state transitions and PO line management."""
from __future__ import annotations

import datetime as dt

from purchasetracker.models import Item, POLine, PurchaseOrder, Receipt
from purchasetracker.services import recompute_item_state, set_item_state


def _make_item(db, **kw):
    defaults = dict(description="Widget", qty=10, unit_cost=5.0,
                    state="requested")
    defaults.update(kw)
    item = Item(**defaults)
    db.session.add(item)
    db.session.commit()
    return item


def _make_po(db, po_number="PO-1", status="draft"):
    po = PurchaseOrder(po_number=po_number, status=status)
    db.session.add(po)
    db.session.commit()
    return po


def test_initial_state_is_requested(db):
    item = _make_item(db)
    assert item.state == "requested"


def test_approved_persists_until_ordered(db):
    item = _make_item(db)
    set_item_state(item, "approved")
    db.session.commit()
    recompute_item_state(item)
    db.session.commit()
    assert item.state == "approved"


def test_full_allocation_moves_to_ordered(db):
    item = _make_item(db, qty=10)
    po = _make_po(db, status="ordered")
    line = POLine(po_id=po.id, item_id=item.id, qty=10, unit_cost=5.0)
    db.session.add(line)
    db.session.commit()
    recompute_item_state(item)
    assert item.state == "ordered"


def test_draft_po_does_not_advance_item_state(db):
    """Draft POs are a working state; items on them stay 'requested'."""
    item = _make_item(db, qty=10)
    po = _make_po(db, status="draft")
    db.session.add(POLine(po_id=po.id, item_id=item.id, qty=10, unit_cost=5.0))
    db.session.commit()
    recompute_item_state(item)
    assert item.state == "requested"


def test_approved_po_marks_item_approved(db):
    """Items on an approved PO follow the PO into 'approved' (not 'ordered')."""
    item = _make_item(db, qty=10)
    po = _make_po(db, status="approved")
    db.session.add(POLine(po_id=po.id, item_id=item.id, qty=10, unit_cost=5.0))
    db.session.commit()
    recompute_item_state(item)
    assert item.state == "approved"


def test_po_status_change_propagates_to_items(client, db):
    """Setting a PO to 'approved' via the edit form updates its line items."""
    item = _make_item(db, qty=2, name="X", description="d", vendor="V",
                      url="https://u", unit_cost=10.0)
    po = _make_po(db, status="draft", po_number="PO-PROP")
    db.session.add(POLine(po_id=po.id, item_id=item.id, qty=2, unit_cost=10.0))
    db.session.commit()

    client.post(f"/pos/{po.id}/edit",
                data={"vendor": "", "ship_to": "", "notes": "",
                      "status": "approved"},
                follow_redirects=True)
    db.session.refresh(item)
    assert item.state == "approved"

    client.post(f"/pos/{po.id}/edit",
                data={"vendor": "", "ship_to": "", "notes": "",
                      "status": "ordered"},
                follow_redirects=True)
    db.session.refresh(item)
    assert item.state == "ordered"


def test_partial_allocation_keeps_state_requested(db):
    item = _make_item(db, qty=10)
    po = _make_po(db)
    db.session.add(POLine(po_id=po.id, item_id=item.id, qty=4, unit_cost=5.0))
    db.session.commit()
    recompute_item_state(item)
    # Not fully allocated and not received -> stays at requested
    assert item.state == "requested"
    assert item.qty_unallocated == 6


def test_partial_receipt_is_partial(db):
    item = _make_item(db, qty=10)
    po = _make_po(db, status="ordered")
    line = POLine(po_id=po.id, item_id=item.id, qty=10, unit_cost=5.0)
    db.session.add(line)
    db.session.flush()
    db.session.add(Receipt(line_id=line.id, qty=3))
    db.session.commit()
    recompute_item_state(item)
    assert item.state == "partial"
    assert item.qty_received == 3


def test_full_receipt_is_received(db):
    item = _make_item(db, qty=10)
    po = _make_po(db, status="ordered")
    line = POLine(po_id=po.id, item_id=item.id, qty=10, unit_cost=5.0)
    db.session.add(line)
    db.session.flush()
    db.session.add(Receipt(line_id=line.id, qty=10))
    db.session.commit()
    recompute_item_state(item)
    assert item.state == "received"


def test_cancelled_item_is_terminal(db):
    item = _make_item(db)
    set_item_state(item, "cancelled")
    db.session.commit()
    recompute_item_state(item)
    assert item.state == "cancelled"


def test_cancelled_po_is_ignored_for_state(db):
    item = _make_item(db, qty=5)
    po = _make_po(db, status="cancelled")
    db.session.add(POLine(po_id=po.id, item_id=item.id, qty=5, unit_cost=5.0))
    db.session.commit()
    recompute_item_state(item)
    # Cancelled POs do not count as allocations.
    assert item.state == "requested"
    assert item.qty_on_active_pos == 0
    assert item.qty_unallocated == 5


def test_split_across_two_pos(db):
    item = _make_item(db, qty=10)
    po1 = _make_po(db, po_number="PO-A", status="ordered")
    po2 = _make_po(db, po_number="PO-B", status="ordered")
    db.session.add(POLine(po_id=po1.id, item_id=item.id, qty=4, unit_cost=5.0))
    db.session.add(POLine(po_id=po2.id, item_id=item.id, qty=6, unit_cost=5.0))
    db.session.commit()
    recompute_item_state(item)
    assert item.state == "ordered"
    assert item.qty_on_active_pos == 10
