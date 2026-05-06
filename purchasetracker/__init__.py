"""
PurchaseTracker - application factory.

Run with:  python3 -m flask --app purchasetracker run
"""
import os
import shutil
from pathlib import Path

from flask import Flask

from .extensions import db
from .auth import init_auth


def create_app(config_overrides=None):
    app = Flask(__name__, instance_relative_config=True)

    # Ensure instance directory and copy default config on first run.
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    instance_cfg = Path(app.instance_path) / "config.py"
    if not instance_cfg.exists():
        # config.example.py lives at the project root, two levels above this file.
        example = Path(__file__).resolve().parent.parent / "config.example.py"
        if example.exists():
            shutil.copy(example, instance_cfg)

    # Load config: defaults from package, then instance overrides, then test overrides.
    app.config.from_object("purchasetracker.default_config")
    app.config.from_pyfile("config.py", silent=True)
    if config_overrides:
        app.config.update(config_overrides)

    # Resolve UPLOAD_DIR relative to the project root if it's not absolute.
    upload_dir = Path(app.config["UPLOAD_DIR"])
    if not upload_dir.is_absolute():
        upload_dir = Path(app.root_path).parent / upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    app.config["UPLOAD_DIR_RESOLVED"] = str(upload_dir)
    app.config["MAX_CONTENT_LENGTH"] = app.config["MAX_UPLOAD_MB"] * 1024 * 1024

    db.init_app(app)
    init_auth(app)

    # Blueprints
    from .blueprints.items import bp as items_bp
    from .blueprints.pos import bp as pos_bp
    from .blueprints.attachments import bp as attachments_bp
    from .blueprints.io import bp as io_bp
    from .blueprints.main import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(items_bp, url_prefix="/items")
    app.register_blueprint(pos_bp, url_prefix="/pos")
    app.register_blueprint(attachments_bp, url_prefix="/attachments")
    app.register_blueprint(io_bp, url_prefix="/io")

    # Create tables on first run, then run any in-place schema migrations
    # for installs that already had data from an earlier version.
    with app.app_context():
        db.create_all()
        from .migrations import run_migrations
        run_migrations()

    # Template helpers
    @app.template_filter("currency")
    def currency_filter(value):
        if value is None:
            return ""
        try:
            return f"${value:,.2f}"
        except (TypeError, ValueError):
            return str(value)

    return app
