"""Real Gemini embedding adapter via the official google-genai SDK."""
from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Protocol

from google import genai
from google.genai import errors as genai_errors
from google.genai import types


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class MyEmbedder:
    """Google Gemini embedding provider adapter (no mock fallback)."""

    _KNOWN_DIMENSIONS = {
        "gemini-embedding-001": 3072,
    }

    def __init__(
        self,
        model_name: str = "gemini-embedding-001",
        api_key: str | None = None,
        output_dimensionality: int | None = None,
        batch_size: int = 18,
        max_retries: int = 7,
        max_items_per_minute: int = 90,
        max_tokens_per_minute: int = 22000,
        window_seconds: float = 63.0,
        cache_path: str | Path | None = None,
    ) -> None:
        resolved_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        if not resolved_key:
            raise RuntimeError("GEMINI_API_KEY is missing. Set it in starter/.env before running.")
        if batch_size <= 0 or batch_size > 100:
            raise ValueError("batch_size must be between 1 and 100")

        self.model_name = model_name.strip()
        self.output_dimensionality = output_dimensionality
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.max_items_per_minute = max_items_per_minute
        self.max_tokens_per_minute = max_tokens_per_minute
        self.window_seconds = window_seconds
        self._history: deque[tuple[float, int, int]] = deque()

        # Disable silent inner SDK retry loops so rate-limit waits honor server retryDelay cleanly.
        self.client = genai.Client(
            api_key=resolved_key,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
        )

        self.total_api_calls = 0
        self.total_texts_embedded = 0
        self.total_chars_embedded = 0
        self.total_api_seconds = 0.0
        self.cache_hits = 0
        self.errors: list[str] = []

        if output_dimensionality is not None:
            self.dimensions = int(output_dimensionality)
        elif self.model_name in self._KNOWN_DIMENSIONS:
            self.dimensions = self._KNOWN_DIMENSIONS[self.model_name]
        else:
            probe = self._embed_batch(["dimension probe"], task_type="RETRIEVAL_DOCUMENT")
            self.dimensions = len(probe[0])

        raw_cache = cache_path if cache_path is not None else os.getenv("EMBEDDING_CACHE_PATH", "").strip()
        self.cache_path: Path | None = Path(raw_cache) if raw_cache else None
        self._cache: dict[str, list[float]] = {}
        self._load_cache()

    def _cache_key(self, task_type: str, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"{self.model_name}|{self.dimensions}|{task_type}|{digest}"

    def _load_cache(self) -> None:
        if self.cache_path is None or not self.cache_path.exists():
            return
        with self.cache_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                rec = json.loads(raw)
                key = rec.get("key")
                vec = rec.get("vector")
                if isinstance(key, str) and isinstance(vec, list) and len(vec) == self.dimensions:
                    self._cache[key] = [float(v) for v in vec]

    def _append_cache(self, keys_and_vectors: list[tuple[str, list[float]]]) -> None:
        if self.cache_path is None or not keys_and_vectors:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("a", encoding="utf-8") as handle:
            for key, vec in keys_and_vectors:
                handle.write(json.dumps({"key": key, "vector": vec}) + "\n")
            handle.flush()

    @staticmethod
    def _estimate_tokens(texts: list[str]) -> int:
        return sum(max(1, (len(t) + 2) // 3) for t in texts)

    @staticmethod
    def _parse_retry_seconds(exc: Exception) -> float | None:
        text = str(exc)
        match_float = re.search(r"Please retry in ([\d.]+)s", text)
        if match_float:
            return float(match_float.group(1))
        match_int = re.search(r"'retryDelay':\s*'(\d+)s'", text)
        if match_int:
            return float(match_int.group(1))
        return None

    def _throttle(self, texts: list[str]) -> None:
        """Honor Free Tier sliding-window limits before issuing an API batch."""
        n_items = len(texts)
        est_tokens = self._estimate_tokens(texts)
        while True:
            now = time.monotonic()
            while self._history and (now - self._history[0][0]) >= self.window_seconds:
                self._history.popleft()
            used_items = sum(entry[1] for entry in self._history)
            used_tokens = sum(entry[2] for entry in self._history)
            fits_items = (used_items + n_items) <= self.max_items_per_minute
            fits_tokens = (used_tokens + est_tokens) <= self.max_tokens_per_minute
            if not self._history or (fits_items and fits_tokens):
                self._history.append((now, n_items, est_tokens))
                return
            wait_sec = max(0.5, self.window_seconds - (now - self._history[0][0]) + 0.5)
            if wait_sec >= 3.0:
                print(
                    f"[embed] Rate-limit window wait {wait_sec:.1f}s "
                    f"(items={used_items}/{self.max_items_per_minute}, est_tokens={used_tokens}/{self.max_tokens_per_minute})",
                    flush=True,
                )
            time.sleep(wait_sec)

    def _build_config(self, task_type: str) -> types.EmbedContentConfig:
        if self.output_dimensionality is not None:
            return types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.output_dimensionality,
            )
        return types.EmbedContentConfig(task_type=task_type)

    def _embed_batch(self, texts: list[str], task_type: str) -> list[list[float]]:
        config = self._build_config(task_type)
        delay = 5.0
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            self._throttle(texts)
            t0 = time.perf_counter()
            try:
                response = self.client.models.embed_content(
                    model=self.model_name,
                    contents=texts,
                    config=config,
                )
                elapsed = time.perf_counter() - t0
                if not response.embeddings or len(response.embeddings) != len(texts):
                    raise RuntimeError(
                        f"Expected {len(texts)} embeddings from {self.model_name}, "
                        f"got {0 if not response.embeddings else len(response.embeddings)}"
                    )
                vectors = [list(map(float, emb.values)) for emb in response.embeddings]
                self.total_api_calls += 1
                self.total_texts_embedded += len(texts)
                self.total_chars_embedded += sum(len(t) for t in texts)
                self.total_api_seconds += elapsed
                return vectors
            except genai_errors.APIError as exc:
                last_exc = exc
                status = getattr(exc, "code", None)
                msg = f"attempt={attempt} status={status} type={type(exc).__name__}"
                self.errors.append(msg)
                if "PerDay" in str(exc):
                    raise RuntimeError(f"Gemini daily quota exhausted ({msg}): {exc}") from exc
                if status in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    parsed = self._parse_retry_seconds(exc)
                    sleep_sec = min(max(delay, (parsed + 3.0) if parsed else delay), 75.0)
                    print(f"[embed] {msg}; sleeping {sleep_sec:.1f}s before retry", flush=True)
                    time.sleep(sleep_sec)
                    delay = min(delay * 2.0, 65.0)
                    continue
                raise RuntimeError(f"Gemini embedding API failed ({msg}): {exc}") from exc
            except Exception as exc:
                last_exc = exc
                msg = f"attempt={attempt} type={type(exc).__name__}"
                self.errors.append(msg)
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay = min(delay * 2.0, 60.0)
                    continue
                raise RuntimeError(f"Gemini embedding request failed ({msg}): {exc}") from exc

        raise RuntimeError(f"Gemini embedding failed after {self.max_retries} retries: {last_exc}")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed document chunks in batches using RETRIEVAL_DOCUMENT task type."""
        if not texts:
            return []

        unique_missing: list[tuple[str, str]] = []
        seen_missing: set[str] = set()
        for text in texts:
            key = self._cache_key("RETRIEVAL_DOCUMENT", text)
            if key in self._cache:
                self.cache_hits += 1
            elif key not in seen_missing:
                seen_missing.add(key)
                unique_missing.append((key, text))

        total_batches = (len(unique_missing) + self.batch_size - 1) // self.batch_size
        for batch_idx, start in enumerate(range(0, len(unique_missing), self.batch_size), start=1):
            pair_batch = unique_missing[start : start + self.batch_size]
            batch_texts = [t for _, t in pair_batch]
            vecs = self._embed_batch(batch_texts, task_type="RETRIEVAL_DOCUMENT")
            to_persist: list[tuple[str, list[float]]] = []
            for (key, _), vec in zip(pair_batch, vecs):
                if len(vec) != self.dimensions:
                    raise ValueError(
                        f"Vector dimension mismatch: expected {self.dimensions}, got {len(vec)}"
                    )
                self._cache[key] = vec
                to_persist.append((key, vec))
            self._append_cache(to_persist)
            print(
                f"[embed] RETRIEVAL_DOCUMENT batch {batch_idx}/{total_batches} "
                f"({min(start + self.batch_size, len(unique_missing))}/{len(unique_missing)} unique chunks)",
                flush=True,
            )

        return [self._cache[self._cache_key("RETRIEVAL_DOCUMENT", text)] for text in texts]

    def embed_query(self, question: str) -> list[float]:
        """Embed a retrieval query using RETRIEVAL_QUERY task type."""
        if not question.strip():
            raise ValueError("Query string must not be empty")
        key = self._cache_key("RETRIEVAL_QUERY", question)
        if key in self._cache:
            self.cache_hits += 1
            return list(self._cache[key])
        vecs = self._embed_batch([question], task_type="RETRIEVAL_QUERY")
        if len(vecs[0]) != self.dimensions:
            raise ValueError(
                f"Query vector dimension mismatch: expected {self.dimensions}, got {len(vecs[0])}"
            )
        self._cache[key] = vecs[0]
        self._append_cache([(key, vecs[0])])
        return list(vecs[0])
