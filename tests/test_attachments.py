"""Attachment storage tests."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from purchasetracker.models import Attachment, Item
from purchasetracker.services import (
    attachment_path, delete_attachment, store_attachment,
)


def test_store_creates_hashed_path(app, db):
    item = Item(description="Anything", qty=1, unit_cost=1.0)
    db.session.add(item)
    db.session.commit()

    payload = b"hello world"
    expected_sha = hashlib.sha256(payload).hexdigest()
    att = store_attachment(io.BytesIO(payload), "hello.txt", "text/plain",
                           kind="other", item_id=item.id)
    db.session.commit()

    assert att.sha256 == expected_sha
    assert att.size_bytes == len(payload)
    p = attachment_path(att)
    assert p.exists()
    assert str(p).endswith(f"{expected_sha[0:2]}/{expected_sha[2:4]}/{expected_sha}")
    assert p.read_bytes() == payload


def test_dedup_same_content(app, db):
    item = Item(description="X", qty=1, unit_cost=1.0)
    db.session.add(item)
    db.session.commit()

    payload = b"shared content"
    a1 = store_attachment(io.BytesIO(payload), "a.txt", "text/plain",
                          item_id=item.id)
    a2 = store_attachment(io.BytesIO(payload), "b.txt", "text/plain",
                          item_id=item.id)
    db.session.commit()

    assert a1.sha256 == a2.sha256
    # Same on-disk file is shared.
    assert attachment_path(a1) == attachment_path(a2)


def test_delete_keeps_blob_when_still_referenced(app, db):
    item = Item(description="X", qty=1, unit_cost=1.0)
    db.session.add(item)
    db.session.commit()

    payload = b"keep me"
    a1 = store_attachment(io.BytesIO(payload), "a.txt", item_id=item.id)
    a2 = store_attachment(io.BytesIO(payload), "b.txt", item_id=item.id)
    db.session.commit()

    path = attachment_path(a1)
    assert path.exists()

    delete_attachment(a1)
    db.session.commit()
    # a2 still references the blob, so file should remain.
    assert path.exists()

    delete_attachment(a2)
    db.session.commit()
    # All references gone -> blob deleted.
    assert not path.exists()
