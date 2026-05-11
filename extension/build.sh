#!/usr/bin/env bash
# Package the extension as hfcs.xpi (== a zip with manifest.json at the root).
#
# Usage:   ./build.sh             # writes dist/hfcs-<version>.xpi
#          ./build.sh out.xpi     # writes the given path
#
# The resulting .xpi is what you submit to AMO for self-distribution signing,
# and is also what Firefox installs directly when signatures are disabled
# (Developer Edition / Nightly / ESR with policy). See README.md.

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"

version="$(grep -E '"version"' manifest.json | head -n1 | sed -E 's/.*"version"\s*:\s*"([^"]+)".*/\1/')"
out="${1:-dist/hfcs-${version}.xpi}"
mkdir -p "$(dirname "$out")"
# Absolute path so the zip command (run from a subshell) writes the right file.
case "$out" in
  /*) abs_out="$out" ;;
  *)  abs_out="$here/$out" ;;
esac
rm -f "$abs_out"

# Files to include. manifest.json MUST be at the root of the zip.
# Excludes: dist/, build.sh, README.md, and anything that starts with a dot.
zip -rq "$abs_out" \
    manifest.json \
    background.js \
    content \
    popup \
    lib \
    icons \
    -x "*.DS_Store" "*.swp" "*.swo"

echo "Wrote $out"
echo
echo "Next steps:"
echo "  1. Sign for self-distribution at https://addons.mozilla.org/developers/"
echo "     (choose 'On your own' when prompted). You'll get back a signed .xpi"
echo "     your colleagues can install on any Firefox build."
echo "  2. Or, for unsigned testing, host the .xpi on any HTTPS URL and open it"
echo "     in Firefox Developer Edition / Nightly / ESR with"
echo "     xpinstall.signatures.required = false in about:config."
