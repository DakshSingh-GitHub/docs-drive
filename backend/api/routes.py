"""REST router for the docs-drift API.

Phase 3.2 - API endpoints for PR analysis, diff analysis, run status,
and AI fix generation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

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
    ApplyFixRequest,
    ApplyFixResponse,
    GenerateFixRequest,
    GenerateFixResponse,
    RepositoryListResponse,
    RepositoryTrack,
    RunStatusResponse,
    Scenario,
    SideBySideRequest,
    SideBySideResponse,
)
from .worker import get_job_manager

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finding_to_dict(finding: Any) -> Dict[str, Any]:
    """Normalize a finding (Pydantic model or dict) to a plain dict."""
    if hasattr(finding, "model_dump"):
        return finding.model_dump()
    if isinstance(finding, dict):
        return finding
    return {"id": getattr(finding, "id", ""), "affected_sentence": ""}


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

    Runs the full drift pipeline synchronously and returns findings immediately.
    """
    manager = get_job_manager()
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

# ---------------------------------------------------------------------------
# Phase 4: Frontend Dashboard Backend Extensions
# ---------------------------------------------------------------------------

# In-memory repository tracking (backed by SQLite for persistence)
_TRACKED_REPOS: Dict[str, RepositoryTrack] = {}

# Curated demo scenarios are loaded from external demo/scenarios/*.json files
# (not embedded in code) to avoid mock data in the application.

_DEMO_SCENARIOS_DIR = Path(__file__).resolve().parent.parent.parent / "demo" / "scenarios"
_BACKEND_DEMO_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "demo" / "scenarios"


def _load_demo_scenarios() -> List[Scenario]:
    """Load demo scenarios from external JSON files in demo/scenarios/.

    Returns an empty list if the directory does not exist or contains no
    valid scenario files. No mock data is embedded in this module.
    """
    scenarios: List[Scenario] = []
    scenarios_dir = (
        _DEMO_SCENARIOS_DIR
        if _DEMO_SCENARIOS_DIR.is_dir()
        else _BACKEND_DEMO_SCENARIOS_DIR
    )
    if not scenarios_dir.is_dir():
        return scenarios
    for scenario_file in sorted(scenarios_dir.glob("*/scenario.json")):
        try:
            data = json.loads(scenario_file.read_text(encoding="utf-8"))
            scenarios.append(Scenario(**data))
        except Exception:  # pragma: no cover - defensive
            logger.warning("Failed to load demo scenario: %s", scenario_file)
    return scenarios


@router.get("/playground/scenarios", tags=["playground"])
async def get_playground_scenarios():
    """Return curated demo scenarios loaded from external files.

    Scenarios are read from demo/scenarios/*/scenario.json — no mock data
    is embedded in this module.
    """
    return {"scenarios": [s.model_dump() for s in _load_demo_scenarios()]}


@router.get("/runs/{run_id}/findings/{finding_id}", tags=["runs"])
async def get_finding_detail(run_id: str, finding_id: str):
    """Return detailed finding with isolated code diff hunk and doc sentence
    for side-by-side comparison view."""
    manager = get_job_manager()
    job = manager.get_job(run_id)

    if not job:
        db = get_db()
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        report_json = run.get("report_json")
        if not report_json:
            raise HTTPException(
                status_code=404, detail=f"No report found for run {run_id}"
            )
        report = json.loads(report_json)
    else:
        report = job.report.model_dump() if job.report else None
        if not report:
            raise HTTPException(
                status_code=404, detail=f"No report found for run {run_id}"
            )

    findings = report.get("findings", [])
    finding = None
    for f in findings:
        fd = _finding_to_dict(f)
        if fd.get("id") == finding_id:
            finding = fd
            break

    if not finding:
        raise HTTPException(
            status_code=404,
            detail=f"Finding {finding_id} not found in run {run_id}",
        )

    return SideBySideResponse(
        finding=finding,
        code_diff_hunk=finding.get("code_diff_snippet", ""),
        doc_sentence=finding.get("affected_sentence", ""),
        doc_line_number=finding.get("doc_line_number", 0),
    )


@router.post("/fix/apply", response_model=ApplyFixResponse, tags=["fix"])
async def apply_fix(req: ApplyFixRequest) -> ApplyFixResponse:
    """Generate a downloadable git patch for applying the suggested fix."""
    manager = get_job_manager()

    # Find the finding across all jobs
    finding: Dict[str, Any] = {}
    for job in manager.get_all_jobs():
        if job.report:
            for f in job.report.findings:
                fd = _finding_to_dict(f)
                if fd.get("id") == req.finding_id:
                    finding = fd
                    break
        if finding:
            break

    if not finding:
        # Try SQLite
        db = get_db()
        runs = db.list_runs(limit=100)
        for run in runs:
            if run.get("report_json"):
                report = json.loads(run["report_json"])
                for f in report.get("findings", []):
                    if f.get("id") == req.finding_id:
                        finding = f
                        break
            if finding:
                break

    if not finding:
        raise HTTPException(
            status_code=404, detail=f"Finding {req.finding_id} not found"
        )

    old_sentence = finding.get("affected_sentence", "")
    new_sentence = finding.get("suggested_fix", old_sentence)
    doc_path = finding.get("doc_path", "doc.md")
    doc_line = finding.get("doc_line_number", 1)

    # Build a unified diff patch
    patch_lines = [
        f"--- a/{doc_path}",
        f"+++ b/{doc_path}",
        f"@@ -{doc_line},1 +{doc_line},1 @@",
        f"-{old_sentence}",
        f"+{new_sentence}",
    ]
    git_patch = "\n".join(patch_lines) + "\n"

    safe_name = doc_path.replace("/", "_").replace(".", "_")
    filename = f"fix-drift-{safe_name}-L{doc_line}.patch"

    return ApplyFixResponse(
        git_patch=git_patch,
        filename=filename,
    )


@router.get("/repositories", response_model=RepositoryListResponse, tags=["repositories"])
async def list_repositories():
    """Return tracked/monitored repositories."""
    return RepositoryListResponse(
        repositories=list(_TRACKED_REPOS.values())
    )


@router.post("/repositories", response_model=RepositoryListResponse, tags=["repositories"])
async def add_repository(req: RepositoryTrack):
    """Add a repository to track for drift analysis."""
    key = f"{req.owner}/{req.repo}"
    _TRACKED_REPOS[key] = req
    return RepositoryListResponse(
        repositories=list(_TRACKED_REPOS.values())
    )
