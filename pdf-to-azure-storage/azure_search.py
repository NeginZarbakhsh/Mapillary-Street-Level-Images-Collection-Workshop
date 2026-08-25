"""Azure AI Search as the vector store, replacing the local index.json file
and Voyage AI embeddings -- moving the pipeline fully onto Azure.

This exists for one specific reason: Voyage AI throttles accounts with no
payment method on file to 10,000 tokens per minute, which a single
90-chunk document blows through in one call (see the RateLimitError this
was built to route around). Rather than fight that limit, this replaces
both pieces it touches:

    Voyage AI embeddings  -> Azure OpenAI embeddings (same resource you
                              already set up for answering questions)
    local index.json file -> a real Azure AI Search vector index

Unlike Azure Blob Storage (Step 2) or Azure OpenAI (Step 7's answer engine),
Azure AI Search is NOT a "create it and go" service -- it needs an index
with a defined schema before you can store anything in it. Run the setup
step below once, before the first `build`.

Usage:
    python3 azure_search.py setup
        One-time: creates the vector index. Run this once, or again after
        deleting the index, or if you change EMBEDDING_DIMENSIONS.

Then set VECTOR_STORE=azure in .env, and vector_search.py's `build` and
`ask` commands use this automatically instead of the local file.

UNEXERCISED against a live resource -- there's no Azure AI Search instance
in this environment to test against. Written against the current SDK
surface (checked directly against the installed azure-search-documents
package, and against Microsoft's own sample repository for the index
schema shape) -- not from memory. Test the `setup` step against your real
resource before trusting the rest.
"""

from __future__ import annotations

import os
import sys
from typing import List

from dotenv import load_dotenv

load_dotenv()

# Must match whatever embedding model AZURE_OPENAI_EMBEDDING_DEPLOYMENT points
# at: 1536 for text-embedding-3-small / text-embedding-ada-002, 3072 for
# text-embedding-3-large. Get this wrong and index creation succeeds but every
# upload fails with a dimension-mismatch error -- check your deployed model's
# dimensions in Azure AI Foundry before changing this.
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "1536"))


def _search_index_client():
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents.indexes import SearchIndexClient

    endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
    key = os.environ.get("AZURE_SEARCH_KEY")
    if not endpoint or not key:
        print(
            "AZURE_SEARCH_ENDPOINT and/or AZURE_SEARCH_KEY are not set. Get them "
            "from the Azure Portal -- your Azure AI Search resource -> Keys -> "
            "Manage admin keys. See README.md 'Setting up Azure AI Search'.",
            file=sys.stderr,
        )
        sys.exit(1)
    return SearchIndexClient(endpoint, AzureKeyCredential(key))


def _search_client():
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient

    endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
    key = os.environ.get("AZURE_SEARCH_KEY")
    index_name = os.environ.get("AZURE_SEARCH_INDEX", "governance-chunks")
    if not endpoint or not key:
        print(
            "AZURE_SEARCH_ENDPOINT and/or AZURE_SEARCH_KEY are not set. See "
            "README.md 'Setting up Azure AI Search'.",
            file=sys.stderr,
        )
        sys.exit(1)
    return SearchClient(endpoint, index_name, AzureKeyCredential(key))


def create_index() -> None:
    """One-time setup: define and create the vector index.

    Safe to run again -- create_or_update_index replaces the schema rather
    than erroring if the index already exists (existing documents are kept
    unless the field definitions themselves changed incompatibly).
    """
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    index_name = os.environ.get("AZURE_SEARCH_INDEX", "governance-chunks")

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="doc_id", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="heading", type=SearchFieldDataType.String),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(name="page_start", type=SearchFieldDataType.Int32, filterable=True),
        SimpleField(name="page_end", type=SearchFieldDataType.Int32, filterable=True),
        SearchField(
            name="contentVector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=EMBEDDING_DIMENSIONS,
            vector_search_profile_name="myHnswProfile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="myHnsw")],
        profiles=[VectorSearchProfile(name="myHnswProfile", algorithm_configuration_name="myHnsw")],
        # No vectorizer configured: we compute embeddings ourselves (via Azure
        # OpenAI, in vector_search.py) and hand vectors over already-built.
        # This avoids granting the Search resource its own access to the
        # OpenAI resource, which integrated vectorization would require.
    )

    index = SearchIndex(name=index_name, fields=fields, vector_search=vector_search)
    client = _search_index_client()
    result = client.create_or_update_index(index)
    print(f"Index '{result.name}' created/updated, dimensions={EMBEDDING_DIMENSIONS}.")


def upsert_chunks(chunks: List[dict]) -> None:
    """`chunks` must already have an "embedding" key on each dict (see
    vector_search.py build_index, which adds it before calling this)."""
    client = _search_client()
    docs = [
        {
            "id": chunk["id"],
            "doc_id": chunk["doc_id"],
            "heading": chunk["heading"],
            "content": chunk["text"],
            "page_start": chunk["page_start"],
            "page_end": chunk["page_end"],
            "contentVector": chunk["embedding"],
        }
        for chunk in chunks
    ]
    client.upload_documents(docs)
    print(f"Uploaded {len(docs)} chunk(s) to Azure AI Search index '{os.environ.get('AZURE_SEARCH_INDEX', 'governance-chunks')}'.")


def query_vector(vector: List[float], top_k: int) -> list:
    """Returns a list of (score, chunk_dict) pairs, same shape `search()` in
    vector_search.py returns for the local-file path -- so the `ask` command
    doesn't need to know which store answered it."""
    from azure.search.documents.models import VectorizedQuery

    client = _search_client()
    vector_query = VectorizedQuery(vector=vector, k_nearest_neighbors=top_k, fields="contentVector")
    results = client.search(search_text=None, vector_queries=[vector_query], top=top_k)

    matches = []
    for r in results:
        chunk = {
            "id": r["id"],
            "doc_id": r["doc_id"],
            "heading": r["heading"],
            "text": r["content"],
            "page_start": r["page_start"],
            "page_end": r["page_end"],
        }
        matches.append((r["@search.score"], chunk))
    return matches


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "setup":
        print("Usage: python3 azure_search.py setup", file=sys.stderr)
        sys.exit(1)
    create_index()
