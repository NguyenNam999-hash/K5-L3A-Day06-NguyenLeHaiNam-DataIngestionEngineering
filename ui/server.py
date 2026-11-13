"""Local step-by-step UI for the Day 06 ingestion pipeline.

Run from the repository root with the starter virtualenv:

    starter/.venv/bin/python ui/server.py

The server orchestrates the real pipeline modules in ``starter/src``. It is a
teaching/inspection surface, not a second implementation: chunking, embeddings,
pgvector search and evaluation all come from the lab code. Retrieval evaluation
artifacts are written to ``artifacts/ui_runs/<run_id>/`` so the official
``results/`` snapshot is never overwritten.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
import time
import traceback
import unicodedata
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
STARTER = ROOT / "starter"
STATIC = Path(__file__).resolve().parent / "static"
sys.path.insert(0, str(STARTER))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(STARTER / ".env")

from src.chunking import by_paragraph, fixed_size  # noqa: E402
from src.embeddings import MyEmbedder  # noqa: E402
from src.evaluation import evaluate_query, summarize  # noqa: E402
from src.ingestion import load_pages_jsonl, normalize_text  # noqa: E402
from src.run import STRATEGIES, validate_benchmark, validate_corpus  # noqa: E402
from src.store import PgVectorStore  # noqa: E402

DATA_PATH = ROOT / "data" / "pages.jsonl"
RAW_PATH = ROOT / "data" / "alice_raw.txt"
MANIFEST_PATH = ROOT / "data" / "manifest.json"
BENCHMARK_PATH = ROOT / "benchmark" / "benchmark.json"
OFFICIAL_RESULTS = ROOT / "results"
UI_RUNS = ROOT / "artifacts" / "ui_runs"
SAMPLE_IDS = ["q01", "q04", "q05", "q07", "q10", "q16", "q18", "q20"]
DEFAULT_PORT = 8000

STEP_ORDER = ("1", "2", "3", "4", "5")
STRATEGY_NOTES = {
    "A": "Fixed-size windows, 1,600 chars with 200 overlap. No metadata filter.",
    "B": "Paragraph grouping capped at 1,000 chars. No metadata filter.",
    "C": "Same chunks as B; adds a `section` metadata filter when the question carries one.",
}

# Counting patterns for the cleaning audit. They describe the same shapes that
# ``src.ingestion.normalize_text`` rewrites, but count only runs that need fixing
# (the runtime regexes also match harmless single spaces / plain newlines).
_AUDIT_ILLUSTRATION_RE = re.compile(r"^\s*\[Illustration\]\s*$", re.MULTILINE)
_AUDIT_SEPARATOR_RE = re.compile(r"^\s*(?:\*\s*){3,}$", re.MULTILINE)
_AUDIT_SPACE_RUN_RE = re.compile(r"[ \t]{2,}")
_AUDIT_SPACE_NEWLINE_RE = re.compile(r"[ \t]+\n|\n[ \t]+")
_AUDIT_BLANK_RUN_RE = re.compile(r"\n{3,}")
_AUDIT_ITALIC_RE = re.compile(r"_([^_\n]{1,80})_")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def _audit_norm(text: str) -> str:
    """Same quote matcher as benchmark/verify_benchmark.py (curly apostrophe folded)."""
    return re.sub(r"\s+", " ", (text or "").replace("\u2019", "'")).strip().casefold()


def _raw_body() -> str | None:
    """Return the book body of the raw source, mirroring starter/scripts/build_corpus.py."""
    if not RAW_PATH.exists():
        return None
    raw = RAW_PATH.read_text(encoding="utf-8")
    start = raw.find("CHAPTER I.")
    end = raw.find("*** END OF THE PROJECT GUTENBERG")
    return raw[start:end] if 0 <= start < end else raw


def _cleaning_audit(pages) -> dict[str, Any]:
    """Measure what normalize_text() removes, and what it leaves behind.

    Counts come from the raw snapshot, not from memory: the panel shows exactly
    how many occurrences each rule met in this corpus.
    """
    body = _raw_body()
    sample = pages[0].text if pages else ""
    audit: dict[str, Any] = {
        "raw_source": _relative(RAW_PATH),
        "raw_available": body is not None,
        "idempotent": normalize_text(normalize_text(sample)) == normalize_text(sample),
        "rules": [],
        "residual": [],
    }
    if body is None:
        return audit

    crlf = body.count("\r\n")
    lone_cr = body.count("\r") - crlf
    nfc = unicodedata.normalize("NFC", body)
    nfc_changed = sum(1 for before, after in zip(body, nfc) if before != after) if nfc != body else 0

    audit["body_chars"] = len(body)
    audit["rules"] = [
        {"rule": "Unicode NFC", "found": nfc_changed, "action": "compose decomposed sequences"},
        {"rule": "CRLF / lone CR", "found": crlf + lone_cr, "action": "collapse to one LF"},
        {"rule": "Soft hyphen U+00AD", "found": body.count("\u00ad"), "action": "delete"},
        {"rule": "Byte-order mark U+FEFF", "found": body.count("\ufeff"), "action": "delete"},
        {"rule": "No-break space U+00A0", "found": body.count("\u00a0"), "action": "replace with space"},
        {"rule": "[Illustration] lines", "found": len(_AUDIT_ILLUSTRATION_RE.findall(body)), "action": "drop line"},
        {"rule": "* * * separator lines", "found": len(_AUDIT_SEPARATOR_RE.findall(body)), "action": "drop line"},
        {"rule": "Runs of spaces / tabs", "found": len(_AUDIT_SPACE_RUN_RE.findall(body)), "action": "collapse to one space"},
        {"rule": "Padded line edges", "found": len(_AUDIT_SPACE_NEWLINE_RE.findall(body)), "action": "trim"},
        {"rule": "Blank-line runs ≥ 3", "found": len(_AUDIT_BLANK_RUN_RE.findall(body)), "action": "cap at two newlines"},
    ]

    corpus = "\n".join(page.text for page in pages)
    audit["residual"] = [
        {"rule": "_italic_ markers kept", "found": len(_AUDIT_ITALIC_RE.findall(corpus))},
        {"rule": "double spaces left", "found": len(re.findall(r" {2,}", corpus))},
        {"rule": "blank-line runs left", "found": len(_AUDIT_BLANK_RUN_RE.findall(corpus))},
        {"rule": "[Illustration] left", "found": corpus.count("[Illustration]")},
    ]
    return audit


def _ingest_checks(pages, manifest, questions, pages_by_id) -> list[dict[str, Any]]:
    """Report the gates that src.run validates, with the observed values.

    The gates themselves still come from validate_corpus / validate_benchmark;
    this only renders the same thresholds as pass/fail rows for inspection.
    """
    rows: list[dict[str, Any]] = []

    def add(label: str, observed: str, expected: str, passed: bool, scope: str) -> None:
        rows.append(
            {"label": label, "observed": observed, "expected": expected, "passed": bool(passed), "scope": scope}
        )

    ids = [page.page for page in pages]
    empties = sum(1 for page in pages if not page.text.strip())
    min_chars = min((len(page.text) for page in pages), default=0)
    add("At least 30 content units", f"{len(pages)} units", "≥ 30", len(pages) >= 30, "corpus")
    add("Page IDs unique", f"{len(set(ids))} / {len(ids)}", "no duplicates", len(set(ids)) == len(ids), "corpus")
    add("No empty unit", f"{empties} empty", "0 empty", empties == 0, "corpus")
    add("Unit length floor", f"min {min_chars:,} chars", "≥ 1,500", min_chars >= 1500, "corpus")

    manifest_count = int((manifest or {}).get("page_count", 0) or 0)
    add("manifest.page_count", f"{manifest_count} vs {len(pages)}", "equal", manifest_count == len(pages), "manifest")
    manifest_ids = {
        int(item["page"]) for item in (manifest or {}).get("pages", []) if isinstance(item, dict) and "page" in item
    }
    add(
        "manifest page IDs",
        f"{len(manifest_ids)} IDs",
        f"same {len(ids)} IDs",
        manifest_ids == set(ids),
        "manifest",
    )

    qids = [question.get("id") for question in questions]
    dev = sum(1 for question in questions if question.get("split") == "dev")
    test = sum(1 for question in questions if question.get("split") == "test")
    filter_questions = [question for question in questions if question.get("filter")]
    test_filters = sum(1 for question in filter_questions if question.get("split") == "test")
    add("Question count", f"{len(questions)}", "≥ 20", len(questions) >= 20, "benchmark")
    add(
        "Question IDs unique",
        f"{len(set(qids))} / {len(qids)}",
        "no duplicates",
        len(set(qids)) == len(qids),
        "benchmark",
    )
    add("Split sizes", f"{dev} dev · {test} test", "≥ 15 · ≥ 5", dev >= 15 and test >= 5, "benchmark")
    add(
        "Filter questions",
        f"{len(filter_questions)} ({test_filters} in test)",
        "≥ 3, ≥ 1 test",
        len(filter_questions) >= 3 and test_filters >= 1,
        "benchmark",
    )

    evidence_ok = 0
    source_ok = 0
    for question in questions:
        page = pages_by_id.get(int(question.get("gold_page", -1) or -1))
        if page is None:
            continue
        if page.document_id == question.get("gold_source_id"):
            source_ok += 1
        if _audit_norm(question.get("evidence_quote", "")) in _audit_norm(page.text):
            evidence_ok += 1

    filter_pairs = [
        (question, key, value)
        for question in filter_questions
        for key, value in (question.get("filter") or {}).items()
    ]
    filter_ok = 0
    for question, key, value in filter_pairs:
        page = pages_by_id.get(int(question.get("gold_page", -1) or -1))
        if page is not None and str(page.metadata.get(key, "")) == str(value):
            filter_ok += 1

    leaks = [
        question.get("id")
        for question in questions
        if _audit_norm(question.get("evidence_quote", ""))
        and _audit_norm(question.get("evidence_quote", "")) in _audit_norm(question.get("question", ""))
    ]

    add(
        "Evidence quote verbatim in gold page",
        f"{evidence_ok} / {len(questions)}",
        "all",
        evidence_ok == len(questions),
        "evidence",
    )
    add(
        "gold_source_id matches the page",
        f"{source_ok} / {len(questions)}",
        "all",
        source_ok == len(questions),
        "evidence",
    )
    add(
        "Filter value present on gold page",
        f"{filter_ok} / {len(filter_pairs)}",
        "all",
        bool(filter_pairs) and filter_ok == len(filter_pairs),
        "evidence",
    )
    add(
        "No evidence quote inside its question",
        f"{len(leaks)} leaks",
        "0 leaks",
        not leaks,
        "evidence",
    )
    return rows


def _quote_translate(text: str) -> str:
    table = str.maketrans(
        {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-"}
    )
    return text.translate(table)


def _highlight_span(text: str, quote: str) -> list[int] | None:
    """Return the raw [start, end) span of the evidence quote inside a chunk.

    Evidence quotes are verified against whitespace-normalized text, so the
    matcher collapses whitespace and maps the match back onto raw offsets.
    """
    if not text or not quote:
        return None

    def normalise(value: str) -> tuple[str, list[int]]:
        chars: list[str] = []
        mapping: list[int] = []
        previous_space = True
        for index, char in enumerate(value):
            char = _quote_translate(char)
            if char.isspace():
                if previous_space:
                    continue
                chars.append(" ")
                mapping.append(index)
                previous_space = True
                continue
            for lowered in char.lower():
                chars.append(lowered)
                mapping.append(index)
            previous_space = False
        return "".join(chars), mapping

    norm_text, mapping = normalise(text)
    norm_quote, _ = normalise(quote)
    if not norm_quote:
        return None
    start = norm_text.find(norm_quote)
    if start < 0:
        return None
    last = start + len(norm_quote) - 1
    return [mapping[start], mapping[last] + 1]


def _strict_hit_flag(question: dict[str, Any], hit: dict[str, Any]) -> bool:
    """Mirror the lab's strict hit definition for UI display."""
    if hit.get("document_id") != question.get("gold_source_id"):
        return False
    try:
        if int(hit.get("page")) != int(question.get("gold_page")):
            return False
    except (TypeError, ValueError):
        return False
    norm = lambda value: " ".join(_quote_translate(value or "").split()).casefold()  # noqa: E731
    quote = norm(question.get("evidence_quote", ""))
    return bool(quote) and quote in norm(hit.get("text", ""))


def last_official_metrics() -> dict[str, Any]:
    metrics = _read_json(OFFICIAL_RESULTS / "metrics.json")
    meta = _read_json(OFFICIAL_RESULTS / "run_metadata.json") or {}
    return {
        "metrics": metrics,
        "generated_at": meta.get("generated_at"),
        "embedding": meta.get("embedding"),
        "strategies": meta.get("strategies"),
        "source": "results/metrics.json",
    }


class State:
    """In-process pipeline state. One operator at a time."""

    def __init__(self) -> None:
        self.guard = threading.Lock()
        self.data_lock = threading.Lock()
        self.pages = None
        self.pages_by_id: dict[int, Any] = {}
        self.manifest = None
        self.questions: list[dict[str, Any]] = []
        self.chunks: dict[str, list] = {}
        self.vectors: dict[str, list[list[float]]] = {}
        self.embedder = None
        self.store = None
        self.benchmark_summary: dict[str, Any] | None = None
        self.benchmark_traces: list[dict[str, Any]] = []
        self.run_metadata: dict[str, Any] | None = None
        self.reset_steps()

    def reset_steps(self) -> None:
        self.steps = {
            key: {
                "status": "pending",
                "result": None,
                "started_at": None,
                "finished_at": None,
                "seconds": None,
                "error": None,
            }
            for key in STEP_ORDER
        }

    def reset(self) -> None:
        with self.data_lock:
            self.pages = None
            self.pages_by_id = {}
            self.manifest = None
            self.questions = []
            self.chunks = {}
            self.vectors = {}
            self.embedder = None
            self.store = None
            self.benchmark_summary = None
            self.benchmark_traces = []
            self.run_metadata = None
            self.reset_steps()

    def snapshot(self) -> dict[str, Any]:
        with self.data_lock:
            return {
                "steps": json.loads(json.dumps(self.steps)),
                "benchmark": self.benchmark_summary,
                "run_metadata": self.run_metadata,
            }

    def step_done(self, number: int) -> bool:
        return self.steps[str(number)]["status"] == "done"

    def require(self, number: int) -> None:
        if number > 1 and not self.step_done(number - 1):
            raise RuntimeError(f"Run step {number - 1} first; steps execute in order.")


STATE = State()

_EMBEDDER = None
_EMBEDDER_LOCK = threading.Lock()


def get_embedder():
    """Build the local model once and reuse it across steps and requests."""
    global _EMBEDDER
    with _EMBEDDER_LOCK:
        if _EMBEDDER is None:
            _EMBEDDER = MyEmbedder()
        return _EMBEDDER


def warm_embedder() -> None:
    try:
        embedder = get_embedder()
        print(f"[ui] embedding model warm: {embedder.model_name} ({embedder.dimensions} dims)")
    except Exception as exc:  # pragma: no cover - startup diagnostics only
        print(f"[ui] embedding warm-up failed: {exc}")


def _corpus_preview() -> dict[str, Any]:
    pages = load_pages_jsonl(DATA_PATH)
    manifest = _read_json(MANIFEST_PATH) or {}
    questions = _read_json(BENCHMARK_PATH) or []
    sections = sorted({str((page.metadata or {}).get("section", "")) for page in pages} - {""})
    return {
        "page_count": len(pages),
        "content_chars": sum(len(page.text) for page in pages),
        "document_ids": sorted({page.document_id for page in pages}),
        "source": pages[0].source if pages else "",
        "sections": sections,
        "manifest": {
            "title": manifest.get("title"),
            "license": manifest.get("license_or_permission"),
            "accessed_at": manifest.get("accessed_at"),
            "page_count": manifest.get("page_count"),
            "source_path": manifest.get("source_path_or_url"),
        },
        "benchmark": {
            "questions": len(questions),
            "dev": sum(1 for q in questions if q.get("split") == "dev"),
            "test": sum(1 for q in questions if q.get("split") == "test"),
            "filter_questions": sum(1 for q in questions if q.get("filter")),
            "types": sorted({str(q.get("type")) for q in questions}),
        },
    }


def _public_question(question: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": question["id"],
        "split": question["split"],
        "type": question.get("type"),
        "question": question["question"],
        "filter": question.get("filter"),
        "gold_source_id": question["gold_source_id"],
        "gold_page": question["gold_page"],
        "section": question.get("section"),
        "expected_answer": question["expected_answer"],
        "evidence_quote": question["evidence_quote"],
        "sample": question["id"] in SAMPLE_IDS,
    }


def _sample_questions() -> list[dict[str, Any]]:
    questions = _read_json(BENCHMARK_PATH) or []
    by_id = {q["id"]: q for q in questions}
    chosen = [by_id[qid] for qid in SAMPLE_IDS if qid in by_id]
    if not chosen:
        chosen = questions[:8]
    return [_public_question(q) for q in chosen]


def _all_questions() -> list[dict[str, Any]]:
    return [_public_question(q) for q in (_read_json(BENCHMARK_PATH) or [])]


def _strategy_payload() -> dict[str, Any]:
    return {
        key: {**value, "note": STRATEGY_NOTES[key], "label": f"Strategy {key}"}
        for key, value in STRATEGIES.items()
    }


def module_info() -> dict[str, Any]:
    return {
        "embedding": {
            "provider": "fastembed (local ONNX)",
            "model": "BAAI/bge-base-en-v1.5",
            "dimensions": 768,
            "query": "query_embed with model-specific query prefix",
        },
        "store": {
            "backend": "PostgreSQL + pgvector",
            "host": "127.0.0.1:55433",
            "table": "chunks",
            "metric": "cosine",
            "search": "exact scan (no ANN index)",
        },
        "chunking": {
            "A": {"method": "fixed_size", "size": 1600, "overlap": 200},
            "B": {"method": "by_paragraph", "max_size": 1000},
            "C": {"method": "by_paragraph", "max_size": 1000, "filter": True},
        },
    }


def bootstrap() -> dict[str, Any]:
    snapshot = STATE.snapshot()
    corpus = _corpus_preview()
    return {
        "generated_at": _now_iso(),
        "corpus": corpus,
        "strategies": _strategy_payload(),
        "modules": module_info(),
        "last_run": last_official_metrics(),
        "state": snapshot,
        "samples": _sample_questions(),
        "questions": _all_questions(),
        "ui_runs_path": _relative(UI_RUNS),
        "official_results_path": _relative(OFFICIAL_RESULTS),
    }


def _chunk_stats(chunks: list) -> dict[str, Any]:
    lengths = [len(chunk.text) for chunk in chunks]
    if not lengths:
        return {"chunks": 0, "total_chars": 0, "avg_chars": 0, "min_chars": 0, "max_chars": 0}
    return {
        "chunks": len(lengths),
        "total_chars": sum(lengths),
        "avg_chars": round(sum(lengths) / len(lengths), 1),
        "min_chars": min(lengths),
        "max_chars": max(lengths),
        "lengths": lengths,
        "pages_covered": len({chunk.page for chunk in chunks}),
        "chunks_per_page": round(len(lengths) / max(1, len({chunk.page for chunk in chunks})), 2),
    }


def _sample_chunks(chunks: list, limit: int = 3) -> list[dict[str, Any]]:
    if not chunks:
        return []
    picks = [chunks[0], chunks[len(chunks) // 2], chunks[-1]]
    seen: set[str] = set()
    output = []
    for chunk in picks:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        output.append(
            {
                "chunk_id": chunk.chunk_id,
                "page": chunk.page,
                "section": chunk.metadata.get("section", ""),
                "chars": len(chunk.text),
                "text": _truncate(chunk.text, 420),
            }
        )
        if len(output) >= limit:
            break
    return output


def _comparison_payload(source: str, metrics: dict[str, Any] | None, generated_at: str | None) -> dict[str, Any]:
    return {"source": source, "metrics": metrics, "generated_at": generated_at}


def step_1(emit: Callable[..., None]) -> dict[str, Any]:
    emit(6, f"Reading {_relative(DATA_PATH)}")
    pages = load_pages_jsonl(DATA_PATH)
    emit(30, f"Loaded {len(pages)} content units")
    manifest = _read_json(MANIFEST_PATH)
    emit(46, "Cross-checking manifest page IDs and character counts")
    validate_corpus(pages, manifest)
    emit(66, "Corpus contract passed (>=30 units, unique IDs, >=1,500 chars each)")

    emit(72, f"Auditing normalize_text against {_relative(RAW_PATH)}")
    cleaning = _cleaning_audit(pages)
    emit(78, "Cleaning audit measured: NFC, control characters, separators, whitespace")

    questions = _read_json(BENCHMARK_PATH) or []
    pages_by_id = {page.page: page for page in pages}
    emit(84, f"Loading {len(questions)} frozen benchmark questions")
    validate_benchmark(questions, pages_by_id)
    emit(94, "Benchmark evidence and filters verified against the corpus")
    checks = _ingest_checks(pages, manifest, questions, pages_by_id)
    failed = sum(1 for row in checks if not row["passed"])
    emit(97, f"{len(checks) - failed}/{len(checks)} error checks passed; failures would stop the run")

    sections = sorted({str((page.metadata or {}).get("section", "")) for page in pages} - {""})
    preview = [
        {
            "page": page.page,
            "section": (page.metadata or {}).get("section", ""),
            "chars": len(page.text),
            "preview": _truncate(page.text, 170),
        }
        for page in pages[:6]
    ]
    lengths = [len(page.text) for page in pages]

    with STATE.data_lock:
        STATE.pages = pages
        STATE.pages_by_id = pages_by_id
        STATE.manifest = manifest
        STATE.questions = questions

    emit(100, "Ingestion ready: normalized text, source and page kept for every unit")
    return {
        "page_count": len(pages),
        "content_chars": sum(lengths),
        "document_ids": sorted({page.document_id for page in pages}),
        "sections": len(sections),
        "section_names": sections,
        "min_chars": min(lengths),
        "max_chars": max(lengths),
        "avg_chars": round(sum(lengths) / len(lengths), 1),
        "lengths": lengths,
        "preview": preview,
        "manifest": manifest,
        "cleaning": cleaning,
        "checks": checks,
        "checks_failed": failed,
        "benchmark": {
            "questions": len(questions),
            "dev": sum(1 for q in questions if q.get("split") == "dev"),
            "test": sum(1 for q in questions if q.get("split") == "test"),
            "filter_questions": sum(1 for q in questions if q.get("filter")),
        },
    }


def step_2(emit: Callable[..., None]) -> dict[str, Any]:
    pages = STATE.pages
    if not pages:
        raise RuntimeError("Corpus is not loaded; run step 1 first.")

    emit(4, "Chunking strategy A - fixed-size windows (1,600 / 200)")
    chunks_a = [
        chunk
        for page in pages
        for chunk in fixed_size(page, STRATEGIES["A"]["chunk_size"], STRATEGIES["A"]["overlap"])
    ]
    emit(38, f"Strategy A produced {len(chunks_a)} chunks")

    emit(46, "Chunking strategy B - paragraph grouping (max 1,000 chars)")
    chunks_b = [
        chunk
        for page in pages
        for chunk in by_paragraph(page, STRATEGIES["B"]["chunk_size"])
    ]
    emit(74, f"Strategy B produced {len(chunks_b)} chunks")

    emit(80, "Strategy C reuses B chunks; only the section filter differs")
    chunks = {"A": chunks_a, "B": chunks_b, "C": chunks_b}
    if not all(chunks.values()):
        raise RuntimeError("A strategy produced no chunks")

    stats = {key: _chunk_stats(value) for key, value in chunks.items()}
    emit(92, "Comparing chunk counts and length distributions")

    with STATE.data_lock:
        STATE.chunks = chunks

    latest = last_official_metrics()
    if STATE.benchmark_summary:
        comparison = _comparison_payload(
            "evaluated in this session (step 5)",
            STATE.benchmark_summary,
            (STATE.run_metadata or {}).get("generated_at"),
        )
    else:
        comparison = _comparison_payload(
            "official snapshot: results/metrics.json",
            latest["metrics"],
            latest["generated_at"],
        )

    emit(100, "Chunking comparison ready")
    return {
        "strategies": {
            key: {
                **stats[key],
                "config": STRATEGIES[key],
                "note": STRATEGY_NOTES[key],
                "samples": _sample_chunks(chunks[key]),
            }
            for key in ("A", "B", "C")
        },
        "total_chunks": len(chunks_a) + len(chunks_b),
        "comparison": comparison,
    }


def step_3(emit: Callable[..., None]) -> dict[str, Any]:
    chunks = STATE.chunks
    if not chunks:
        raise RuntimeError("No chunks in memory; run step 2 first.")

    emit(6, "Loading the local ONNX embedding model (first use may download weights)")
    embedder = get_embedder()
    emit(16, f"Model ready: {embedder.model_name} ({embedder.dimensions} dims)")

    emit(22, "Counting model tokens for the corpus and both chunk sets")
    token_counts = {"corpus": embedder.count_tokens([page.text for page in STATE.pages])}

    vectors: dict[str, list[list[float]]] = {}
    embedding_seconds: dict[str, float] = {}
    order = ("A", "B")
    for position, key in enumerate(order):
        texts = [chunk.text for chunk in chunks[key]]
        emit(28 + position * 24, f"Embedding strategy {key}: {len(texts)} documents in one batch")
        tick = time.perf_counter()
        vectors[key] = embedder.embed_documents(texts)
        embedding_seconds[key] = round(time.perf_counter() - tick, 3)
        token_counts[key] = embedder.count_tokens(texts)
        emit(
            round(52 + position * 24, 1),
            f"Strategy {key}: {len(texts)} vectors ready in {embedding_seconds[key]}s",
        )

    emit(78, "Strategy C reuses B vectors; the filter is applied at query time")
    vectors["C"] = vectors["B"]
    embedding_seconds["C"] = 0.0
    token_counts["C"] = token_counts["B"]

    # A small, real slice of the first vector, drawn as a fingerprint in the UI.
    fingerprint = [round(value, 5) for value in vectors["A"][0][:28]]

    with STATE.data_lock:
        STATE.embedder = embedder
        STATE.vectors = vectors

    emit(100, "All 768-dimension vectors computed locally (no API cost)")
    return {
        "model": embedder.model_name,
        "provider": "fastembed (local ONNX)",
        "dimensions": embedder.dimensions,
        "query_method": "query_embed with the model's query prefix",
        "strategies": {
            key: {
                "chunks": len(vectors[key]),
                "vectors": len(vectors[key]),
                "dimensions": embedder.dimensions,
                "embedding_seconds": embedding_seconds[key],
                "model_tokens": token_counts[key],
            }
            for key in ("A", "B", "C")
        },
        "corpus_tokens": token_counts["corpus"],
        "total_seconds": round(sum(embedding_seconds.values()), 3),
        "estimated_cost_usd": 0.0,
        "fingerprint": fingerprint,
    }


def step_4(emit: Callable[..., None]) -> dict[str, Any]:
    chunks = STATE.chunks
    vectors = STATE.vectors
    embedder = STATE.embedder
    if not chunks or not vectors or embedder is None:
        raise RuntimeError("Chunks or vectors are missing; run steps 2 and 3 first.")

    database_url = load_database_url()
    emit(6, "Connecting to PostgreSQL + pgvector on 127.0.0.1:55433")
    store = PgVectorStore(database_url, embedder.dimensions)
    store.create_schema()
    emit(14, "Schema verified: chunks(strategy, chunk_id, metadata JSONB, embedding)")

    results: dict[str, Any] = {}
    total_inserted = 0
    for position, key in enumerate(("A", "B", "C")):
        before = store.strategy_stats(key)
        tick = time.perf_counter()
        store.upsert(key, chunks[key], vectors[key])
        seconds = round(time.perf_counter() - tick, 3)
        after = store.strategy_stats(key)
        total_inserted += after["chunks"]
        results[key] = {
            "before": before,
            "after": after,
            "seconds": seconds,
            "stable": before["chunks"] == after["chunks"],
        }
        emit(
            round(22 + (position + 1) * 24, 1),
            f"Strategy {key}: {before['chunks']} rows before, {after['chunks']} after upsert",
        )

    with STATE.data_lock:
        STATE.store = store

    emit(100, "Upsert complete: re-ingestion keeps row counts stable")
    return {
        "backend": "PostgreSQL + pgvector",
        "table": "chunks",
        "metric": "cosine",
        "search": "exact scan",
        "strategies": results,
        "total_rows": total_inserted,
        "reingest_stable": all(item["stable"] for item in results.values()),
    }


def _write_metrics_csv(summary: dict[str, Any], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["strategy", "split", "n", "hit1", "hit3", "hit1_rate", "hit3_rate"])
        for strategy in sorted(summary):
            for split in ("overall", "dev", "test"):
                row = summary[strategy][split]
                writer.writerow(
                    [
                        strategy,
                        split,
                        row["n"],
                        row["hit1"],
                        row["hit3"],
                        row.get("hit1_rate"),
                        row.get("hit3_rate"),
                    ]
                )


def step_5(emit: Callable[..., None]) -> dict[str, Any]:
    store = STATE.store
    embedder = STATE.embedder
    questions = STATE.questions
    if store is None or embedder is None or not questions:
        raise RuntimeError("The store or benchmark is not ready; run steps 1-4 first.")

    started = time.perf_counter()
    traces: list[dict[str, Any]] = []
    search_seconds: dict[str, float] = {}
    for position, strategy in enumerate(("A", "B", "C")):
        tick = time.perf_counter()
        emit(4 + position * 26, f"Querying strategy {strategy} over {len(questions)} frozen questions")
        for index, question in enumerate(questions, start=1):
            filters = question.get("filter") if STRATEGIES[strategy]["filter"] else None
            query_vector = embedder.embed_query(question["question"])
            hits = store.search(strategy, query_vector, top_k=3, filters=filters)
            scored = evaluate_query({**question, "_strategy": strategy}, hits)
            traces.append(
                {
                    "strategy": strategy,
                    "id": question["id"],
                    "split": question["split"],
                    "type": question.get("type"),
                    "question": question["question"],
                    "expected_answer": question["expected_answer"],
                    "gold_source_id": question["gold_source_id"],
                    "gold_page": question["gold_page"],
                    "evidence_quote": question["evidence_quote"],
                    "filter": filters,
                    "hit@1": scored["hit@1"],
                    "hit@3": scored["hit@3"],
                    "top3": scored["top3"],
                }
            )
            if index % 5 == 0 or index == len(questions):
                emit(
                    round(4 + position * 26 + (index / len(questions)) * 25, 1),
                    f"Strategy {strategy}: {index}/{len(questions)} questions scored",
                )
        search_seconds[strategy] = round(time.perf_counter() - tick, 3)

    summary = summarize(traces)
    emit(84, "Writing run artifacts to artifacts/ui_runs/ (results/ stays untouched)")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = UI_RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "traces.json").write_text(
        json.dumps(traces, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "metrics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_metrics_csv(summary, run_dir / "metrics.csv")

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "surface": "ui/server.py step-by-step run",
        "note": "Artifacts from a UI session. Reported results remain in results/.",
        "corpus": {
            "page_count": len(STATE.pages),
            "content_chars": sum(len(page.text) for page in STATE.pages),
            "document_ids": sorted({page.document_id for page in STATE.pages}),
        },
        "embedding": {
            "provider": "fastembed (local ONNX)",
            "model": embedder.model_name,
            "dimensions": embedder.dimensions,
        },
        "store": {
            "backend": "PostgreSQL + pgvector",
            "table": "chunks",
            "metric": "cosine",
            "search": "exact scan (no ANN index)",
        },
        "strategies": {
            strategy: {
                **STRATEGIES[strategy],
                "stats": store.strategy_stats(strategy),
                "search_seconds": search_seconds[strategy],
            }
            for strategy in ("A", "B", "C")
        },
        "totals": {
            "evaluation_seconds": round(time.perf_counter() - started, 3),
            "estimated_cost_usd": 0.0,
        },
        "benchmark": {
            "path": "benchmark/benchmark.json",
            "questions": len(questions),
            "dev": sum(1 for q in questions if q["split"] == "dev"),
            "test": sum(1 for q in questions if q["split"] == "test"),
            "filter_questions": sum(1 for q in questions if q.get("filter")),
        },
    }
    (run_dir / "run_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with STATE.data_lock:
        STATE.benchmark_summary = summary
        STATE.benchmark_traces = traces
        STATE.run_metadata = metadata

    emit(100, f"Saved 4 artifact files to {_relative(run_dir)}")
    return {
        "summary": summary,
        "run_id": run_id,
        "artifacts": [
            _relative(run_dir / "traces.json"),
            _relative(run_dir / "metrics.json"),
            _relative(run_dir / "metrics.csv"),
            _relative(run_dir / "run_metadata.json"),
        ],
        "search_seconds": search_seconds,
        "evaluation_seconds": metadata["totals"]["evaluation_seconds"],
        "counts": {
            "questions": len(questions),
            "traces": len(traces),
            "dev": metadata["benchmark"]["dev"],
            "test": metadata["benchmark"]["test"],
        },
        "comparison": _comparison_payload(
            "evaluated in this session (step 5)", summary, metadata["generated_at"]
        ),
    }


STEPS: dict[str, tuple[str, Callable[[Callable[..., None]], dict[str, Any]]]] = {
    "1": ("Ingest", step_1),
    "2": ("Chunk", step_2),
    "3": ("Embed", step_3),
    "4": ("Store", step_4),
    "5": ("Save & Test", step_5),
}


def load_database_url() -> str:
    import os

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is missing; copy starter/.env.example to starter/.env")
    return database_url


def _serialise(value: Any) -> Any:
    """Make pgvector rows JSON-safe."""
    if isinstance(value, dict):
        return {key: _serialise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialise(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def retrieve(question_id: str, top_k: int = 3) -> dict[str, Any]:
    store = STATE.store
    embedder = STATE.embedder
    if store is None or embedder is None or not STATE.benchmark_traces:
        raise RuntimeError("Run steps 1-5 before testing retrieval.")

    question = next((q for q in STATE.questions if q["id"] == question_id), None)
    if question is None:
        raise RuntimeError(f"Unknown question id: {question_id}")

    tick = time.perf_counter()
    query_vector = embedder.embed_query(question["question"])
    result: dict[str, Any] = {"question": _public_question(question), "strategies": {}}
    for strategy in ("A", "B", "C"):
        filters = question.get("filter") if STRATEGIES[strategy]["filter"] else None
        hits = store.search(strategy, query_vector, top_k=top_k, filters=filters)
        scored = evaluate_query({**question, "_strategy": strategy}, hits)
        rows = []
        for hit in scored["top3"]:
            rows.append(
                {
                    "rank": hit["rank"],
                    "chunk_id": hit["chunk_id"],
                    "page": hit["page"],
                    "section": hit["section"],
                    "score": round(float(hit["score"]), 4),
                    "hit": _strict_hit_flag(question, hit),
                    "evidence_span": _highlight_span(hit.get("text", ""), question["evidence_quote"]),
                    "text": hit.get("text", ""),
                }
            )
        result["strategies"][strategy] = {
            "filter": filters,
            "hit@1": scored["hit@1"],
            "hit@3": scored["hit@3"],
            "hits": rows,
        }
    result["elapsed_seconds"] = round(time.perf_counter() - tick, 3)
    return result


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "Day06UI/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        print(f"[ui] {self.address_string()} {format % args}")

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(_serialise(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status=status)

    def _static(self, name: str) -> None:
        targets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/favicon.svg": ("favicon.svg", "image/svg+xml"),
        }
        if name not in targets:
            self._error("Not found", 404)
            return
        filename, content_type = targets[name]
        path = STATIC / filename
        if not path.exists():
            self._error(f"Missing static asset: {filename}", 500)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html", "/app.js", "/styles.css", "/favicon.svg"}:
            self._static(path)
            return
        if path == "/api/bootstrap":
            try:
                self._json({"ok": True, **bootstrap()})
            except Exception as exc:  # pragma: no cover - operator feedback
                traceback.print_exc()
                self._error(str(exc), 500)
            return
        if path == "/api/health":
            self._json({"ok": True, "time": _now_iso()})
            return
        self._error("Not found", 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/reset":
            try:
                self._json({"ok": True, "state": self._reset()})
            except Exception as exc:
                self._error(str(exc), 409)
            return
        if path == "/api/retrieve":
            self._handle_retrieve()
            return
        if path.startswith("/api/steps/") and path.endswith("/run"):
            number = path.split("/")[3] if len(path.split("/")) == 5 else ""
            if number not in STEPS:
                self._error("Unknown step", 404)
                return
            self._handle_step(number)
            return
        self._error("Not found", 404)

    def _reset(self) -> dict[str, Any]:
        if not STATE.guard.acquire(blocking=False):
            raise RuntimeError("A step is running; wait for it to finish before resetting.")
        try:
            STATE.reset()
            return STATE.snapshot()
        finally:
            STATE.guard.release()

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"Invalid JSON body: {exc}") from exc
        return payload if isinstance(payload, dict) else {}

    def _handle_retrieve(self) -> None:
        try:
            payload = self._read_json_body()
            question_id = str(payload.get("id") or "")
            top_k = int(payload.get("top_k") or 3)
            if not STATE.guard.acquire(blocking=False):
                self._error("The pipeline is busy running a step; try again in a moment.", 409)
                return
            try:
                self._json({"ok": True, **retrieve(question_id, top_k=top_k)})
            finally:
                STATE.guard.release()
        except Exception as exc:
            traceback.print_exc()
            self._error(str(exc), 400)

    def _handle_step(self, number: str) -> None:
        title, runner = STEPS[number]
        if not STATE.guard.acquire(blocking=False):
            self._error("Another step is already running.", 409)
            return

        # Streamed response; the connection ends when the step finishes.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        aborted = False

        def emit(percent: float | None = None, message: str | None = None, **extra: Any) -> None:
            nonlocal aborted
            if aborted:
                return
            data: dict[str, Any] = {"step": int(number), "title": title}
            if percent is not None:
                data["percent"] = max(0.0, min(100.0, float(percent)))
            if message:
                data["message"] = message
            data.update(extra)
            chunk = f"event: progress\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            try:
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                aborted = True

        try:
            STATE.require(int(number))
        except Exception as exc:
            self._sse_error(str(exc))
            STATE.guard.release()
            return

        started = time.perf_counter()
        STATE.steps[number].update(
            {"status": "running", "started_at": _now_iso(), "error": None, "result": None}
        )
        emit(0, f"Starting step {number}: {title}")
        try:
            result = runner(emit)
            seconds = round(time.perf_counter() - started, 3)
            STATE.steps[number].update(
                {
                    "status": "done",
                    "finished_at": _now_iso(),
                    "seconds": seconds,
                    "result": _serialise(result),
                }
            )
            emit(100, f"Step {number} complete in {seconds}s")
            payload = {"step": int(number), "title": title, "seconds": seconds, "result": _serialise(result)}
            try:
                self.wfile.write(
                    f"event: done\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                )
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        except Exception as exc:
            traceback.print_exc()
            STATE.steps[number].update(
                {
                    "status": "error",
                    "finished_at": _now_iso(),
                    "seconds": round(time.perf_counter() - started, 3),
                    "error": str(exc),
                }
            )
            self._sse_error(str(exc))
        finally:
            STATE.guard.release()

    def _sse_error(self, message: str) -> None:
        try:
            data = json.dumps({"ok": False, "error": message}, ensure_ascii=False)
            self.wfile.write(f"event: error\ndata: {data}\n\n".encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Day 06 pipeline UI -> http://{args.host}:{args.port}", flush=True)
    print(f"Corpus: {_relative(DATA_PATH)} | benchmark: {_relative(BENCHMARK_PATH)}", flush=True)
    print(f"UI run artifacts: {_relative(UI_RUNS)} | official snapshot: {_relative(OFFICIAL_RESULTS)}", flush=True)
    print("[ui] warming the embedding model in the background…", flush=True)
    threading.Thread(target=warm_embedder, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping UI server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
