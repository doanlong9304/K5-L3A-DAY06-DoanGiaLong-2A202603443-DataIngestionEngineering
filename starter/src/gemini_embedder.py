"""Gemini embedding adapter (google-genai SDK). Fails loudly; never returns mock vectors."""
from __future__ import annotations

import logging
import os
import time

from google import genai
from google.genai import errors, types

log = logging.getLogger(__name__)
RETRYABLE = {429, 500, 503}


class GeminiEmbedder:
    """Implements the Embedder protocol in src/embeddings.py."""

    def __init__(self, model_name: str, dimensions: int, batch_size: int = 50,
                 document_task: str = "RETRIEVAL_DOCUMENT", query_task: str = "RETRIEVAL_QUERY",
                 max_retries: int = 6) -> None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set (starter/.env or environment).")
        self.client = genai.Client(api_key=key)
        self.model_name = model_name
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.document_task = document_task
        self.query_task = query_task
        self.max_retries = max_retries
        self.api_calls = 0

    def _embed(self, texts: list[str], task: str) -> list[list[float]]:
        config = types.EmbedContentConfig(task_type=task, output_dimensionality=self.dimensions)
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i: i + self.batch_size]
            for attempt in range(self.max_retries + 1):
                try:
                    resp = self.client.models.embed_content(model=self.model_name, contents=batch, config=config)
                    self.api_calls += 1
                    break
                except errors.APIError as exc:
                    if exc.code not in RETRYABLE or attempt == self.max_retries:
                        log.error("Embedding failed (HTTP %s): %s", exc.code, exc.message)
                        raise
                    wait = min(60, 5 * 2 ** attempt)
                    log.warning("Embedding HTTP %s, retry %d in %ss", exc.code, attempt + 1, wait)
                    time.sleep(wait)
            vectors = [e.values for e in resp.embeddings]
            if len(vectors) != len(batch) or any(len(v) != self.dimensions for v in vectors):
                raise RuntimeError("Embedding API returned wrong count or dimensions.")
            out.extend(vectors)
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, self.document_task) if texts else []

    def embed_query(self, question: str) -> list[float]:
        return self._embed([question], self.query_task)[0]

    def embed_queries(self, questions: list[str]) -> list[list[float]]:
        return self._embed(questions, self.query_task) if questions else []

    def count_tokens(self, texts: list[str]) -> int:
        total = 0
        for i in range(0, len(texts), self.batch_size):
            total += self.client.models.count_tokens(
                model=self.model_name, contents=texts[i: i + self.batch_size]).total_tokens
        return total
