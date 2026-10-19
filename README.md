# Zettelkasten

An Obsidian plugin plus a local Python service. Sources as notes, GraphRAG communities and runnable code.

Design: [Zettelkasten: Obsidian plugin or ground-up build?](https://andrewrkane.com/lab/zettelkasten-design). Spike results: `spike/REPORT.md`.

## Layout

```text
plugin/    Obsidian plugin (TypeScript, esbuild). Desktop only.
sidecar/   Python service (FastAPI). No UI, localhost only.
scripts/   new-vault.sh, dev-obsidian.sh
spike/     Phase 0 experiments. Not part of the build.
```

The plugin starts the sidecar when the vault opens. The sidecar takes a free port on 127.0.0.1 and prints a ready line. Every request needs a token that's new each session. The sidecar exits when Obsidian closes, even if Obsidian crashes.

Model settings live in the plugin settings, per vault. Ollama is the default. Any OpenAI-compatible API works.

## Develop

Needs `uv` and Obsidian.

```sh
make setup      # .venv with node, the sidecar, plugin packages
make test       # vitest and pytest
make dev-vault  # dev-vault/ with the plugin symlinked
make obsidian   # open it in a separate Obsidian profile
make dev        # rebuild the plugin on every change
```

`make obsidian` uses its own profile in `.obsidian-dev/`, so it never touches your own vaults. Turn on community plugins once in that vault. After a rebuild, run "Reload app without saving" from the command palette.

## A real vault

```sh
scripts/new-vault.sh ~/Zettelkasten
```

This copies the plugin in and points it at this repo's sidecar. Open the folder in Obsidian as a vault.

## Status

Phase 1, the scaffold. Next is Phase 2: sources.

## Licence

MIT
