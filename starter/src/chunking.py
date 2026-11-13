"""Fixed-size and paragraph/structure-aware chunking with source metadata."""
from __future__ import annotations

import re

from .models import Chunk, DocumentPage


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE_RE = re.compile(r"\s+")


def _base_metadata(page: DocumentPage) -> dict:
    metadata = dict(page.metadata)
    metadata.setdefault("section", metadata.get("section", ""))
    metadata["document_id"] = page.document_id
    metadata["source"] = page.source
    metadata["page"] = page.page
    metadata["char_count"] = len(page.text)
    return metadata


def _make_chunk(page: DocumentPage, index: int, suffix: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=f"{page.document_id}-p{page.page:03d}-{suffix}{index:03d}",
        document_id=page.document_id,
        source=page.source,
        page=page.page,
        text=text.strip(),
        metadata=_base_metadata(page),
    )


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Split page text into overlapping character windows."""
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be >= 0 and < size")

    text = page.text
    if not text:
        return []

    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        window = text[start:end].strip()
        if window:
            chunks.append(_make_chunk(page, len(chunks), "f", window))
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _split_long_paragraph(paragraph: str, max_size: int) -> list[str]:
    """Split an oversized paragraph at sentence boundaries, then hard-split."""
    if len(paragraph) <= max_size:
        return [paragraph]

    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_RE.split(paragraph):
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_size:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)

    result: list[str] = []
    for piece in pieces:
        while len(piece) > max_size:
            cut = piece.rfind(" ", 0, max_size)
            if cut < max_size // 3:
                cut = max_size
            result.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            result.append(piece)
    return result


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Group paragraphs up to max_size, splitting only when a paragraph is too long."""
    if max_size <= 0:
        raise ValueError("max_size must be positive")

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", page.text) if p.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        units.extend(_split_long_paragraph(_WHITESPACE_RE.sub(" ", paragraph), max_size))

    chunks: list[Chunk] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}".strip() if current else unit
        if current and len(candidate) > max_size:
            chunks.append(_make_chunk(page, len(chunks), "b", current))
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(_make_chunk(page, len(chunks), "b", current))
    return chunks
