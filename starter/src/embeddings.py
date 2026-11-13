"""Real local embedding adapter (FastEmbed / ONNX), with no mock fallback."""
from __future__ import annotations

import os
from typing import Protocol

DEFAULT_MODEL = "BAAI/bge-base-en-v1.5"


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
    """FastEmbed adapter for a real local embedding model.

    The model is downloaded on first use. Provider/network failures raise; they
    are never replaced with random or mock vectors.
    """

    def __init__(self, model_name: str | None = None, cache_dir: str | None = None) -> None:
        self.model_name = model_name or os.getenv("EMBEDDING_MODEL") or DEFAULT_MODEL
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - environment guard
            raise RuntimeError(
                "fastembed is not installed. Run `python -m pip install fastembed` "
                "from starter/ before running the pipeline."
            ) from exc

        try:
            self.dimensions = int(TextEmbedding.get_embedding_size(self.model_name))
        except Exception:
            # Fall back to a probe only when the registry cannot resolve the model.
            self.dimensions = 0
        self._model = TextEmbedding(
            model_name=self.model_name,
            cache_dir=cache_dir or os.getenv("FASTEMBED_CACHE_DIR") or None,
        )
        if not self.dimensions:
            self.dimensions = len(self.embed_query("dimension probe"))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = [list(map(float, vector)) for vector in self._model.embed(texts)]
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding provider returned a different number of vectors than inputs")
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise RuntimeError(
                    f"Embedding dimension mismatch: expected {self.dimensions}, got {len(vector)}"
                )
        return vectors

    def count_tokens(self, texts: list[str]) -> int:
        """Count model tokens for cost/size reporting (not used for retrieval)."""
        if not texts:
            return 0
        return int(self._model.token_count(texts))

    def embed_query(self, question: str) -> list[float]:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        vectors = list(self._model.query_embed(question))
        if len(vectors) != 1:
            raise RuntimeError("Embedding provider returned an unexpected number of query vectors")
        vector = list(map(float, vectors[0]))
        if self.dimensions and len(vector) != self.dimensions:
            raise RuntimeError(
                f"Embedding dimension mismatch: expected {self.dimensions}, got {len(vector)}"
            )
        return vector
