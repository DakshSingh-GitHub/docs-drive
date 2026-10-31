"""Background job manager for async PR drift analysis.

Phase 3.1 - Worker layer.

Processes analysis requests asynchronously, tracking progress through stages:
  queued (0%) -> processing (20%) -> processing (50%) -> processing (80%) -> completed (100%)
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.ast_extractor import ASTSymbolExtractor
from core.diff_parser import parse_diff
from core.doc_parser import DocParser
from core.drift_verifier import detect_documentation_drift
from core.hybrid_matcher import HybridMatcher
from core.models import DriftAnalysisReport
from core.serverless import is_serverless
from api.database import get_db

logger = logging.getLogger(__name__)


@dataclass
class Job:
    """In-memory representation of an analysis job."""

    run_id: str
    repo_url: Optional[str] = None
    pr_number: Optional[int] = None
    github_token: Optional[str] = None
    diff_text: Optional[str] = None
    docs: List[dict] = field(default_factory=list)
    confidence_threshold: float = 0.75
    status: str = "queued"
    progress: int = 0
    current_step: Optional[str] = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    report: Optional[DriftAnalysisReport] = None


class JobManager:
    """In-memory job manager backed by SQLite persistence."""

    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self._tasks: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------ enqueue

    async def enqueue_pr_analysis(
        self,
        repo_url: str,
        pr_number: int,
        github_token: Optional[str] = None,
        confidence_threshold: float = 0.75,
    ) -> str:
        """Create a queued PR analysis job and start the background task."""
        from uuid import uuid4

        run_id = f"run_{uuid4().hex}"
        job = Job(
            run_id=run_id,
            repo_url=repo_url,
            pr_number=pr_number,
            github_token=github_token,
            confidence_threshold=confidence_threshold,
        )
        self._jobs[run_id] = job

        db = get_db()
        db.create_run(
            run_id=run_id,
            repo_name=repo_url,
            pr_number=pr_number,
            base_commit="",
            head_commit="",
        )

        task = asyncio.create_task(self._run_pr_analysis(run_id))
        self._tasks[run_id] = task
        logger.info("Enqueued PR analysis job %s for PR %s", run_id, pr_number)
        return run_id

    async def enqueue_diff_analysis(
        self,
        diff_text: str,
        docs: List[dict],
        confidence_threshold: float = 0.75,
    ) -> str:
        """Create a queued diff-analysis (playground) job and start the task."""
        from uuid import uuid4

        run_id = f"run_{uuid4().hex}"
        job = Job(
            run_id=run_id,
            diff_text=diff_text,
            docs=docs,
            confidence_threshold=confidence_threshold,
        )
        self._jobs[run_id] = job

        db = get_db()
        db.create_run(
            run_id=run_id,
            repo_name="playground",
            pr_number=None,
            base_commit="",
            head_commit="",
        )

        task = asyncio.create_task(self._run_diff_analysis(run_id))
        self._tasks[run_id] = task
        logger.info("Enqueued diff analysis job %s", run_id)
        return run_id

    # ------------------------------------------------------------------ synchronous execution (serverless-friendly)

    def run_pr_analysis_sync(
        self,
        repo_url: str,
        pr_number: int,
        github_token: Optional[str] = None,
        confidence_threshold: float = 0.75,
    ) -> str:
        """Run PR analysis synchronously (for serverless environments).

        Runs the full pipeline inline and returns the run_id once completed.
        """
        from uuid import uuid4

        run_id = f"run_{uuid4().hex}"
        job = Job(
            run_id=run_id,
            repo_url=repo_url,
            pr_number=pr_number,
            github_token=github_token,
            confidence_threshold=confidence_threshold,
        )
        self._jobs[run_id] = job

        db = get_db()
        db.create_run(
            run_id=run_id,
            repo_name=repo_url,
            pr_number=pr_number,
            base_commit="",
            head_commit="",
        )

        try:
            self._run_pr_analysis_sync_body(job, db)
            logger.info("PR analysis job %s completed synchronously", run_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Sync job %s failed: %s", run_id, exc)
            job.status = "failed"
            job.error_message = str(exc)
            db.update_run(run_id, status="failed", error_message=str(exc))
        return run_id

    def run_diff_analysis_sync(
        self,
        diff_text: str,
        docs: List[dict],
        confidence_threshold: float = 0.75,
    ) -> str:
        """Run diff analysis synchronously (for serverless environments)."""
        from uuid import uuid4

        run_id = f"run_{uuid4().hex}"
        job = Job(
            run_id=run_id,
            diff_text=diff_text,
            docs=docs,
            confidence_threshold=confidence_threshold,
        )
        self._jobs[run_id] = job

        db = get_db()
        db.create_run(
            run_id=run_id,
            repo_name="playground",
            pr_number=None,
            base_commit="",
            head_commit="",
        )

        try:
            self._run_diff_analysis_sync_body(job, db)
            logger.info("Diff analysis job %s completed synchronously", run_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Sync diff job %s failed: %s", run_id, exc)
            job.status = "failed"
            job.error_message = str(exc)
            db.update_run(run_id, status="failed", error_message=str(exc))
        return run_id

    def _run_pr_analysis_sync_body(self, job: Job, db) -> None:
        """Synchronous body for PR analysis (mirrors _run_pr_analysis)."""
        # Stage 1: Parse diff (20%)
        self._update_sync(job, "processing", 20, "Fetching PR diff and parsing", db)
        diff_text = self._fetch_pr_diff_sync(job)
        parser = parse_diff(diff_text)

        # Stage 2: Index documentation (50%)
        self._update_sync(job, "processing", 50, "Scanning documentation", db)
        doc_parser = DocParser()
        for doc in job.docs:
            doc_parser.parse_text(doc["path"], doc["content"])

        # Stage 3: Extract code changes
        extractor = ASTSymbolExtractor()
        changes = extractor.extract_from_diff(parser.code_files)

        # Stage 4: Hybrid match (80%)
        self._update_sync(job, "processing", 80, "Verifying drift", db)
        matcher = HybridMatcher(top_k=10)
        all_sentences: List[Any] = []
        for doc in doc_parser.documents.values():
            all_sentences.extend(doc.sentences)
        matcher.index_documents(all_sentences)

        pairs: List[tuple] = []
        for change in changes:
            cands = matcher.retrieve_candidates(change, top_k=10)
            pairs.extend((change, doc) for doc, _ in cands)

        # Stage 5: Verify drift
        findings = detect_documentation_drift(
            pairs, confidence_threshold=job.confidence_threshold
        )

        # Stage 6: Persist report
        repo_name = job.repo_url or "playground"
        report = DriftAnalysisReport(
            repo_name=repo_name,
            pr_number=job.pr_number,
            base_commit=getattr(parser, "base_commit", "") or "",
            head_commit=getattr(parser, "head_commit", "") or "",
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_files_analyzed=len(parser.files),
            total_symbols_changed=len(changes),
            total_doc_sentences_scanned=len(all_sentences),
            findings=findings,
            execution_time_seconds=0.0,
        )

        self._update_sync(job, "completed", 100, None, db, report=report)
        db.save_report(job.run_id, report)

    def _run_diff_analysis_sync_body(self, job: Job, db) -> None:
        """Synchronous body for diff analysis (mirrors _run_diff_analysis)."""
        self._update_sync(job, "processing", 20, "Parsing diff and extracting symbols", db)
        parser = parse_diff(job.diff_text)

        self._update_sync(job, "processing", 50, "Indexing documentation", db)
        doc_parser = DocParser()
        for doc in job.docs:
            doc_parser.parse_text(doc["path"], doc["content"])

        extractor = ASTSymbolExtractor()
        changes = extractor.extract_from_diff(parser.code_files)

        self._update_sync(job, "processing", 80, "Matching & verifying drift", db)
        matcher = HybridMatcher(top_k=10)
        all_sentences: List[Any] = []
        for doc in doc_parser.documents.values():
            all_sentences.extend(doc.sentences)
        matcher.index_documents(all_sentences)

        pairs: List[tuple] = []
        for change in changes:
            cands = matcher.retrieve_candidates(change, top_k=10)
            pairs.extend((change, doc) for doc, _ in cands)

        findings = detect_documentation_drift(
            pairs, confidence_threshold=job.confidence_threshold
        )

        report = DriftAnalysisReport(
            repo_name="playground",
            pr_number=None,
            base_commit="",
            head_commit="",
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_files_analyzed=len(parser.files),
            total_symbols_changed=len(changes),
            total_doc_sentences_scanned=len(all_sentences),
            findings=findings,
            execution_time_seconds=0.0,
        )

        self._update_sync(job, "completed", 100, None, db, report=report)
        db.save_report(job.run_id, report)

    def _update_sync(
        self,
        job: Job,
        status: str,
        progress: int,
        current_step: Optional[str],
        db,
        report: Optional[DriftAnalysisReport] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Update job in memory and persist to SQLite (synchronous)."""
        job.status = status
        job.progress = progress
        job.current_step = current_step
        if report is not None:
            job.report = report
        if error_message is not None:
            job.error_message = error_message
        if status == "completed":
            job.completed_at = datetime.now(timezone.utc).isoformat()

        db.update_run(
            run_id=job.run_id,
            status=status,
            progress=progress,
            current_step=current_step,
            total_findings=len(report.findings) if report else None,
            report_json=report.model_dump_json(indent=2) if report else None,
            error_message=error_message,
            completed=(status == "completed"),
        )

    # ------------------------------------------------------------------ query

    def get_job(self, run_id: str) -> Optional[Job]:
        return self._jobs.get(run_id)

    def get_all_jobs(self) -> List[Job]:
        return sorted(
            self._jobs.values(), key=lambda j: j.created_at, reverse=True
        )

    # ------------------------------------------------------------------ background tasks

    async def _run_pr_analysis(self, run_id: str) -> None:
        """Full PR analysis pipeline: fetch diff, index docs, extract, match, verify."""
        job = self._jobs.get(run_id)
        if not job:
            return
        db = get_db()

        try:
            # --- Stage 1: Parse diff (20%)
            await self._update(job, "processing", 20, "Fetching PR diff and parsing")
            diff_text = await self._fetch_pr_diff(job)
            parser = parse_diff(diff_text)

            # --- Stage 2: Index documentation (50%)
            await self._update(job, "processing", 50, "Scanning documentation & computing embeddings")
            doc_parser = DocParser()
            # In playground/docs-dir mode, docs are passed directly. For PR mode,
            # we would clone/fetch repo docs; here we accept pre-loaded docs if any.
            for doc in job.docs:
                doc_parser.parse_text(doc["path"], doc["content"])

            # --- Stage 3: Extract code changes
            extractor = ASTSymbolExtractor()
            changes = extractor.extract_from_diff(parser.code_files)

            # --- Stage 4: Hybrid match (80%)
            await self._update(job, "processing", 80, "Verifying drift with Ollama gemma4:e4b")
            matcher = HybridMatcher(top_k=10)
            all_sentences: List[Any] = []
            for doc in doc_parser.documents.values():
                all_sentences.extend(doc.sentences)
            matcher.index_documents(all_sentences)

            pairs: List[tuple] = []
            for change in changes:
                cands = matcher.retrieve_candidates(change, top_k=10)
                pairs.extend((change, doc) for doc, _ in cands)

            # --- Stage 5: Verify drift
            findings = detect_documentation_drift(
                pairs, confidence_threshold=job.confidence_threshold
            )

            # --- Stage 6: Persist report
            repo_name = job.repo_url or "playground"
            report = DriftAnalysisReport(
                repo_name=repo_name,
                pr_number=job.pr_number,
                base_commit=getattr(parser, "base_commit", "") or "",
                head_commit=getattr(parser, "head_commit", "") or "",
                timestamp=datetime.now(timezone.utc).isoformat(),
                total_files_analyzed=len(parser.files),
                total_symbols_changed=len(changes),
                total_doc_sentences_scanned=len(all_sentences),
                findings=findings,
                execution_time_seconds=0.0,
            )

            await self._update(
                job,
                "completed",
                100,
                None,
                report=report,
            )
            db.save_report(run_id, report)
            logger.info("PR analysis job %s completed with %d findings", run_id, len(findings))

        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Job %s failed: %s", run_id, exc)
            await self._update(
                job,
                "failed",
                job.progress,
                job.current_step,
                error_message=str(exc),
            )
            db = get_db()
            db.update_run(run_id, status="failed", error_message=str(exc))

    async def _run_diff_analysis(self, run_id: str) -> None:
        """Playground diff analysis pipeline (synchronous-style but async-wrapped)."""
        job = self._jobs.get(run_id)
        if not job:
            return
        db = get_db()

        try:
            await self._update(job, "processing", 20, "Parsing diff and extracting symbols")
            parser = parse_diff(job.diff_text)

            await self._update(job, "processing", 50, "Indexing documentation")
            doc_parser = DocParser()
            for doc in job.docs:
                doc_parser.parse_text(doc["path"], doc["content"])

            extractor = ASTSymbolExtractor()
            changes = extractor.extract_from_diff(parser.code_files)

            await self._update(job, "processing", 80, "Matching & verifying drift")
            matcher = HybridMatcher(top_k=10)
            all_sentences: List[Any] = []
            for doc in doc_parser.documents.values():
                all_sentences.extend(doc.sentences)
            matcher.index_documents(all_sentences)

            pairs: List[tuple] = []
            for change in changes:
                cands = matcher.retrieve_candidates(change, top_k=10)
                pairs.extend((change, doc) for doc, _ in cands)

            findings = detect_documentation_drift(
                pairs, confidence_threshold=job.confidence_threshold
            )

            report = DriftAnalysisReport(
                repo_name="playground",
                pr_number=None,
                base_commit="",
                head_commit="",
                timestamp=datetime.now(timezone.utc).isoformat(),
                total_files_analyzed=len(parser.files),
                total_symbols_changed=len(changes),
                total_doc_sentences_scanned=len(all_sentences),
                findings=findings,
                execution_time_seconds=0.0,
            )

            await self._update(
                job,
                "completed",
                100,
                None,
                report=report,
            )
            db.save_report(run_id, report)
            logger.info("Diff analysis job %s completed with %d findings", run_id, len(findings))

        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Job %s failed: %s", run_id, exc)
            await self._update(
                job,
                "failed",
                job.progress,
                job.current_step,
                error_message=str(exc),
            )
            db.update_run(run_id, status="failed", error_message=str(exc))

    # ------------------------------------------------------------------ helpers

    async def _update(
        self,
        job: Job,
        status: str,
        progress: int,
        current_step: Optional[str],
        report: Optional[DriftAnalysisReport] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Update job in memory and persist to SQLite."""
        job.status = status
        job.progress = progress
        job.current_step = current_step
        if report is not None:
            job.report = report
        if error_message is not None:
            job.error_message = error_message
        if status == "completed":
            job.completed_at = datetime.now(timezone.utc).isoformat()

        db = get_db()
        db.update_run(
            run_id=job.run_id,
            status=status,
            progress=progress,
            current_step=current_step,
            total_findings=len(report.findings) if report else None,
            report_json=report.model_dump_json(indent=2) if report else None,
            error_message=error_message,
            completed=(status == "completed"),
        )

    async def _fetch_pr_diff(self, job: Job) -> str:
        """Fetch PR diff from GitHub API (uses httpx if available, else placeholder)."""
        import httpx

        repo_url = str(job.repo_url).rstrip("/")
        # Extract owner/repo from URL
        parts = repo_url.replace("https://github.com/", "").split("/")
        owner, repo = parts[0], parts[1]

        headers = {"Accept": "application/vnd.github.v3.diff"}
        if job.github_token:
            headers["Authorization"] = f"token {job.github_token}"

        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{job.pr_number}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text

    def _fetch_pr_diff_sync(self, job: Job) -> str:
        """Fetch PR diff from GitHub API synchronously (for serverless)."""
        import httpx

        repo_url = str(job.repo_url).rstrip("/")
        parts = repo_url.replace("https://github.com/", "").split("/")
        owner, repo = parts[0], parts[1]

        headers = {"Accept": "application/vnd.github.v3.diff"}
        if job.github_token:
            headers["Authorization"] = f"token {job.github_token}"

        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{job.pr_number}"
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

job_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    global job_manager
    if job_manager is None:
        job_manager = JobManager()
    return job_manager
