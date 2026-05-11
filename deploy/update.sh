#!/bin/bash
# Update an existing PurchaseTracker deployment in place.
#
#   - Pulls the latest code from the configured git remote.
#   - Re-runs deploy/setup.sh (idempotent) so any new directories or default
#     files get created.
#   - Restarts the systemd service. In-place SQLite migrations run on app
#     startup, so no separate migration step is needed.
#
# Usage:
#   sudo bash deploy/update.sh /opt/POThang
#
# Optional overrides (env vars):
#   SERVICE=purchasetracker  systemd unit to restart
#   BRANCH=main              branch to fast-forward to (defaults to current)

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "usage: $0 <deployment-dir>" >&2
    exit 2
fi

DEPLOY_DIR="$(cd "$1" && pwd)"
SERVICE="${SERVICE:-purchasetracker}"

if [ ! -d "$DEPLOY_DIR/.git" ]; then
    echo "error: $DEPLOY_DIR is not a git checkout" >&2
    exit 1
fi

cd "$DEPLOY_DIR"

if [ -n "$(git status --porcelain)" ]; then
    echo "error: working tree in $DEPLOY_DIR is dirty." >&2
    echo "       commit or stash local changes before updating." >&2
    exit 1
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
TARGET_BRANCH="${BRANCH:-$CURRENT_BRANCH}"

echo "==> Fetching from origin"
git fetch --prune --tags origin

if [ "$TARGET_BRANCH" != "$CURRENT_BRANCH" ]; then
    echo "==> Switching from $CURRENT_BRANCH to $TARGET_BRANCH"
    git checkout "$TARGET_BRANCH"
fi

OLD_REV="$(git rev-parse HEAD)"
echo "==> Fast-forwarding $TARGET_BRANCH"
git pull --ff-only origin "$TARGET_BRANCH"
NEW_REV="$(git rev-parse HEAD)"

if [ "$OLD_REV" = "$NEW_REV" ]; then
    echo "==> Already up to date ($NEW_REV)."
else
    echo "==> Updated $OLD_REV -> $NEW_REV"
    git --no-pager log --oneline "$OLD_REV..$NEW_REV"
fi

echo "==> Re-running deploy/setup.sh"
bash "$DEPLOY_DIR/deploy/setup.sh"

if command -v systemctl >/dev/null 2>&1 \
        && systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE}\.service"; then
    echo "==> Restarting ${SERVICE}.service"
    systemctl restart "${SERVICE}.service"
    systemctl --no-pager --lines=10 status "${SERVICE}.service" || true
else
    echo "==> systemd unit ${SERVICE}.service not found; skipping restart."
    echo "    Restart the app process manually so migrations run."
fi

echo "==> Update complete."
