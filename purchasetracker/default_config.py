"""
Built-in defaults. instance/config.py overrides these on a real install.
Kept in sync with config.example.py at the project root.
"""

SECRET_KEY = "dev-only-change-me"
SQLALCHEMY_DATABASE_URI = "sqlite:///purchasetracker.sqlite"
SQLALCHEMY_TRACK_MODIFICATIONS = False

UPLOAD_DIR = "uploads"
MAX_UPLOAD_MB = 25
ALLOWED_UPLOAD_EXTENSIONS = {
    "pdf", "png", "jpg", "jpeg", "gif", "webp", "txt", "md",
    "xlsx", "xls", "csv", "docx", "doc", "odt", "ods",
}

# Folder of reusable xlsx PO templates. Auto-seeded with the sample template
# on first run. Path resolved relative to the project root if not absolute.
PO_TEMPLATES_DIR = "po_templates"

ITEM_STATES = [
    "requested",
    "approved",
    "ordered",
    "partial",
    "received",
    "cancelled",
]

AUTH_MODE = "single_user"
SINGLE_USER_NAME = "bill"

PROXY_HEADER_NAME = "X-Remote-User"
PROXY_HEADER_EMAIL = "X-Remote-Email"
TRUSTED_PROXIES = ["127.0.0.1", "::1"]

LDAP_URI = ""
LDAP_BIND_DN_TEMPLATE = ""
LDAP_USER_SEARCH_BASE = ""
LDAP_USER_SEARCH_FILTER = ""
LDAP_TLS_VERIFY = True
