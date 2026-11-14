# Benchmark Design & Pre-Registered Selection Criteria

- **Corpus:** NIST AI RMF 1.0 (`NIST.AI.100-1.pdf`, Jan 2023) & NIST Generative AI Profile (`NIST.AI.600-1.pdf`, Jul 2024) — 95 content pages total.
- **File:** [questions.json](questions.json) (24 questions, verified verbatim against normalized corpus pages prior to running retrieval).
- **Split (fixed before retrieval):**
  - `dev`: 18 questions (`q01`–`q18`) — 10 from `nist-ai-100-1`, 8 from `nist-ai-600-1`; includes 3 metadata filter questions (`q16`–`q18`).
  - `test`: 6 holdout questions (`q19`–`q24`) — 3 from `nist-ai-100-1`, 3 from `nist-ai-600-1`; includes 2 metadata filter questions (`q23`–`q24`).
- **Question Types Covered:**
  - `truc_tiep` (direct factual lookup)
  - `dien_dat_lai` (paraphrased semantic formulation)
  - `dieu_kien_ngoai_le` (conditional/exception rules & limitations)
  - `metadata_filter` (upfront scope constraint: `year`, `document_type`, `section`)
- **Multiple Valid Sources:** Core subcategory headings repeated verbatim in `NIST.AI.600-1` (`q06`, `q07`, `q20`) explicitly register both documents in `alternative_gold` so valid alternative citations are never penalized.

## Pre-Registered Strategy Selection Criterion (Frozen Before Viewing Test)

1. **Primary:** Maximize `Evidence Hit@3` on `dev` (and metadata filter compliance for scoped queries).
2. **Secondary:** Higher `Chunk Coherence` and `Grounding Quality` (retaining complete conditions, subcategory definitions, and action-risk links without mid-clause truncation).
3. **Tertiary:** Higher `Hit@1` and lower index footprint / embedding cost (`chunk_count`, ingest time, query latency).
