#!/bin/bash
# Run once after cloning to prepare the directory structure for production.
# Re-running is safe (all operations are idempotent).
#
# Usage (from /srv/purchasetracker):
#   sudo bash deploy/setup.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
APP_USER="${APP_USER:-user}"

cd "$APP_DIR"

echo "==> Creating required directories in $APP_DIR"
install -d -m 755 instance
install -d -m 755 uploads
install -d -m 755 po_templates

echo "==> Copying default config (if not present)"
if [ ! -f instance/config.py ]; then
    cp config.example.py instance/config.py
    echo "    Created instance/config.py — edit it before starting the service."
else
    echo "    instance/config.py already exists, skipping."
fi

# If running as root, fix ownership so the service user can write.
if [ "$(id -u)" -eq 0 ]; then
    echo "==> Setting ownership to $APP_USER"
    chown -R "$APP_USER:$APP_USER" instance uploads po_templates
fi

echo "==> Done. Next steps:"
echo "    1. Edit instance/config.py (set SECRET_KEY, AUTH_MODE, etc.)"
echo "    2. sudo cp deploy/purchasetracker.service /etc/systemd/system/"
echo "    3. sudo systemctl daemon-reload && sudo systemctl enable --now purchasetracker"
