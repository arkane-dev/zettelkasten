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
| Entity extraction | 3 h 16 min, 752 calls (two per chunk: the first pass and one gleaning pass). This includes the summaries below: extraction alone took 2 h 21 min. |
| Description summaries | 55 min, 2,204 calls |
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

Same corpus, settings and prompts. A restart stopped the run at chunk 194 of 376. It resumed from the LLM cache, so no call ran twice. Times below add both parts.

| Measure | gemma3 | qwen3 |
|---|---|---|
| Wall time | 5 h 29 min | 10 h 51 min |
| Entity extraction | 2 h 21 min | 3 h 23 min |
| Description summaries | 55 min, 2,204 calls | 1 h 31 min, 4,428 calls |
| Community reports | 2 h 11 min, 984 calls | 5 h 57 min, 2,064 calls |
| Entities, relationships | 8,928, 7,276 | 13,702, 14,643 |
| Communities by level | 50, 335, 508, 87, 4 | 49, 529, 1,271, 210, 6, 2 |
| Communities at levels 0 and 1 | 385 | 578 |
| Purity, level 0 / level 1 | 98.8% / 99.6% | 96.0% / 98.6% |
| Median entity description | 9 words | 25 words |
| Median community report | 430 words | 1,064 words |
| People | 3,085 | 2,839 |
| Quantity-like entities ("1 kg") | 448 | 320 |

Embeddings failed the same way as the gemma3 run.

### Findings

- **qwen3 writes more of everything.** It finds 1.5 times the entities and twice the relationships. Descriptions are nearly 3 times as long, and reports 2.5 times. Every later step then has more to do, so the run took twice as long.
- **The graph is denser, not cleaner.** Citation-list authors are still there (2,839 people). There are fewer quantities, but still 320.
- **Communities mix domains a little more.** Level-0 purity drops from 98.8% to 96.0%. That's still high.
- **Entity types fit notes better.** CONCEPT (4,732) and METHOD (1,751) lead. In gemma3, PERSON leads.
- **Some descriptions add facts that aren't in the source text.** Seen in the first extraction calls. Not measured yet.

### Verdict

gemma3 stays the default. qwen3 costs twice the time for a bigger graph with the same noise. Its longer descriptions also invent facts, which is the wrong trade for notes that must stay true to their sources. Revisit qwen3 if the noise filters from "To do before Phase 4" leave gemma3's graph too thin.

## GraphRAG: fast method (noun-phrase graph)

Same corpus and settings as the gemma3 run, with `--method fast`. Regex noun phrases replace LLM extraction. Edges are co-occurrence within a chunk, with no type or description. gemma3 still writes the community reports, from the raw text chunks. This isolates the extraction method. A disconnect stopped the run at report 337 of 976. It resumed from the cache. Times below add both parts.

| Measure | gemma3 (LLM graph) | fast (noun phrases) |
|---|---|---|
| Wall time | 5 h 29 min | 7 h 52 min |
| Graph build | 3 h 16 min | 6 min (77 s extraction, 4 min 22 s pruning) |
| Community reports | 2 h 11 min, 984 calls | 7 h 33 min, 976 calls |
| Reports for levels 0 and 1 only | 57 min, 385 reports | 47 min, 104 reports |
| Entities, relationships | 8,928, 7,276 | 4,174, 119,969 (after pruning) |
| Communities by level | 50, 335, 508, 87, 4 | 19, 85, 243, 377, 227, 23, 2 |
| Purity, level 0 / level 1 | 98.8% / 99.6% | 98.9% / 99.7% |
| Level-1 communities spanning >1 document | 31% | 59% |
| Median community report | 430 words | 498 words |

Embeddings failed the same way as the other runs.

### Findings

- **Co-occurrence keeps the domains apart.** Purity matches the LLM graph at every level. Typed, described edges aren't needed for clean communities.
- **Communities connect more documents.** At level 1, 59% span more than one document, against 31% for gemma3. The tree is deeper, with fewer, broader communities at the top.
- **The graph is nearly free. The reports are not.** Each report reads raw text, up to 8,000 tokens, so it's about 3.5 times slower than a report built from entity descriptions. Writing every level, the fast run took longer overall.
- **Reports are grounded.** They cite their source chunks and dates. Spot checks on the mimic-one and ReAct communities matched the source text.
- **Entities are noisy and untyped.** The most frequent are FIGURE, APPENDIX, PROCEEDINGS, CONFERENCE PAPER, LOST and GOOD IDEA, plus authors (LIU, ZHANG). One top-level community is built from benchmark questions in the ReAct appendix (Nicholas Ray, Elia Kazan). They aren't usable as notes without a stop-list and stripped references and appendices.

### Verdict

For community notes, the fast graph wins if we only write levels 0 and 1, which was already the plan. Graph plus top-level reports would take about 53 minutes, against about 4 h 13 min for the gemma3 graph. Entity notes still need an LLM or a typed NER model (GLiNER), because noun phrases have no type or description. Next test: fast graph for communities, GLiNER for entities, with references and appendices stripped first.

## GraphRAG: hybrid (stripped corpus, fast graph, top-level reports, GLiNER entities)

The fast run's verdict, tested end to end.

- `strip.py` cuts reference lists and appendices from the 19 papers. Every cut lands on a real References, Bibliography or Appendix heading. 376 chunks become 292.
- Fast graph as before. `index_top.py` adds one workflow that writes reports for levels 0 and 1 only. GraphRAG has no setting for that. No embeddings.
- `gliner_entities.py` tags each chunk with GLiNER (`urchade/gliner_medium-v2.1`). Types: concept, method, dataset, tool, material, ingredient. No people or quantities.

| Measure | gemma3 (LLM graph) | fast | hybrid |
|---|---|---|---|
| Wall time | 5 h 29 min | 7 h 52 min | **35 min** |
| Graph build | 3 h 16 min | 6 min | 5 min |
| Community reports | 2 h 11 min, 984 | 7 h 33 min, 976 | 30 min, 85 |
| Typed entities | 8,928 (LLM) | none | 1,734 (GLiNER, 8 s) |
| Purity, level 0 / level 1 | 98.8% / 99.6% | 98.9% / 99.7% | 98.8% / 99.6% |
| Level-1 communities spanning >1 document | 31% | 59% | 60% |
| People / quantity-like entities | 3,085 / 448 | untyped | 0 / 4 |

### Findings

- **Same communities, a tenth of the time.** Purity and reach match the fast run. Writing only the 85 top-level reports cuts the run to 35 minutes.
- **Stripping removes most of the noise.** FIGURE, PROCEEDINGS, APPENDIX, LOST and author surnames drop out of the top noun phrases. Left over: page headers (ICLR, CONFERENCE PAPER, PREPRINT) and stray words (GOOD IDEA, DON). A stop-list handles those.
- **GLiNER entities make good link targets.** Joined to communities by shared chunks, the top entities read like a note's links. HRM → sudoku-extreme, hierarchical memory, q-learning. Cosplay armor → worbla, EVA foam, hot air gun, Dremel. Game theory → prisoner's dilemma, shadow of the future, theory of mind.
- **GLiNER types are rough.** Model names come out as datasets (gpt 4.1, loras). Maker supplies come out as ingredients (resin, body filler, gesso). Some generic phrases get through ("our method", "supplementary material"). 1,343 of 1,734 entities appear in only one chunk.
- **No descriptions.** GLiNER finds and types entities. It doesn't write about them. An entity note would need a short LLM pass, and only for entities worth a note: the 391 that appear in two or more chunks.

### Verdict

Use the hybrid for Phase 4. Strip, fast graph, top-level reports, GLiNER entities. That's about 35 minutes for this corpus, against 5.5 hours, with the same community quality and cleaner entities. Still to do:

- Stop-list for page headers and stray noun phrases.
- Better GLiNER labels. Try "AI model" and "software", and split "material" from "ingredient" by domain.
- A short LLM description pass for entities in two or more chunks.

## Design note: keeping the graph in sync

### What GraphRAG does (v3.3.0, `--method standard-update`)

1. Finds new documents by **title** against the saved `documents` table.
2. Runs the full pipeline on the new documents alone, as a separate small graph, Leiden included.
3. Merges entities by title. Pooled descriptions get a fresh LLM summary.
4. **Appends** the new communities after the old ones. Old communities and reports are never touched.

Gaps:

- **Communities never re-form.** New papers on a topic form their own community. They never join the existing one, and its report never mentions them. Drift grows with every update.
- **Edits are invisible.** Only titles are compared, so a rewritten note is skipped.
- **Deletions are ignored.** The deleted list is computed but never used.

GraphRAG gives no rule for when to rebuild.

### What we'll do instead

Costs on this corpus: Leiden takes 7 s. Community reports take hours. Extraction takes hours, but the cache means each chunk is only paid for once. So re-cluster everything, every time, and only pay the LLM for what changed.

1. Extract from new or changed notes only. Detect changes by content hash, not title.
2. Remove the entities and relationships of deleted notes.
3. Re-run Leiden on the whole graph, with a fixed seed.
4. Match each new community to its old one by shared entities (Jaccard).
5. Rewrite a report only when membership changed by more than a threshold, roughly 20%.

There's no "rebuild after N documents". A full rebuild is only for a new extraction prompt or model.

**Leiden stability: answered in Phase 4 (2026-10-24).** Five trials on this corpus: cluster 45 documents, add 5 at random, cluster all 50, and match by Jaccard (0.25 or more, best pairs first).

| Level | Communities | Keep at least 80% of their phrases | Matched | Median Jaccard of matches |
|---|---|---|---|---|
| 0 | 16 to 17, then 18 | 82 to 100% | all | 0.92 to 0.97 |
| 1 | 62 to 70, then 72 | 66 to 74% | 85 to 90% | 0.73 to 0.78 |

Top-level communities hold. Level 1 moves more, so about a third of its reports get rewritten when the vault grows by 10%. Matching keeps the ids and notes either way.

### Chunking

The spike uses GraphRAG's default: 1,200 tokens with 100 overlap. 50 documents gave 376 chunks. The median is 5 per document, the range 1 to 36.

For the zettelkasten, chunk by note first. Split a note into 1,200-token chunks only when it's longer than that. Atomic notes stay whole, so no idea is cut in half. One note maps to one chunk, which makes step 1 above exact: a changed note means one chunk to re-extract. Long sources (papers, PDFs) still split.
