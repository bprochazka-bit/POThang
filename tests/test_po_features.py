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
