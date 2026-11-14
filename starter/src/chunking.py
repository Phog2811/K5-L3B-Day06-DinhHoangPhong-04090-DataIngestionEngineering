"""Implement fixed-size and paragraph/structure-aware chunking."""
from __future__ import annotations

import re
from typing import Any

from .models import Chunk, DocumentPage

_HEADING_ONLY_RE = re.compile(
    r"^(?:Executive Summary|Part \d+:.*|Table \d+:.*|Fig\. \d+\..*|"
    r"Appendix [A-D][.:]?.*|Action ID Suggested Action GAI Risks|"
    r"\d+(?:\.\d+)*\.?\s+[A-Z][^\n]{1,90}|"
    r"(?:GOVERN|MAP|MEASURE|MANAGE)\s+\d+(?:\.\d+)?:[^\n]{0,220})$"
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"(])")


def _detect_chunk_section(text: str, default_section: str) -> str:
    """Refine section metadata when a chunk starts or explicitly defines a NIST function."""
    head = text[:260]
    if re.search(r"\b(?:5\.1\s+Govern|GOVERN\s+\d|GV-\d+\.\d+-\d+)\b", head):
        return "govern"
    if re.search(r"\b(?:5\.2\s+Map|MAP\s+\d|MP-\d+\.\d+-\d+)\b", head):
        return "map"
    if re.search(r"\b(?:5\.3\s+Measure|MEASURE\s+\d|MS-\d+\.\d+-\d+)\b", head):
        return "measure"
    if re.search(r"\b(?:5\.4\s+Manage|MANAGE\s+\d|MG-\d+\.\d+-\d+)\b", head):
        return "manage"
    if re.search(r"\b(?:6\s+AI RMF Profiles|AI RMF cross-sectoral profiles)\b", head):
        return "profiles"
    if re.search(r"\bAppendix\s+[A-D]\b", head):
        return "appendix"
    return default_section


def _split_oversized_block(block: str, max_size: int) -> list[str]:
    """Split an oversized paragraph at sentence or word boundaries without cutting words."""
    if len(block) <= max_size:
        return [block]

    sentences = _SENTENCE_SPLIT_RE.split(block)
    pieces: list[str] = []
    cur = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) > max_size:
            if cur:
                pieces.append(cur)
                cur = ""
            # Word-boundary fallback for very long single sentences/tables.
            words = sent.split(" ")
            w_buf = ""
            for w in words:
                candidate = f"{w_buf} {w}".strip() if w_buf else w
                if len(candidate) <= max_size:
                    w_buf = candidate
                else:
                    if w_buf:
                        pieces.append(w_buf)
                    w_buf = w
            if w_buf:
                pieces.append(w_buf)
            continue

        candidate = f"{cur} {sent}".strip() if cur else sent
        if len(candidate) <= max_size:
            cur = candidate
        else:
            if cur:
                pieces.append(cur)
            cur = sent
    if cur:
        pieces.append(cur)
    return pieces


def fixed_size(page: DocumentPage, size: int = 500, overlap: int = 50) -> list[Chunk]:
    """Split normalized page text into fixed-width character windows with overlap."""
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must satisfy 0 <= overlap < size")

    text = page.text.strip()
    if not text:
        return []

    chunks: list[Chunk] = []
    step = size - overlap
    idx = 0
    for start in range(0, len(text), step):
        end = min(start + size, len(text))
        window = text[start:end].strip()
        if not window:
            continue
        meta: dict[str, Any] = dict(page.metadata)
        meta["chunk_strategy"] = "fixed_size"
        meta["char_start"] = start
        meta["char_end"] = end
        chunks.append(
            Chunk(
                chunk_id=f"{page.document_id}:p{page.page:03d}:fixed:{idx:03d}",
                document_id=page.document_id,
                source=page.source,
                page=page.page,
                text=window,
                metadata=meta,
            )
        )
        idx += 1
        if end >= len(text):
            break
    return chunks


def _build_atomic_units(paragraphs: list[str]) -> list[str]:
    """Glue short headings/labels to their following paragraph so headings are never orphaned."""
    units: list[str] = []
    i = 0
    while i < len(paragraphs):
        cur = paragraphs[i].strip()
        if not cur:
            i += 1
            continue
        if cur.startswith(("AI Actor Tasks:", "Trustworthy AI Characteristics:")) and units:
            units[-1] = f"{units[-1]}\n\n{cur}"
            i += 1
            continue
        if (
            (_HEADING_ONLY_RE.match(cur) or len(cur) < 110)
            and i + 1 < len(paragraphs)
            and not cur.endswith(".")
        ):
            nxt = paragraphs[i + 1].strip()
            if nxt and not nxt.startswith(("AI Actor Tasks:", "Trustworthy AI Characteristics:")):
                units.append(f"{cur}\n\n{nxt}")
                i += 2
                continue
        units.append(cur)
        i += 1
    return units


def by_paragraph(page: DocumentPage, max_size: int = 1100) -> list[Chunk]:
    """Group coherent paragraph units up to max_size, splitting oversized units cleanly."""
    if max_size <= 0:
        raise ValueError("max_size must be positive")

    raw_paragraphs = [p.strip() for p in page.text.split("\n\n") if p.strip()]
    if not raw_paragraphs:
        return []

    units = _build_atomic_units(raw_paragraphs)
    expanded_units: list[str] = []
    for unit in units:
        expanded_units.extend(_split_oversized_block(unit, max_size))

    groups: list[str] = []
    buf = ""
    for unit in expanded_units:
        if not buf:
            buf = unit
        elif len(buf) + 2 + len(unit) <= max_size:
            buf = f"{buf}\n\n{unit}"
        else:
            groups.append(buf)
            buf = unit
    if buf:
        groups.append(buf)

    default_section = str(page.metadata.get("section", "other"))
    running_section = default_section
    chunks: list[Chunk] = []
    for idx, chunk_text in enumerate(groups):
        running_section = _detect_chunk_section(chunk_text, running_section)
        meta: dict[str, Any] = dict(page.metadata)
        meta["section"] = running_section
        meta["chunk_strategy"] = "by_paragraph"
        chunks.append(
            Chunk(
                chunk_id=f"{page.document_id}:p{page.page:03d}:para:{idx:03d}",
                document_id=page.document_id,
                source=page.source,
                page=page.page,
                text=chunk_text,
                metadata=meta,
            )
        )
    return chunks
