"""Build deterministic page units + manifest from Project Gutenberg eBook #11."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "starter"))

from src.ingestion import normalize_text  # noqa: E402

RAW = ROOT / "data" / "alice_raw.txt"
PAGES = ROOT / "data" / "pages.jsonl"
MANIFEST = ROOT / "data" / "manifest.json"
DOCUMENT_ID = "alice-1865"
SOURCE_URL = "https://www.gutenberg.org/files/11/11-0.txt"
TARGET = 3800
MAX_SIZE = 5000
MIN_SIZE = 1500

CHAPTER_RE = re.compile(r"^CHAPTER ([IVX]+)\.\s*\n([^\n]+)\n", re.MULTILINE)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def split_long(paragraph: str) -> list[str]:
    if len(paragraph) <= MAX_SIZE:
        return [paragraph]
    pieces, current = [], ""
    for sentence in SENTENCE_RE.split(paragraph):
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > MAX_SIZE:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    result = []
    for piece in pieces:
        while len(piece) > MAX_SIZE:
            cut = piece.rfind(" ", 0, MAX_SIZE)
            cut = cut if cut >= MAX_SIZE // 3 else MAX_SIZE
            result.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            result.append(piece)
    return result


def main() -> None:
    raw = RAW.read_text(encoding="utf-8")
    body = raw[raw.index("CHAPTER I.") : raw.index("*** END OF THE PROJECT GUTENBERG")]
    matches = list(CHAPTER_RE.finditer(body))
    if len(matches) != 12:
        raise SystemExit(f"Expected 12 chapters, found {len(matches)}")

    pages: list[dict] = []
    page_number = 0
    for index, match in enumerate(matches):
        chapter_number, chapter_title = match.group(1), match.group(2).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        chapter_text = normalize_text(body[start:end])
        paragraphs = [
            " ".join(part.split())
            for part in re.split(r"\n\s*\n", chapter_text)
            if part.strip()
        ]
        units: list[str] = []
        for paragraph in paragraphs:
            units.extend(split_long(paragraph))

        section = f"Chapter {chapter_number} - {chapter_title}"
        grouped: list[str] = []
        current = ""
        for unit in units:
            candidate = f"{current}\n\n{unit}".strip() if current else unit
            if current and len(candidate) > TARGET:
                grouped.append(current)
                current = unit
            else:
                current = candidate
        if current:
            grouped.append(current)

        # Merge an undersized tail into its previous page when possible.
        if len(grouped) > 1 and len(grouped[-1]) < MIN_SIZE:
            merged = f"{grouped[-2]}\n\n{grouped[-1]}"
            if len(merged) <= MAX_SIZE + 500:
                grouped[-2:] = [merged]
            elif len(grouped[-1]) < MIN_SIZE:
                raise SystemExit(f"Chapter {chapter_number} tail is too short: {len(grouped[-1])}")

        for text in grouped:
            if len(text) < MIN_SIZE:
                raise SystemExit(f"Page below {MIN_SIZE} chars in chapter {chapter_number}: {len(text)}")
            page_number += 1
            pages.append(
                {
                    "document_id": DOCUMENT_ID,
                    "source": SOURCE_URL,
                    "page": page_number,
                    "text": text,
                    "metadata": {
                        "section": section,
                        "chapter": chapter_number,
                        "language": "en",
                        "document_type": "public_domain_book",
                        "char_count": len(text),
                    },
                }
            )

    if len(pages) < 30:
        raise SystemExit(f"Only {len(pages)} pages; need at least 30")

    PAGES.write_text(
        "".join(json.dumps(page, ensure_ascii=False) + "\n" for page in pages),
        encoding="utf-8",
    )
    manifest = {
        "source_id": DOCUMENT_ID,
        "title": "Alice's Adventures in Wonderland",
        "author": "Lewis Carroll",
        "source_path_or_url": SOURCE_URL,
        "license_or_permission": (
            "Public domain in the United States. Redistributed under the "
            "Project Gutenberg License (https://www.gutenberg.org/policy/license.html)."
        ),
        "accessed_at": date.today().isoformat(),
        "snapshot": "Project Gutenberg eBook #11 (11-0.txt), Millennium Fulcrum Edition 3.0",
        "raw_file": "data/alice_raw.txt",
        "raw_sha256": hashlib.sha256(RAW.read_bytes()).hexdigest(),
        "content_unit": "Sequential text unit (the source is plain text, not a paginated PDF).",
        "counting_rule": (
            "Each unit is a contiguous, non-overlapping text segment within one chapter, "
            "built at paragraph boundaries to approximately 3,800 characters (1,500 minimum, "
            "5,000 maximum). Covers, contents, Project Gutenberg boilerplate and separator "
            "marks are excluded. Page IDs are 1-based positions after normalization."
        ),
        "page_count": len(pages),
        "content_chars": sum(len(page["text"]) for page in pages),
        "pages": [
            {
                "page": page["page"],
                "section": page["metadata"]["section"],
                "char_count": page["metadata"]["char_count"],
            }
            for page in pages
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(pages)} pages, {manifest['content_chars']} chars")
    for page in pages:
        print(page["page"], page["metadata"]["section"][:38], len(page["text"]))


if __name__ == "__main__":
    main()
