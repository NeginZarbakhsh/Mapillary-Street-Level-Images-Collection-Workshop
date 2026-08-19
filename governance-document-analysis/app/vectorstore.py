"""Vector storage, behind one interface with three backends.

`LocalVectorStore` is real, runs in-process, and is exercised by the tests in
this repo -- it is the right choice for a single-digit-to-low-hundreds
document pilot (see RESEARCH.md). `AzureAISearchStore` and `MongoDBAtlasStore`
are written against the current SDK surface of each service but are NOT
exercised here -- this environment has no live Azure or MongoDB Atlas
resource to test against. Treat them as a correct starting point, not as
verified code; run them against a real resource before trusting them in
production.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

import numpy as np


@dataclass
class StoredChunk:
    id: str
    doc_id: str
    heading: str
    text: str
    page_start: int
    page_end: int
    score: float = 0.0


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, doc_id: str, chunks, vectors: List[List[float]]) -> None:
        ...

    @abstractmethod
    def query(self, vector: List[float], top_k: int = 6, doc_id: Optional[str] = None) -> List[StoredChunk]:
        ...


class LocalVectorStore(VectorStore):
    """In-memory cosine-similarity store. No external service, no credentials.

    This is genuinely sufficient for a pilot: the meeting's own conclusion was
    "start small". A brute-force scan over a few hundred documents' worth of
    chunks (low thousands of vectors) runs in milliseconds -- there is no
    latency or scale problem to solve yet at that volume, which is exactly the
    "does a managed service earn its cost" question RESEARCH.md answers.
    """

    def __init__(self):
        self._chunks: List[StoredChunk] = []
        self._vectors: List[np.ndarray] = []

    def upsert(self, doc_id: str, chunks, vectors: List[List[float]]) -> None:
        for chunk, vector in zip(chunks, vectors):
            self._chunks.append(
                StoredChunk(
                    id=chunk.id,
                    doc_id=doc_id,
                    heading=chunk.heading,
                    text=chunk.text,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                )
            )
            arr = np.array(vector, dtype=np.float64)
            norm = np.linalg.norm(arr)
            self._vectors.append(arr / norm if norm > 0 else arr)

    def query(self, vector: List[float], top_k: int = 6, doc_id: Optional[str] = None) -> List[StoredChunk]:
        if not self._vectors:
            return []
        q = np.array(vector, dtype=np.float64)
        q_norm = np.linalg.norm(q)
        if q_norm > 0:
            q = q / q_norm

        scored = []
        for chunk, v in zip(self._chunks, self._vectors):
            if doc_id is not None and chunk.doc_id != doc_id:
                continue
            score = float(np.dot(q, v))
            scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        results = []
        for score, chunk in scored[:top_k]:
            results.append(StoredChunk(**{**chunk.__dict__, "score": score}))
        return results


class AzureAISearchStore(VectorStore):
    """Reference implementation against azure-search-documents.

    UNEXERCISED: needs a live Azure AI Search resource (AZURE_SEARCH_ENDPOINT,
    AZURE_SEARCH_KEY) to run. Requires an index already created with a
    `contentVector` field sized to your embedder's dimension -- see
    RESEARCH.md for the index schema and `az search index create` guidance.
    """

    def __init__(self):
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
        key = os.environ["AZURE_SEARCH_KEY"]
        index = os.environ.get("AZURE_SEARCH_INDEX", "governance-chunks")
        self.client = SearchClient(endpoint, index, AzureKeyCredential(key))

    def upsert(self, doc_id: str, chunks, vectors: List[List[float]]) -> None:
        docs = [
            {
                "id": chunk.id,
                "doc_id": doc_id,
                "heading": chunk.heading,
                "content": chunk.text,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "contentVector": vector,
            }
            for chunk, vector in zip(chunks, vectors)
        ]
        self.client.upload_documents(docs)

    def query(self, vector: List[float], top_k: int = 6, doc_id: Optional[str] = None) -> List[StoredChunk]:
        from azure.search.documents.models import VectorizedQuery

        vector_query = VectorizedQuery(vector=vector, k_nearest_neighbors=top_k, fields="contentVector")
        results = self.client.search(
            search_text=None,
            vector_queries=[vector_query],
            filter=f"doc_id eq '{doc_id}'" if doc_id else None,
            select=["id", "doc_id", "heading", "content", "page_start", "page_end"],
        )
        return [
            StoredChunk(
                id=r["id"], doc_id=r["doc_id"], heading=r["heading"], text=r["content"],
                page_start=r["page_start"], page_end=r["page_end"], score=r.get("@search.score", 0.0),
            )
            for r in results
        ]


class MongoDBAtlasStore(VectorStore):
    """Reference implementation against pymongo + Atlas Vector Search.

    UNEXERCISED: needs a live Atlas cluster (MONGODB_URI) with a
    `$vectorSearch` index already created on the `embedding` field, sized to
    your embedder's dimension -- see RESEARCH.md.
    """

    def __init__(self):
        from pymongo import MongoClient

        client = MongoClient(os.environ["MONGODB_URI"])
        db = client[os.environ.get("MONGODB_DB", "governance")]
        self.collection = db[os.environ.get("MONGODB_COLLECTION", "chunks")]
        self.index_name = os.environ.get("MONGODB_VECTOR_INDEX", "vector_index")

    def upsert(self, doc_id: str, chunks, vectors: List[List[float]]) -> None:
        docs = [
            {
                "_id": chunk.id,
                "doc_id": doc_id,
                "heading": chunk.heading,
                "text": chunk.text,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "embedding": vector,
            }
            for chunk, vector in zip(chunks, vectors)
        ]
        for doc in docs:
            self.collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    def query(self, vector: List[float], top_k: int = 6, doc_id: Optional[str] = None) -> List[StoredChunk]:
        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.index_name,
                    "path": "embedding",
                    "queryVector": vector,
                    "numCandidates": max(top_k * 10, 100),
                    "limit": top_k,
                    **({"filter": {"doc_id": doc_id}} if doc_id else {}),
                }
            },
            {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
        ]
        results = self.collection.aggregate(pipeline)
        return [
            StoredChunk(
                id=r["_id"], doc_id=r["doc_id"], heading=r["heading"], text=r["text"],
                page_start=r["page_start"], page_end=r["page_end"], score=r.get("score", 0.0),
            )
            for r in results
        ]


def get_vector_store() -> VectorStore:
    backend = os.environ.get("VECTOR_STORE", "local")
    if backend == "azure":
        return AzureAISearchStore()
    if backend == "mongodb":
        return MongoDBAtlasStore()
    return LocalVectorStore()
