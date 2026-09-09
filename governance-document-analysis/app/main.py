"""HTTP layer: upload a governance document, ask it a question, get a
structured, page-cited, grounded answer back."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import analyzer, extract
from .models import AnalysisResponse

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(
    title="Governance Document Analysis",
    description="Structured, page-cited voting-rights and control extraction from association bylaws and similar documents, powered by Claude.",
    version="0.1.0",
)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": analyzer.MODEL,
        "effort": analyzer.EFFORT,
        "credentials_configured": bool(
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        ),
        "vector_store": os.environ.get("VECTOR_STORE", "local"),
        "embedding_provider": "voyage" if os.environ.get("VOYAGE_API_KEY") else "local-hash (testing only)",
    }


@app.post("/api/analyse", response_model=AnalysisResponse)
async def analyse(file: UploadFile = File(...), question: str = Form(default="")) -> AnalysisResponse:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")

    try:
        document = extract.extract(file.filename or "upload", data)
    except extract.ExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from .chunker import chunk_document
    sections = chunk_document(document, doc_id=file.filename or "doc")

    try:
        analysis, mode, input_tokens, output_tokens = analyzer.analyse(
            document, file.filename or "document", question.strip() or None
        )
    except analyzer.AnalysisError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    voting_rules, discarded = analyzer.verify_voting_rules(analysis.voting_rules, document)
    votes_per_member = analyzer.verify_numeric_finding(analysis.votes_per_ordinary_member, document)
    max_votes = analyzer.verify_numeric_finding(analysis.max_votes_single_natural_person, document)
    plural_voting = analyzer.verify_numeric_finding(analysis.plural_voting_exists, document)

    return AnalysisResponse(
        document_name=file.filename or "document",
        page_count=document.page_count,
        sections_detected=len(sections),
        retrieval_mode=mode,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        document_type=analysis.document_type,
        entity_name=analysis.entity_name,
        votes_per_ordinary_member=votes_per_member,
        max_votes_single_natural_person=max_votes,
        plural_voting_exists=plural_voting,
        voting_rules=voting_rules,
        control_summary=analysis.control_summary,
        quorum_and_majority_rules=analysis.quorum_and_majority_rules,
        open_questions=analysis.open_questions,
        discarded_rules=discarded,
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
