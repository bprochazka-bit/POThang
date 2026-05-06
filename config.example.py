"""
PurchaseTracker configuration.

This file is copied to instance/config.py on first run. Edit the copy in
instance/, not this file.
"""

# --- Core ---
SECRET_KEY = "change-me-to-a-random-string"
SQLALCHEMY_DATABASE_URI = "sqlite:///purchasetracker.sqlite"
SQLALCHEMY_TRACK_MODIFICATIONS = False

# --- Uploads ---
# Path is interpreted relative to the project root (parent of instance/).
UPLOAD_DIR = "uploads"
MAX_UPLOAD_MB = 25
ALLOWED_UPLOAD_EXTENSIONS = {
    "pdf", "png", "jpg", "jpeg", "gif", "webp", "txt", "md",
    "xlsx", "xls", "csv", "docx", "doc", "odt", "ods",
}

# Folder of reusable xlsx PO templates shown in the "Render from template"
# dropdown. Drop any .xlsx files here; the sample template is copied in on
# first run. Path resolved relative to the project root if not absolute.
PO_TEMPLATES_DIR = "po_templates"

# --- Workflow ---
# Order matters; the UI presents transitions in this order.
ITEM_STATES = [
    "requested",
    "approved",
    "ordered",
    "partial",
    "received",
    "cancelled",
]

# --- Auth ---
# One of: "single_user", "proxy_header", "ldap"
AUTH_MODE = "single_user"

# single_user mode
SINGLE_USER_NAME = "bill"

# proxy_header mode (Authentik, oauth2-proxy, nginx auth_request, etc.)
PROXY_HEADER_NAME = "X-Remote-User"
PROXY_HEADER_EMAIL = "X-Remote-Email"
# Only trust the header if REMOTE_ADDR is in this list. Set to your reverse
# proxy's address. Use ["127.0.0.1"] when proxying on the same host.
TRUSTED_PROXIES = ["127.0.0.1", "::1"]

# ldap mode (requires python3-ldap3)
LDAP_URI = "ldaps://ldap.example.org"
LDAP_BIND_DN_TEMPLATE = "uid={username},ou=people,dc=example,dc=org"
LDAP_USER_SEARCH_BASE = "ou=people,dc=example,dc=org"
LDAP_USER_SEARCH_FILTER = "(uid={username})"
LDAP_TLS_VERIFY = True
