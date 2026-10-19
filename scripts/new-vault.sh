#!/usr/bin/env bash
# Create a new Zettelkasten vault with the plugin installed.
#
#   scripts/new-vault.sh <path> [--link]
#
#   --link  symlink the plugin build instead of copying it, so a rebuild shows up
#           after "Reload app without saving" in Obsidian. Use it for development.
#
# The vault points the plugin at this repo's sidecar (.venv/bin/zk-sidecar).
# Open it in Obsidian with "Open folder as vault", then turn on community plugins once.
set -euo pipefail

die() { echo "error: $*" >&2; exit 1; }

[[ $# -ge 1 ]] || die "usage: $0 <path> [--link]"
vault=$1
link=${2:-}
[[ -z $link || $link == --link ]] || die "unknown option: $link"

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
plugin=$repo/plugin
sidecar=$repo/.venv/bin/zk-sidecar
[[ -f $plugin/main.js ]] || die "plugin not built: make plugin"
[[ -x $sidecar ]] || die "sidecar not installed: make setup"
if [[ -e $vault ]] && [[ -n $(ls -A "$vault") ]]; then die "$vault exists and is not empty"; fi

mkdir -p "$vault"
vault=$(cd "$vault" && pwd)
mkdir -p "$vault/Sources" "$vault/Communities" "$vault/.zk/outputs" "$vault/.obsidian/plugins/zettelkasten"

dest=$vault/.obsidian/plugins/zettelkasten
for f in main.js manifest.json styles.css; do
	if [[ $link == --link ]]; then ln -s "$plugin/$f" "$dest/$f"; else cp "$plugin/$f" "$dest/$f"; fi
done
printf '{\n\t"sidecarCommand": "%s"\n}\n' "$sidecar" > "$dest/data.json"
echo '["zettelkasten"]' > "$vault/.obsidian/community-plugins.json"

cat > "$vault/Start here.md" <<'EOF'
# Start here

This vault runs the Zettelkasten plugin.

- **Sources/** holds one folder per source: the original file, its text and a note.
- **Communities/** holds the GraphRAG community notes. They're generated, but your notes on them are kept.
- **.zk/** holds the plugin's caches.

The status bar shows the sidecar. Click it to start the sidecar again if it stopped.
EOF

echo "Vault ready: $vault"
echo "Open it in Obsidian, then Settings → Community plugins → Turn on community plugins."
