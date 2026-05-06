"""Tests confirming the tag-combobox is wired correctly server-side."""
from __future__ import annotations

import json
import re

from purchasetracker.models import Item, Tag
from purchasetracker.services import get_or_create_tag


def _tags_from_island(html: str, island_id: str) -> list[str]:
    """Extract the JSON tag list from a <script type=application/json> island."""
    m = re.search(
        rf'<script id="{island_id}" type="application/json">(.*?)</script>',
        html, re.DOTALL,
    )
    assert m, f"island '{island_id}' not found"
    return json.loads(m.group(1))


# ---------- Quick add page ----------

def test_quick_page_includes_combobox_script(client, db):
    resp = client.get("/quick")
    body = resp.data.decode()
    assert 'src="/static/tag-combobox.js"' in body
    assert 'class="tagbox"' in body
    assert 'id="task-list"' in body


def test_quick_page_renders_existing_tags_into_html(client, db):
    """Tag list is in the initial HTML, not fetched async after load."""
    get_or_create_tag("Camp 2026")
    get_or_create_tag("Pack 1151")
    db.session.commit()

    resp = client.get("/quick")
    body = resp.data.decode()
    tags = _tags_from_island(body, "initial-tags")
    assert "Camp 2026" in tags
    assert "Pack 1151" in tags


def test_quick_page_empty_tag_island_when_none(client, db):
    resp = client.get("/quick")
    body = resp.data.decode()
    tags = _tags_from_island(body, "initial-tags")
    assert tags == []


def test_quick_page_no_legacy_datalist(client, db):
    """We replaced <datalist> with the combobox; make sure it's gone."""
    resp = client.get("/quick")
    body = resp.data.decode()
    assert "<datalist" not in body
    assert 'list="task-suggestions"' not in body


# ---------- Item edit form ----------

def test_item_create_page_renders_combobox(client, db):
    get_or_create_tag("alpha")
    db.session.commit()
    resp = client.get("/items/new")
    body = resp.data.decode()
    assert 'id="item-tags-input"' in body
    assert 'id="item-tags-list"' in body
    tags = _tags_from_island(body, "all-tags")
    assert "alpha" in tags


def test_item_edit_page_renders_combobox_with_current_tags(client, db):
    item = Item(description="Thing", qty=1, unit_cost=0.0)
    db.session.add(item)
    db.session.flush()
    item.tags = [get_or_create_tag("urgent"), get_or_create_tag("lab")]
    db.session.commit()

    resp = client.get(f"/items/{item.id}/edit")
    body = resp.data.decode()
    # Current tags pre-filled in the input value
    assert 'value="urgent, lab"' in body or 'value="lab, urgent"' in body
    # All tags available as suggestions
    tags = _tags_from_island(body, "all-tags")
    assert "urgent" in tags
    assert "lab" in tags


# ---------- Static asset is reachable ----------

def test_tag_combobox_js_is_served(client):
    resp = client.get("/static/tag-combobox.js")
    assert resp.status_code == 200
    body = resp.data.decode()
    # Sanity: the global is exported
    assert "TagCombobox" in body
    assert "setSuggestions" in body
