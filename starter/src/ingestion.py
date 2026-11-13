"""Read and normalize source data while preserving page-level traceability."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from .models import DocumentPage


_ILLUSTRATION_RE = re.compile(r"^\s*\[Illustration\]\s*$", re.MULTILINE)
_SEPARATOR_RE = re.compile(r"^\s*(?:\*\s*){3,}$", re.MULTILINE)
_SPACES_RE = re.compile(r"[ \t]+")
_SPACE_AROUND_NEWLINE_RE = re.compile(r" *\n *")
_MANY_NEWLINES_RE = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Normalize Unicode and whitespace without discarding paragraph structure."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00ad", "")  # soft hyphen
    text = text.replace("\ufeff", "")
    text = text.replace("\u00a0", " ")
    text = _ILLUSTRATION_RE.sub("", text)
    text = _SEPARATOR_RE.sub("", text)
    text = _SPACES_RE.sub(" ", text)
    text = _SPACE_AROUND_NEWLINE_RE.sub("\n", text)
    text = _MANY_NEWLINES_RE.sub("\n\n", text)
    return text.strip()


def load_pdf(path: Path, document_id: str, source: str) -> list[DocumentPage]:
    """Extract each physical page, retaining its page number and source.

    Empty/scanned pages are returned with empty text and logged by the caller,
    rather than silently dropped.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[DocumentPage] = []
    for index, pdf_page in enumerate(reader.pages, start=1):
        text = normalize_text(pdf_page.extract_text() or "")
        pages.append(
            DocumentPage(
                document_id=document_id,
                source=source,
                page=index,
                text=text,
                metadata={"char_count": len(text)},
            )
        )
    return pages


def load_pages_jsonl(path: Path) -> list[DocumentPage]:
    """Load the prepared page units used by the reproducible lab corpus."""
    pages: list[DocumentPage] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            try:
                page = DocumentPage(
                    document_id=record["document_id"],
                    source=record["source"],
                    page=int(record["page"]),
                    text=normalize_text(record["text"]),
                    metadata=dict(record.get("metadata", {})),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid page record at {path}:{line_number}: {exc}") from exc
            if not page.text:
                raise ValueError(f"Empty page text at {path}:{line_number}")
            pages.append(page)
    return pages
