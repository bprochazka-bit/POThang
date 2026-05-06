"""Shared test fixtures."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from purchasetracker import create_app
from purchasetracker.extensions import db as _db


@pytest.fixture
def app(tmp_path):
    upload_dir = tmp_path / "uploads"
    db_path = tmp_path / "test.sqlite"
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()

    overrides = {
        "TESTING": True,
        "SECRET_KEY": "test",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "UPLOAD_DIR": str(upload_dir),
        "AUTH_MODE": "single_user",
        "SINGLE_USER_NAME": "tester",
    }
    app = create_app(config_overrides=overrides)
    # Reset DB to a known state.
    with app.app_context():
        _db.drop_all()
        _db.create_all()
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    with app.app_context():
        yield _db
