"""Embed chunks, build a searchable index, ask questions against it.

The last three steps of the pipeline:
    embed the chunks -> save them as a searchable index -> ask a question

Two commands:

    python3 vector_search.py build chunks --out index.json
        Reads every chunks/*.chunks.json file (from chunk_text.py), embeds
        each chunk's text, and saves everything -- text, page, and its
        number-list -- into one index.json file. Do this once per document,
        or whenever you add new documents.

    python3 vector_search.py ask "your question" --index index.json
        Embeds your question, finds the closest-matching chunks in the
        index, shows them, and (if ANTHROPIC_API_KEY is set) sends them to
        Claude and prints the answer.

Needs a VOYAGE_API_KEY to generate real, meaning-aware embeddings (see
README.md "Setting up Voyage AI" for how to get one -- it's not Azure, and
it's not the Anthropic key you already have, it's a third, separate key).
Without one, this falls back to a crude word-overlap approximation so you can
still see the mechanism working end-to-end -- just don't trust its answers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import List

# Reads a .env file in the current folder (if one exists) and loads any KEY=value
# lines into the environment -- so VOYAGE_API_KEY / ANTHROPIC_API_KEY below are
# found whether they came from `export` or from .env. Does nothing if there's
# no .env file, so this is always safe to leave in.
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Step 4: embedding
# ---------------------------------------------------------------------------

def embed_texts(texts: List[str]) -> List[List[float]]:
    """Turn a list of strings into a list of number-lists (one per string).

    Real embeddings via Voyage AI if VOYAGE_API_KEY is set; otherwise a
    deterministic word-overlap stand-in so the rest of the pipeline (index,
    search, ranking) can still be run and checked without any API key.
    """
    if os.environ.get("VOYAGE_API_KEY"):
        import voyageai

        client = voyageai.Client()
        model = os.environ.get("EMBEDDING_MODEL", "voyage-3-large")
        result = client.embed(texts, model=model, input_type="document")
        return result.embeddings

    return [_local_hash_vector(t) for t in texts]


_HASH_DIM = 256


def _local_hash_vector(text: str) -> List[float]:
    """No API key, no real understanding of meaning -- just counts which
    words appear, in a fixed-size format. Good enough to prove the plumbing
    works; not good enough to trust the answers it retrieves."""
    vector = [0.0] * _HASH_DIM
    for token in re.findall(r"\w+", text.lower()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % _HASH_DIM
        vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    norm = sum(v * v for v in vector) ** 0.5
    return [v / norm for v in vector] if norm > 0 else vector


# ---------------------------------------------------------------------------
# Step 5: build the index
# ---------------------------------------------------------------------------

def build_index(chunks_dir: str, out_path: str) -> None:
    chunk_files = sorted(Path(chunks_dir).glob("*.chunks.json"))
    if not chunk_files:
        print(f"No *.chunks.json files found in {chunks_dir}/ -- run chunk_text.py first.", file=sys.stderr)
        sys.exit(1)

    all_chunks = []
    for f in chunk_files:
        doc_id = f.stem.replace(".chunks", "")
        for chunk in json.loads(f.read_text(encoding="utf-8")):
            chunk["doc_id"] = doc_id
            all_chunks.append(chunk)

    print(f"Embedding {len(all_chunks)} chunks from {len(chunk_files)} document(s)...")
    vectors = embed_texts([c["text"] for c in all_chunks])
    for chunk, vector in zip(all_chunks, vectors):
        chunk["embedding"] = vector

    Path(out_path).write_text(json.dumps(all_chunks, ensure_ascii=False), encoding="utf-8")
    print(f"Saved index with {len(all_chunks)} chunks -> {out_path}")


# ---------------------------------------------------------------------------
# Step 6: search, then ask
# ---------------------------------------------------------------------------

def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def search(question: str, index_path: str, top_k: int) -> list:
    chunks = json.loads(Path(index_path).read_text(encoding="utf-8"))
    [q_vector] = embed_texts([question])

    scored = [(_cosine(q_vector, c["embedding"]), c) for c in chunks]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:top_k]


def using_azure_openai() -> bool:
    """True when Azure OpenAI credentials are present -- this is what decides
    which of the two branches in `ask_model` runs. Checked *before*
    ANTHROPIC_API_KEY so that on a machine with only Azure access (e.g. a
    sandbox with no Anthropic account), the script picks Azure automatically
    rather than telling you to set a key you can't get.
    """
    return bool(os.environ.get("AZURE_OPENAI_API_KEY") and os.environ.get("AZURE_OPENAI_ENDPOINT"))


def has_any_model_credentials() -> bool:
    return using_azure_openai() or bool(os.environ.get("ANTHROPIC_API_KEY"))


_SYSTEM_PROMPT = (
    "Answer only from the excerpts given. If they don't contain the answer, "
    "say so plainly rather than guessing. Cite the page number(s) you used."
)


def _ask_azure_openai(question: str, context: str) -> str:
    """Same job as _ask_claude, via Azure OpenAI's v1 API instead -- no
    dated api_version to track (GA since August 2025), just an endpoint,
    a key, and the deployment name you chose when you deployed a model in
    Azure AI Foundry (not necessarily the underlying model's own name).
    """
    from openai import OpenAI

    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    if not deployment:
        raise RuntimeError(
            "AZURE_OPENAI_DEPLOYMENT is not set. This is the deployment name you "
            "chose in Azure AI Foundry when you deployed a model (e.g. 'gpt-4o'), "
            "not necessarily the model's own name -- check Azure AI Foundry -> "
            "Deployments for the exact name."
        )

    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    client = OpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        base_url=f"{endpoint}/openai/v1/",
    )
    response = client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"<excerpts>\n{context}\n</excerpts>\n\nQuestion: {question}"},
        ],
    )
    return response.choices[0].message.content


def _ask_claude(question: str, context: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=os.environ.get("GOVERNANCE_MODEL", "claude-opus-5"),
        max_tokens=2000,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"<excerpts>\n{context}\n</excerpts>\n\nQuestion: {question}"}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def ask_model(question: str, matched_chunks: list) -> str:
    """Send the matched chunks + question to whichever model is configured,
    and return its plain-text answer. Picks Azure OpenAI if those
    credentials are present, otherwise Claude via the Anthropic API.
    """
    context = "\n\n---\n\n".join(
        f"[{c['doc_id']}, pages {c['page_start']}-{c['page_end']}, {c['heading']}]\n{c['text']}"
        for _, c in matched_chunks
    )
    if using_azure_openai():
        return _ask_azure_openai(question, context)
    return _ask_claude(question, context)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="Embed all chunks and save the index")
    p_build.add_argument("chunks_dir")
    p_build.add_argument("--out", default="index.json")

    p_ask = sub.add_parser("ask", help="Ask a question against the index")
    p_ask.add_argument("question")
    p_ask.add_argument("--index", default="index.json")
    p_ask.add_argument("--top-k", type=int, default=5)

    args = parser.parse_args()

    if args.command == "build":
        build_index(args.chunks_dir, args.out)

    elif args.command == "ask":
        using_real_embeddings = bool(os.environ.get("VOYAGE_API_KEY"))
        print(f'Question: "{args.question}"\n')
        print(
            "Embedding method: Voyage AI (real, meaning-aware)\n"
            if using_real_embeddings
            else "Embedding method: local word-overlap fallback (VOYAGE_API_KEY not set)\n"
            "  -> This ranking is NOT reliable. It matches shared words, not meaning.\n"
            "     Set VOYAGE_API_KEY for real results -- see README.md Step 7.\n"
        )

        matches = search(args.question, args.index, args.top_k)
        print(f"Closest-matching sections found (best match first):\n")
        for rank, (score, chunk) in enumerate(matches, start=1):
            print(f"  {rank}. {chunk['heading']}  (pages {chunk['page_start']}-{chunk['page_end']}, in {chunk['doc_id']})")
            print(f"     match strength: {score:.2f}  (0 = unrelated, 1 = identical wording)")

        if not has_any_model_credentials():
            misnamed = [f.name for f in Path(".").glob(".env.*") if f.is_file() and f.name != ".env.example"]
            hint = (
                f"Found {', '.join(misnamed)} in this folder -- load_dotenv() only "
                f"reads a file named exactly `.env`, not that. Rename {misnamed[0]} "
                "to `.env` and try again."
                if misnamed
                else "Set either ANTHROPIC_API_KEY, or AZURE_OPENAI_API_KEY + "
                "AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_DEPLOYMENT, in .env "
                "(see README.md 'Setting up your API keys') and run this again "
                "to get an actual written answer."
            )
            print(
                "\n--------------------------------------------------------------\n"
                "No answer generated: no model credentials found, so nothing was\n"
                "asked -- the sections above are only the search step.\n"
                f"{hint}\n"
                "--------------------------------------------------------------"
            )
            return

        engine = f"Azure OpenAI (deployment: {os.environ.get('AZURE_OPENAI_DEPLOYMENT', '?')})" if using_azure_openai() else "Claude"
        print(f"\nAsking {engine} to answer from these sections...\n")
        answer = ask_model(args.question, matches)
        print("=" * 64)
        print("ANSWER")
        print("=" * 64)
        print(answer)


if __name__ == "__main__":
    main()
