"""Tests for the mobile API, OCR matching, and the received-shipments view."""
from __future__ import annotations

import datetime as dt
import io

import pytest

from purchasetracker import create_app
from purchasetracker.extensions import db as _db
from purchasetracker.models import Attachment, Item, POLine, PurchaseOrder, Receipt
from purchasetracker.ocr_match import OcrText, score_item, suggest_item_fields


def _po(db, number="PO-1", status="ordered", vendor="Acme", lines=()):
    po = PurchaseOrder(po_number=number, status=status, vendor=vendor)
    db.session.add(po)
    db.session.flush()
    for item, qty in lines:
        db.session.add(POLine(po_id=po.id, item_id=item.id, qty=qty,
                              unit_cost=item.unit_cost))
    db.session.commit()
    return po


def _item(db, **kw):
    kw.setdefault("qty", 1)
    kw.setdefault("unit_cost", 10.0)
    item = Item(**kw)
    db.session.add(item)
    db.session.commit()
    return item


@pytest.fixture
def stock(db):
    headphones = _item(db, name="Sony Wireless Noise Cancelling Headphones",
                       model="WH-1000XM5", vendor="Amazon", qty=3)
    cable = _item(db, name="USB-C to USB-C Cable 6ft", vendor_sku="B0BX1234",
                  vendor="Amazon", qty=10)
    drill = _item(db, name="Cordless Drill Driver Kit", model="DCD771C2",
                  vendor="Home Depot", qty=1)
    po = _po(db, lines=[(headphones, 3), (cable, 10)])
    return {"po": po, "headphones": headphones, "cable": cable, "drill": drill}


# ---------- OCR matching ----------

def test_model_number_wins_over_name_words():
    class I:  # minimal stand-in for Item
        name, description, vendor, vendor_sku = "Headphones", None, None, None
        model = "WH-1000XM5"
    score, reasons = score_item(I, OcrText("SONY\nModel: WH-1000XM5\nMade in Malaysia"))
    assert score >= 100
    assert any("model" in r for r in reasons)


def test_model_number_tolerates_ocr_confusables():
    class I:
        name, description, vendor, vendor_sku = "Thing", None, None, None
        model = "SB-1050"
    # O for 0 and 5 for S - classic label misreads.
    score, reasons = score_item(I, OcrText("Part 5B-1O5O"))
    assert score >= 80
    assert "fuzzy" in reasons[0]


def test_name_words_with_a_typo_still_match():
    class I:
        name, description, vendor, vendor_sku, model = (
            "Cordless Drill Driver Kit", None, None, None, None)
    score, _ = score_item(I, OcrText("20V MAX CORDLES DRILL / DRIVER KIT"))
    assert score >= 40


def test_unrelated_text_scores_nothing():
    class I:
        name, description, vendor, vendor_sku, model = (
            "Cordless Drill Driver Kit", None, None, "DCD771C2", None)
    score, _ = score_item(I, OcrText("Bananas organic 2 lb"))
    assert score < 12


# ---------- field suggestions ----------

def test_suggest_fields_from_product_page():
    text = (
        "amazon.com/Anker-Charger/dp/B0C1234\n"
        "Anker 735 Charger (Nano II 65W)\n"
        "Visit the Anker Store\n"
        "$45.99\n"
        "Model Number: A2667\n"
        "ASIN: B0C1234567\n"
    )
    lines = [{"text": l, "height": 40 if "Charger (" in l else 14}
             for l in text.splitlines()]
    f = suggest_item_fields(text, lines)
    assert f["name"] == "Anker 735 Charger (Nano II 65W)"
    assert f["vendor"] == "Amazon"
    assert f["unit_cost"] == 45.99
    assert f["model"] == "A2667"
    assert f["vendor_sku"] == "B0C1234567"
    assert f["brand"] == "Anker"
    assert f["url"].startswith("https://amazon.com/")


def test_suggest_fields_without_heights_uses_longest_line():
    f = suggest_item_fields("SALE\nHeavy Duty Shelving Unit 5-Tier\nQty: 4")
    assert f["name"] == "Heavy Duty Shelving Unit 5-Tier"
    assert f["qty"] == 4


# ---------- API auth ----------

@pytest.fixture
def token_app(tmp_path):
    app = create_app(config_overrides={
        "TESTING": True, "SECRET_KEY": "t",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 't.sqlite'}",
        "UPLOAD_DIR": str(tmp_path / "up"),
        "AUTH_MODE": "ldap",  # no session -> anonymous unless a token is sent
        "API_TOKENS": {"s3cret": "dock-phone"},
    })
    with app.app_context():
        _db.drop_all()
        _db.create_all()
    return app


def test_api_requires_auth_in_non_single_user_modes(token_app):
    c = token_app.test_client()
    r = c.get("/api/v1/ping")
    assert r.status_code == 401
    assert r.get_json()["ok"] is False


def test_api_rejects_wrong_token(token_app):
    c = token_app.test_client()
    r = c.get("/api/v1/ping", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_api_accepts_token_and_records_user(token_app):
    c = token_app.test_client()
    r = c.get("/api/v1/ping", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200
    assert r.get_json()["user"] == "dock-phone"


def test_bearer_token_also_works_on_web_routes(token_app):
    c = token_app.test_client()
    r = c.get("/pos/receiving/shipments", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


# ---------- receiving via API ----------

def test_receivable_pos_and_lines(client, stock):
    r = client.get("/api/v1/receiving/pos")
    pos = r.get_json()["pos"]
    assert [p["po_number"] for p in pos] == ["PO-1"]
    assert pos[0]["outstanding"] == 13

    r = client.get(f"/api/v1/pos/{stock['po'].id}/lines")
    lines = r.get_json()["lines"]
    assert len(lines) == 2
    assert lines[0]["item"]["model"] == "WH-1000XM5"


def test_match_finds_line_by_model(client, stock):
    r = client.post("/api/v1/receiving/match",
                    json={"text": "SONY wh-1000xm5 Wireless\nMade in Malaysia"})
    data = r.get_json()
    assert data["ok"]
    assert data["matches"][0]["item"]["id"] == stock["headphones"].id
    assert data["matches"][0]["outstanding"] == 3


def test_match_reports_items_not_on_open_pos(client, stock):
    r = client.post("/api/v1/receiving/match",
                    json={"text": "DEWALT DCD771C2 20V drill"})
    data = r.get_json()
    assert data["matches"] == []
    assert data["other_items"][0]["id"] == stock["drill"].id


def test_match_requires_text(client, stock):
    r = client.post("/api/v1/receiving/match", json={"text": "  "})
    assert r.status_code == 400


def test_receive_via_api_clamps_and_records_user(client, db, stock):
    line = stock["po"].lines[0]
    r = client.post(f"/api/v1/lines/{line.id}/receive",
                    json={"qty": 5, "notes": "box 1"})
    data = r.get_json()
    assert r.status_code == 200, data
    assert data["receipt"]["qty"] == 3          # clamped to outstanding
    assert data["receipt"]["received_by"] == "tester"
    assert data["line"]["outstanding"] == 0
    assert "Only 3" in data["message"]
    db.session.expire_all()
    assert db.session.get(Item, stock["headphones"].id).state == "received"

    r = client.post(f"/api/v1/lines/{line.id}/receive", json={"qty": 1})
    assert r.status_code == 400
    assert "already fully received" in r.get_json()["error"]


def test_receive_rejects_bad_qty(client, stock):
    line = stock["po"].lines[1]
    assert client.post(f"/api/v1/lines/{line.id}/receive",
                       json={"qty": 0}).status_code == 400
    assert client.post(f"/api/v1/lines/{line.id}/receive",
                       json={"qty": "x"}).status_code == 400
    assert client.post("/api/v1/lines/9999/receive",
                       json={"qty": 1}).status_code == 404


def test_receiving_all_lines_marks_po_received(client, db, stock):
    for line in stock["po"].lines:
        client.post(f"/api/v1/lines/{line.id}/receive", json={"qty": line.qty})
    db.session.expire_all()
    assert db.session.get(PurchaseOrder, stock["po"].id).status == "received"
    assert client.get("/api/v1/receiving/pos").get_json()["pos"] == []


def test_web_receive_now_records_receiver(client, db, stock):
    line = stock["po"].lines[1]
    client.post(f"/pos/lines/{line.id}/receive", data={"qty": 2})
    r = db.session.query(Receipt).one()
    assert r.received_by == "tester"


# ---------- creating items ----------

def test_create_item_json(client, db):
    r = client.post("/api/v1/items", json={
        "name": "Label printer", "model": "QL-800", "unit_cost": "89.5",
        "qty": 2, "tags": ["Office", "Shipping"]})
    assert r.status_code == 201
    item = db.session.get(Item, r.get_json()["item"]["id"])
    assert item.model == "QL-800" and item.unit_cost == 89.5 and item.qty == 2
    assert sorted(t.name for t in item.tags) == ["Office", "Shipping"]


def test_create_item_multipart_with_photo(client, db):
    r = client.post("/api/v1/items", data={
        "name": "Shelf", "tags": "Garage,Storage",
        "photo": (io.BytesIO(b"\x89PNG fake"), "shot.png"),
    }, content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    data = r.get_json()
    assert data["photos"] == 1
    assert data["item"]["image_url"].startswith("/attachments/")
    att = db.session.query(Attachment).one()
    assert att.kind == "image"
    assert sorted(t.name for t in att.item.tags) == ["Garage", "Storage"]


def test_create_item_rejects_non_image_upload(client, db):
    r = client.post("/api/v1/items", data={
        "name": "X", "photo": (io.BytesIO(b"MZ"), "virus.exe"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert db.session.query(Item).count() == 0


def test_create_item_requires_name(client):
    assert client.post("/api/v1/items", json={"model": "x"}).status_code == 400


def test_parse_endpoint(client):
    r = client.post("/api/v1/items/parse", json={"text": "Widget Pro 3000\n$12.00"})
    f = r.get_json()["fields"]
    assert f["name"] == "Widget Pro 3000"
    assert f["unit_cost"] == 12.0


# ---------- received shipments view ----------

def _receipt(db, line, qty, when, by="tester"):
    r = Receipt(line_id=line.id, qty=qty, received_at=when, received_by=by)
    db.session.add(r)
    db.session.commit()
    return r


@pytest.fixture
def utc_app_config(app):
    app.config["DISPLAY_TIMEZONE"] = "UTC"
    return app


def test_shipments_groups_by_day_and_po(client, db, stock, utc_app_config):
    l1, l2 = stock["po"].lines
    other = _po(db, number="PO-2", lines=[(stock["drill"], 1)])
    now = dt.datetime.utcnow().replace(hour=12)
    yesterday = now - dt.timedelta(days=1)
    _receipt(db, l1, 1, now)
    _receipt(db, l2, 4, now)
    _receipt(db, other.lines[0], 1, now)
    _receipt(db, l1, 1, yesterday)

    with client.application.app_context():
        from purchasetracker.services import receipts_by_day
        days = receipts_by_day()
    assert [d["date"] for d in days] == [now.date(), yesterday.date()]
    assert days[0]["receipt_count"] == 3
    assert days[0]["units"] == 6
    assert [g["po"].po_number for g in days[0]["po_groups"]] == ["PO-1", "PO-2"]
    assert days[1]["units"] == 1

    html = client.get("/pos/receiving/shipments").get_data(as_text=True)
    assert "Received shipments" in html
    assert now.strftime("%A, %B") in html
    assert "PO-2" in html


def test_shipments_respects_timezone_day_boundaries(client, db, stock, app):
    app.config["DISPLAY_TIMEZONE"] = "America/Chicago"
    line = stock["po"].lines[0]
    # 03:00 UTC is still the previous evening in Chicago.
    when = dt.datetime(2026, 3, 10, 3, 0)
    _receipt(db, line, 1, when)
    with app.app_context():
        from purchasetracker.services import receipts_by_day
        days = receipts_by_day(start=dt.date(2026, 3, 9), end=dt.date(2026, 3, 9))
    assert len(days) == 1 and days[0]["date"] == dt.date(2026, 3, 9)


def test_shipments_filters(client, db, stock, utc_app_config):
    l1, l2 = stock["po"].lines
    now = dt.datetime.utcnow()
    _receipt(db, l1, 1, now)
    r2 = _receipt(db, l2, 2, now)
    r2.verified_at = now
    db.session.commit()

    page = client.get("/pos/receiving/shipments?q=wh-1000").get_data(as_text=True)
    assert "Sony Wireless" in page and "USB-C to USB-C" not in page
    page = client.get("/pos/receiving/shipments?verified=no").get_data(as_text=True)
    assert "Sony Wireless" in page and "USB-C to USB-C" not in page
    old = (now - dt.timedelta(days=40)).date().isoformat()
    page = client.get(f"/pos/receiving/shipments?from={old}&to={old}").get_data(as_text=True)
    assert "No receipts match" in page


def test_verify_single_and_bulk(client, db, stock):
    l1, l2 = stock["po"].lines
    now = dt.datetime.utcnow()
    a = _receipt(db, l1, 1, now)
    b = _receipt(db, l2, 2, now)

    r = client.post(f"/pos/receipts/{a.id}/verify", data={"verified": "1"},
                    headers={"Accept": "application/json"})
    state = r.get_json()["receipt"]
    assert state["verified"] and state["verified_by"] == "tester"

    r = client.post("/pos/receipts/verify-bulk",
                    data={"receipt_ids": [a.id, b.id], "verified": "1"},
                    headers={"Accept": "application/json"})
    assert all(s["verified"] for s in r.get_json()["receipts"])

    client.post(f"/pos/receipts/{a.id}/verify", data={"verified": "0"})
    db.session.expire_all()
    assert db.session.get(Receipt, a.id).verified_at is None
    assert db.session.get(Receipt, b.id).verified_at is not None


def test_editing_receipt_qty_clears_verification(client, db, stock):
    line = stock["po"].lines[1]
    r = _receipt(db, line, 2, dt.datetime.utcnow())
    r.verified_at, r.verified_by = dt.datetime.utcnow(), "checker"
    db.session.commit()
    client.post(f"/pos/receipts/{r.id}/update", data={"qty": 3})
    db.session.expire_all()
    assert db.session.get(Receipt, r.id).verified_at is None


def test_api_receipts_by_day_and_verify(client, db, stock):
    line = stock["po"].lines[0]
    r = _receipt(db, line, 1, dt.datetime.utcnow())
    data = client.get("/api/v1/receipts/by-day").get_json()
    assert data["days"][0]["pos"][0]["receipts"][0]["item_model"] == "WH-1000XM5"
    v = client.post(f"/api/v1/receipts/{r.id}/verify", json={"verified": True})
    assert v.get_json()["receipt"]["verified_by"] == "tester"
