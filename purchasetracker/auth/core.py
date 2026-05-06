"""
Auth backends and request-level integration.

The single entry point is init_auth(app). It registers a before_request
hook that populates flask.g.user (a dict with at least a 'name' key) and
mounts a /login and /logout route for the modes that need them.
"""
from __future__ import annotations

from functools import wraps
from typing import Optional

from flask import (
    Blueprint, current_app, flash, g, redirect, render_template, request,
    session, url_for,
)


# ---------- Public helpers ----------

def current_user() -> Optional[dict]:
    return getattr(g, "user", None)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


# ---------- Wiring ----------

def init_auth(app):
    @app.before_request
    def _resolve_user():
        mode = app.config.get("AUTH_MODE", "single_user")
        if mode == "single_user":
            g.user = {"name": app.config.get("SINGLE_USER_NAME", "user")}
            return

        if mode == "proxy_header":
            trusted = set(app.config.get("TRUSTED_PROXIES", []))
            remote = request.remote_addr
            header = app.config.get("PROXY_HEADER_NAME", "X-Remote-User")
            email_header = app.config.get("PROXY_HEADER_EMAIL", "X-Remote-Email")
            if remote in trusted and request.headers.get(header):
                g.user = {
                    "name": request.headers[header],
                    "email": request.headers.get(email_header),
                }
            else:
                g.user = None
            return

        if mode == "ldap":
            name = session.get("username")
            g.user = {"name": name} if name else None
            return

        # Unknown mode: fail closed.
        g.user = None

    app.register_blueprint(_make_auth_bp())


def _make_auth_bp() -> Blueprint:
    bp = Blueprint("auth", __name__, url_prefix="/auth")

    @bp.route("/login", methods=["GET", "POST"])
    def login():
        mode = current_app.config.get("AUTH_MODE", "single_user")
        if mode == "single_user":
            return redirect(url_for("main.index"))
        if mode == "proxy_header":
            # Login is the proxy's job; if we got here, the proxy didn't
            # set the header (or we're not behind a trusted proxy).
            return render_template(
                "auth/proxy_required.html",
                header=current_app.config["PROXY_HEADER_NAME"],
            ), 403
        if mode == "ldap":
            error = None
            if request.method == "POST":
                username = request.form.get("username", "").strip()
                password = request.form.get("password", "")
                if _ldap_authenticate(username, password):
                    session["username"] = username
                    nxt = request.args.get("next") or url_for("main.index")
                    return redirect(nxt)
                error = "Invalid credentials."
            return render_template("auth/login.html", error=error)
        return ("Unknown AUTH_MODE", 500)

    @bp.route("/logout", methods=["POST"])
    def logout():
        session.pop("username", None)
        flash("Signed out.")
        return redirect(url_for("main.index"))

    return bp


# ---------- LDAP backend (lazy-imported) ----------

def _ldap_authenticate(username: str, password: str) -> bool:
    if not username or not password:
        return False
    try:
        from ldap3 import Server, Connection, Tls, ALL  # type: ignore
        import ssl
    except ImportError:
        current_app.logger.error(
            "AUTH_MODE=ldap but python3-ldap3 is not installed."
        )
        return False

    cfg = current_app.config
    tls = Tls(validate=ssl.CERT_REQUIRED) if cfg.get("LDAP_TLS_VERIFY", True) \
        else Tls(validate=ssl.CERT_NONE)
    server = Server(cfg["LDAP_URI"], tls=tls, get_info=ALL)
    bind_dn = cfg["LDAP_BIND_DN_TEMPLATE"].format(username=username)
    try:
        conn = Connection(server, user=bind_dn, password=password,
                          auto_bind=True)
        conn.unbind()
        return True
    except Exception as e:  # ldap3 raises a variety of exceptions
        current_app.logger.info("LDAP auth failed for %s: %s", username, e)
        return False
