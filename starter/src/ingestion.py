"""Read and normalize source PDFs while keeping full page-level traceability."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import unicodedata
from typing import Any

from pypdf import PdfReader

from .models import DocumentPage

# Verified true compound pairs where a line-wrap hyphen must be preserved.
_KEEP_HYPHEN_PAIRS = frozenset({
    ("ai", "generated"), ("context", "relevant"), ("context", "specific"),
    ("cost", "effective"), ("decision", "making"), ("domain", "specific"),
    ("gai", "led"), ("human", "ai"), ("language", "models"),
    ("non", "systematical"), ("off", "label"), ("of", "chinese"),
    ("of", "the"), ("pre", "trained"), ("privacy", "enhancing"),
    ("re", "evaluated"), ("real", "world"), ("red", "teaming"),
    ("retrieval", "augmented"), ("round", "trip"), ("sub", "sampling"),
    ("tamper", "proof"), ("third", "party"), ("wp", "content"),
})
_KEEP_HYPHEN_CONJUNCTIONS = frozenset({"or", "and", "to"})

_ACTION_ID_RE = re.compile(r"^((?:GV|MP|MS|MG)-\d+\.\d+-\d+)\b(.*)$")
_HEADING_PREFIX_RE = re.compile(
    r"^(Executive Summary|Part \d+:|Table \d+:|Fig\. \d+\.|Appendix [A-D]\b|"
    r"\d+(?:\.\d+)*\.?\s+[A-Z]|(?:GOVERN|MAP|MEASURE|MANAGE)\s+\d+(?:\.\d+)?:|"
    r"Action ID\s+Suggested Action\s+GAI Risks|AI Actor Tasks:|"
    r"Trustworthy AI Characteristics:|GAI Risks:?)\b"
)


@dataclass
class DocumentSpec:
    """Configuration describing a source PDF and its verified static metadata."""

    document_id: str
    title: str
    path: Path
    source: str
    year: int
    document_type: str
    license_or_permission: str
    source_url: str
    excluded_pages: dict[int, str] = field(default_factory=dict)


@dataclass
class IngestionStats:
    """Audit metrics collected while loading and normalizing a corpus."""

    document_id: str
    source: str
    physical_pages: int
    excluded_pages: dict[int, str]
    empty_pages: list[int]
    low_text_pages: list[tuple[int, int]]
    content_pages: int
    raw_chars: int
    normalized_chars: int
    errors: list[str] = field(default_factory=list)


def _extract_page_with_layout_breaks(page: Any) -> str:
    """Extract text in PDF content-stream order while preserving vertical paragraph gaps."""
    lines: list[str] = []
    cur: list[str] = []
    prev_y: float | None = None

    def visitor(text: str, cm: Any, tm: Any, font_dict: Any, font_size: float) -> None:
        nonlocal prev_y, cur
        if not text or text in ("\n", "\r\n", "\r"):
            return
        if not text.strip():
            if cur and not cur[-1].endswith(" "):
                cur.append(" ")
            return
        y = float(tm[5])
        if prev_y is not None and abs(y - prev_y) > 7.5:
            line = " ".join("".join(cur).split())
            if line:
                lines.append(line)
            cur = []
            # Vertical spacing > 17.5 pt or upward column/cell jump starts a new paragraph block.
            if (prev_y - y) > 17.5 or (y - prev_y) > 7.5:
                if lines and lines[-1] != "":
                    lines.append("")
        cur.append(text)
        prev_y = y

    page.extract_text(visitor_text=visitor)
    if cur:
        line = " ".join("".join(cur).split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _should_keep_hyphen(left: str, right: str, line_context: str) -> bool:
    if "http://" in line_context or "https://" in line_context:
        return True
    l_low = left.lower()
    r_low = right.lower()
    if (l_low, r_low) in _KEEP_HYPHEN_PAIRS or r_low in _KEEP_HYPHEN_CONJUNCTIONS:
        return True
    if right and right[0].isupper():
        return True
    return False


def _unwrap_paragraph(lines: list[str]) -> str:
    """Join wrapped lines within one paragraph while repairing syllable hyphenation."""
    if not lines:
        return ""
    acc = lines[0]
    for nxt in lines[1:]:
        nxt = nxt.strip()
        if not nxt:
            continue
        if acc.endswith("-") and not acc.endswith("--"):
            m_left = re.search(r"([A-Za-z0-9]+)-$", acc)
            m_right = re.match(r"^([A-Za-z0-9]+)", nxt)
            if m_left and m_right:
                left_tok, right_tok = m_left.group(1), m_right.group(1)
                if _should_keep_hyphen(left_tok, right_tok, acc + nxt):
                    if right_tok.lower() in {"or", "and", "to"}:
                        acc = f"{acc} {nxt}"
                    else:
                        acc = f"{acc}{nxt}"
                else:
                    acc = f"{acc[:-1]}{nxt}"
                continue
        acc = f"{acc} {nxt}"
    return re.sub(r"[ \t]{2,}", " ", acc).strip()


def _strip_running_header_footer(lines: list[str]) -> list[str]:
    """Remove recurring NIST headers and standalone page numbers at page boundaries."""
    cleaned: list[str] = []
    for ln in lines:
        s = ln.strip()
        if s == "NIST AI 100-1 AI RMF 1.0":
            continue
        if re.fullmatch(r"Page\s+(?:\d+|[ivx]+)", s):
            continue
        cleaned.append(ln)

    while cleaned and cleaned[0].strip() == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1].strip() == "":
        cleaned.pop()

    # NIST AI 600-1 puts a standalone printed page number at top/bottom of page.
    if cleaned and re.fullmatch(r"\d{1,3}", cleaned[0].strip()):
        cleaned.pop(0)
    if cleaned and re.fullmatch(r"\d{1,3}", cleaned[-1].strip()):
        cleaned.pop()
    return cleaned


def _merge_action_blocks(paragraphs: list[str]) -> list[str]:
    """Keep NIST AI 600-1 Action IDs coupled with their action text and risk tags."""
    merged: list[str] = []
    i = 0
    while i < len(paragraphs):
        cur = paragraphs[i]
        m = _ACTION_ID_RE.match(cur)
        if m and len(cur) <= 24 and i + 1 < len(paragraphs):
            nxt = paragraphs[i + 1]
            if not _ACTION_ID_RE.match(nxt) and not _HEADING_PREFIX_RE.match(nxt):
                combined = f"{cur}: {nxt}"
                i += 2
                # Attach trailing GAI Risks list if present before next Action ID or heading.
                if (
                    i < len(paragraphs)
                    and not _ACTION_ID_RE.match(paragraphs[i])
                    and not _HEADING_PREFIX_RE.match(paragraphs[i])
                    and len(paragraphs[i]) <= 220
                    and not paragraphs[i].endswith(".")
                ):
                    combined = f"{combined} [GAI Risks: {paragraphs[i]}]"
                    i += 1
                merged.append(combined)
                continue
        merged.append(cur)
        i += 1
    return merged


def normalize_text(text: str) -> str:
    """Normalize Unicode ligatures and PDF whitespace while preserving paragraph structure."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", text)
    norm = norm.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    norm = norm.replace("\r\n", "\n").replace("\r", "\n")

    raw_lines = _strip_running_header_footer(norm.split("\n"))
    paragraphs: list[str] = []
    buf: list[str] = []

    for raw_line in raw_lines:
        stripped = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not stripped:
            if buf:
                paragraphs.append(_unwrap_paragraph(buf))
                buf = []
            continue
        # Start a new paragraph on explicit bullet items or section/action headers.
        if buf and (
            stripped.startswith("•")
            or _ACTION_ID_RE.match(stripped)
            or _HEADING_PREFIX_RE.match(stripped)
        ):
            paragraphs.append(_unwrap_paragraph(buf))
            buf = [stripped]
        else:
            buf.append(stripped)

    if buf:
        paragraphs.append(_unwrap_paragraph(buf))

    paragraphs = [p for p in paragraphs if p]
    paragraphs = _merge_action_blocks(paragraphs)
    return "\n\n".join(paragraphs)


def _extract_printed_page(raw_text: str) -> str | None:
    for line in (raw_text or "").splitlines():
        s = line.strip()
        if re.fullmatch(r"Page\s+(\d+|[ivx]+)", s):
            return s.split()[-1]
        if re.fullmatch(r"\d{1,3}", s):
            return s
    return None


def infer_section_for_nist_page(document_id: str, page_num: int) -> str:
    """Deterministic section label derived strictly from published document structure."""
    if document_id == "nist-ai-100-1":
        if 6 <= page_num <= 8:
            return "executive_summary"
        if 9 <= page_num <= 24:
            return "foundational"
        if page_num == 25:
            return "core_overview"
        if 26 <= page_num <= 29:
            return "govern"
        if 30 <= page_num <= 33:
            return "map"
        if 34 <= page_num <= 36:
            return "measure"
        if 37 <= page_num <= 38:
            return "manage"
        if page_num == 39:
            return "profiles"
        if 40 <= page_num <= 47:
            return "appendix"
    elif document_id == "nist-ai-600-1":
        if 5 <= page_num <= 6:
            return "introduction"
        if 7 <= page_num <= 16:
            return "gai_risks"
        if 17 <= page_num <= 26:
            return "govern"
        if 27 <= page_num <= 32:
            return "map"
        if 33 <= page_num <= 43:
            return "measure"
        if 44 <= page_num <= 50:
            return "manage"
        if 51 <= page_num <= 57:
            return "appendix"
    return "other"


def load_pdf(
    path: Path,
    document_id: str,
    source: str,
    excluded_pages: dict[int, str] | None = None,
    static_metadata: dict[str, Any] | None = None,
) -> tuple[list[DocumentPage], IngestionStats]:
    """Extract each physical PDF page, track empty/low-text pages, and normalize text."""
    reader = PdfReader(str(path))
    excluded_map = excluded_pages or {}
    base_meta = dict(static_metadata or {})
    pages: list[DocumentPage] = []
    empty_pages: list[int] = []
    low_text_pages: list[tuple[int, int]] = []
    raw_chars_total = 0
    norm_chars_total = 0

    for idx, pdf_page in enumerate(reader.pages, start=1):
        if idx in excluded_map:
            continue
        raw_layout = _extract_page_with_layout_breaks(pdf_page)
        raw_chars_total += len(raw_layout)
        printed_page = _extract_printed_page(raw_layout)
        normalized = normalize_text(raw_layout)

        if not normalized.strip():
            empty_pages.append(idx)
            continue

        norm_len = len(normalized)
        norm_chars_total += norm_len
        if norm_len < 500:
            low_text_pages.append((idx, norm_len))

        page_meta = {
            **base_meta,
            "section": infer_section_for_nist_page(document_id, idx),
            "printed_page": printed_page,
            "char_count": norm_len,
        }
        pages.append(
            DocumentPage(
                document_id=document_id,
                source=source,
                page=idx,
                text=normalized,
                metadata=page_meta,
            )
        )

    stats = IngestionStats(
        document_id=document_id,
        source=source,
        physical_pages=len(reader.pages),
        excluded_pages=dict(excluded_map),
        empty_pages=empty_pages,
        low_text_pages=low_text_pages,
        content_pages=len(pages),
        raw_chars=raw_chars_total,
        normalized_chars=norm_chars_total,
    )
    return pages, stats


def get_default_corpus_specs(data_dir: Path) -> list[DocumentSpec]:
    """Return the verified public-domain NIST AI RMF corpus specification."""
    return [
        DocumentSpec(
            document_id="nist-ai-100-1",
            title="Artificial Intelligence Risk Management Framework (AI RMF 1.0)",
            path=data_dir / "NIST.AI.100-1.pdf",
            source="starter/data/NIST.AI.100-1.pdf",
            year=2023,
            document_type="core_framework",
            license_or_permission=(
                "U.S. Federal Government work (17 U.S.C. 105, public domain in the U.S.); "
                "NIST grants non-exclusive worldwide royalty-free right to reprint/reproduce "
                "with attribution ('Republished courtesy of the National Institute of Standards and Technology')."
            ),
            source_url="https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf",
            excluded_pages={
                1: "cover_page",
                2: "title_doi_metadata",
                3: "disclaimer_and_version_notice",
                4: "table_of_contents",
                5: "list_of_figures",
                48: "back_cover",
            },
        ),
        DocumentSpec(
            document_id="nist-ai-600-1",
            title="Artificial Intelligence Risk Management Framework: Generative Artificial Intelligence Profile",
            path=data_dir / "NIST.AI.600-1.pdf",
            source="starter/data/NIST.AI.600-1.pdf",
            year=2024,
            document_type="genai_profile",
            license_or_permission=(
                "U.S. Federal Government work (17 U.S.C. 105, public domain in the U.S.); "
                "NIST grants non-exclusive worldwide royalty-free right to reprint/reproduce "
                "with attribution ('Republished courtesy of the National Institute of Standards and Technology')."
            ),
            source_url="https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf",
            excluded_pages={
                1: "cover_page",
                2: "title_doi_metadata",
                3: "about_acknowledgments_disclaimer",
                4: "table_of_contents",
                58: "references_only",
                59: "references_only",
                60: "references_only",
                61: "references_only",
                62: "references_only",
                63: "references_only",
                64: "references_only",
            },
        ),
    ]


def load_corpus(data_dir: Path) -> tuple[list[DocumentPage], list[IngestionStats]]:
    """Load and normalize every document in the verified corpus."""
    all_pages: list[DocumentPage] = []
    all_stats: list[IngestionStats] = []
    for spec in get_default_corpus_specs(data_dir):
        if not spec.path.exists():
            raise FileNotFoundError(f"Missing corpus file: {spec.path}")
        pages, stats = load_pdf(
            path=spec.path,
            document_id=spec.document_id,
            source=spec.source,
            excluded_pages=spec.excluded_pages,
            static_metadata={
                "title": spec.title,
                "year": spec.year,
                "document_type": spec.document_type,
            },
        )
        all_pages.extend(pages)
        all_stats.append(stats)
    return all_pages, all_stats
