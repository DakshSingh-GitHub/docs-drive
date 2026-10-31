"""FastAPI application entrypoint for the docs-drift API.

Phase 3.1 - Web service wrapper exposing the core drift detection engine
over HTTP with async background workers and SQLite persistence.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

# Ensure the `backend/` directory is on sys.path so absolute imports like
# `from core...` and `from api...` resolve in Vercel serverless and root-level runs.
_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import routes  # noqa: F401  (ensures routes module is loaded)
from .database import get_db
from .schemas import HealthResponse
from .worker import get_job_manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and verify Ollama on startup."""
    db = get_db()
    db.init_db()
    logger.info("Database initialized at %s", db.db_path)

    # Ollama health check (best-effort; does not block startup)
    ollama_info: Dict[str, Any] = {"available": False, "models": [], "error": None}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                ollama_info["available"] = True
                ollama_info["models"] = [m.get("name", "") for m in data.get("models", [])]
            else:
                ollama_info["error"] = f"HTTP {resp.status_code}"
    except Exception as exc:  # pragma: no cover - Ollama optional
        ollama_info["error"] = str(exc)
        logger.warning("Ollama not reachable at startup: %s", exc)

    app.state.ollama_info = ollama_info
    yield
    logger.info("Shutting down docs-drift API")


app = FastAPI(
    title="docs-drift API",
    description="Automated Documentation Drift Detection - Phase 3 Web API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS for Next.js frontend
cors_origins = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(routes.router, prefix="/api")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    """Return service health and Ollama connectivity status."""
    ollama_info: Dict[str, Any] = getattr(app.state, "ollama_info", {})
    return HealthResponse(
        status="ok",
        ollama=ollama_info,
    )
