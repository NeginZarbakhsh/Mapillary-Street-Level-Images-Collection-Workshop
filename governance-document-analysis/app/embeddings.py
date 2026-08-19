"""Pluggable embedding providers.

Anthropic does not run an embeddings endpoint; Voyage AI is Anthropic's
recommended embedding partner (voyage-3-large leads MTEB among options
benchmarked for Claude use, per Voyage's published pricing/positioning) and is
the default here. Swap providers by implementing `Embedder`.

`LocalHashEmbedder` exists only so the retrieval pipeline (chunk -> embed ->
store -> query -> rank) can be run and tested in this environment without any
API key. It is a deterministic hashing vectoriser, not a real semantic
embedding -- do not use it for anything but exercising the plumbing. This is
the same "prove the mechanism, be honest about what still needs a real
provider" split used for the Claude call itself.
"""

from __future__ import annotations

import hashlib
import os
import re
from abc import ABC, abstractmethod
from typing import List

import numpy as np


class Embedder(ABC):
    dimension: int

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        ...


class VoyageEmbedder(Embedder):
    """Real semantic embeddings via Voyage AI. Requires VOYAGE_API_KEY."""

    def __init__(self, model: str = None):
        import voyageai  # local import: optional dependency

        self.model = model or os.environ.get("EMBEDDING_MODEL", "voyage-3-large")
        self.client = voyageai.Client()  # reads VOYAGE_API_KEY from env
        self.dimension = 1024  # voyage-3-large default; overwritten after first call

    def embed(self, texts: List[str]) -> List[List[float]]:
        # input_type="document" vs "query" measurably improves retrieval quality
        # for Voyage models -- callers pass the right one via embed_query/embed_docs.
        result = self.client.embed(texts, model=self.model, input_type="document")
        if result.embeddings:
            self.dimension = len(result.embeddings[0])
        return result.embeddings


class LocalHashEmbedder(Embedder):
    """Deterministic, dependency-free, non-semantic. Testing only."""

    def __init__(self, dimension: int = 256):
        self.dimension = dimension

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> List[float]:
        vector = np.zeros(self.dimension, dtype=np.float64)
        for token in re.findall(r"\w+", text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector /= norm
        return vector.tolist()


def get_embedder() -> Embedder:
    if os.environ.get("VOYAGE_API_KEY"):
        return VoyageEmbedder()
    return LocalHashEmbedder()
