"""Draft the frozen benchmark with an LLM, then verify every evidence quote.

This script is only a drafting aid. It writes benchmark/candidates.json; the
curated, frozen questions live in benchmark/benchmark.json. Run it from the
repository root with the same provider variables used for the lab:

    set -a && source starter/.env && set +a
    python3 benchmark/generate_questions.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "data" / "pages.jsonl"
OUT = ROOT / "benchmark" / "candidates.json"

SPECS = [
    # page, split, type, filter?
    (2, "dev", "direct", False),
    (4, "dev", "paraphrase", False),
    (6, "test", "conditional", False),
    (7, "dev", "direct", False),
    (9, "test", "direct", True),
    (11, "dev", "paraphrase", True),
    (13, "dev", "conditional", False),
    (15, "dev", "direct", False),
    (17, "dev", "paraphrase", False),
    (19, "dev", "direct", True),
    (21, "dev", "conditional", False),
    (23, "test", "paraphrase", False),
    (25, "dev", "direct", False),
    (27, "dev", "paraphrase", False),
    (28, "dev", "conditional", True),
    (31, "dev", "direct", False),
    (33, "test", "direct", True),
    (34, "dev", "paraphrase", False),
    (38, "dev", "conditional", False),
    (39, "test", "direct", False),
]


def load_pages() -> dict[int, dict]:
    pages = {}
    with PAGES.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                pages[int(record["page"])] = record
    return pages


def call_model(prompt: str) -> str:
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("MODEL") or os.environ.get("OPENAI_MODEL")
    if not key or not model:
        raise SystemExit("Set OPENAI_API_KEY and MODEL before running this script.")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You create retrieval-benchmark questions from a single source page. "
                    "Return only a JSON object with keys: question, expected_answer, "
                    "evidence_quote, type. The evidence_quote must be copied verbatim "
                    "from the page and must fully support the answer."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1600,
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "StatusAgentLab-Day06/1.0",
            **(
                {"x-opencode-session": os.environ["OPENCODE_SESSION_ID"]}
                if os.environ.get("OPENCODE_SESSION_ID") and "opencode.ai" in base
                else {}
            ),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["choices"][0]["message"].get("content") or ""


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(text[start : end + 1])


def locate_quote(text: str, quote: str) -> str | None:
    quote = " ".join(quote.split())
    if quote and quote in text:
        return quote
    return None


def main() -> None:
    pages = load_pages()
    results = []
    for page_number, split, qtype, use_filter in SPECS:
        page = pages[page_number]
        section = page["metadata"]["section"]
        filter_line = (
            f"The question must explicitly mention the chapter/section \"{section}\" "
            "so a section metadata filter is justified."
            if use_filter
            else "Do not mention the chapter title, page number, or source metadata."
        )
        prompt = f"""Source page (page unit {page_number}, {section}):

{page['text']}

Write one {qtype} question in English whose answer is explicitly supported by this page.
Requirements:
- The question must be answerable from this page alone and must not rely on general knowledge.
- expected_answer must be a short, precise answer.
- evidence_quote must be a contiguous verbatim excerpt from the page, 20-300 characters,
  containing the exact fact needed for the answer.
- {filter_line}
- Return JSON only: {{"question": "...", "expected_answer": "...", "evidence_quote": "...", "type": "{qtype}"}}
"""
        parsed = None
        for attempt in range(1, 4):
            try:
                raw = call_model(prompt)
                candidate = parse_json(raw)
                actual_quote = locate_quote(page["text"], str(candidate.get("evidence_quote", "")))
                if not actual_quote:
                    raise ValueError("evidence_quote is not a verbatim substring of the page")
                candidate["evidence_quote"] = actual_quote
                candidate["type"] = qtype
                candidate["page"] = page_number
                candidate["split"] = split
                candidate["section"] = section
                candidate["document_id"] = page["document_id"]
                candidate["filter"] = {"section": section} if use_filter else None
                parsed = candidate
                break
            except (ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError) as exc:
                print(f"page {page_number} attempt {attempt} failed: {exc}", file=sys.stderr)
                time.sleep(2)
        if parsed is None:
            print(f"SKIP page {page_number}", file=sys.stderr)
            continue
        results.append(parsed)
        print(f"page {page_number}: {parsed['question']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(results)} candidates to {OUT}")


if __name__ == "__main__":
    main()
