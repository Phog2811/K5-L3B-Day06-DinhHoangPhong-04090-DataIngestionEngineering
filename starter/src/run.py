"""Day 06 ingestion, embedding, storage, and retrieval benchmark pipeline."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import time
from typing import Any

from dotenv import load_dotenv

from .chunking import by_paragraph, fixed_size
from .embeddings import MyEmbedder
from .evaluation import _canonicalize, evaluate_query, summarize
from .ingestion import get_default_corpus_specs, load_corpus
from .models import Chunk, DocumentPage
from .store import PgVectorStore

REPO_ROOT = Path(__file__).resolve().parents[2]
STARTER_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = STARTER_DIR / "data"
MANIFEST_PATH = REPO_ROOT / "data" / "manifest.json"
BENCHMARK_PATH = REPO_ROOT / "benchmark" / "questions.json"
RESULTS_DIR = REPO_ROOT / "results"
STARTER_RESULTS_DIR = STARTER_DIR / "results"

# Pre-registered 10 source anchor locations (chosen deterministically across both docs before retrieval).
COHERENCE_ANCHORS: list[dict[str, Any]] = [
    {"loc_id": "L01", "document_id": "nist-ai-100-1", "page": 6, "anchor": "The AI RMF refers to an AI system as an engineered or machine-based system"},
    {"loc_id": "L02", "document_id": "nist-ai-100-1", "page": 9, "anchor": "Risk refers to the composite measure of an event’s probability of occurring"},
    {"loc_id": "L03", "document_id": "nist-ai-100-1", "page": 12, "anchor": "While the AI RMF can be used to prioritize risk, it does not prescribe risk tolerance"},
    {"loc_id": "L04", "document_id": "nist-ai-100-1", "page": 18, "anchor": "Validation is the “confirmation, through the provision of objective evidence"},
    {"loc_id": "L05", "document_id": "nist-ai-100-1", "page": 28, "anchor": "GOVERN 1.6: Mechanisms are in place to inventory AI systems"},
    {"loc_id": "L06", "document_id": "nist-ai-600-1", "page": 5, "anchor": "A profile is an implementation of the AI RMF functions, categories, and subcategories"},
    {"loc_id": "L07", "document_id": "nist-ai-600-1", "page": 10, "anchor": "Confabulation” refers to a phenomenon in which GAI systems generate and confidently present"},
    {"loc_id": "L08", "document_id": "nist-ai-600-1", "page": 18, "anchor": "GV-1.2-001: Establish transparency policies and processes for documenting"},
    {"loc_id": "L09", "document_id": "nist-ai-600-1", "page": 28, "anchor": "MAP 2.1: The specific tasks and methods used to implement the tasks"},
    {"loc_id": "L10", "document_id": "nist-ai-600-1", "page": 44, "anchor": "MANAGE 1.3: Responses to the AI risks deemed high priority"},
]


def _chunk_stats(chunks: list[Chunk]) -> dict[str, Any]:
    lengths = sorted(len(c.text) for c in chunks)
    n = len(lengths)
    if not n:
        return {"count": 0, "total_chars": 0}
    return {
        "count": n,
        "total_chars": sum(lengths),
        "min_chars": lengths[0],
        "p50_chars": float(statistics.median(lengths)),
        "mean_chars": round(sum(lengths) / n, 1),
        "p95_chars": lengths[min(int(0.95 * n), n - 1)],
        "max_chars": lengths[-1],
    }


def build_manifest(pages: list[DocumentPage], stats_list: list[Any]) -> dict[str, Any]:
    """Create a verifiable corpus manifest with exact page counts and license info."""
    specs = {s.document_id: s for s in get_default_corpus_specs(DATA_DIR)}
    documents_entry: list[dict[str, Any]] = []
    for st in stats_list:
        spec = specs[st.document_id]
        doc_pages = [p for p in pages if p.document_id == st.document_id]
        documents_entry.append(
            {
                "source_id": st.document_id,
                "title": spec.title,
                "source_path_or_url": {
                    "local_path": st.source,
                    "official_url": spec.source_url,
                },
                "year": spec.year,
                "document_type": spec.document_type,
                "license_or_permission": spec.license_or_permission,
                "accessed_at": "2026-11-14T09:35:00+07:00",
                "physical_page_count": st.physical_pages,
                "excluded_pages": {str(k): v for k, v in st.excluded_pages.items()},
                "included_page_count": st.content_pages,
                "empty_pages": st.empty_pages,
                "low_text_pages": [
                    {"page": p_num, "normalized_chars": c_len}
                    for p_num, c_len in st.low_text_pages
                ],
                "total_normalized_chars": st.normalized_chars,
                "pages": [
                    {
                        "page_id": f"{p.document_id}:p{p.page:03d}",
                        "physical_page": p.page,
                        "printed_page": p.metadata.get("printed_page"),
                        "section": p.metadata.get("section"),
                        "normalized_chars": len(p.text),
                    }
                    for p in doc_pages
                ],
            }
        )

    total_physical = sum(s.physical_pages for s in stats_list)
    total_content = sum(s.content_pages for s in stats_list)
    return {
        "corpus_name": "NIST AI Risk Management Framework (AI RMF 1.0 + Generative AI Profile)",
        "counting_rule": (
            "Each PDF page is indexed by 1-based physical page number. Cover pages, title/DOI pages, "
            "disclaimers/acknowledgments, Table of Contents, List of Figures, back covers, and "
            "reference-only pages (Appendix B of NIST AI 600-1) are excluded. Only pages containing "
            "substantive domain content are counted."
        ),
        "total_physical_pages": total_physical,
        "total_content_pages": total_content,
        "total_normalized_chars": sum(s.normalized_chars for s in stats_list),
        "documents": documents_entry,
    }


def validate_benchmark(
    questions: list[dict[str, Any]],
    pages: list[DocumentPage],
) -> None:
    """Verify split counts, metadata filter coverage, and ground-truth quotes on normalized pages."""
    page_lookup = {(p.document_id, p.page): _canonicalize(p.text) for p in pages}
    if len(questions) < 20:
        raise ValueError(f"Benchmark requires >= 20 questions, found {len(questions)}")

    dev_q = [q for q in questions if q.get("split") == "dev"]
    test_q = [q for q in questions if q.get("split") == "test"]
    if len(dev_q) < 15 or len(test_q) < 5:
        raise ValueError(f"Split must be >= 15 dev and >= 5 test, got {len(dev_q)} dev / {len(test_q)} test")

    filter_q = [q for q in questions if q.get("filter")]
    test_filter_q = [q for q in test_q if q.get("filter")]
    if len(filter_q) < 3 or len(test_filter_q) < 1:
        raise ValueError(
            f"Need >= 3 filter questions total and >= 1 in test; got {len(filter_q)} total / {len(test_filter_q)} in test"
        )

    for q in questions:
        key = (q["gold_source_id"], int(q["gold_page"]))
        if key not in page_lookup:
            raise ValueError(f"Question {q['id']} references missing gold page {key}")
        quote_canon = _canonicalize(q["evidence_quote"])
        if quote_canon not in page_lookup[key]:
            raise ValueError(
                f"Question {q['id']} evidence_quote not found on normalized {key}: {q['evidence_quote']!r}"
            )
        for alt in q.get("alternative_gold") or []:
            akey = (alt["source_id"], int(alt["page"]))
            aquote = _canonicalize(alt.get("evidence_quote") or q["evidence_quote"])
            if akey not in page_lookup or aquote not in page_lookup[akey]:
                raise ValueError(f"Question {q['id']} invalid alternative_gold {akey}")


def _extract_coherence_samples(
    strategy_chunks: dict[str, list[Chunk]],
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for anc in COHERENCE_ANCHORS:
        anchor_canon = _canonicalize(anc["anchor"])
        entry: dict[str, Any] = {
            "loc_id": anc["loc_id"],
            "document_id": anc["document_id"],
            "page": anc["page"],
            "anchor_text": anc["anchor"],
            "strategies": {},
        }
        for strat, chunks in strategy_chunks.items():
            matched = [
                c
                for c in chunks
                if c.document_id == anc["document_id"]
                and c.page == anc["page"]
                and anchor_canon in _canonicalize(c.text)
            ]
            if not matched:
                # Fall back to partial prefix match (e.g. if fixed-size split the anchor across chunks)
                prefix = anchor_canon[:28]
                matched = [
                    c
                    for c in chunks
                    if c.document_id == anc["document_id"]
                    and c.page == anc["page"]
                    and prefix in _canonicalize(c.text)
                ]
            entry["strategies"][strat] = [
                {
                    "chunk_id": c.chunk_id,
                    "char_len": len(c.text),
                    "section": c.metadata.get("section"),
                    "text": c.text,
                }
                for c in matched
            ]
        samples.append(entry)
    return samples


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Day 06 Data Ingestion pipeline & benchmark.")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Ingest corpus, build manifest, and export normalized page cache without calling API/DB.",
    )
    parser.add_argument(
        "--cache-file",
        default=None,
        help="Optional JSONL path to persist/resume verified Gemini API vectors.",
    )
    args = parser.parse_args()

    load_dotenv(STARTER_DIR / ".env")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    STARTER_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Load & normalize corpus; validate >= 30 useful content pages.
    t_ingest_start = time.perf_counter()
    pages, stats_list = load_corpus(DATA_DIR)
    t_ingest_sec = round(time.perf_counter() - t_ingest_start, 3)

    total_content_pages = sum(s.content_pages for s in stats_list)
    if total_content_pages < 30:
        raise RuntimeError(f"Corpus has only {total_content_pages} content pages (< 30 required).")

    manifest = build_manifest(pages, stats_list)
    _write_json(MANIFEST_PATH, manifest)

    # Build chunks for A (fixed_size) and B/C (by_paragraph).
    chunks_a = [c for p in pages for c in fixed_size(p, size=500, overlap=50)]
    chunks_b = [c for p in pages for c in by_paragraph(p, max_size=1100)]
    chunks_c = chunks_b  # Controlled comparison B-C: identical chunks and metadata!

    chunk_stats = {
        "A": _chunk_stats(chunks_a),
        "B": _chunk_stats(chunks_b),
        "C": _chunk_stats(chunks_c),
    }

    ingestion_log = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_physical_pages": manifest["total_physical_pages"],
        "total_content_pages": manifest["total_content_pages"],
        "ingestion_seconds": t_ingest_sec,
        "documents": [
            {
                "document_id": s.document_id,
                "physical_pages": s.physical_pages,
                "content_pages": s.content_pages,
                "excluded_pages_count": len(s.excluded_pages),
                "empty_pages": s.empty_pages,
                "low_text_pages": [{"page": pg, "chars": ch} for pg, ch in s.low_text_pages],
                "raw_chars": s.raw_chars,
                "normalized_chars": s.normalized_chars,
                "errors": s.errors,
            }
            for s in stats_list
        ],
        "chunk_stats": chunk_stats,
    }
    _write_json(RESULTS_DIR / "ingestion_log.json", ingestion_log)

    if args.prepare_only:
        _write_json(
            STARTER_RESULTS_DIR / "normalized_pages_cache.json",
            [
                {
                    "document_id": p.document_id,
                    "source": p.source,
                    "page": p.page,
                    "metadata": p.metadata,
                    "text": p.text,
                }
                for p in pages
            ],
        )
        print(
            f"Prepared manifest ({total_content_pages} content pages) and chunk stats: "
            f"A={chunk_stats['A']['count']} chunks, B/C={chunk_stats['B']['count']} chunks."
        )
        return

    # 2. Load & strictly validate benchmark before running retrieval.
    if not BENCHMARK_PATH.exists():
        raise FileNotFoundError(f"Benchmark not found at {BENCHMARK_PATH}")
    questions: list[dict[str, Any]] = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    validate_benchmark(questions, pages)

    # 3. Initialize real Gemini embedder and PostgreSQL + pgvector store.
    database_url = os.getenv("DATABASE_URL", "").strip()
    model_name = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001").strip() or "gemini-embedding-001"
    embedder = MyEmbedder(model_name=model_name, cache_path=args.cache_file)
    store = PgVectorStore(database_url=database_url, dimensions=embedder.dimensions)
    store.create_schema()

    # 4. Embed & upsert strategies A, B, C (B and C share exact embeddings/chunks).
    t_embed_a0 = time.perf_counter()
    vectors_a = embedder.embed_documents([c.text for c in chunks_a])
    t_embed_a = time.perf_counter() - t_embed_a0

    t_upsert_a0 = time.perf_counter()
    store.upsert("A", chunks_a, vectors_a)
    t_upsert_a = time.perf_counter() - t_upsert_a0
    upsert_a_stats = dict(store.last_upsert_stats)

    t_embed_b0 = time.perf_counter()
    vectors_b = embedder.embed_documents([c.text for c in chunks_b])
    t_embed_b = time.perf_counter() - t_embed_b0

    t_upsert_b0 = time.perf_counter()
    store.upsert("B", chunks_b, vectors_b)
    t_upsert_b = time.perf_counter() - t_upsert_b0
    upsert_b_stats = dict(store.last_upsert_stats)

    t_upsert_c0 = time.perf_counter()
    store.upsert("C", chunks_c, vectors_b)
    t_upsert_c = time.perf_counter() - t_upsert_c0
    upsert_c_stats = dict(store.last_upsert_stats)

    # 5. Verify idempotency: re-upsert Strategy B unchanged + verify obsolete chunk pruning.
    count_b_before = store.count_chunks("B")
    store.upsert("B", chunks_b, vectors_b)
    count_b_after = store.count_chunks("B")
    reingest_b_stats = dict(store.last_upsert_stats)

    # Verify obsolete chunk removal when a document is updated with fewer chunks.
    probe_strategy = "__idempotency_probe__"
    store.upsert(probe_strategy, chunks_b[:3], vectors_b[:3])
    probe_initial = store.count_chunks(probe_strategy)
    store.upsert(probe_strategy, chunks_b[:1], vectors_b[:1])
    probe_after_shrink = store.count_chunks(probe_strategy)
    probe_deleted_obsolete = store.last_upsert_stats.get("deleted_obsolete", 0)
    store.upsert(probe_strategy, [], [])
    probe_cleaned = store.count_chunks(probe_strategy)

    idempotency_report = {
        "strategy_b_before_reupsert": count_b_before,
        "strategy_b_after_reupsert": count_b_after,
        "no_duplicates_confirmed": (count_b_before == count_b_after == len(chunks_b)),
        "strategy_b_reupsert_stats": reingest_b_stats,
        "obsolete_pruning_probe": {
            "initial_count": probe_initial,
            "after_doc_shrink_count": probe_after_shrink,
            "deleted_obsolete_rows": probe_deleted_obsolete,
            "after_cleanup_count": probe_cleaned,
        },
    }

    # 6. Embed queries once so A, B, C search with the identical query vector.
    t_qembed0 = time.perf_counter()
    query_vectors: dict[str, list[float]] = {
        q["id"]: embedder.embed_query(q["question"]) for q in questions
    }
    t_qembed = time.perf_counter() - t_qembed0

    # 7. Run retrieval top-3 for A, B, C.
    strategies_cfg = {
        "A": {"use_filter": False, "chunking": "fixed_size(500, overlap=50)"},
        "B": {"use_filter": False, "chunking": "by_paragraph(max_size=1100)"},
        "C": {"use_filter": True, "chunking": "by_paragraph(max_size=1100)"},
    }

    all_traces: list[dict[str, Any]] = []
    query_timings: dict[str, float] = {}
    for strat_name, cfg in strategies_cfg.items():
        t_q0 = time.perf_counter()
        for q in questions:
            filters_applied = q.get("filter") if cfg["use_filter"] and q.get("filter") else None
            hits = store.search(
                strategy=strat_name,
                query_vector=query_vectors[q["id"]],
                top_k=3,
                filters=filters_applied,
            )
            q_for_eval = dict(q)
            q_for_eval["filter_applied"] = filters_applied
            trace = evaluate_query(q_for_eval, hits)
            trace["strategy"] = strat_name
            all_traces.append(trace)
        query_timings[strat_name] = round(time.perf_counter() - t_q0, 4)

    summary = summarize(all_traces)
    run_report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": {
            "provider": "Google Gemini API (google-genai)",
            "model_name": embedder.model_name,
            "dimensions": embedder.dimensions,
            "document_task_type": "RETRIEVAL_DOCUMENT",
            "query_task_type": "RETRIEVAL_QUERY",
            "total_api_calls": embedder.total_api_calls,
            "total_texts_embedded": embedder.total_texts_embedded,
            "total_chars_embedded": embedder.total_chars_embedded,
            "total_api_seconds": round(embedder.total_api_seconds, 3),
            "cache_hits": embedder.cache_hits,
            "estimated_cost_usd": 0.0,
            "cost_note": "Gemini API free tier ($0.00 actual spend); list reference rate is $0.15/1M tokens.",
            "api_errors_retried": embedder.errors,
        },
        "vector_store": {
            "engine": "PostgreSQL 16 + pgvector 0.8.7",
            "metric": "cosine_distance (<=>), similarity = 1 - distance",
            "search_mode": "exact sequential scan (no approximate HNSW/IVFFlat index)",
            "upsert_stats": {
                "A": upsert_a_stats,
                "B": upsert_b_stats,
                "C": upsert_c_stats,
            },
        },
        "timings_seconds": {
            "ingestion": t_ingest_sec,
            "embed_A": round(t_embed_a, 3),
            "upsert_A": round(t_upsert_a, 3),
            "embed_B": round(t_embed_b, 3),
            "upsert_B": round(t_upsert_b, 3),
            "embed_C_reused_from_B": 0.0,
            "upsert_C": round(t_upsert_c, 3),
            "embed_queries": round(t_qembed, 3),
            "query_search": query_timings,
        },
        "chunk_stats": chunk_stats,
        "idempotency": idempotency_report,
        "metrics_summary": summary,
    }

    coherence_samples = _extract_coherence_samples({"A": chunks_a, "B": chunks_b, "C": chunks_c})
    test_traces = [t for t in all_traces if t["split"] == "test"]

    _write_json(RESULTS_DIR / "traces_all.json", all_traces)
    _write_json(RESULTS_DIR / "summary_metrics.json", run_report)
    _write_json(RESULTS_DIR / "coherence_samples.json", coherence_samples)
    _write_json(RESULTS_DIR / "test_traces_for_audit.json", test_traces)
    _write_json(STARTER_RESULTS_DIR / "summary_metrics.json", run_report)

    print("Pipeline completed successfully.")
    for key, val in summary.items():
        print(
            f"  {key:8s} | Hit@1={val['hit1_fraction']} ({val['hit1_rate']:.2%}) | "
            f"Evidence Hit@3={val['hit3_fraction']} ({val['hit3_rate']:.2%}) | "
            f"Filter={val['filter_fraction']}"
        )


if __name__ == "__main__":
    main()
