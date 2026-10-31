"""API route tests (Phase 3.2).

Validates payload validation and responses for:
  - POST /api/analyze-diff
  - POST /api/generate-fix (with mocked Ollama)
  - GET /api/runs/{run_id} progress tracking
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient
from api.main import app
import api.database as db_mod


@pytest.fixture
def client():
    tmp_db = Path(tempfile.mkdtemp()) / "test_routes.db"
    orig_db_path = db_mod.DEFAULT_DB_PATH
    db_mod.DEFAULT_DB_PATH = tmp_db
    db_mod.db = None

    with TestClient(app) as tc:
        yield tc

    db_mod.DEFAULT_DB_PATH = orig_db_path
    db_mod.db = None
    tmp_db.unlink(missing_ok=True)


def test_analyze_diff_returns_findings(client):
    """POST /api/analyze-diff accepts raw diff and doc content, returns drift findings."""
    diff = """diff --git a/cli.py b/cli.py
index abc123..def456 100644
--- a/cli.py
+++ b/cli.py
@@ -5,7 +5,7 @@ parser = argparse.ArgumentParser()
-    parser.add_argument('--timeout-secs', type=int, default=30)
+    parser.add_argument('--timeout', type=int, default=60)
     parser.add_argument('--verbose', action='store_true')
"""
    docs = [
        {
            "path": "README.md",
            "content": "Run the tool with `--timeout-secs 30` to configure timeout.",
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
    assert data["status"] in ("queued", "processing", "completed")
    assert "findings_count" in data
    assert isinstance(data["findings_count"], int)


def test_analyze_diff_empty_docs(client):
    """POST /api/analyze-diff with no docs still returns a valid response."""
    diff = """diff --git a/cli.py b/cli.py
index abc123..def456 100644
--- a/cli.py
+++ b/cli.py
@@ -1 +1 @@
-x = 1
+y = 2
"""
    resp = client.post(
        "/api/analyze-diff",
        json={"diff_text": diff, "docs": [], "confidence_threshold": 0.75},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data


def test_generate_fix_with_mocked_ollama(client):
    """POST /api/generate-fix returns both sentence and git patch formats."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {
            "content": json.dumps({
                "suggested_sentence": "The default timeout is 60 seconds.",
                "suggested_paragraph": "The default connection timeout is 60 seconds.",
                "git_patch": "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-The default timeout is 30 seconds.\n+The default timeout is 60 seconds.",
                "explanation": "Timeout default changed from 30 to 60 seconds.",
            })
        }
    }

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        resp = client.post(
            "/api/generate-fix",
            json={
                "finding_id": "f1",
                "doc_sentence": "The default timeout is 30 seconds.",
                "code_diff": "-timeout=30\n+timeout=60",
                "tone": "concise",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["original_sentence"] == "The default timeout is 30 seconds."
    assert data["suggested_sentence"] == "The default timeout is 60 seconds."
    assert data["suggested_paragraph"] == "The default connection timeout is 60 seconds."
    assert "```" in data["git_patch"] or "--- a/" in data["git_patch"]
    assert "Timeout default changed" in data["explanation"]


def test_generate_fix_ollama_unavailable(client):
    """POST /api/generate-fix falls back gracefully when Ollama is down."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        resp = client.post(
            "/api/generate-fix",
            json={
                "doc_sentence": "Original sentence.",
                "code_diff": "-old\n+new",
                "tone": "detailed",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["original_sentence"] == "Original sentence."
    assert data["suggested_sentence"] == "Original sentence."
    assert "Ollama unavailable" in data["explanation"]


def test_run_progress_tracking(client):
    """GET /api/runs/{run_id} reflects run progress through stages."""
    diff = """diff --git a/cli.py b/cli.py
index abc123..def456 100644
--- a/cli.py
+++ b/cli.py
@@ -1 +1 @@
-x = 1
+y = 2
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
    assert 0 <= data["progress"] <= 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
