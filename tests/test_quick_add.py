"""Tests for the task-oriented quick-add workflow."""
from __future__ import annotations

import json

from purchasetracker.models import Item, Tag
from purchasetracker.services import get_or_create_tag


# ---------- /quick page ----------

def test_quick_page_loads(client):
    resp = client.get("/quick")
    assert resp.status_code == 200
    assert b"Quick add" in resp.data
    assert b"task-input" in resp.data


def test_quick_page_accepts_initial_tag(client):
    resp = client.get("/quick?tag=Camp%202026")
    assert resp.status_code == 200
    assert b"Camp 2026" in resp.data


# ---------- /items/api/tags ----------

def test_api_tags_lists_existing(client, db):
    get_or_create_tag("alpha")
    get_or_create_tag("beta")
    db.session.commit()
    resp = client.get("/items/api/tags")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert "alpha" in payload["tags"]
    assert "beta" in payload["tags"]


def test_api_tags_empty_when_none(client, db):
    resp = client.get("/items/api/tags")
    payload = resp.get_json()
    assert payload["tags"] == []


# ---------- /items/api/quick-add ----------

def test_quick_add_creates_stub_item(client, db):
    resp = client.post(
        "/items/api/quick-add",
        data=json.dumps({"name": "Tent stakes", "qty": 12, "tag": "Camp"}),
        content_type="application/json",
    )
    assert resp.status_code == 201
    payload = resp.get_json()
    assert payload["name"] == "Tent stakes"
    assert payload["qty"] == 12
    assert payload["state"] == "requested"
    assert "Camp" in payload["tags"]
    assert payload["unit_cost"] == 0.0
    assert payload["vendor"] is None
    assert payload["description"] is None  # filled in later
    assert payload["is_complete"] is False
    assert "edit_url" in payload
    assert "detail_url" in payload

    items = db.session.query(Item).all()
    assert len(items) == 1
    assert items[0].name == "Tent stakes"


def test_quick_add_accepts_legacy_description_param(client, db):
    """v3 contract sent 'description'; we still accept it as the name."""
    resp = client.post(
        "/items/api/quick-add",
        data=json.dumps({"description": "Legacy", "tag": "X"}),
        content_type="application/json",
    )
    assert resp.status_code == 201
    item = db.session.query(Item).one()
    assert item.name == "Legacy"


def test_quick_add_creates_new_tag_if_not_present(client, db):
    assert db.session.query(Tag).count() == 0
    client.post(
        "/items/api/quick-add",
        data=json.dumps({"description": "X", "tag": "BrandNew"}),
        content_type="application/json",
    )
    tags = [t.name for t in db.session.query(Tag).all()]
    assert "BrandNew" in tags


def test_quick_add_reuses_existing_tag(client, db):
    existing = get_or_create_tag("Camp")
    db.session.commit()
    existing_id = existing.id

    client.post(
        "/items/api/quick-add",
        data=json.dumps({"description": "Item A", "tag": "Camp"}),
        content_type="application/json",
    )
    client.post(
        "/items/api/quick-add",
        data=json.dumps({"description": "Item B", "tag": "Camp"}),
        content_type="application/json",
    )
    tags = db.session.query(Tag).filter_by(name="Camp").all()
    assert len(tags) == 1
    assert tags[0].id == existing_id


def test_quick_add_rejects_empty_description(client, db):
    resp = client.post(
        "/items/api/quick-add",
        data=json.dumps({"description": "  ", "tag": "X"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert db.session.query(Item).count() == 0


def test_quick_add_qty_defaults_to_one(client, db):
    resp = client.post(
        "/items/api/quick-add",
        data=json.dumps({"description": "Default qty", "tag": "X"}),
        content_type="application/json",
    )
    assert resp.status_code == 201
    item = db.session.query(Item).one()
    assert item.qty == 1


def test_quick_add_qty_floor_is_one(client, db):
    resp = client.post(
        "/items/api/quick-add",
        data=json.dumps({"description": "Bad qty", "qty": 0, "tag": "X"}),
        content_type="application/json",
    )
    assert resp.status_code == 201
    item = db.session.query(Item).one()
    assert item.qty == 1


def test_quick_add_works_without_tag(client, db):
    """Tag is optional; an untagged stub item should still be created."""
    resp = client.post(
        "/items/api/quick-add",
        data=json.dumps({"description": "Untagged", "qty": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 201
    item = db.session.query(Item).one()
    assert item.tags == []


# ---------- /items/api/by-tag ----------

def test_by_tag_returns_only_matching(client, db):
    a = Item(name="Camp item", qty=1, unit_cost=0.0)
    b = Item(name="Office item", qty=1, unit_cost=0.0)
    db.session.add_all([a, b])
    db.session.flush()
    a.tags = [get_or_create_tag("Camp")]
    b.tags = [get_or_create_tag("Office")]
    db.session.commit()

    resp = client.get("/items/api/by-tag?tag=Camp")
    payload = resp.get_json()
    assert payload["count"] == 1
    assert payload["items"][0]["name"] == "Camp item"


def test_by_tag_orders_newest_first(client, db):
    """Most recently created item appears first in the list."""
    tag = get_or_create_tag("X")
    db.session.commit()

    # Create two items in order.
    client.post(
        "/items/api/quick-add",
        data=json.dumps({"name": "First", "tag": "X"}),
        content_type="application/json",
    )
    client.post(
        "/items/api/quick-add",
        data=json.dumps({"name": "Second", "tag": "X"}),
        content_type="application/json",
    )

    resp = client.get("/items/api/by-tag?tag=X")
    items = resp.get_json()["items"]
    assert items[0]["name"] == "Second"
    assert items[1]["name"] == "First"


def test_by_tag_empty_query_returns_empty(client, db):
    get_or_create_tag("X")
    db.session.commit()
    resp = client.get("/items/api/by-tag?tag=")
    assert resp.get_json()["items"] == []


def test_by_tag_unknown_tag_returns_empty(client, db):
    resp = client.get("/items/api/by-tag?tag=DoesNotExist")
    assert resp.status_code == 200
    assert resp.get_json()["count"] == 0
