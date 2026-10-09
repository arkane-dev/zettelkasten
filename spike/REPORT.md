# Phase 0 spike report

Started 2026-10-09. Does the plan hold up? Three questions: GraphRAG on a local model, reader views in Obsidian, and code run from a note.

## Corpus

50 documents from `/media/Books` (list in `corpus.txt`), about 244k words, 376 chunks of 1,200 tokens.

- 19 AI and robotics papers.
- 8 props and cosplay books.
- 12 Chinese recipe PDFs and 11 saved Oakden recipe pages.

## Conversion

| Source | Tool | Result |
|---|---|---|
| Papers and books | MarkItDown (pdfminer) | Glued words together in 16 of 19 papers. Rejected for PDFs. |
| Papers and books | PyMuPDF `get_text()` | Clean. Under 0.1 s per paper. |
| Recipe PDFs | none had text | "Microsoft Print to PDF" draws the letters as shapes. |
| Recipe PDFs | RapidOCR on pages with no text | Clean. About 5 s per two-page recipe. |
| Saved pages | MarkItDown | Kept menus, shop and basket links. These would become entities. |
| Saved pages | trafilatura | Only the article. |

Ingest: PyMuPDF for PDFs, with OCR on pages with no text. trafilatura for web pages. MarkItDown for Word and Excel.

## Readers in Obsidian

Tested in a separate Obsidian 1.13.7 instance with a scratch vault. Screenshots are in `shots/`.

| File | Result |
|---|---|
| Word | mammoth in a `FileView`. Headings, bold and italic, lists, tables and images all show. The title style needs a style map. |
| Excel | SheetJS, with a tab per sheet. Shows the cached formula value. |
| Web page, "save complete" with a `_files` folder | Obsidian blocks the sandboxed frame from loading vault files. No styles, no images. |
| Web page, single file with inlined assets | Looks like the live page. No scripts run, and external requests are blocked. |
| PDF | Obsidian's own viewer. |

Change to the plan: web capture must produce one self-contained file (SingleFile).

## Code from a note

The plugin sends code to the sidecar (FastAPI and `jupyter_client`, with token auth). There's one kernel per note.

- `x = 1 + 1` then `print(x * 21)` gives 42 in the next block, so state carries between blocks.
- The first run takes 950 ms, including kernel start. Later runs take about 7 ms.
- A matplotlib plot comes back as a PNG.

Change to the plan: a code block processor takes every block in its language. The plugin must check for `run` in the fence line itself, and render other blocks as plain code.

## GraphRAG: gemma3 27B (Q4_K_M, 16k context)

graphrag 3.3.0 on Ollama 0.13.5, RTX 5090. Ollama was restarted partway through, moving from 1 to 4 parallel slots.

| Measure | Result |
|---|---|
| Wall time | 5 h 29 min |
| Entity extraction | 3 h 16 min, 752 calls (two per chunk: the first pass and one gleaning pass) |
| Description summaries | 2,204 calls |
| Community reports | 2 h 11 min, 984 calls |
| Embeddings | 566 calls. Failed at the end, see below. |
| Entities, relationships | 8,928, 7,276 |
| Communities by level (0 to 4) | 50, 335, 508, 87, 4 |

Parallel slots: 3.8 extraction calls a minute with 1 slot. 5.6 with 4.

### Quality

- **Top-level communities cross documents.** The median spans 4 documents, and 74% span more than one.
  - "Costume & Prop Creation" spans 10 making books.
  - "Culinary" holds all 21 recipes, PDFs and web pages together.
  - "NANDA" joins the two NANDA papers.
- **Lower levels are mostly single-document detail.**
- **Communities almost never mix the three domains.** That's 99% purity at every level.
- **Reports are specific and grounded.** Each finding cites the entities and relationships behind it. The median report is 430 words.
- **Noise: people from citation lists.** 3,085 of the 8,928 entities are people, mostly authors in reference sections ("Y. Liu", "X. Zhang").
- **Noise: quantities.** Values like "1 kg" and "105°C" come out as entities.

### Problems found

- **Embeddings fail.** Ollama caps nomic-embed-text at 2,048 tokens, whatever `num_ctx` asks for, and full community reports are longer. Embeddings only serve GraphRAG search, which v1 doesn't include. Fix in Phase 4: embed a shorter field, or use a long-context embedder.
- **984 communities is too many notes for 50 documents.** Write levels 0 and 1 as notes, which gives 385. Keep the deeper levels in the graph view only.

### To do before Phase 4

- Strip reference sections before indexing.
- Drop numeric and quantity entities, through the prompt or a filter after extraction.

## GraphRAG: qwen3 30B-A3B instruct 2507 (Q4_K_M, 16k context)

Same corpus, settings and prompts. Running.

First look: qwen3 writes about 2.5 times as much per extraction call as gemma3, with more entities and longer descriptions. At 4 calls a minute, it gets through fewer chunks per minute than gemma3. Some descriptions add facts that aren't in the source text.
