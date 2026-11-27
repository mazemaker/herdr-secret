#!/bin/sh
# herdr-secret installer for macOS and Linux: installs the herdr plugin, then runs its setup.
#   curl -fsSL https://raw.githubusercontent.com/mazemaker/herdr-secret/main/install.sh | sh
set -eu
REPO="mazemaker/herdr-secret"
ID="mazemaker.secret"

fail() { printf 'herdr-secret: %s\n' "$1" >&2; exit 1; }
command -v herdr >/dev/null 2>&1 || fail "herdr is required: https://herdr.dev"
command -v python3 >/dev/null 2>&1 || fail "Python 3.8 or newer is required (python3 on PATH)"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || fail "Python 3.8 or newer is required"
[ -r /dev/tty ] || fail "run this from an interactive terminal"

root() {
  herdr plugin list --json 2>/dev/null | python3 -c '
import json, sys
for p in json.load(sys.stdin).get("result", {}).get("plugins", []):
    if p.get("plugin_id") == sys.argv[1]:
        print(p.get("plugin_root", ""))' "$ID"
}

if [ -n "$(root)" ]; then
  echo "herdr-secret is already installed; updating it."
  herdr plugin update "$ID" </dev/tty || true
else
  herdr plugin install "$REPO" </dev/tty
fi
dir="$(root)"
[ -n "$dir" ] || fail "herdr did not report the plugin folder; run: herdr plugin action invoke $ID.setup"
exec python3 -I "$dir/bin/herdr_secret.py" setup </dev/tty
