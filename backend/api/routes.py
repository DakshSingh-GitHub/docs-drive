"""REST router for the docs-drift API.

Phase 3.2 - API endpoints for PR analysis, diff analysis, run status,
and AI fix generation.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
import httpx

from core.ast_extractor import ASTSymbolExtractor
from core.diff_parser import parse_diff
from core.doc_parser import DocParser
from core.drift_verifier import detect_documentation_drift
from core.hybrid_matcher import HybridMatcher
from core.models import DriftAnalysisReport
from core.serverless import is_serverless
from api.database import get_db
from api.schemas import (
    AnalyzeDiffRequest,
    AnalyzeDiffResponse,
    AnalyzePRRequest,
    AnalyzePRResponse,
    GenerateFixRequest,
    GenerateFixResponse,
    RunStatusResponse,
)
from .worker import get_job_manager

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# PR Analysis
# ---------------------------------------------------------------------------

@router.post("/analyze-pr", response_model=AnalyzePRResponse, tags=["analysis"])
async def analyze_pr(req: AnalyzePRRequest) -> AnalyzePRResponse:
    """Trigger drift analysis for a GitHub Pull Request.

    Enqueues a background job and returns immediately with a run_id.
    Poll GET /api/runs/{run_id} for progress and results.
    In serverless environments, runs synchronously and returns completed status.
    """
    manager = get_job_manager()
    if is_serverless():
        run_id = manager.run_pr_analysis_sync(
            repo_url=str(req.repo_url),
            pr_number=req.pr_number,
            github_token=req.github_token,
            confidence_threshold=req.confidence_threshold,
        )
        job = manager.get_job(run_id)
        status = job.status if job else "completed"
        return AnalyzePRResponse(
            run_id=run_id,
            status=status,
            message="PR analysis completed (serverless synchronous mode).",
        )
    run_id = await manager.enqueue_pr_analysis(
        repo_url=str(req.repo_url),
        pr_number=req.pr_number,
        github_token=req.github_token,
        confidence_threshold=req.confidence_threshold,
    )
    return AnalyzePRResponse(
        run_id=run_id,
        status="queued",
        message="PR analysis queued. Poll /api/runs/{run_id} for progress.",
    )


@router.post("/analyze-diff", response_model=AnalyzeDiffResponse, tags=["analysis"])
async def analyze_diff(req: AnalyzeDiffRequest) -> AnalyzeDiffResponse:
    """Playground mode: analyze a raw diff and documentation content.

    Runs the full drift pipeline synchronously and returns findings.
    In serverless environments, runs synchronously and returns results immediately.
    """
    manager = get_job_manager()
    if is_serverless():
        run_id = manager.run_diff_analysis_sync(
            diff_text=req.diff_text,
            docs=[d.model_dump() for d in req.docs],
            confidence_threshold=req.confidence_threshold,
        )
        job = manager.get_job(run_id)
        report = job.report.model_dump() if job and job.report else None
        findings_count = len(job.report.findings) if job and job.report else 0
        return AnalyzeDiffResponse(
            run_id=run_id,
            status=job.status if job else "completed",
            findings_count=findings_count,
            report=report,
        )
    run_id = await manager.enqueue_diff_analysis(
        diff_text=req.diff_text,
        docs=[d.model_dump() for d in req.docs],
        confidence_threshold=req.confidence_threshold,
    )

    job = manager.get_job(run_id)
    report = None
    findings_count = 0
    if job and job.report:
        findings_count = len(job.report.findings)
        report = job.report.model_dump()

    return AnalyzeDiffResponse(
        run_id=run_id,
        status=job.status if job else "queued",
        findings_count=findings_count,
        report=report,
    )


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

@router.get("/runs", tags=["runs"])
async def list_runs(limit: int = 50):
    """Return recent analysis runs ordered by created_at DESC."""
    db = get_db()
    runs = db.list_runs(limit=limit)
    return {"runs": runs, "total": len(runs)}


@router.get("/runs/{run_id}", response_model=RunStatusResponse, tags=["runs"])
async def get_run(run_id: str) -> RunStatusResponse:
    """Return real-time status and progress for a run.

    When completed, includes the full DriftAnalysisReport.
    """
    manager = get_job_manager()
    job = manager.get_job(run_id)

    if not job:
        db = get_db()
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        return RunStatusResponse(
            run_id=run_id,
            status=run["status"],
            progress=run["progress"],
            current_step=run["current_step"],
            report=(
                json.loads(run["report_json"]) if run.get("report_json") else None
            ),
            error_message=run.get("error_message"),
        )

    report = job.report.model_dump() if job.report else None
    return RunStatusResponse(
        run_id=run_id,
        status=job.status,
        progress=job.progress,
        current_step=job.current_step,
        report=report,
        error_message=job.error_message,
    )


# ---------------------------------------------------------------------------
# AI Fix Generation
# ---------------------------------------------------------------------------

@router.post("/generate-fix", response_model=GenerateFixResponse, tags=["fix"])
async def generate_fix(req: GenerateFixRequest) -> GenerateFixResponse:
    """Generate an AI-assisted documentation correction using Ollama gemma4:e4b.

    Returns the rewritten sentence, a full paragraph rewrite, a git patch,
    and an explanation.
    """
    system_prompt = (
        "You are an expert documentation engineer. "
        "Rewrite the given documentation sentence to reflect the code change "
        "shown in the diff. Output must be valid JSON."
    )

    user_prompt = f"""### DOCUMENTATION SENTENCE (OUTDATED)
{req.doc_sentence}

### CODE DIFF
{req.code_diff}

Tone: {req.tone or 'concise'}

Produce a corrected documentation sentence, a full paragraph rewrite, and a
unified git patch. Output JSON:
{{
  "suggested_sentence": "single corrected sentence",
  "suggested_paragraph": "full paragraph rewrite",
  "git_patch": "--- a/doc.md\n+++ b/doc.md\n@@ ... @@\n-old sentence\n+new sentence",
  "explanation": "brief rationale"
}}
"""

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": "gemma4:e4b",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "format": "json",
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "{}")
    except Exception as exc:  # pragma: no cover - Ollama optional
        logger.warning("Ollama fix generation failed: %s", exc)
        suggested = req.doc_sentence
        explanation = (
            "Ollama unavailable. Returned original sentence unchanged. "
            f"Error: {exc}"
        )
        return GenerateFixResponse(
            original_sentence=req.doc_sentence,
            suggested_sentence=suggested,
            suggested_paragraph=suggested,
            git_patch=(
                f"--- a/doc.md\n+++ b/doc.md\n@@ -1 +1 @@\n-{req.doc_sentence}\n+{suggested}"
            ),
            explanation=explanation,
        )

    try:
        parsed = json.loads(content)
        suggested_sentence = parsed.get("suggested_sentence", req.doc_sentence)
        suggested_paragraph = parsed.get("suggested_paragraph", suggested_sentence)
        git_patch = parsed.get(
            "git_patch",
            f"--- a/doc.md\n+++ b/doc.md\n@@ -1 +1 @@\n-{req.doc_sentence}\n+{suggested_sentence}",
        )
        explanation = parsed.get("explanation", "AI-generated fix.")
    except Exception:
        suggested_sentence = content[:200]
        suggested_paragraph = suggested_sentence
        git_patch = f"--- a/doc.md\n+++ b/doc.md\n@@ -1 +1 @@\n-{req.doc_sentence}\n+{suggested_sentence}"
        explanation = "Raw model output (JSON parse fallback)."

    return GenerateFixResponse(
        original_sentence=req.doc_sentence,
        suggested_sentence=suggested_sentence,
        suggested_paragraph=suggested_paragraph,
        git_patch=git_patch,
        explanation=explanation,
    )
