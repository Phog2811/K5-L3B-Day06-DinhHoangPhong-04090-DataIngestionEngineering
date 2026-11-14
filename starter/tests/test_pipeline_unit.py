"""Unit tests for ingestion normalization, chunking strategies, and retrieval evaluation."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest

STARTER_DIR = Path(__file__).resolve().parents[1]
if str(STARTER_DIR) not in sys.path:
    sys.path.insert(0, str(STARTER_DIR))

from src.chunking import by_paragraph, fixed_size
from src.evaluation import evaluate_query, summarize
from src.ingestion import normalize_text
from src.models import DocumentPage


class PipelineUnitTests(unittest.TestCase):
    def test_normalize_text_ligatures_hyphen_and_headers(self) -> None:
        raw = (
            "NIST AI 100-1 AI RMF 1.0\n\n"
            "6\n\n"
            "2.2. Con\ufb01dentiality\n\n"
            "GAI systems conﬁdently manage envi-\nronment and third-\nparty risks.\n"
            "Page 12"
        )
        norm = normalize_text(raw)
        self.assertNotIn("NIST AI 100-1 AI RMF 1.0", norm)
        self.assertNotIn("Page 12", norm)
        self.assertIn("Confidentiality", norm)
        self.assertIn("confidently", norm)
        self.assertIn("environment", norm)
        self.assertIn("third-party", norm)

    def test_fixed_size_and_by_paragraph_keep_metadata(self) -> None:
        text = (
            "GOVERN 1.2: Trustworthy policies.\n\n"
            "GV-1.2-001: Document training data origin and provenance. [GAI Risks: Data Privacy]\n\n"
            "AI Actor Tasks: Governance and Oversight"
        )
        page = DocumentPage(
            document_id="nist-ai-600-1",
            source="starter/data/NIST.AI.600-1.pdf",
            page=18,
            text=text,
            metadata={"year": 2024, "document_type": "genai_profile", "section": "govern"},
        )
        f_chunks = fixed_size(page, size=100, overlap=20)
        p_chunks = by_paragraph(page, max_size=500)
        self.assertGreaterEqual(len(f_chunks), 2)
        self.assertEqual(len(p_chunks), 1)
        for ch in f_chunks + p_chunks:
            self.assertEqual(ch.metadata["year"], 2024)
            self.assertEqual(ch.metadata["document_type"], "genai_profile")
            self.assertEqual(ch.page, 18)

    def test_evaluate_query_accepts_alternative_gold_and_tracks_filter(self) -> None:
        q = {
            "id": "q_test",
            "split": "test",
            "type": "metadata_filter",
            "question": "What does GOVERN 6.2 require?",
            "expected_answer": "Contingency processes for high-risk third-party failures.",
            "gold_source_id": "nist-ai-100-1",
            "gold_page": 29,
            "evidence_quote": "GOVERN 6.2: Contingency processes are in place.",
            "alternative_gold": [{"source_id": "nist-ai-600-1", "page": 25}],
            "filter": {"document_type": "core_framework"},
        }
        hits = [
            {
                "chunk_id": "c1",
                "document_id": "nist-ai-600-1",
                "source": "s2",
                "page": 25,
                "score": 0.91,
                "distance": 0.09,
                "metadata": {"document_type": "genai_profile"},
                "text": "GOVERN 6.2: Contingency processes are in place.",
            }
        ]
        tr = evaluate_query(q, hits)
        tr["strategy"] = "B"
        self.assertTrue(tr["hit_at_1"])
        self.assertTrue(tr["evidence_hit_at_3"])
        self.assertFalse(tr["filter_compliant"])
        agg = summarize([tr])
        self.assertEqual(agg["B:test"]["hit1_fraction"], "1/1")
        self.assertEqual(agg["B:test"]["filter_fraction"], "0/1")


if __name__ == "__main__":
    unittest.main()
