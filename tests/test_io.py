"""Round-trip tests for JSON and CSV import/export."""
from __future__ import annotations

import io
import json

from purchasetracker.models import Item, POLine, PurchaseOrder, Receipt, Tag


def test_json_export_includes_all(client, db):
    a = Item(description="A", qty=1, unit_cost=1.0)
    b = Item(description="B", qty=2, unit_cost=3.0)
    db.session.add_all([a, b])
    db.session.flush()
    po = PurchaseOrder(po_number="PO-X", vendor="V")
    db.session.add(po)
    db.session.flush()
    line = POLine(po_id=po.id, item_id=a.id, qty=1, unit_cost=1.0)
    db.session.add(line)
    db.session.flush()
    db.session.add(Receipt(line_id=line.id, qty=1))
    db.session.commit()

    resp = client.get("/io/export/json")
    assert resp.status_code == 200
    payload = json.loads(resp.data)
    assert len(payload["items"]) == 2
    assert len(payload["purchase_orders"]) == 1
    assert payload["purchase_orders"][0]["lines"][0]["receipts"][0]["qty"] == 1


def test_json_round_trip(client, db):
    a = Item(description="A", qty=2, unit_cost=4.0, vendor="V1", model="M1")
    db.session.add(a)
    db.session.flush()
    po = PurchaseOrder(po_number="PO-RT", vendor="V1")
    db.session.add(po)
    db.session.flush()
    line = POLine(po_id=po.id, item_id=a.id, qty=2, unit_cost=4.0)
    db.session.add(line)
    db.session.flush()
    db.session.add(Receipt(line_id=line.id, qty=1))
    db.session.commit()

    resp = client.get("/io/export/json")
    blob = resp.data

    # Wipe and reimport.
    db.session.query(Receipt).delete()
    db.session.query(POLine).delete()
    db.session.query(PurchaseOrder).delete()
    db.session.query(Item).delete()
    db.session.commit()
    assert db.session.query(Item).count() == 0

    resp = client.post(
        "/io/import",
        data={"kind": "json", "file": (io.BytesIO(blob), "x.json")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    items = db.session.query(Item).all()
    assert len(items) == 1
    assert items[0].description == "A"
    assert items[0].qty == 2

    pos = db.session.query(PurchaseOrder).all()
    assert len(pos) == 1
    assert pos[0].po_number == "PO-RT"
    assert len(pos[0].lines) == 1
    assert pos[0].lines[0].qty == 2
    assert pos[0].lines[0].receipts[0].qty == 1


def test_csv_export(client, db):
    a = Item(description="Widget", qty=3, unit_cost=1.5, vendor="Acme",
             model="W-1")
    a.tags = [Tag(name="urgent"), Tag(name="lab")]
    db.session.add(a)
    db.session.commit()

    resp = client.get("/io/export/csv")
    assert resp.status_code == 200
    text = resp.data.decode("utf-8")
    assert "Widget" in text
    assert "Acme" in text
    assert "urgent" in text


def test_csv_import_creates_items(client, db):
    """Old CSV format (description-only, no name column).
    Backward compat: description-as-title is loaded into the name field."""
    csv_text = (
        "description,model,vendor,vendor_sku,url,qty,unit_cost,state,tags,notes\n"
        "Widget,W-1,Acme,SKU1,http://x,5,2.5,requested,urgent;lab,note\n"
        "Sprocket,S-2,BetaCo,SKU2,,1,9.99,approved,,\n"
    )
    resp = client.post(
        "/io/import",
        data={"kind": "csv", "file": (io.BytesIO(csv_text.encode()), "x.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    # Legacy CSV: description column was the title -> stored in name.
    items = {i.name: i for i in db.session.query(Item).all()}
    assert "Widget" in items
    assert items["Widget"].qty == 5
    assert items["Widget"].unit_cost == 2.5
    assert {t.name for t in items["Widget"].tags} == {"urgent", "lab"}
    assert items["Sprocket"].state == "approved"


def test_csv_import_modern_format(client, db):
    """New CSV format with separate name and description columns."""
    csv_text = (
        "name,description,model,vendor,vendor_sku,url,qty,unit_cost,state,tags,notes\n"
        "GPU,RTX 4090 Founders,RTX4090,Newegg,SKU1,http://x,1,1899,requested,lab,\n"
    )
    resp = client.post(
        "/io/import",
        data={"kind": "csv", "file": (io.BytesIO(csv_text.encode()), "x.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    item = db.session.query(Item).one()
    assert item.name == "GPU"
    assert item.description == "RTX 4090 Founders"
    assert item.vendor == "Newegg"
    assert item.url == "http://x"
    assert item.is_complete  # all required fields present
