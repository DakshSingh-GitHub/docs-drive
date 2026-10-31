"""SQLite storage for analysis runs and findings.

Phase 3.1 - Database layer using aiosqlite for async access.

Schema:
  analysis_runs  - one row per analysis job
  findings       - one row per drift finding within a run
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import aiosqlite
    AIOSQLITE_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback to sync sqlite3
    AIOSQLITE_AVAILABLE = False

from core.models import DriftAnalysisReport, DriftFinding
from core.serverless import get_serverless_tmp_path


DEFAULT_DB_PATH = Path(get_serverless_tmp_path("drift_runs.db"))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    id TEXT PRIMARY KEY,
    repo_name TEXT NOT NULL,
    pr_number INTEGER,
    base_commit TEXT,
    head_commit TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    progress INTEGER DEFAULT 0,
    current_step TEXT,
    total_findings INTEGER DEFAULT 0,
    report_json TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    doc_path TEXT NOT NULL,
    doc_line_number INTEGER NOT NULL,
    affected_sentence TEXT NOT NULL,
    code_file_path TEXT NOT NULL,
    code_line_start INTEGER NOT NULL,
    code_line_end INTEGER NOT NULL,
    drift_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    confidence REAL NOT NULL,
    explanation TEXT NOT NULL,
    suggested_fix TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES analysis_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_findings_run_id ON findings(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON analysis_runs(created_at DESC);
"""


class Database:
    """Async SQLite database for persisting analysis runs and findings."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._sync_conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------ init

    def init_db(self) -> None:
        """Create tables if they do not exist (synchronous, called at startup)."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ runs

    def create_run(
        self,
        run_id: str,
        repo_name: str,
        pr_number: Optional[int],
        base_commit: str,
        head_commit: str,
    ) -> None:
        """Insert a new run in 'queued' status."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                INSERT INTO analysis_runs
                    (id, repo_name, pr_number, base_commit, head_commit,
                     status, progress, current_step, total_findings, created_at)
                VALUES (?, ?, ?, ?, ?, 'queued', 0, NULL, 0, ?)
                """,
                (
                    run_id,
                    repo_name,
                    pr_number,
                    base_commit,
                    head_commit,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def update_run(
        self,
        run_id: str,
        status: Optional[str] = None,
        progress: Optional[int] = None,
        current_step: Optional[str] = None,
        total_findings: Optional[int] = None,
        report_json: Optional[str] = None,
        error_message: Optional[str] = None,
        completed: bool = False,
    ) -> None:
        """Update run fields dynamically."""
        fields: List[str] = []
        values: List[Any] = []

        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if progress is not None:
            fields.append("progress = ?")
            values.append(progress)
        if current_step is not None:
            fields.append("current_step = ?")
            values.append(current_step)
        if total_findings is not None:
            fields.append("total_findings = ?")
            values.append(total_findings)
        if report_json is not None:
            fields.append("report_json = ?")
            values.append(report_json)
        if error_message is not None:
            fields.append("error_message = ?")
            values.append(error_message)
        if completed:
            fields.append("completed_at = ?")
            values.append(datetime.now(timezone.utc).isoformat())

        if not fields:
            return

        values.append(run_id)
        sql = f"UPDATE analysis_runs SET {', '.join(fields)} WHERE id = ?"
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(sql, values)
            conn.commit()
        finally:
            conn.close()

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single run by ID."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM analysis_runs WHERE id = ?", (run_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List recent runs ordered by created_at DESC."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM analysis_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------ findings

    def save_findings(self, run_id: str, findings: List[DriftFinding]) -> None:
        """Bulk-insert findings for a run."""
        if not findings:
            return
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.executemany(
                """
                INSERT OR REPLACE INTO findings
                    (id, run_id, doc_path, doc_line_number, affected_sentence,
                     code_file_path, code_line_start, code_line_end, drift_type,
                     severity, confidence, explanation, suggested_fix)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        f.id,
                        run_id,
                        f.doc_path,
                        f.doc_line_number,
                        f.affected_sentence,
                        f.code_file_path,
                        f.code_line_start,
                        f.code_line_end,
                        f.drift_type.value if hasattr(f.drift_type, "value") else f.drift_type,
                        f.severity.value if hasattr(f.severity, "value") else f.severity,
                        f.confidence,
                        f.explanation,
                        f.suggested_fix,
                    )
                    for f in findings
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def get_findings(self, run_id: str) -> List[Dict[str, Any]]:
        """Fetch all findings for a run."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM findings WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------ report

    def save_report(self, run_id: str, report: DriftAnalysisReport) -> None:
        """Persist a completed DriftAnalysisReport as JSON."""
        report_json = report.model_dump_json(indent=2)
        self.update_run(
            run_id,
            status="completed",
            progress=100,
            total_findings=len(report.findings),
            report_json=report_json,
            completed=True,
        )
        self.save_findings(run_id, report.findings)

    def get_report(self, run_id: str) -> Optional[DriftAnalysisReport]:
        """Rehydrate a DriftAnalysisReport from stored JSON."""
        run = self.get_run(run_id)
        if not run or not run.get("report_json"):
            return None
        return DriftAnalysisReport.model_validate_json(run["report_json"])


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

db: Optional[Database] = None


def get_db() -> Database:
    """Return the module-level Database singleton, initializing if needed."""
    global db
    if db is None:
        db = Database()
        db.init_db()
    return db
