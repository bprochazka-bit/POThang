"""
Data model.

Item                 - a thing we want to buy. Has a desired qty and unit cost.
Tag                  - free-form labels on items (many-to-many).
PurchaseOrder        - a group of POLines sent to a vendor.
POLine               - allocates some qty of an Item into a PO. An item can
                       appear on multiple POs if split (partial purchasing).
Receipt              - a receipt event against a POLine, with qty received.
Attachment           - a file attached to an item or a PO, stored on disk
                       under sha256-named subdirs, with original filename and
                       mime type kept in the DB.

State on an Item is derived from the lifecycle below. We store an explicit
`state` column for fast filtering, but it is recomputed whenever lines or
receipts change (see services.recompute_item_state).

Lifecycle (from config: requested -> approved -> ordered -> partial -> received,
plus cancelled which is terminal):

  - requested:  no POLine yet, or all POLines on cancelled POs
  - approved:   manually marked approved; still no active POLine
  - ordered:    sum(POLine.qty for active POs) == item.qty AND no receipts
  - partial:    sum(received) > 0 but < item.qty
  - received:   sum(received) >= item.qty
  - cancelled:  user-set; ignored for total math
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, ForeignKey, Table,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship

from .extensions import db


# Many-to-many: items <-> tags
item_tags = Table(
    "item_tags",
    db.metadata,
    Column("item_id", Integer, ForeignKey("items.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(db.Model):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)

    def __repr__(self):
        return f"<Tag {self.name}>"


class Item(db.Model):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, default="")
    description = Column(Text)
    model = Column(String(128))
    vendor = Column(String(128))
    vendor_sku = Column(String(128))
    url = Column(String(1024))
    qty = Column(Integer, nullable=False, default=1)
    unit_cost = Column(Float, nullable=False, default=0.0)
    notes = Column(Text)
    state = Column(String(32), nullable=False, default="requested", index=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=dt.datetime.utcnow,
                        onupdate=dt.datetime.utcnow, nullable=False)

    tags = relationship("Tag", secondary=item_tags, backref="items")
    lines = relationship("POLine", back_populates="item",
                         cascade="all, delete-orphan")
    attachments = relationship(
        "Attachment", back_populates="item",
        primaryjoin="and_(Attachment.item_id==Item.id)",
        cascade="all, delete-orphan",
    )

    # ----- Required-fields completeness -----
    # Fields required before an item is considered ready to be on a PO.
    # Tuple of (attribute_name, human_label).
    REQUIRED_FIELDS = (
        ("name", "name"),
        ("description", "description"),
        ("url", "URL"),
        ("vendor", "vendor"),
        ("qty", "qty"),
        ("unit_cost", "price"),
    )

    @property
    def missing_fields(self) -> list[str]:
        """Human-readable labels of required fields not yet populated."""
        missing = []
        for attr, label in self.REQUIRED_FIELDS:
            value = getattr(self, attr, None)
            if attr == "qty":
                if not value or value < 1:
                    missing.append(label)
            elif attr == "unit_cost":
                if not value or value <= 0:
                    missing.append(label)
            else:
                if value is None or (isinstance(value, str) and not value.strip()):
                    missing.append(label)
        return missing

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields

    # ----- Derived helpers -----
    @property
    def estimated_total(self) -> float:
        return (self.unit_cost or 0.0) * (self.qty or 0)

    @property
    def qty_on_active_pos(self) -> int:
        return sum(line.qty for line in self.lines
                   if line.po and line.po.status != "cancelled")

    @property
    def qty_received(self) -> int:
        total = 0
        for line in self.lines:
            if line.po and line.po.status == "cancelled":
                continue
            total += sum(r.qty for r in line.receipts)
        return total

    @property
    def qty_unallocated(self) -> int:
        return max(0, (self.qty or 0) - self.qty_on_active_pos)

    def to_dict(self, include_lines: bool = True) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "model": self.model,
            "vendor": self.vendor,
            "vendor_sku": self.vendor_sku,
            "url": self.url,
            "qty": self.qty,
            "unit_cost": self.unit_cost,
            "estimated_total": self.estimated_total,
            "notes": self.notes,
            "state": self.state,
            "tags": [t.name for t in self.tags],
            "is_complete": self.is_complete,
            "missing_fields": self.missing_fields,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_lines:
            d["lines"] = [line.to_dict() for line in self.lines]
        return d


class PurchaseOrder(db.Model):
    __tablename__ = "purchase_orders"
    id = Column(Integer, primary_key=True)
    po_number = Column(String(64), unique=True, nullable=False)
    vendor = Column(String(128))
    ship_to = Column(String(255))
    notes = Column(Text)
    status = Column(String(32), nullable=False, default="draft", index=True)
    # status: draft | approved | ordered | received (auto) | cancelled
    ordered_at = Column(DateTime)
    created_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=dt.datetime.utcnow,
                        onupdate=dt.datetime.utcnow, nullable=False)

    lines = relationship("POLine", back_populates="po",
                         cascade="all, delete-orphan")
    attachments = relationship(
        "Attachment", back_populates="po",
        primaryjoin="and_(Attachment.po_id==PurchaseOrder.id)",
        cascade="all, delete-orphan",
    )

    @property
    def total(self) -> float:
        return sum((l.unit_cost or 0.0) * (l.qty or 0) for l in self.lines)

    @property
    def fully_received(self) -> bool:
        if not self.lines:
            return False
        return all(line.qty_received >= line.qty for line in self.lines)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "po_number": self.po_number,
            "vendor": self.vendor,
            "ship_to": self.ship_to,
            "notes": self.notes,
            "status": self.status,
            "ordered_at": self.ordered_at.isoformat() if self.ordered_at else None,
            "total": self.total,
            "lines": [line.to_dict() for line in self.lines],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class POLine(db.Model):
    """Allocation of (some qty of) an Item to a PO."""
    __tablename__ = "po_lines"
    id = Column(Integer, primary_key=True)
    po_id = Column(Integer, ForeignKey("purchase_orders.id", ondelete="CASCADE"),
                   nullable=False)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"),
                     nullable=False)
    qty = Column(Integer, nullable=False, default=1)
    # Snapshot of cost at PO creation time (item unit_cost may drift later).
    unit_cost = Column(Float, nullable=False, default=0.0)
    notes = Column(Text)

    po = relationship("PurchaseOrder", back_populates="lines")
    item = relationship("Item", back_populates="lines")
    receipts = relationship("Receipt", back_populates="line",
                            cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_poline_po_item", "po_id", "item_id"),
    )

    @property
    def qty_received(self) -> int:
        return sum(r.qty for r in self.receipts)

    @property
    def line_total(self) -> float:
        return (self.unit_cost or 0.0) * (self.qty or 0)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "po_id": self.po_id,
            "po_number": self.po.po_number if self.po else None,
            "item_id": self.item_id,
            "item_name": self.item.name if self.item else None,
            "item_description": self.item.description if self.item else None,
            "qty": self.qty,
            "unit_cost": self.unit_cost,
            "line_total": self.line_total,
            "qty_received": self.qty_received,
            "notes": self.notes,
            "receipts": [r.to_dict() for r in self.receipts],
        }


class Receipt(db.Model):
    __tablename__ = "receipts"
    id = Column(Integer, primary_key=True)
    line_id = Column(Integer, ForeignKey("po_lines.id", ondelete="CASCADE"),
                     nullable=False)
    qty = Column(Integer, nullable=False)
    received_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)
    received_by = Column(String(128))
    notes = Column(Text)

    line = relationship("POLine", back_populates="receipts")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "line_id": self.line_id,
            "qty": self.qty,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "received_by": self.received_by,
            "notes": self.notes,
        }


class Attachment(db.Model):
    """File on disk; reachable from either an Item or a PO (or both - we
    store one row per attachment-target relationship for simplicity)."""
    __tablename__ = "attachments"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"))
    po_id = Column(Integer, ForeignKey("purchase_orders.id", ondelete="CASCADE"))
    sha256 = Column(String(64), nullable=False, index=True)
    original_filename = Column(String(255), nullable=False)
    mime_type = Column(String(128))
    size_bytes = Column(Integer)
    kind = Column(String(32))  # quote | image | datasheet | other
    uploaded_at = Column(DateTime, default=dt.datetime.utcnow, nullable=False)
    uploaded_by = Column(String(128))

    item = relationship("Item", back_populates="attachments",
                        foreign_keys=[item_id])
    po = relationship("PurchaseOrder", back_populates="attachments",
                      foreign_keys=[po_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "item_id": self.item_id,
            "po_id": self.po_id,
            "sha256": self.sha256,
            "original_filename": self.original_filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "kind": self.kind,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
            "uploaded_by": self.uploaded_by,
        }
