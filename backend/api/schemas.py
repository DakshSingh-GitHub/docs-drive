"""Request and response Pydantic models for the docs-drift FastAPI API.

Phase 3.2 - API Schemas
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl


def _new_run_id() -> str:
    return f"run_{uuid4().hex}"


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------

class RawDocFile(BaseModel):
    """A single documentation file with its path and raw content."""

    path: str = Field(..., description="Documentation file path, e.g. docs/guide.md")
    content: str = Field(..., description="Raw markdown / reStructuredText content")


class AnalyzePRRequest(BaseModel):
    """Request to trigger drift analysis for a GitHub Pull Request."""

    repo_url: HttpUrl = Field(..., description="GitHub repository URL, e.g. https://github.com/owner/repo")
    pr_number: int = Field(..., description="Pull request number")
    github_token: Optional[str] = Field(None, description="GitHub token for private repos (optional)")
    confidence_threshold: float = Field(0.75, ge=0.0, le=1.0, description="Minimum confidence to report drift")


class AnalyzeDiffRequest(BaseModel):
    """Request for playground mode: analyze a raw diff and doc content."""

    diff_text: str = Field(..., description="Unified diff text to analyze")
    docs: List[RawDocFile] = Field(default_factory=list, description="Documentation files to scan")
    confidence_threshold: float = Field(0.75, ge=0.0, le=1.0, description="Minimum confidence to report drift")


class GenerateFixRequest(BaseModel):
    """Request to generate an AI-assisted documentation fix for a drift finding."""

    finding_id: Optional[str] = Field(None, description="ID of an existing drift finding (optional)")
    doc_sentence: str = Field(..., description="The outdated documentation sentence")
    code_diff: str = Field(..., description="The responsible code diff snippet")
    tone: Optional[str] = Field("concise", description="Fix tone: 'concise', 'detailed', or 'tutorial'")


# ---------------------------------------------------------------------------
# Response Schemas
# ---------------------------------------------------------------------------

class RunStatusResponse(BaseModel):
    """Status and progress of an analysis run."""

    run_id: str
    status: str  # 'queued', 'processing', 'completed', 'failed'
    progress: int  # 0 to 100
    current_step: Optional[str] = None
    report: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


class AnalyzePRResponse(BaseModel):
    """Response for PR analysis submission."""

    run_id: str
    status: str
    message: str


class AnalyzeDiffResponse(BaseModel):
    """Response for playground diff analysis."""

    run_id: str
    status: str
    findings_count: int
    report: Optional[Dict[str, Any]] = None


class GenerateFixResponse(BaseModel):
    """Response for AI-assisted documentation fix generation."""

    original_sentence: str
    suggested_sentence: str
    suggested_paragraph: str
    git_patch: str
    explanation: str


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    ollama: Dict[str, Any]
    version: str = "1.0.0"


class RunsListResponse(BaseModel):
    """List of recent analysis runs."""

    runs: List[Dict[str, Any]]
    total: int
