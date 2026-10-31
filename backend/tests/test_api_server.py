"""API server integration tests (Phase 3.1).

Tests the FastAPI application: health endpoint, background task transitions,
and SQLite persistence.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient
from api.main import app
from api.database import Database


@pytest.fixture
def client():
    """Create a test client with a temporary database."""
    tmp_db = Path(tempfile.mkdtemp()) / "test_drift.db"

    import api.database as db_mod
    orig_db_path = db_mod.DEFAULT_DB_PATH
    db_mod.DEFAULT_DB_PATH = tmp_db
    db_mod.db = None

    with TestClient(app) as tc:
        yield tc

    db_mod.DEFAULT_DB_PATH = orig_db_path
    db_mod.db = None
    tmp_db.unlink(missing_ok=True)


def test_health_endpoint(client):
    """GET /api/health returns 200 with health info."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "ollama" in data
    assert "available" in data["ollama"]


def test_analyze_diff_endpoint(client):
    """POST /api/analyze-diff accepts raw diff and returns findings."""
    diff = """diff --git a/cli.py b/cli.py
index abc123..def456 100644
--- a/cli.py
+++ b/cli.py
@@ -1,5 +1,5 @@
-def main(timeout=30):
+def main(timeout=60):
     pass
"""
    docs = [
        {
            "path": "README.md",
            "content": "# CLI Tool\n\nThe default timeout is set to 30 seconds.",
        }
    ]
    resp = client.post(
        "/api/analyze-diff",
        json={
            "diff_text": diff,
            "docs": docs,
            "confidence_threshold": 0.5,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert "status" in data
    assert "findings_count" in data


def test_generate_fix_endpoint(client):
    """POST /api/generate-fix returns fix response (Ollama fallback path)."""
    resp = client.post(
        "/api/generate-fix",
        json={
            "doc_sentence": "The default timeout is 30 seconds.",
            "code_diff": "-def main(timeout=30):\n+def main(timeout=60):",
            "tone": "concise",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "original_sentence" in data
    assert "suggested_sentence" in data
    assert "suggested_paragraph" in data
    assert "git_patch" in data
    assert "explanation" in data
    assert data["original_sentence"] == "The default timeout is 30 seconds."


def test_run_status_endpoint(client):
    """GET /api/runs/{run_id} reflects run progress."""
    diff = """diff --git a/cli.py b/cli.py
index abc123..def456 100644
--- a/cli.py
+++ b/cli.py
@@ -1,5 +1,5 @@
-def main(timeout=30):
+def main(timeout=60):
     pass
"""
    resp = client.post(
        "/api/analyze-diff",
        json={"diff_text": diff, "docs": [], "confidence_threshold": 0.5},
    )
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    resp2 = client.get(f"/api/runs/{run_id}")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["run_id"] == run_id
    assert "status" in data
    assert "progress" in data


def test_list_runs_endpoint(client):
    """GET /api/runs returns a list of runs."""
    resp = client.get("/api/runs?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "runs" in data
    assert "total" in data
    assert isinstance(data["runs"], list)


def test_nonexistent_run_returns_404(client):
    """GET /api/runs/{run_id} returns 404 for missing run."""
    resp = client.get("/api/runs/nonexistent_run_id")
    assert resp.status_code == 404


def test_database_persistence():
    """SQLite database persists runs and findings correctly."""
    tmp_db = Path(tempfile.mkdtemp()) / "test_persistence.db"
    db = Database(tmp_db)
    db.init_db()

    run_id = "test_run_123"
    db.create_run(
        run_id=run_id,
        repo_name="test-repo",
        pr_number=1,
        base_commit="abc",
        head_commit="def",
    )

    run = db.get_run(run_id)
    assert run is not None
    assert run["status"] == "queued"
    assert run["repo_name"] == "test-repo"

    db.update_run(run_id, status="processing", progress=50, current_step="test")
    run = db.get_run(run_id)
    assert run["status"] == "processing"
    assert run["progress"] == 50

    from core.models import DriftFinding, DriftType, Severity

    finding = DriftFinding(
        id="f1",
        doc_path="README.md",
        doc_line_number=5,
        affected_sentence="The timeout is 30 seconds.",
        code_file_path="cli.py",
        code_line_start=1,
        code_line_end=1,
        code_diff_snippet="-timeout=30\n+timeout=60",
        drift_type=DriftType.INCORRECT_DEFAULT,
        severity=Severity.MEDIUM,
        confidence=0.9,
        explanation="Test explanation",
        suggested_fix="The timeout is 60 seconds.",
    )
    db.save_findings(run_id, [finding])

    findings = db.get_findings(run_id)
    assert len(findings) == 1
    assert findings[0]["doc_path"] == "README.md"

    runs = db.list_runs()
    assert len(runs) >= 1

    tmp_db.unlink(missing_ok=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
