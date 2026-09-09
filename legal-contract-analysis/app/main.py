"""HTTP layer: upload a contract, get a verified review back."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import analyzer, extract, playbook
from .models import AnalysisResponse

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(
    title="Contract Review",
    description="Structured, evidence-anchored contract review powered by Claude.",
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
    }


@app.get("/api/playbook")
def get_playbook() -> dict:
    return {
        "rules": [
            {
                "id": rule.id,
                "topic": rule.topic,
                "position": rule.position,
                "breach_severity": rule.breach_severity,
            }
            for rule in playbook.RULES
        ]
    }


@app.post("/api/analyse", response_model=AnalysisResponse)
async def analyse(file: UploadFile = File(...)) -> AnalysisResponse:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    try:
        text = extract.extract(file.filename or "upload", data)
    except extract.ExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        token_count = analyzer.count_input_tokens(text, file.filename or "contract")
    except Exception as exc:  # count_tokens is a nicety; never fail the request on it
        token_count = 0
        del exc

    if token_count > analyzer.MAX_INPUT_TOKENS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Document is {token_count:,} tokens, above the {analyzer.MAX_INPUT_TOKENS:,} "
                "token limit for a single review. Split it by schedule and review each part, "
                "rather than reviewing a truncated document."
            ),
        )

    try:
        analysis, input_tokens, output_tokens = analyzer.analyse(text, file.filename or "contract")
    except analyzer.AnalysisError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    findings, discarded = analyzer.verify_findings(analysis.findings, text)
    obligations = analyzer.verify_obligations(analysis.obligations, text)

    return AnalysisResponse(
        document_name=file.filename or "contract",
        character_count=len(text),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        key_terms=analysis.key_terms,
        findings=findings,
        obligations=obligations,
        missing_protections=analysis.missing_protections,
        overall_assessment=analysis.overall_assessment,
        discarded_findings=discarded,
        source_text=text,
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
