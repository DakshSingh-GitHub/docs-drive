"""API playground scenario tests (Phase 4).

Tests the new Phase 4 backend endpoints:
  - GET /api/playground/scenarios
  - GET /api/runs/{run_id}/findings/{finding_id}
  - POST /api/fix/apply
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
    """Create a test client with a temporary database."""
    tmp_db = Path(tempfile.mkdtemp()) / "test_playground.db"
    orig_db_path = db_mod.DEFAULT_DB_PATH
    db_mod.DEFAULT_DB_PATH = tmp_db
    db_mod.db = None

    with TestClient(app) as tc:
        yield tc

    db_mod.DEFAULT_DB_PATH = orig_db_path
    db_mod.db = None
    tmp_db.unlink(missing_ok=True)


def test_playground_scenarios_endpoint(client):
    """GET /api/playground/scenarios returns curated demo scenarios."""
    resp = client.get("/api/playground/scenarios")
    assert resp.status_code == 200
    data = resp.json()
    assert "scenarios" in data
    scenarios = data["scenarios"]
    assert len(scenarios) >= 1

    # Verify scenario structure
    s = scenarios[0]
    assert "id" in s
    assert "name" in s
    assert "description" in s
    assert "diff" in s
    assert "docs" in s
    assert "expected_drift" in s

    # Verify all 4 demo scenarios are present
    scenario_ids = {s["id"] for s in scenarios}
    expected_ids = {
        "scenario-01-renamed-flag",
        "scenario-02-changed-default",
        "scenario-03-removed-parameter",
        "scenario-04-false-alarm-refactor",
    }
    assert expected_ids.issubset(scenario_ids)


def test_playground_scenario_has_expected_structure(client):
    """Each scenario has the expected fields for frontend consumption."""
    resp = client.get("/api/playground/scenarios")
    assert resp.status_code == 200
    scenarios = resp.json()["scenarios"]

    renamed_flag = next(s for s in scenarios if s["id"] == "scenario-01-renamed-flag")
    assert renamed_flag["name"] == "CLI Flag Renamed"
    assert renamed_flag["expected_drift"] is True
    assert "timeout-secs" in renamed_flag["diff"]
    assert "--timeout" in renamed_flag["diff"]
    assert renamed_flag["docs"]  # non-empty docs dict


def test_finding_detail_endpoint(client):
    """GET /api/runs/{run_id}/findings/{finding_id} returns side-by-side data."""
    # First create a run with findings
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
            "content": "# CLI Tool\n\nRun the tool with `--timeout-secs 30` to configure timeout.",
        }
    ]
    resp = client.post(
        "/api/analyze-diff",
        json={"diff_text": diff, "docs": docs, "confidence_threshold": 0.5},
    )
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    # Get the run to find a finding ID
    run_resp = client.get(f"/api/runs/{run_id}")
    assert run_resp.status_code == 200
    findings = run_resp.json().get("report", {}).get("findings", [])

    if findings:
        finding_id = findings[0]["id"]

        # Test findings detail endpoint
        detail_resp = client.get(f"/api/runs/{run_id}/findings/{finding_id}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert "finding" in detail
        assert "code_diff_hunk" in detail
        assert "doc_sentence" in detail
        assert "doc_line_number" in detail
        assert detail["finding"]["id"] == finding_id


def test_finding_detail_not_found(client):
    """GET /api/runs/{run_id}/findings/{finding_id} returns 404 for missing finding."""
    # Create a run (even with no findings)
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

    # Request a non-existent finding
    resp = client.get(f"/api/runs/{run_id}/findings/nonexistent_finding")
    assert resp.status_code == 404


def test_finding_detail_run_not_found(client):
    """GET /api/runs/{run_id}/findings/{finding_id} returns 404 for missing run."""
    resp = client.get("/api/runs/nonexistent_run/findings/f1")
    assert resp.status_code == 404


def test_apply_fix_endpoint(client):
    """POST /api/fix/apply returns a git patch for the suggested fix."""
    # First create a run with findings
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
            "content": "# CLI Tool\n\nRun the tool with `--timeout-secs 30` to configure timeout.",
        }
    ]
    resp = client.post(
        "/api/analyze-diff",
        json={"diff_text": diff, "docs": docs, "confidence_threshold": 0.5},
    )
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    # Get finding ID
    run_resp = client.get(f"/api/runs/{run_id}")
    findings = run_resp.json().get("report", {}).get("findings", [])

    if findings:
        finding_id = findings[0]["id"]

        # Test fix apply endpoint
        fix_resp = client.post(
            "/api/fix/apply",
            json={"finding_id": finding_id, "fix_type": "sentence"},
        )
        assert fix_resp.status_code == 200
        fix_data = fix_resp.json()
        assert "git_patch" in fix_data
        assert "filename" in fix_data
        assert fix_data["git_patch"].startswith("--- a/")
        assert "+++ b/" in fix_data["git_patch"]
        assert fix_data["filename"].endswith(".patch")


def test_apply_fix_not_found(client):
    """POST /api/fix/apply returns 404 for non-existent finding."""
    resp = client.post(
        "/api/fix/apply",
        json={"finding_id": "nonexistent_finding", "fix_type": "sentence"},
    )
    assert resp.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
