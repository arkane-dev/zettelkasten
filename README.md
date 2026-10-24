# Zettelkasten

An Obsidian plugin plus a local Python service. Sources as notes, GraphRAG communities and runnable code.

Design: [Zettelkasten: Obsidian plugin or ground-up build?](https://andrewrkane.com/lab/zettelkasten-design). Spike results: `spike/REPORT.md`.

## Layout

```text
plugin/    Obsidian plugin (TypeScript, esbuild). Desktop only.
sidecar/   Python service (FastAPI, jupyter_client, graspologic). No UI, localhost only.
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

## Code

Add `run` after the language in a code block's fence line:

````markdown
```python run
x = 21
print(x * 2)
```
````

- **Buttons:** Run, Run all, Stop, Restart, Pin and Clear sit under each block. Blocks without `run` stay plain code.
- **Kernels:** one Jupyter kernel per note, so variables carry from block to block. Idle kernels stop after an hour.
- **Python:** each vault gets its own, in `.zk/python`, made with uv on the first run. Install packages from a block with `%pip install pandas`. To use another Python, set "Python for code" in the settings. It needs ipykernel.
- **Other languages:** any Jupyter kernel installed on the machine works. Its language becomes runnable, like ```` ```r run ````.
- **Output:** text, errors, images, HTML tables and Markdown. It's cached in `.zk/outputs/`, so the note stays clean.
- **Pin:** writes the output into the note, below the block, between `<!-- zk:output -->` markers. Images become attachments. Pinning again replaces it.
- **Commands:** "Run all code in this note", "Stop the running code", "Restart this file's kernel", "Clear code output in this note".

Notebooks (`.ipynb`) open in their own view: cells, outputs, a kernel picker, Run all, Stop and Restart. Click a code cell to edit it. Shift+Enter runs it and moves on. Ctrl+Enter runs it and stays. Outputs save into the file, which still opens in Jupyter. "New notebook" makes one.

## Communities

"Rebuild communities" reads every source and note, finds the ideas they share, and groups them. Each group gets a note in `Communities/L0/` (broad topics) or `Communities/L1/` (their sub-topics), with a report the model writes from the group's own text.

```text
Communities/
  L0/<Title>.md     summary, findings with links to their sources, key ideas, sources, sub-communities
  L1/<Title>.md
  _retired/         groups that went away, kept because you wrote in them
```

- **Method:** GraphRAG's fast method, reworked for a vault. Noun phrases (TextBlob) linked by co-occurrence, PMI weights, pruning, hierarchical Leiden with a fixed seed. Key ideas come from GLiNER, a small NER model, if it's installed. Reference lists are cut from PDFs first.
- **What it reads:** each source's text and your `## Notes` in it, your notes, and the Markdown cells of notebooks. Never the community notes, so the index doesn't feed on itself. One note is one chunk unless it's long.
- **Your notes stay:** a rebuild matches each group to last time's by shared phrases. A matched group keeps its id, its note and your `## Notes`. A group that's gone is deleted if you never wrote in it, or moved to `_retired/` if you did.
- **Only what changed:** extraction is cached per chunk. A report is rewritten only for a new group, or one whose members moved more than 20%. "Rebuild communities and rewrite every report" forces it.
- **When:** the command, or daily at the time in the settings (03:00 by default) if the vault changed. A missed night runs soon after Obsidian opens. The first build is always yours to start.
- **Graph:** "Open the community graph" shows the top-level groups. Click one to see its sub-groups and sources, and a sub-group to see its sources. Key ideas are listed beside it. The list has every item as a button, for the keyboard.
- **Names:** a note is named after its group's title. If a rewritten report changes the title, Obsidian renames the note, so your links follow. A note you renamed yourself keeps your name.

- **Cost:** on the spike's 50 documents (340 chunks), a first build takes about 14 minutes on an RTX 5090 with gemma3 27B, almost all of it the 92 reports. Editing one note and rebuilding takes about 35 seconds.

GLiNER needs PyTorch, several GB. `make setup` installs it. Without it, key ideas are the group's top phrases. The first build downloads NLTK's language data (about 15 MB) to `~/.cache/zk-sidecar`.

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

Phase 4, GraphRAG communities. Next is Phase 5: the NEONDECK theme and a public release.

## Licence

MIT
