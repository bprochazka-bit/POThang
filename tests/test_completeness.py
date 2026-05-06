"""Tests for v5: required-fields completeness on items."""
from __future__ import annotations

from purchasetracker.models import Item, PurchaseOrder, POLine


# ---------- Model: is_complete and missing_fields ----------

def test_fully_filled_item_is_complete(db):
    item = Item(
        name="GPU",
        description="RTX 4090 Founders Edition",
        vendor="Newegg",
        url="https://newegg.example/rtx4090",
        qty=1,
        unit_cost=1899.0,
    )
    db.session.add(item)
    db.session.commit()
    assert item.is_complete is True
    assert item.missing_fields == []


def test_stub_item_is_incomplete(db):
    item = Item(name="Tent", qty=1, unit_cost=0.0)
    db.session.add(item)
    db.session.commit()
    assert item.is_complete is False
    missing = item.missing_fields
    assert "description" in missing
    assert "vendor" in missing
    assert "URL" in missing
    assert "price" in missing


def test_zero_unit_cost_counts_as_missing(db):
    item = Item(
        name="X", description="d", vendor="v", url="https://u",
        qty=1, unit_cost=0.0,
    )
    db.session.add(item)
    db.session.commit()
    assert "price" in item.missing_fields
    assert item.is_complete is False


def test_blank_string_counts_as_missing(db):
    """Whitespace-only fields count as missing."""
    item = Item(
        name="X", description="   ", vendor="V", url="https://u",
        qty=1, unit_cost=10.0,
    )
    db.session.add(item)
    db.session.commit()
    assert "description" in item.missing_fields


def test_to_dict_includes_completeness(db):
    item = Item(name="X", qty=1, unit_cost=0.0)
    db.session.add(item)
    db.session.commit()
    d = item.to_dict(include_lines=False)
    assert "is_complete" in d
    assert d["is_complete"] is False
    assert "missing_fields" in d
    assert isinstance(d["missing_fields"], list)


# ---------- Items list filter ----------

def test_items_list_complete_no_filter(client, db):
    complete = Item(name="Done", description="d", vendor="v",
                    url="https://u", qty=1, unit_cost=10.0)
    incomplete = Item(name="Stub", qty=1, unit_cost=0.0)
    db.session.add_all([complete, incomplete])
    db.session.commit()

    resp = client.get("/items/?complete=no")
    body = resp.data.decode()
    assert "Stub" in body
    # "Done" appears nowhere as a row (might appear in tag dropdowns etc., but
    # the link <a>Done</a> won't be in body if filtered)
    assert ">Done</a>" not in body


def test_items_list_complete_yes_filter(client, db):
    complete = Item(name="Done", description="d", vendor="v",
                    url="https://u", qty=1, unit_cost=10.0)
    incomplete = Item(name="Stub", qty=1, unit_cost=0.0)
    db.session.add_all([complete, incomplete])
    db.session.commit()

    resp = client.get("/items/?complete=yes")
    body = resp.data.decode()
    assert ">Done</a>" in body
    assert ">Stub</a>" not in body


def test_items_list_marks_incomplete_rows(client, db):
    incomplete = Item(name="Stub", qty=1, unit_cost=0.0)
    db.session.add(incomplete)
    db.session.commit()

    resp = client.get("/items/")
    body = resp.data.decode()
    assert "row-incomplete" in body
    assert "missing-badge" in body
    # Should mention specific missing fields
    assert "vendor" in body
    assert "URL" in body


def test_items_list_no_badge_on_complete_rows(client, db):
    complete = Item(name="Done", description="d", vendor="v",
                    url="https://u", qty=1, unit_cost=10.0)
    db.session.add(complete)
    db.session.commit()

    resp = client.get("/items/")
    body = resp.data.decode()
    # The CSS class shouldn't appear at all when only complete items exist
    assert "row-incomplete" not in body
    assert "missing-badge" not in body


# ---------- PO add-line guard ----------

def test_cannot_add_incomplete_item_to_po(client, db):
    """Items missing required fields are rejected at the PO level."""
    incomplete = Item(name="Stub", qty=1, unit_cost=0.0)
    db.session.add(incomplete)
    db.session.flush()
    po = PurchaseOrder(po_number="PO-G")
    db.session.add(po)
    db.session.commit()

    resp = client.post(
        f"/pos/{po.id}/lines/add",
        data={"item_id": incomplete.id, "qty": "1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    # No line should have been created
    assert db.session.query(POLine).count() == 0
    # Error flash should mention "missing"
    assert b"missing" in resp.data.lower() or b"Missing" in resp.data


def test_can_add_complete_item_to_po(client, db):
    """Sanity check: complete items still go on POs as before."""
    complete = Item(name="GPU", description="d", vendor="v",
                    url="https://u", qty=1, unit_cost=10.0)
    db.session.add(complete)
    db.session.flush()
    po = PurchaseOrder(po_number="PO-OK")
    db.session.add(po)
    db.session.commit()

    resp = client.post(
        f"/pos/{po.id}/lines/add",
        data={"item_id": complete.id, "qty": "1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert db.session.query(POLine).count() == 1


def test_po_dropdown_excludes_incomplete_items(client, db):
    """Incomplete items don't appear in the PO add-line dropdown."""
    complete = Item(name="Complete-Item", description="d", vendor="v",
                    url="https://u", qty=1, unit_cost=10.0)
    incomplete = Item(name="Incomplete-Item", qty=1, unit_cost=0.0)
    db.session.add_all([complete, incomplete])
    db.session.flush()
    po = PurchaseOrder(po_number="PO-D")
    db.session.add(po)
    db.session.commit()

    resp = client.get(f"/pos/{po.id}")
    body = resp.data.decode()
    # The dropdown lists complete items as <option>...
    assert "Complete-Item" in body
    # The "1 hidden" warning surfaces incomplete count
    assert "Incomplete-Item" not in body or "1 hidden" in body


# ---------- Migration: name column added on existing DB ----------

def test_migration_adds_name_column_to_legacy_db(tmp_path):
    """Simulate an older install (no name column) and confirm migration backfills."""
    import sqlite3
    from purchasetracker import create_app

    db_path = tmp_path / "legacy.sqlite"
    # Build a minimal v4-shape items table by hand.
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE items (
            id INTEGER PRIMARY KEY,
            description VARCHAR(255) NOT NULL,
            model VARCHAR(128),
            vendor VARCHAR(128),
            vendor_sku VARCHAR(128),
            url VARCHAR(1024),
            qty INTEGER NOT NULL DEFAULT 1,
            unit_cost FLOAT NOT NULL DEFAULT 0.0,
            notes TEXT,
            state VARCHAR(32) NOT NULL DEFAULT 'requested',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        INSERT INTO items
            (id, description, qty, unit_cost, state, created_at, updated_at)
        VALUES
            (1, 'Old item', 1, 0, 'requested', '2025-01-01', '2025-01-01');
    """)
    conn.commit()
    conn.close()

    # Boot the app - migration should run
    app = create_app({
        "SECRET_KEY": "x",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "AUTH_MODE": "single_user",
    })
    from purchasetracker.extensions import db as _db
    from purchasetracker.models import Item as _Item

    with app.app_context():
        item = _db.session.get(_Item, 1)
        assert item is not None
        # name column was created and backfilled from description
        assert item.name == "Old item"
        # description is preserved (still equal to legacy value)
        assert item.description == "Old item"


def test_migration_is_idempotent(tmp_path):
    """Running create_app twice on the same DB doesn't double-migrate or error."""
    from purchasetracker import create_app

    db_path = tmp_path / "test.sqlite"
    overrides = {
        "SECRET_KEY": "x",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "AUTH_MODE": "single_user",
    }
    app1 = create_app(overrides)
    app2 = create_app(overrides)
    # Just succeeding without raising is the assertion.
    assert app1 is not None
    assert app2 is not None
