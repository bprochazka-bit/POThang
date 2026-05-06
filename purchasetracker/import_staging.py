"""
Staging service for the multi-step import wizard.

Between the upload, mapping, and commit steps we need a place to keep the
parsed source rows and the user's mapping decisions. Storing them in the
session cookie would bloat it past browser limits for non-trivial imports,
so we write a JSON sidecar to instance/import_staging/<uuid>.json and only
keep the UUID in the session.

Stale staging files are pruned on each new upload (older than STALE_HOURS).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from flask import current_app, session


STALE_HOURS = 24
SESSION_KEY = "import_staging_uuid"


def _staging_dir() -> Path:
    p = Path(current_app.instance_path) / "import_staging"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path_for(staging_id: str) -> Path:
    # Defend against funny IDs in the URL/session.
    if not all(c.isalnum() or c == "-" for c in staging_id):
        raise ValueError("Invalid staging id")
    return _staging_dir() / f"{staging_id}.json"


def create(parsed: dict, original_filename: str) -> str:
    """Write a new staging document and return its id."""
    _prune_stale()
    sid = uuid.uuid4().hex
    payload = {
        "created_at": time.time(),
        "original_filename": original_filename,
        "format": parsed.get("format"),
        "had_header_row": parsed.get("had_header_row", False),
        "headers": parsed["headers"],
        "rows": parsed["rows"],
        "mapping": {},        # filled in at the mapping step
        "constants": {},      # pt_field -> constant string value
        "edits": {},          # row_index (str) -> {pt_field: value} overrides
    }
    _path_for(sid).write_text(json.dumps(payload))
    session[SESSION_KEY] = sid
    return sid


def load(staging_id: str | None = None) -> dict | None:
    sid = staging_id or session.get(SESSION_KEY)
    if not sid:
        return None
    try:
        path = _path_for(sid)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def save(staging_id: str, payload: dict) -> None:
    _path_for(staging_id).write_text(json.dumps(payload))


def discard(staging_id: str | None = None) -> None:
    sid = staging_id or session.pop(SESSION_KEY, None)
    if not sid:
        return
    try:
        p = _path_for(sid)
    except ValueError:
        return
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def _prune_stale() -> None:
    cutoff = time.time() - STALE_HOURS * 3600
    try:
        for f in _staging_dir().iterdir():
            if not f.is_file() or f.suffix != ".json":
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
    except OSError:
        pass
