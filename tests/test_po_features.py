"""Tests for PO status transitions and the add-line tag filter."""
from __future__ import annotations

from purchasetracker.models import Item, POLine, PurchaseOrder, Receipt, Tag
from purchasetracker.services import get_or_create_tag


# ---------- PO status transitions ----------

def test_new_po_defaults_to_draft(client, db):
    client.post("/pos/new", data={"po_number": "PO-NEW"}, follow_redirects=True)
    po = db.session.query(PurchaseOrder).filter_by(po_number="PO-NEW").one()
    assert po.status == "draft"


def test_set_po_to_approved(client, db):
    client.post("/pos/new", data={"po_number": "PO-A"}, follow_redirects=True)
    po = db.session.query(PurchaseOrder).filter_by(po_number="PO-A").one()
    resp = client.post(
        f"/pos/{po.id}/edit",
        data={"vendor": "", "ship_to": "", "notes": "", "status": "approved"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    db.session.refresh(po)
    assert po.status == "approved"


def test_set_po_to_ordered_stamps_ordered_at(client, db):
    client.post("/pos/new", data={"po_number": "PO-O"}, follow_redirects=True)
    po = db.session.query(PurchaseOrder).filter_by(po_number="PO-O").one()
    assert po.ordered_at is None
    client.post(
        f"/pos/{po.id}/edit",
        data={"vendor": "", "ship_to": "", "notes": "", "status": "ordered"},
        follow_redirects=True,
    )
    db.session.refresh(po)
    assert po.status == "ordered"
    assert po.ordered_at is not None


def test_set_po_to_cancelled(client, db):
    client.post("/pos/new", data={"po_number": "PO-X"}, follow_redirects=True)
    po = db.session.query(PurchaseOrder).filter_by(po_number="PO-X").one()
    client.post(
        f"/pos/{po.id}/edit",
        data={"vendor": "", "ship_to": "", "notes": "", "status": "cancelled"},
        follow_redirects=True,
    )
    db.session.refresh(po)
    assert po.status == "cancelled"


def test_unknown_po_status_rejected(client, db):
    client.post("/pos/new", data={"po_number": "PO-BAD"}, follow_redirects=True)
    po = db.session.query(PurchaseOrder).filter_by(po_number="PO-BAD").one()
    client.post(
        f"/pos/{po.id}/edit",
        data={"vendor": "", "ship_to": "", "notes": "", "status": "bogus"},
        follow_redirects=True,
    )
    db.session.refresh(po)
    assert po.status == "draft"  # unchanged


def test_full_receipt_marks_po_received(client, db):
    item = Item(name="W", description="Widget", qty=2, unit_cost=1.0,
                vendor="V", url="http://v")
    db.session.add(item)
    db.session.flush()
    po = PurchaseOrder(po_number="PO-RX", status="ordered")
    db.session.add(po)
    db.session.flush()
    line = POLine(po_id=po.id, item_id=item.id, qty=2, unit_cost=1.0)
    db.session.add(line)
    db.session.commit()

    client.post(f"/pos/lines/{line.id}/receive", data={"qty": "2"},
                follow_redirects=True)
    db.session.refresh(po)
    assert po.status == "received"


# ---------- Add-line tag filter ----------

def _seed_tagged_items(db):
    # Items must be complete (have name, description, vendor, url, qty, unit_cost)
    # to be addable to a PO.
    a = Item(name="Lab supplies", description="Beakers etc", qty=1,
             unit_cost=10.0, vendor="Acme", url="http://acme.example/lab")
    b = Item(name="Office stuff", description="Pens etc", qty=1,
             unit_cost=5.0, vendor="OffMax", url="http://offmax.example/o")
    c = Item(name="Lab equipment", description="Microscope", qty=1,
             unit_cost=99.0, vendor="Sci", url="http://sci.example/m")
    db.session.add_all([a, b, c])
    db.session.flush()
    lab = get_or_create_tag("lab")
    urgent = get_or_create_tag("urgent")
    a.tags = [lab]
    c.tags = [lab, urgent]
    db.session.commit()


def test_default_shows_all_items(client, db):
    _seed_tagged_items(db)
    client.post("/pos/new", data={"po_number": "PO-T"}, follow_redirects=True)
    po = db.session.query(PurchaseOrder).filter_by(po_number="PO-T").one()
    resp = client.get(f"/pos/{po.id}")
    body = resp.data.decode()
    assert "Lab supplies" in body
    assert "Office stuff" in body
    assert "Lab equipment" in body


def test_tag_filter_narrows_candidates(client, db):
    _seed_tagged_items(db)
    client.post("/pos/new", data={"po_number": "PO-T2"}, follow_redirects=True)
    po = db.session.query(PurchaseOrder).filter_by(po_number="PO-T2").one()
    resp = client.get(f"/pos/{po.id}?tag=lab")
    body = resp.data.decode()
    assert "Lab supplies" in body
    assert "Lab equipment" in body
    assert "Office stuff" not in body


def test_tag_filter_empty_string_shows_all(client, db):
    _seed_tagged_items(db)
    client.post("/pos/new", data={"po_number": "PO-T3"}, follow_redirects=True)
    po = db.session.query(PurchaseOrder).filter_by(po_number="PO-T3").one()
    resp = client.get(f"/pos/{po.id}?tag=")
    body = resp.data.decode()
    assert "Office stuff" in body  # not excluded by an empty filter


def test_tag_filter_no_matches_shows_empty_state(client, db):
    _seed_tagged_items(db)
    client.post("/pos/new", data={"po_number": "PO-T4"}, follow_redirects=True)
    po = db.session.query(PurchaseOrder).filter_by(po_number="PO-T4").one()
    resp = client.get(f"/pos/{po.id}?tag=nonexistent")
    body = resp.data.decode()
    assert "No items with tag" in body


# ---------- Add-line merge: duplicates collapse onto an existing line ----------

def _seed_one_complete_item(db, qty=5):
    item = Item(name="Bolt", description="M5x10", qty=qty, unit_cost=0.10,
                vendor="McMaster", url="http://mc.example/b")
    db.session.add(item)
    db.session.flush()
    po = PurchaseOrder(po_number="PO-MERGE")
    db.session.add(po)
    db.session.commit()
    return item, po


def test_add_duplicate_item_merges_into_existing_line(client, db):
    item, po = _seed_one_complete_item(db, qty=5)

    client.post(f"/pos/{po.id}/lines/add",
                data={"item_id": item.id, "qty": "2"},
                follow_redirects=True)
    client.post(f"/pos/{po.id}/lines/add",
                data={"item_id": item.id, "qty": "3"},
                follow_redirects=True)

    lines = db.session.query(POLine).filter_by(po_id=po.id).all()
    assert len(lines) == 1, "duplicate add should merge, not create a new row"
    assert lines[0].qty == 5


# ---------- Edit qty on an existing line ----------

def test_update_line_qty_increases(client, db):
    item, po = _seed_one_complete_item(db, qty=10)
    client.post(f"/pos/{po.id}/lines/add",
                data={"item_id": item.id, "qty": "2"},
                follow_redirects=True)
    line = db.session.query(POLine).filter_by(po_id=po.id).one()
    client.post(f"/pos/lines/{line.id}/qty",
                data={"qty": "7"}, follow_redirects=True)
    db.session.refresh(line)
    assert line.qty == 7


def test_update_line_qty_below_received_is_rejected(client, db):
    item, po = _seed_one_complete_item(db, qty=10)
    client.post(f"/pos/{po.id}/lines/add",
                data={"item_id": item.id, "qty": "5"},
                follow_redirects=True)
    line = db.session.query(POLine).filter_by(po_id=po.id).one()
    # Receive 3
    client.post(f"/pos/lines/{line.id}/receive",
                data={"qty": "3"}, follow_redirects=True)
    # Try to drop below 3
    resp = client.post(f"/pos/lines/{line.id}/qty",
                       data={"qty": "2"}, follow_redirects=True)
    assert b"already received" in resp.data
    db.session.refresh(line)
    assert line.qty == 5


def test_update_line_qty_over_item_pool_is_rejected(client, db):
    item, po = _seed_one_complete_item(db, qty=4)
    client.post(f"/pos/{po.id}/lines/add",
                data={"item_id": item.id, "qty": "2"},
                follow_redirects=True)
    line = db.session.query(POLine).filter_by(po_id=po.id).one()
    resp = client.post(f"/pos/lines/{line.id}/qty",
                       data={"qty": "99"}, follow_redirects=True)
    assert b"available" in resp.data
    db.session.refresh(line)
    assert line.qty == 2


# ---------- Receiving page ----------

def test_receiving_index_lists_pos_with_outstanding(client, db):
    item, po = _seed_one_complete_item(db, qty=5)
    client.post(f"/pos/{po.id}/lines/add",
                data={"item_id": item.id, "qty": "5"},
                follow_redirects=True)
    # Move PO to ordered so it shows up in receiving.
    client.post(f"/pos/{po.id}/edit",
                data={"vendor": "", "ship_to": "", "notes": "",
                      "status": "ordered"},
                follow_redirects=True)

    resp = client.get("/pos/receiving/")
    assert resp.status_code == 200
    assert b"PO-MERGE" in resp.data


def test_receiving_excludes_draft_pos(client, db):
    item, po = _seed_one_complete_item(db, qty=5)
    client.post(f"/pos/{po.id}/lines/add",
                data={"item_id": item.id, "qty": "5"},
                follow_redirects=True)
    # Leave PO as draft.
    resp = client.get("/pos/receiving/")
    assert resp.status_code == 200
    assert b"PO-MERGE" not in resp.data


def test_receiving_batch_receive_records_receipts(client, db):
    item, po = _seed_one_complete_item(db, qty=5)
    client.post(f"/pos/{po.id}/lines/add",
                data={"item_id": item.id, "qty": "5"},
                follow_redirects=True)
    client.post(f"/pos/{po.id}/edit",
                data={"vendor": "", "ship_to": "", "notes": "",
                      "status": "ordered"},
                follow_redirects=True)
    line = db.session.query(POLine).filter_by(po_id=po.id).one()

    resp = client.post(f"/pos/receiving/{po.id}/receive",
                       data={f"qty_{line.id}": "3", "notes": "Box A"},
                       follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(line)
    assert line.qty_received == 3
    receipts = db.session.query(Receipt).filter_by(line_id=line.id).all()
    assert len(receipts) == 1
    assert receipts[0].notes == "Box A"


def test_receiving_full_qty_marks_po_received(client, db):
    item, po = _seed_one_complete_item(db, qty=5)
    client.post(f"/pos/{po.id}/lines/add",
                data={"item_id": item.id, "qty": "5"},
                follow_redirects=True)
    client.post(f"/pos/{po.id}/edit",
                data={"vendor": "", "ship_to": "", "notes": "",
                      "status": "ordered"},
                follow_redirects=True)
    line = db.session.query(POLine).filter_by(po_id=po.id).one()

    client.post(f"/pos/receiving/{po.id}/receive",
                data={f"qty_{line.id}": "5"},
                follow_redirects=True)
    db.session.refresh(po)
    assert po.status == "received"


def test_receiving_caps_qty_at_outstanding(client, db):
    item, po = _seed_one_complete_item(db, qty=5)
    client.post(f"/pos/{po.id}/lines/add",
                data={"item_id": item.id, "qty": "5"},
                follow_redirects=True)
    client.post(f"/pos/{po.id}/edit",
                data={"vendor": "", "ship_to": "", "notes": "",
                      "status": "ordered"},
                follow_redirects=True)
    line = db.session.query(POLine).filter_by(po_id=po.id).one()

    # Ask to receive 999; should be capped to 5.
    client.post(f"/pos/receiving/{po.id}/receive",
                data={f"qty_{line.id}": "999"},
                follow_redirects=True)
    db.session.refresh(line)
    assert line.qty_received == 5
