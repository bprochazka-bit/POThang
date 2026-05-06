"""Light end-to-end coverage hitting each blueprint."""
from __future__ import annotations


def test_index_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data


def test_items_list_loads(client):
    resp = client.get("/items/")
    assert resp.status_code == 200
    assert b"Items" in resp.data


def test_create_item_flow(client, db):
    resp = client.post(
        "/items/new",
        data={
            "description": "Test widget",
            "vendor": "Acme",
            "model": "W-1",
            "qty": "3",
            "unit_cost": "12.34",
            "tags": "urgent, lab",
            "notes": "Test note",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    from purchasetracker.models import Item
    items = db.session.query(Item).all()
    assert len(items) == 1
    assert items[0].description == "Test widget"
    assert items[0].qty == 3
    assert {t.name for t in items[0].tags} == {"urgent", "lab"}


def test_create_po_flow(client, db):
    resp = client.post(
        "/pos/new",
        data={"po_number": "PO-7", "vendor": "Acme"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    from purchasetracker.models import PurchaseOrder
    pos = db.session.query(PurchaseOrder).all()
    assert len(pos) == 1
    assert pos[0].po_number == "PO-7"


def test_duplicate_po_number_rejected(client, db):
    client.post("/pos/new", data={"po_number": "PO-DUP"}, follow_redirects=True)
    resp = client.post("/pos/new", data={"po_number": "PO-DUP"},
                       follow_redirects=True)
    assert resp.status_code == 200
    from purchasetracker.models import PurchaseOrder
    assert db.session.query(PurchaseOrder).count() == 1


def test_filter_by_state(client, db):
    from purchasetracker.models import Item
    db.session.add_all([
        Item(description="A", qty=1, unit_cost=1.0, state="requested"),
        Item(description="B", qty=1, unit_cost=1.0, state="approved"),
    ])
    db.session.commit()
    resp = client.get("/items/?state=approved")
    assert b"B" in resp.data
    # A should not be in the visible body table; description appears only
    # if the row is rendered. Check absence as a row.
    assert b">A<" not in resp.data
