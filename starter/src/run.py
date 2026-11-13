"""Day 06 pipeline: ingest -> chunk -> embed -> store -> retrieve -> evaluate."""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .chunking import by_paragraph, fixed_size
from .embeddings import MyEmbedder
from .evaluation import evaluate_query, summarize
from .ingestion import load_pages_jsonl
from .store import PgVectorStore

ROOT = Path(__file__).resolve().parents[2]
STRATEGIES = {
    "A": {"chunking": "fixed_size", "chunk_size": 1600, "overlap": 200, "filter": False},
    "B": {"chunking": "by_paragraph", "chunk_size": 1000, "overlap": 0, "filter": False},
    "C": {"chunking": "by_paragraph", "chunk_size": 1000, "overlap": 0, "filter": True},
}


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def validate_corpus(pages, manifest: dict[str, Any] | None) -> None:
    if len(pages) < 30:
        raise ValueError(f"Corpus has {len(pages)} units; at least 30 content pages are required")
    ids = [page.page for page in pages]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate page IDs in corpus")
    if any(not page.text.strip() for page in pages):
        raise ValueError("Corpus contains an empty page")
    short = [(page.page, len(page.text)) for page in pages if len(page.text) < 1500]
    if short:
        raise ValueError(f"Content units below 1,500 normalized characters: {short[:5]}")
    if manifest:
        if int(manifest.get("page_count", 0)) != len(pages):
            raise ValueError("manifest page_count does not match pages.jsonl")
        manifest_ids = {int(item["page"]) for item in manifest.get("pages", [])}
        if manifest_ids != set(ids):
            raise ValueError("manifest page IDs do not match pages.jsonl")


def validate_benchmark(questions, pages_by_id: dict[int, Any]) -> None:
    if len(questions) < 20:
        raise ValueError(f"Benchmark has {len(questions)} questions; at least 20 required")
    ids = [question.get("id") for question in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate question IDs")
    splits = {split: sum(1 for q in questions if q.get("split") == split) for split in ("dev", "test")}
    if splits["dev"] < 15 or splits["test"] < 5:
        raise ValueError(f"Invalid split: {splits}; need >=15 dev and >=5 test")

    filter_questions = [q for q in questions if q.get("filter")]
    if len(filter_questions) < 3 or not any(q.get("split") == "test" for q in filter_questions):
        raise ValueError("Need >=3 metadata-filter questions, including >=1 in test")

    for question in questions:
        required = {
            "id", "split", "question", "expected_answer", "gold_source_id",
            "gold_page", "evidence_quote",
        }
        missing = required - set(question)
        if missing:
            raise ValueError(f"{question.get('id')} missing fields: {sorted(missing)}")
        page = pages_by_id.get(int(question["gold_page"]))
        if page is None:
            raise ValueError(f"{question['id']} gold_page does not exist")
        if page.document_id != question["gold_source_id"]:
            raise ValueError(f"{question['id']} gold_source_id mismatch")
        if _norm(question["evidence_quote"]) not in _norm(page.text):
            raise ValueError(f"{question['id']} evidence_quote is not in the gold page")
        for key, value in (question.get("filter") or {}).items():
            if str(page.metadata.get(key, "")) != str(value):
                raise ValueError(
                    f"{question['id']} filter {key}={value!r} is not supported by its gold page"
                )


def _write_metrics_csv(summary: dict[str, Any], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["strategy", "split", "n", "hit1", "hit3", "hit1_rate", "hit3_rate"])
        for strategy in sorted(summary):
            for split in ("overall", "dev", "test"):
                row = summary[strategy][split]
                writer.writerow([
                    strategy, split, row["n"], row["hit1"], row["hit3"],
                    row.get("hit1_rate"), row.get("hit3_rate"),
                ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(ROOT / "data" / "pages.jsonl"))
    parser.add_argument("--manifest", default=str(ROOT / "data" / "manifest.json"))
    parser.add_argument("--benchmark", default=str(ROOT / "benchmark" / "benchmark.json"))
    parser.add_argument("--results", default=str(ROOT / "results"))
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--clean", action="store_true", help="delete each strategy before ingesting")
    parser.add_argument("--recreate", action="store_true", help="drop and recreate the chunks table")
    args = parser.parse_args()

    load_dotenv(ROOT / "starter" / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is missing; copy starter/.env.example to starter/.env")

    pages = load_pages_jsonl(Path(args.data))
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    validate_corpus(pages, manifest)
    pages_by_id = {page.page: page for page in pages}
    questions = json.loads(Path(args.benchmark).read_text(encoding="utf-8"))
    validate_benchmark(questions, pages_by_id)
    print(f"Corpus: {len(pages)} pages, {sum(len(p.text) for p in pages)} chars")
    print(f"Benchmark: {len(questions)} questions "
          f"({sum(q['split'] == 'dev' for q in questions)} dev / "
          f"{sum(q['split'] == 'test' for q in questions)} test)")

    started = time.perf_counter()
    embedder = MyEmbedder()
    print(f"Embedding model: {embedder.model_name} ({embedder.dimensions} dims)")
    store = PgVectorStore(database_url, embedder.dimensions)
    if args.recreate:
        store.drop_schema()
    store.create_schema()

    chunks_by_strategy: dict[str, list] = {
        "A": [
            chunk
            for page in pages
            for chunk in fixed_size(page, STRATEGIES["A"]["chunk_size"], STRATEGIES["A"]["overlap"])
        ],
        "B": [
            chunk
            for page in pages
            for chunk in by_paragraph(page, STRATEGIES["B"]["chunk_size"])
        ],
    }
    chunks_by_strategy["C"] = chunks_by_strategy["B"]
    if not all(chunks_by_strategy.values()):
        raise RuntimeError("A strategy produced no chunks")

    token_counts = {
        "corpus": embedder.count_tokens([page.text for page in pages]),
        "A": embedder.count_tokens([chunk.text for chunk in chunks_by_strategy["A"]]),
        "B": embedder.count_tokens([chunk.text for chunk in chunks_by_strategy["B"]]),
    }
    token_counts["C"] = token_counts["B"]

    embedding_seconds: dict[str, float] = {}
    vectors_by_strategy: dict[str, list[list[float]]] = {}
    for strategy in ("A", "B"):
        tick = time.perf_counter()
        vectors = embedder.embed_documents([chunk.text for chunk in chunks_by_strategy[strategy]])
        embedding_seconds[strategy] = round(time.perf_counter() - tick, 3)
        vectors_by_strategy[strategy] = vectors
    # C reuses B's text and vectors; only the retrieval filter differs.
    vectors_by_strategy["C"] = vectors_by_strategy["B"]
    embedding_seconds["C"] = 0.0

    ingest_seconds: dict[str, float] = {}
    for strategy in ("A", "B", "C"):
        if args.clean:
            store.delete_strategy(strategy)
        tick = time.perf_counter()
        store.upsert(strategy, chunks_by_strategy[strategy], vectors_by_strategy[strategy])
        ingest_seconds[strategy] = round(time.perf_counter() - tick, 3)

    traces: list[dict[str, Any]] = []
    search_seconds: dict[str, float] = {}
    for strategy in ("A", "B", "C"):
        tick = time.perf_counter()
        for question in questions:
            filters = question.get("filter") if STRATEGIES[strategy]["filter"] else None
            query_vector = embedder.embed_query(question["question"])
            hits = store.search(strategy, query_vector, top_k=args.top_k, filters=filters)
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
        search_seconds[strategy] = round(time.perf_counter() - tick, 3)

    summary = summarize(traces)
    results_dir = Path(args.results)
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "traces.json").write_text(
        json.dumps(traces, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (results_dir / "metrics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_metrics_csv(summary, results_dir / "metrics.csv")

    strategy_meta = {}
    for strategy in ("A", "B", "C"):
        strategy_meta[strategy] = {
            **STRATEGIES[strategy],
            "stats": store.strategy_stats(strategy),
            "model_tokens": token_counts[strategy],
            "embedding_seconds": embedding_seconds[strategy],
            "ingest_seconds": ingest_seconds[strategy],
            "search_seconds": search_seconds[strategy],
        }
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": {
            "page_count": len(pages),
            "content_chars": sum(len(page.text) for page in pages),
            "model_tokens": token_counts["corpus"],
            "document_ids": sorted({page.document_id for page in pages}),
            "manifest": _relative(manifest_path) if manifest_path.exists() else None,
        },
        "embedding": {
            "provider": "fastembed (local ONNX)",
            "model": embedder.model_name,
            "dimensions": embedder.dimensions,
            "query_method": "query_embed (model-specific query prefix)",
        },
        "store": {
            "backend": "PostgreSQL + pgvector",
            "table": "chunks",
            "metric": "cosine",
            "search": "exact scan (no ANN index)",
        },
        "strategies": strategy_meta,
        "totals": {
            "wall_seconds": round(time.perf_counter() - started, 3),
            "embedding_seconds": round(sum(embedding_seconds.values()), 3),
            "search_seconds": round(sum(search_seconds.values()), 3),
            "estimated_cost_usd": 0.0,
        },
        "benchmark": {
            "path": _relative(Path(args.benchmark)),
            "questions": len(questions),
            "dev": sum(q["split"] == "dev" for q in questions),
            "test": sum(q["split"] == "test" for q in questions),
            "filter_questions": sum(bool(q.get("filter")) for q in questions),
        },
    }
    (results_dir / "run_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("\nStrategy  Split    Hit@1      Hit@3     Chunks")
    for strategy in sorted(summary):
        for split in ("dev", "test", "overall"):
            row = summary[strategy][split]
            count = strategy_meta[strategy]["stats"]["chunks"]
            print(
                f"{strategy:8} {split:7} {row['hit1']:>2}/{row['n']:<2} "
                f"{row['hit3']:>5}/{row['n']:<2} {count:>7}"
            )
    print(f"\nWrote {results_dir}/traces.json, metrics.json, metrics.csv, run_metadata.json")
    print(f"Total wall time: {metadata['totals']['wall_seconds']}s")


if __name__ == "__main__":
    main()
