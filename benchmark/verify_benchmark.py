"""Verify the frozen benchmark against the page corpus without any API calls."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u2019", "'")).strip().casefold()


def main() -> int:
    pages = {}
    with (ROOT / "data" / "pages.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                pages[int(record["page"])] = record
    questions = json.loads((ROOT / "benchmark" / "benchmark.json").read_text(encoding="utf-8"))

    errors: list[str] = []
    if len(questions) != 20:
        errors.append(f"expected 20 questions, got {len(questions)}")
    ids = [q.get("id") for q in questions]
    if len(ids) != len(set(ids)):
        errors.append("duplicate question IDs")
    dev = sum(q.get("split") == "dev" for q in questions)
    test = sum(q.get("split") == "test" for q in questions)
    if dev != 15 or test != 5:
        errors.append(f"expected 15 dev / 5 test, got {dev}/{test}")
    filters = [q for q in questions if q.get("filter")]
    if len(filters) < 3 or not any(q.get("split") == "test" for q in filters):
        errors.append("need >=3 filter questions and >=1 in test")

    for question in questions:
        page = pages.get(int(question.get("gold_page", -1)))
        if page is None:
            errors.append(f"{question.get('id')}: gold page missing")
            continue
        if page["document_id"] != question.get("gold_source_id"):
            errors.append(f"{question.get('id')}: source ID mismatch")
        if norm(question.get("evidence_quote", "")) not in norm(page["text"]):
            errors.append(f"{question.get('id')}: evidence quote not found on page {page['page']}")
        for key, value in (question.get("filter") or {}).items():
            if str(page["metadata"].get(key, "")) != str(value):
                errors.append(f"{question.get('id')}: filter {key}={value!r} not in page metadata")
        print(
            f"{question['id']} {question['split']:4} page={question['gold_page']:>2} "
            f"filter={'yes' if question.get('filter') else 'no ':3} {question['question']}"
        )

    if errors:
        print("\nFAILED:")
        for error in errors:
            print(" -", error)
        return 1
    print(f"\nOK: {len(questions)} questions ({dev} dev / {test} test), {len(filters)} filter questions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
