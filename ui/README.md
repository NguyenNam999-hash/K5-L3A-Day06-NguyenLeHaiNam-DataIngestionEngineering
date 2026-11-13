# Pipeline UI — “The Ingestion Line”

A local, step-by-step console for the Day 06 pipeline. It wraps the real modules in
`starter/src` — nothing is simulated and no embeddings are mocked.

## Run

```bash
cd starter
.venv/bin/python ../ui/server.py
# open http://127.0.0.1:8000
```

Use `--port` / `--host` if 8000 is taken. PostgreSQL must be up:

```bash
docker compose -p day6_student_starter up -d --wait
```

The server warms the local BGE model in the background at launch, so the model load does not
land inside step 3. Embedding both chunk sets on CPU still takes roughly a minute. The
first model download additionally needs Internet.

## The five steps

| Step | What runs | What you see |
|---|---|---|
| 1 · Ingest | `load_pages_jsonl`, `normalize_text` cleaning audit, manifest + benchmark validation | unit/char/section counts, preview table, **per-rule cleaning counts**, **pass/fail gate table** |
| 2 · Chunk | `fixed_size` (A), `by_paragraph` (B), C = B + filter | chunk counts, length histograms, sample chunks, **Hit@ comparison for A/B/C** |
| 3 · Embed | local FastEmbed `BAAI/bge-base-en-v1.5` (768d) | model, token counts, timings, a real vector slice |
| 4 · Store | pgvector schema + idempotent upsert | before/after row ledger, duplicate check |
| 5 · Save & Test | 20 frozen questions × 3 strategies, strict evidence hit | dev/test metrics, saved artifacts, **sample-question retrieval test** |

Each step only unlocks after the previous one completes. A step can be re-run while it is
the frontier; **Start over** resets in-memory progress.

### Cleaning and error checks in step 1

Step 1 shows two audit tables before anything is chunked.

**Cleaning** (`normalize_text` in `starter/src/ingestion.py`, applied again at load time). The
table lists every rule in run order with the number of occurrences it met in the raw book body
(`data/alice_raw.txt`, Project Gutenberg boilerplate excluded) — not in the already-normalized
pages, so a `0` means this corpus never needed that rule (it still applies to the PDF path):

1. Unicode **NFC**; CRLF / lone CR → `\n`
2. delete soft hyphen `U+00AD`, BOM `U+FEFF`
3. `U+00A0` → space
4. drop `[Illustration]` lines and `* * *` separator lines
5. collapse space/tab runs, trim padding around newlines, cap blank-line runs at `\n\n`
6. `strip`

Paragraph boundaries are preserved on purpose: strategy **B** chunks by paragraph, so flattening
newlines would silently change the B/C comparison. The panel also confirms the function is
**idempotent** and reports what cleaning deliberately leaves behind (`_italic_` markers, 220 in
this snapshot).

**Error checks** mirror the gates in `src.run` and `benchmark/verify_benchmark.py`, each row
showing the observed value next to its threshold: ≥30 units, unique page IDs, no empty unit,
every unit ≥1,500 chars, `manifest.page_count` and page IDs matching `pages.jsonl`, ≥20
questions, unique question IDs, ≥15 dev / ≥5 test, ≥3 filter questions with ≥1 in test, every
`evidence_quote` verbatim on its gold page, `gold_source_id` matching the page, every filter
value present on its gold page, and no evidence quote copied into its own question.

These are hard gates, not warnings. A bad record in `pages.jsonl` raises `ValueError` with the
file and line, an empty unit or non-string text raises at load time, and the same conditions
stop `python -m src.run` before ingest. Nothing is repaired silently.

### Hit@ scores in step 2

Step 2 compares the three retrieval configurations with the measured `Hit@1` and
`Evidence Hit@3` from the latest benchmark run (`results/metrics.json`). After step 5
finishes, the same panel is refreshed with this session's scores. A hit is strict: gold
document **and** gold page **and** the full verbatim evidence quote inside one chunk.

A–B isolates chunking; B–C isolates the metadata filter. Both numbers are shown as
fractions and percentages, with the dev/test/overall split switchable.

### Retrieval test in step 5

Pick one of the curated sample questions (or any of the 20 frozen questions) to embed and
search against A, B and C. Every hit shows rank, cosine score, page/section, a hit/miss
flag, and the chunk text with the evidence quote highlighted. Gold page, answer and filter
are displayed so the judgment is auditable.

## Artifacts and honesty

Step 5 writes `traces.json`, `metrics.json`, `metrics.csv` and `run_metadata.json` to
`artifacts/ui_runs/<run_id>/`. The official snapshot in `results/` is only read, never
overwritten, so the report's numbers stay reproducible.

The 5-question test split is small and illustrative. The UI does not relax the strict hit
definition and does not hide misses.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/bootstrap` | corpus/benchmark metadata, last official metrics, session state |
| `POST /api/steps/<1-5>/run` | run a step, streaming SSE progress + event log |
| `POST /api/retrieve` | `{"id":"q04"}` → top-3 hits for A/B/C |
| `POST /api/reset` | clear in-memory pipeline state |

The server binds `127.0.0.1` by default and is intended for local use only.
