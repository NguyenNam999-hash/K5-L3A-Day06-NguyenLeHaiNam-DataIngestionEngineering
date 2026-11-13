"""Retrieval evaluation with source/page + evidence checks and per-query traces."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


_WS_RE = re.compile(r"\s+")
_QUOTE_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
    }
)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").translate(_QUOTE_TRANSLATION)
    return _WS_RE.sub(" ", text).strip().casefold()


def _gold_records(question: dict[str, Any]) -> list[dict[str, Any]]:
    if question.get("gold_sources"):
        return list(question["gold_sources"])
    return [
        {
            "source_id": question.get("gold_source_id"),
            "page": question.get("gold_page"),
            "evidence_quote": question.get("evidence_quote", ""),
        }
    ]


def _hit(hit: dict[str, Any], gold: dict[str, Any]) -> bool:
    if hit.get("document_id") != gold.get("source_id"):
        return False
    try:
        if int(hit.get("page")) != int(gold.get("page")):
            return False
    except (TypeError, ValueError):
        return False
    quote = _norm(gold.get("evidence_quote", ""))
    if not quote:
        return False
    return quote in _norm(hit.get("text", ""))


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Check whether the top-1/top-3 chunks contain a documented gold evidence span."""
    golds = _gold_records(question)
    matched = []
    for rank, hit in enumerate(hits[:3], start=1):
        gold_index = next((i for i, gold in enumerate(golds) if _hit(hit, gold)), None)
        matched.append(
            {
                "rank": rank,
                "chunk_id": hit.get("chunk_id"),
                "document_id": hit.get("document_id"),
                "page": hit.get("page"),
                "section": hit.get("section"),
                "score": hit.get("score"),
                "text": hit.get("text", ""),
                "metadata": hit.get("metadata", {}),
                "hit": gold_index is not None,
                "gold_index": gold_index,
            }
        )
    hit_at_1 = bool(matched and matched[0]["hit"])
    hit_at_3 = any(item["hit"] for item in matched)
    return {
        "id": question.get("id"),
        "split": question.get("split"),
        "strategy": question.get("_strategy"),
        "question": question.get("question"),
        "filter": question.get("filter"),
        "hit@1": hit_at_1,
        "hit@3": hit_at_3,
        "top3": matched,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Hit@1 and Evidence Hit@3 by strategy and dev/test split."""
    summary: dict[str, Any] = {}
    for trace in traces:
        strategy = trace.get("strategy") or "unknown"
        split = trace.get("split") or "unknown"
        bucket = summary.setdefault(
            strategy,
            {"overall": {"n": 0, "hit1": 0, "hit3": 0}, "dev": {"n": 0, "hit1": 0, "hit3": 0},
             "test": {"n": 0, "hit1": 0, "hit3": 0}},
        )
        for key in ("overall", split):
            target = bucket[key]
            target["n"] += 1
            target["hit1"] += int(bool(trace.get("hit@1")))
            target["hit3"] += int(bool(trace.get("hit@3")))

    for bucket in summary.values():
        for target in bucket.values():
            n = target["n"]
            target["hit1_rate"] = round(target["hit1"] / n, 4) if n else None
            target["hit3_rate"] = round(target["hit3"] / n, 4) if n else None
    return summary
