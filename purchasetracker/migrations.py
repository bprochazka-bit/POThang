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
    _migrate_add_po_line_no()


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


def _migrate_add_po_line_no() -> None:
    """v6: give POLine a stable per-PO line number.

    Older installs had no line_no - line ordering came from list order in
    po.lines and the xlsx renderer sorted by vendor/name, which meant the
    "#" shown in the UI didn't match the number printed on the rendered
    document. Backfill assigns 1..N per PO, ordered by id (oldest first).
    """
    insp = inspect(db.engine)
    if "po_lines" not in insp.get_table_names():
        return

    cols = {c["name"] for c in insp.get_columns("po_lines")}
    if "line_no" in cols:
        return

    with db.engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE po_lines ADD COLUMN line_no INTEGER NOT NULL DEFAULT 0"
        ))
        # Backfill: for each row, set line_no to its rank (by id) within its PO.
        # SQLite supports this correlated subquery on a non-trivial table.
        conn.execute(text("""
            UPDATE po_lines
            SET line_no = (
                SELECT COUNT(*)
                FROM po_lines AS p2
                WHERE p2.po_id = po_lines.po_id
                  AND p2.id <= po_lines.id
            )
        """))
