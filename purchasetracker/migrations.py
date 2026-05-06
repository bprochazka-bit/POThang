"""
Lightweight migrations for SQLite.

We don't carry Alembic for this app - the schema is small and SQLite supports
non-destructive ALTER TABLE for the column additions we need. On startup we
detect older schemas and bring them forward in place.

Each migration here must be:
  - idempotent (running twice does nothing the second time)
  - non-destructive (never drops or rewrites existing data)
  - safe on an empty database (used in tests)
"""
from __future__ import annotations

from sqlalchemy import inspect, text

from .extensions import db


def run_migrations() -> None:
    """Run all schema migrations. Called from create_app under app_context."""
    _migrate_add_item_name()


def _migrate_add_item_name() -> None:
    """v5: split items.description into a dedicated `name` field.

    Old schema:  items.description NOT NULL String(255)
    New schema:  items.name NOT NULL String(255), items.description Text NULL

    Strategy:
      - If items.name does not exist, add it with default ''.
      - For rows where name is empty, copy description into name (it was being
        used as the title field), and keep description as the long-text field.
        This preserves user data without losing anything.
    """
    insp = inspect(db.engine)
    if "items" not in insp.get_table_names():
        return  # fresh DB, create_all() will set up the new schema directly

    cols = {c["name"] for c in insp.get_columns("items")}
    if "name" in cols:
        return  # already migrated

    # Add the column. SQLite ALTER TABLE supports ADD COLUMN.
    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE items ADD COLUMN name VARCHAR(255) NOT NULL DEFAULT ''"))
        # Backfill: anywhere name is empty, populate from description.
        conn.execute(text(
            "UPDATE items SET name = COALESCE(description, '') "
            "WHERE name = '' OR name IS NULL"
        ))
