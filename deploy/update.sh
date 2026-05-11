#!/bin/bash
# Update an existing PurchaseTracker deployment from an unpacked source tree.
#
# Workflow:
#   1. Back up the deployment's data dirs (instance/, uploads/, po_templates/)
#      to a timestamped tarball.
#   2. Stop the systemd service.
#   3. rsync code from this source tree into the target deployment,
#      preserving instance/, uploads/, and po_templates/.
#   4. Re-run deploy/setup.sh (idempotent) so any new dirs / defaults appear
#      and ownership stays correct.
#   5. Start the service. SQLite migrations run automatically on startup.
#
# Usage (from the unpacked source tree):
#   sudo bash deploy/update.sh /opt/POThang
#
# Optional env vars:
#   SERVICE=purchasetracker     systemd unit name (default: purchasetracker)
#   BACKUP_DIR=/var/backups/pothang
#                               where to write the backup tarball
#                               (default: <target>/backups)
#   APP_USER=user               user that owns the deployment
#                               (passed through to deploy/setup.sh)
#   SKIP_BACKUP=1               skip the data backup step (not recommended)

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "usage: $0 <deployment-dir>" >&2
    exit 2
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$(cd "$1" && pwd)"
SERVICE="${SERVICE:-purchasetracker}"
BACKUP_DIR="${BACKUP_DIR:-$TARGET_DIR/backups}"
APP_USER="${APP_USER:-user}"

if [ "$SRC_DIR" = "$TARGET_DIR" ]; then
    echo "error: source and target are the same directory ($SRC_DIR)." >&2
    exit 1
fi

if [ ! -d "$SRC_DIR/purchasetracker" ]; then
    echo "error: $SRC_DIR does not look like a PurchaseTracker source tree." >&2
    exit 1
fi

if [ ! -d "$TARGET_DIR/purchasetracker" ]; then
    echo "error: $TARGET_DIR does not look like a PurchaseTracker deployment." >&2
    exit 1
fi

command -v rsync >/dev/null 2>&1 || {
    echo "error: rsync is required but not installed." >&2
    exit 1
}

TIMESTAMP="$(date -u +%Y%m%d-%H%M%SZ)"

# ---------- 1. Backup ----------
if [ "${SKIP_BACKUP:-0}" = "1" ]; then
    echo "==> SKIP_BACKUP=1 set; skipping backup step."
else
    install -d -m 750 "$BACKUP_DIR"
    BACKUP_FILE="$BACKUP_DIR/pothang-data-$TIMESTAMP.tar.gz"
    echo "==> Backing up data to $BACKUP_FILE"
    BACKUP_PATHS=()
    for d in instance uploads po_templates; do
        if [ -e "$TARGET_DIR/$d" ]; then
            BACKUP_PATHS+=("$d")
        fi
    done
    if [ ${#BACKUP_PATHS[@]} -eq 0 ]; then
        echo "    (nothing to back up - no data directories present)"
    else
        tar -czf "$BACKUP_FILE" -C "$TARGET_DIR" "${BACKUP_PATHS[@]}"
        echo "    Backup size: $(du -h "$BACKUP_FILE" | cut -f1)"
    fi
fi

# ---------- 2. Stop service ----------
HAVE_SYSTEMD=0
if command -v systemctl >/dev/null 2>&1 \
        && systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE}\.service"; then
    HAVE_SYSTEMD=1
    echo "==> Stopping ${SERVICE}.service"
    systemctl stop "${SERVICE}.service"
else
    echo "==> systemd unit ${SERVICE}.service not found; will not stop/start it."
fi

# ---------- 3. Sync code ----------
echo "==> Syncing source -> $TARGET_DIR"
# --delete removes stale files (e.g. deleted modules) from the target, but
# the excludes below keep all runtime data safe.
rsync -a --delete \
    --exclude="/instance/" \
    --exclude="/uploads/" \
    --exclude="/po_templates/" \
    --exclude="/backups/" \
    --exclude=".git/" \
    --exclude="__pycache__/" \
    --exclude="*.pyc" \
    --exclude="*.pyo" \
    --exclude="/.venv/" \
    --exclude="/venv/" \
    "$SRC_DIR"/ "$TARGET_DIR"/

# Drop any stale bytecode in the freshly-synced code tree so Python doesn't
# load a cached .pyc whose source was deleted upstream.
find "$TARGET_DIR/purchasetracker" -type d -name __pycache__ -prune -exec rm -rf {} + \
    2>/dev/null || true

# ---------- 4. Run setup ----------
echo "==> Running deploy/setup.sh in target"
APP_USER="$APP_USER" bash "$TARGET_DIR/deploy/setup.sh"

# ---------- 5. Start service ----------
if [ "$HAVE_SYSTEMD" = "1" ]; then
    echo "==> Starting ${SERVICE}.service"
    systemctl start "${SERVICE}.service"
    sleep 1
    systemctl --no-pager --lines=15 status "${SERVICE}.service" || true
else
    echo "==> Restart the app process manually so migrations run on startup."
fi

echo "==> Update complete."
if [ "${SKIP_BACKUP:-0}" != "1" ] && [ -n "${BACKUP_FILE:-}" ] && [ -e "$BACKUP_FILE" ]; then
    echo "    Backup: $BACKUP_FILE"
fi
