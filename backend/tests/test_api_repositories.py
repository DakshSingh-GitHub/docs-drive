"""API repository tracking tests (Phase 4).

Tests the repository tracking endpoints:
  - GET /api/repositories
  - POST /api/repositories
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient
from api.main import app
import api.database as db_mod
from api import routes as routes_mod


@pytest.fixture
def client():
    """Create a test client with a temporary database."""
    tmp_db = Path(tempfile.mkdtemp()) / "test_repos.db"
    orig_db_path = db_mod.DEFAULT_DB_PATH
    db_mod.DEFAULT_DB_PATH = tmp_db
    db_mod.db = None

    # Clear in-memory repo tracking between tests
    routes_mod._TRACKED_REPOS.clear()

    with TestClient(app) as tc:
        yield tc

    # Clear again after test
    routes_mod._TRACKED_REPOS.clear()
    db_mod.DEFAULT_DB_PATH = orig_db_path
    db_mod.db = None
    tmp_db.unlink(missing_ok=True)


def test_list_repositories_empty(client):
    """GET /api/repositories returns empty list initially."""
    resp = client.get("/api/repositories")
    assert resp.status_code == 200
    data = resp.json()
    assert "repositories" in data
    assert data["repositories"] == []


def test_add_repository(client):
    """POST /api/repositories adds a repository to track."""
    resp = client.post(
        "/api/repositories",
        json={"owner": "tiangolo", "repo": "fastapi"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "repositories" in data
    repos = data["repositories"]
    assert len(repos) == 1
    assert repos[0]["owner"] == "tiangolo"
    assert repos[0]["repo"] == "fastapi"


def test_add_multiple_repositories(client):
    """POST /api/repositories can add multiple repositories."""
    # Add first repo
    resp = client.post(
        "/api/repositories",
        json={"owner": "tiangolo", "repo": "fastapi"},
    )
    assert resp.status_code == 200

    # Add second repo
    resp = client.post(
        "/api/repositories",
        json={"owner": "psf", "repo": "requests"},
    )
    assert resp.status_code == 200

    # List all repos
    resp = client.get("/api/repositories")
    assert resp.status_code == 200
    data = resp.json()
    repos = data["repositories"]
    assert len(repos) == 2

    owners = {r["owner"] for r in repos}
    assert owners == {"tiangolo", "psf"}


def test_add_duplicate_repository(client):
    """POST /api/repositories with same owner/repo replaces (dict key)."""
    # Add repo
    resp = client.post(
        "/api/repositories",
        json={"owner": "tiangolo", "repo": "fastapi"},
    )
    assert resp.status_code == 200

    # Add same repo again (should replace, not duplicate)
    resp = client.post(
        "/api/repositories",
        json={"owner": "tiangolo", "repo": "fastapi"},
    )
    assert resp.status_code == 200

    # List repos - should still be 1
    resp = client.get("/api/repositories")
    assert resp.status_code == 200
    data = resp.json()
    repos = data["repositories"]
    assert len(repos) == 1


def test_repository_response_structure(client):
    """Repository response has correct structure for frontend."""
    resp = client.post(
        "/api/repositories",
        json={"owner": "owner", "repo": "repo"},
    )
    assert resp.status_code == 200
    data = resp.json()
    repo = data["repositories"][0]
    assert "owner" in repo
    assert "repo" in repo
    assert isinstance(repo["owner"], str)
    assert isinstance(repo["repo"], str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
