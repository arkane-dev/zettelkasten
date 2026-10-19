#!/usr/bin/env bash
# Open dev-vault/ in its own Obsidian instance, with DevTools on port 9333.
# The profile lives in .obsidian-dev/, so your own vaults and settings are never touched.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
profile=$repo/.obsidian-dev
[[ -d $repo/dev-vault ]] || { echo "error: no dev-vault, run: make dev-vault" >&2; exit 1; }

mkdir -p "$profile"
printf '{"vaults":{"zkdev00000000001":{"path":"%s","ts":1,"open":true}}}\n' "$repo/dev-vault" > "$profile/obsidian.json"
exec obsidian --user-data-dir="$profile" --remote-debugging-port=9333 "$@"
