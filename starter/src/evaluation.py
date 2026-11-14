"""Evaluate retrieved top-3 chunks against gold sources/evidence and preserve full traces."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def _canonicalize(text: str) -> str:
    """Normalize Unicode, quotes/dashes, and whitespace for exact substring evidence checks."""
    s = unicodedata.normalize("NFKC", text or "").lower()
    s = (
        s.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )
    return re.sub(r"\s+", " ", s).strip()


def _matches_filter(metadata: dict[str, Any], filters: dict[str, Any] | None) -> bool | None:
    if not filters:
        return None
    for key, expected in filters.items():
        actual = metadata.get(key)
        if isinstance(expected, (list, tuple, set)):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _collect_gold_candidates(question: dict[str, Any]) -> list[tuple[str, int, str]]:
    candidates: list[tuple[str, int, str]] = [
        (
            str(question["gold_source_id"]),
            int(question["gold_page"]),
            str(question["evidence_quote"]),
        )
    ]
    for alt in question.get("alternative_gold") or []:
        candidates.append(
            (
                str(alt["source_id"]),
                int(alt["page"]),
                str(alt.get("evidence_quote") or question["evidence_quote"]),
            )
        )
    return candidates


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Check source/page + sufficient gold evidence for top-3 hits and return full trace."""
    gold_candidates = _collect_gold_candidates(question)
    filters_req = question.get("filter") or None
    top3 = hits[:3]

    hit_records: list[dict[str, Any]] = []
    first_hit_rank: int | None = None

    for idx, hit in enumerate(top3, start=1):
        hit_doc = str(hit.get("document_id") or hit.get("metadata", {}).get("document_id", ""))
        hit_page = int(hit.get("page") or hit.get("metadata", {}).get("page", -1))
        hit_text_canon = _canonicalize(str(hit.get("text", "")))
        hit_meta = dict(hit.get("metadata") or {})

        source_page_match = False
        evidence_match = False
        for g_doc, g_page, g_quote in gold_candidates:
            if hit_doc == g_doc and hit_page == g_page:
                source_page_match = True
                if _canonicalize(g_quote) in hit_text_canon:
                    evidence_match = True
                    break

        is_hit = source_page_match and evidence_match
        if is_hit and first_hit_rank is None:
            first_hit_rank = idx

        filter_match = _matches_filter(hit_meta, filters_req)
        hit_records.append(
            {
                "rank": idx,
                "chunk_id": hit.get("chunk_id"),
                "document_id": hit_doc,
                "source": hit.get("source"),
                "page": hit_page,
                "score": round(float(hit.get("score", 0.0)), 6),
                "distance": round(float(hit.get("distance", 0.0)), 6),
                "metadata": hit_meta,
                "text": hit.get("text", ""),
                "source_page_match": source_page_match,
                "evidence_match": evidence_match,
                "is_hit": is_hit,
                "filter_match": filter_match,
            }
        )

    hit_at_1 = bool(hit_records and hit_records[0]["is_hit"])
    evidence_hit_at_3 = first_hit_rank is not None
    filter_compliant: bool | None = None
    if filters_req:
        filter_compliant = bool(hit_records) and all(
            r["filter_match"] is True for r in hit_records
        )

    return {
        "question_id": question["id"],
        "split": question["split"],
        "type": question.get("type", ""),
        "question": question["question"],
        "expected_answer": question["expected_answer"],
        "gold_source_id": question["gold_source_id"],
        "gold_page": int(question["gold_page"]),
        "evidence_quote": question["evidence_quote"],
        "alternative_gold": question.get("alternative_gold") or [],
        "filter_requested": filters_req,
        "filter_applied": question.get("filter_applied"),
        "hit_at_1": hit_at_1,
        "evidence_hit_at_3": evidence_hit_at_3,
        "first_hit_rank": first_hit_rank,
        "filter_compliant": filter_compliant,
        "hits": hit_records,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Hit@1, Evidence Hit@3, and filter compliance by (strategy, split)."""
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for tr in traces:
        key = (str(tr.get("strategy", "unknown")), str(tr.get("split", "unknown")))
        buckets.setdefault(key, []).append(tr)

    summary: dict[str, Any] = {}
    for (strategy, split), items in sorted(buckets.items()):
        total = len(items)
        hit1_n = sum(1 for it in items if it["hit_at_1"])
        hit3_n = sum(1 for it in items if it["evidence_hit_at_3"])
        filter_items = [it for it in items if it.get("filter_requested")]
        filter_n = len(filter_items)
        filter_ok = sum(1 for it in filter_items if it.get("filter_compliant") is True)
        summary[f"{strategy}:{split}"] = {
            "strategy": strategy,
            "split": split,
            "total": total,
            "hit1_count": hit1_n,
            "hit1_rate": round(hit1_n / total, 4) if total else 0.0,
            "hit1_fraction": f"{hit1_n}/{total}",
            "hit3_count": hit3_n,
            "hit3_rate": round(hit3_n / total, 4) if total else 0.0,
            "hit3_fraction": f"{hit3_n}/{total}",
            "filter_total": filter_n,
            "filter_compliant_count": filter_ok if filter_n else None,
            "filter_fraction": f"{filter_ok}/{filter_n}" if filter_n else "N/A",
        }
    return summary
