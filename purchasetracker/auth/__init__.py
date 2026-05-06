"""
Pluggable authentication.

Three modes, selected by config.AUTH_MODE:

    single_user   - everyone is config.SINGLE_USER_NAME (default while
                    developing or behind a fully trusted LAN).
    proxy_header  - reverse proxy / Authentik passes the username via a
                    header. Only trusted if request.remote_addr is in
                    config.TRUSTED_PROXIES.
    ldap          - bind against an LDAP server (requires python3-ldap3).

The rest of the app calls current_user() to get the current username, and
applies @login_required to views that should refuse anonymous access.
"""
from .core import current_user, init_auth, login_required

__all__ = ["current_user", "init_auth", "login_required"]
