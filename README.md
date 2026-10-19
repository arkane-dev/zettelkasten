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

Model settings live in the plugin settings, per vault. Ollama is the default. Any OpenAI-compatible API works. Set the context length to match the model, since Ollama's OpenAI API can't change it per request. Long sources are split to fit.

## Sources

Add a PDF, Word, Excel, PowerPoint, HTML, Markdown or text file, or a web page. Use the ribbon, the "Add a source" command, drag and drop, or "Add as a source" on a file in the vault.

```text
Sources/<slug>/
  <Title>.md       the note: reader, summary, your notes
  original.pdf     or .docx, .xlsx, … or snapshot-2026-10-19.html for web pages
  content.md       extracted text, for search and GraphRAG
```

- Web pages are saved with SingleFile as one file, styles and images inlined. Scripts are blocked.
- "Capture this web page again" adds a dated snapshot. It never overwrites one, and adds nothing if the text hasn't changed.
- The summary sits between `<!-- zk:ai -->` markers, and "Summarise this source again" replaces only that. Software never touches `## Notes`.
- The same file or URL twice opens the source you already have.
- Text: PyMuPDF for PDFs, with OCR on pages that have no text. trafilatura for web pages. MarkItDown for Office files.

## Develop

Needs `uv` and Obsidian. Saving web pages needs Chromium: the system one, or Playwright's in `~/.cache/ms-playwright`.

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

Phase 2, sources. Next is Phase 3: notebooks and runnable code.

## Licence

MIT
