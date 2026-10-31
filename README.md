# Automated Documentation Drift Detection

**Problem 05 – Hacktoberfest 2026 – Hack Day Sonipat**
**Team: HisenBugs**

An automated CI check that identifies documentation made incorrect by code changes in a Pull Request. It analyzes the PR diff, links changed code (renamed flags, changed defaults, modified parameters, removed features) to the docs that describe them, and reports the exact documentation sentences that are now out of date — each with a reference to the responsible code change.

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Phase-by-Phase Implementation](#phase-by-phase-implementation)
4. [Installation](#installation)
5. [Usage](#usage)
6. [API Reference](#api-reference)
7. [CLI Reference](#cli-reference)
8. [Testing](#testing)
9. [Serverless Deployment (Vercel)](#serverless-deployment-vercel)
10. [Demo Scenarios](#demo-scenarios)
11. [Full-Stack Integration](#full-stack-integration)
12. [Frontend Integration Guide](#frontend-integration-guide)
13. [Team](#team)

---

## 1. Overview

Documentation drifts when developers update code without synchronizing associated documentation. Traditional doc test tools (e.g., `doctest`, `rustdoc`, `mktestdocs`) only check executable code blocks; they cannot evaluate natural language explanatory prose, configuration tables, tutorial parameters, or architectural descriptions.

This project solves that problem by providing:

- **Core Analysis Engine (Phase 1):** A Python CLI that parses code diffs, extracts semantic changes using AST parsing, links changed code symbols to documentation sentences using hybrid BM25 + dense embeddings, and verifies drift using an LLM (local Ollama gemma4:e4b with offline fallback).

- **GitHub Action / CI Check (Phase 2):** A Dockerized GitHub Action that runs on pull requests, analyzes PR diffs, posts inline review comments with suggestion blocks, creates GitHub Check Runs with file annotations, and validates drift detection against historical commit histories.

- **FastAPI Web API (Phase 3):** A web service wrapping the core engine with async background jobs, SQLite persistence, and REST endpoints for the frontend dashboard.

- **Next.js Frontend Dashboard (Phase 4):** A web interface with side-by-side diff visualization, one-click documentation fixes, PR analysis dashboard, and interactive findings board.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Next.js Frontend (Port 3000)                   │
│  app/lib/api.ts — Typed fetch client for backend API               │
└─────────────────────────────────────────────────────────────────────┘
                            │ HTTPS / JSON
                            │ CORS enabled for localhost:3000
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   FastAPI Backend (Port 8000)                      │
│  api/main.py         — App entrypoint, CORS, health                │
│  api/routes.py       — 10 REST endpoints                           │
│  api/worker.py       — Async + sync job manager                    │
│  api/database.py     — SQLite persistence                          │
│  api/schemas.py      — Pydantic v2 request/response models         │
└─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
                    Phase 1 Core Engine (Python)
                    backend/core/
                    ├── diff_parser.py    # Unified diff parsing
                    ├── ast_extractor.py  # Semantic change extraction
                    ├── doc_parser.py     # Markdown sentence parser
                    ├── hybrid_matcher.py # BM25 + embeddings linker
                    ├── drift_verifier.py # LLM drift verification
                    ├── llm_client.py     # Ollama / OpenAI client
                    ├── embeddings.py     # Embedding service
                    └── models.py         # Pydantic v2 schemas
```

### Core Pipeline

```
Input Diff
    │
    ▼
Diff Parser ────────▶ Extract added/removed lines, classify code vs doc
    │
    ▼
AST Extractor ───────▶ Detect renamed flags, changed defaults, modified params, removed features
    │
    ▼
Doc Parser ─────────▶ Extract doc sentences with exact line numbers & section hierarchy
    │
    ▼
Hybrid Matcher ──────▶ BM25 + dense embeddings (nomic-embed-text) + Reciprocal Rank Fusion
    │
    ▼
Drift Verifier ──────▶ LLM (gemma4:e4b) verifies drift, assigns severity + confidence
    │
    ▼
DriftAnalysisReport ──▶ JSON output with exact sentence, code line, suggested fix
```

---

## 3. Phase-by-Phase Implementation

### Phase 1: Core Analysis Engine (Local CLI/Script)

| Sub-part | Description | Files |
|----------|-------------|-------|
| 1.1 | Environment setup, Pydantic models, Git diff parser | `requirements.txt`, `pyproject.toml`, `core/models.py`, `core/diff_parser.py`, `tests/test_diff_parser.py` |
| 1.2 | AST-based semantic change extractor | `core/ast_extractor.py`, `core/languages/python_extractor.py`, `core/languages/typescript_extractor.py`, `tests/test_ast_extractor.py` |
| 1.3 | Documentation parser, sentence splitter & source mapping | `core/doc_parser.py`, `tests/test_doc_parser.py` |
| 1.4 | Hybrid retrieval engine (BM25 + nomic-embed-text) | `core/hybrid_matcher.py`, `core/embeddings.py`, `tests/test_hybrid_matcher.py` |
| 1.5 | LLM drift detection & false positive filtering | `core/drift_verifier.py`, `core/llm_client.py`, `tests/test_drift_verifier.py` |
| 1.6 | CLI & evaluation runner | `cli.py`, `evaluation/benchmark_runner.py`, `evaluation/dataset/sample_cases.json`, `tests/test_cli.py` |

**Key Features:**
- Parses unified diffs from raw strings, local git repos, or GitHub PR patches
- Extracts semantic code changes: renamed CLI flags, changed defaults, modified parameters, removed features
- Extracts documentation sentences with exact line numbers and section hierarchy
- Hybrid retrieval: BM25 lexical + dense embeddings (nomic-embed-text) with RRF fusion
- LLM verification (gemma4:e4b) with offline heuristic fallback
- Confidence scoring (0.0–1.0) with threshold filtering
- PR doc-update guard (suppresses findings if docs already updated in PR)

---

### Phase 2: GitHub Action / CI Check (Primary Goal)

| Sub-part | Description | Files |
|----------|-------------|-------|
| 2.1 | GitHub Action packaging | `action.yml`, `Dockerfile`, `backend/github/entrypoint.sh`, `.github/workflows/drift-check-test.yml` |
| 2.2 | PR commenter & check runs | `backend/github/pr_commenter.py`, `backend/github/templates/pr_summary.md.j2`, `tests/test_pr_commenter.py` |
| 2.3 | Historical commit replay & benchmark | `backend/evaluation/historical_replayer.py`, `backend/evaluation/run_eval.py`, `backend/evaluation/dataset/real_histories.json`, `tests/test_evaluation_replayer.py` |

**Key Features:**
- Docker-based GitHub Action
- Posts PR summary comment with hidden signature (idempotent updates)
- Posts inline review comments with suggestion blocks on doc lines touched in PR
- Creates GitHub Check Runs with file annotations
- Fails CI check if drift is detected (configurable)
- Historical commit replay to evaluate drift detection before corrective commits

---

### Phase 3: Web API (FastAPI)

| Sub-part | Description | Files |
|----------|-------------|-------|
| 3.1 | FastAPI service setup & background worker | `backend/api/main.py`, `backend/api/database.py`, `backend/api/worker.py`, `tests/test_api_server.py` |
| 3.2 | API endpoints & worker | `backend/api/routes.py`, `backend/api/schemas.py`, `tests/test_api_routes.py` |

**Endpoints:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/health` | Health check + Ollama connectivity |
| `POST /api/analyze-pr` | Trigger PR drift analysis (returns run_id) |
| `POST /api/analyze-diff` | Playground: raw diff + docs analysis |
| `GET /api/runs` | List recent analysis runs |
| `GET /api/runs/{run_id}` | Run status, progress, full report |
| `POST /api/generate-fix` | AI-assisted doc fix generation |
| `GET /api/runs/{run_id}/findings/{finding_id}` | Side-by-side diff + doc sentence |
| `POST /api/fix/apply` | Downloadable git patch for fix |
| `GET /api/playground/scenarios` | Curated demo scenarios |
| `GET /api/repositories` | List tracked repositories |
| `POST /api/repositories` | Add repository to track |

---

### Phase 4: Frontend Dashboard (Stretch Goal)

**Next.js web application** with side-by-side diff visualization and interactive documentation fixes.

**Routes:**
- `/` — Landing page
- `/prs` — PR analysis list & submission
- `/prs/[owner]/[repo]/[pr_number]` — Interactive drift inspector
- `/repositories` — Monitored repositories
- `/how-to-use` — Documentation & user guide
- `/about` — Problem 05 & team details
- `/settings` — Configuration

**Frontend files:** `app/page.tsx`, `app/layout.tsx`, `app/components/`, `app/lib/api.ts`

---

## 4. Installation

### Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.9+ |
| Node.js | 18+ |
| pip | Latest |
| npm / yarn | Latest |

### Backend Setup

```bash
cd /Users/dakshdts/Documents/Hackathons/docs-drive/backend

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install fastapi uvicorn httpx aiosqlite

# Run all tests (75 tests)
.venv/bin/python3 -m pytest tests/ -v
```

### Frontend Setup

```bash
cd /Users/dakshdts/Documents/Hackathons/docs-drive

# Install dependencies
npm install

# Start development server
npm run dev   # Opens http://localhost:3000
```

---

## 5. Usage

### CLI Usage

```bash
cd backend

# Analyze local git repository
.venv/bin/python3 -m backend.cli check --repo . --base main --head HEAD

# Analyze from patch file
.venv/bin/python3 -m backend.cli check --diff pr.patch --docs-dir docs/

# Export JSON report
.venv/bin/python3 -m backend.cli check --diff pr.patch --json-output report.json --fail-on-drift

# Index documentation
.venv/bin/python3 -m backend.cli index --docs-dir docs/

# Run benchmark
.venv/bin/python3 -m backend.cli benchmark --confidence 0.75
```

### CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--repo` | `.` | Path to local git repository |
| `--base` | `main` | Base git revision |
| `--head` | `HEAD` | Head git revision |
| `--diff` | — | Path to unified diff / patch file |
| `--docs-dir` | — | Documentation directory (auto-detected if omitted) |
| `--confidence` | `0.75` | Minimum confidence threshold (0.0–1.0) |
| `--fail-on-drift` | `true` | Exit code 1 if drift detected |
| `--no-fail-on-drift` | — | Don't exit with error on drift |
| `--json-output` | — | Path to write JSON report |

---

### API Usage

Start the server:
```bash
cd backend
.venv/bin/python3 -m uvicorn api.main:app --reload --port 8000
```

Analyze a PR:
```bash
curl -X POST http://localhost:8000/api/analyze-pr \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/owner/repo", "pr_number": 42, "confidence_threshold": 0.75}'
```

Analyze a diff:
```bash
curl -X POST http://localhost:8000/api/analyze-diff \
  -H "Content-Type: application/json" \
  -d '{"diff_text": "...", "docs": [{"path": "README.md", "content": "..."}]}'
```

Generate a fix:
```bash
curl -X POST http://localhost:8000/api/generate-fix \
  -H "Content-Type: application/json" \
  -d '{"doc_sentence": "The timeout is 30 seconds.", "code_diff": "-timeout=30\n+timeout=60", "tone": "concise"}'
```

Get run status:
```bash
curl http://localhost:8000/api/runs/run_abc123
```

---

## 6. Data Schemas

### CodeSymbolChange
```python
{
  "file_path": "cli.py",
  "line_start": 5,
  "line_end": 5,
  "symbol_name": "--timeout",
  "symbol_type": "cli_argument",
  "change_type": "renamed",
  "old_value": "--timeout-secs",
  "new_value": "--timeout",
  "diff_hunk": "...",
  "description": "CLI argument renamed: '--timeout-secs' -> '--timeout'"
}
```

### DocSentence
```python
{
  "doc_path": "docs/guide.md",
  "line_number": 42,
  "sentence_index": 0,
  "sentence_text": "Run the tool with `--timeout-secs 30` to configure timeout.",
  "section_hierarchy": ["CLI Options"],
  "is_table_row": false,
  "table_headers": null,
  "raw_markdown": "..."
}
```

### DriftFinding
```python
{
  "id": "offline-1",
  "doc_path": "docs/guide.md",
  "doc_line_number": 42,
  "affected_sentence": "Run the tool with `--timeout-secs 30` to configure timeout.",
  "code_file_path": "cli.py",
  "code_line_start": 5,
  "code_line_end": 5,
  "code_diff_snippet": "...",
  "drift_type": "renamed_flag",
  "severity": "high",
  "confidence": 0.95,
  "explanation": "The CLI flag `--timeout-secs` was renamed to `--timeout` in `cli.py`.",
  "suggested_fix": "Run the tool with `--timeout` to configure timeout.",
  "is_suppressed": false,
  "suppression_reason": null
}
```

### DriftAnalysisReport
```python
{
  "repo_name": "owner/repo",
  "pr_number": 42,
  "base_commit": "abc123",
  "head_commit": "def456",
  "timestamp": "2026-10-31T10:00:00Z",
  "total_files_analyzed": 3,
  "total_symbols_changed": 5,
  "total_doc_sentences_scanned": 42,
  "findings": [...],
  "execution_time_seconds": 2.34
}
```

---

## 7. Testing

```bash
cd backend

# All tests (75 tests)
.venv/bin/python3 -m pytest tests/ -v

# Specific phase tests
.venv/bin/python3 -m pytest tests/test_diff_parser.py -v
.venv/bin/python3 -m pytest tests/test_ast_extractor.py -v
.venv/bin/python3 -m pytest tests/test_doc_parser.py -v
.venv/bin/python3 -m pytest tests/test_drift_verifier.py -v
.venv/bin/python3 -m pytest tests/test_cli.py -v
.venv/bin/python3 -m pytest tests/test_pr_commenter.py -v
.venv/bin/python3 -m pytest tests/test_evaluation_replayer.py -v
.venv/bin/python3 -m pytest tests/test_api_server.py -v
.venv/bin/python3 -m pytest tests/test_api_routes.py -v
.venv/bin/python3 -m pytest tests/test_api_playground.py -v
.venv/bin/python3 -m pytest tests/test_api_repositories.py -v
```

### Benchmark Evaluation

```bash
# Historical commit replay benchmark
.venv/bin/python3 -m evaluation.run_eval --confidence 0.75

# Phase 1 benchmark
.venv/bin/python3 -m evaluation.benchmark_runner --dataset evaluation/dataset/sample_cases.json
```

---

## 8. Serverless Deployment (Vercel)

### `vercel.json`
```json
{
  "version": 2,
  "builds": [
    {
      "src": "backend/api/main.py",
      "use": "@vercel/python"
    }
  ],
  "routes": [
    {
      "src": "/api/(.*)",
      "dest": "backend/api/main.py"
    }
  ],
  "env": {
    "PYTHONPATH": "backend",
    "CONFIDENCE_THRESHOLD": "0.75",
    "FAIL_ON_DRIFT": "true",
    "OLLAMA_URL": "http://localhost:11434",
    "MODEL_NAME": "gemma4:e4b"
  }
}
```

### Deploy
```bash
npm i -g vercel
vercel
```

**Serverless adaptations:**
- SQLite uses `/tmp` (ephemeral storage on Vercel)
- Sync execution mode (no background tasks)
- Ollama optional (offline fallback)
- Git CLI fallback handled gracefully

---

## 9. Demo Scenarios

Four curated scenarios in `demo/scenarios/`:

| # | Scenario | Code Change | Doc Impact | Severity |
|---|----------|-------------|------------|----------|
| 1 | Renamed Flag | `--timeout-secs` → `--timeout` | README mentions old flag | High |
| 2 | Changed Default | Port 8080 → 3000 | Config table stale | Medium |
| 3 | Removed Parameter | `verify_ssl` deleted | Tutorial uses removed param | Critical |
| 4 | False Alarm | Internal refactor only | No public API change | None |

Each scenario directory contains `scenario.json` with the diff, docs, and ground truth.

---

## 10. Full-Stack Integration

### `start.sh`
```bash
#!/usr/bin/env bash
set -e

echo "🚀 Starting Automated Documentation Drift Detection System..."

# Check Ollama
if curl -s http://localhost:11434/api/tags > /dev/null; then
    echo "✅ Ollama is running at http://localhost:11434"
else
    echo "⚠️ Warning: Ollama is not running on port 11434. Starting with fallback..."
fi

# Start FastAPI Backend
echo "⚡ Launching FastAPI backend on http://localhost:8000..."
cd backend
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
cd ..

# Start Next.js Frontend
echo "💻 Launching Next.js dashboard on http://localhost:3000..."
npm run dev &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID" EXIT INT TERM
wait
```

### `docker-compose.yml`
```yaml
version: "3.8"
services:
  ollama:
    image: ollama/ollama:latest
    ports: ["11434:11434"]
    volumes: ["ollama_data:/root/.ollama"]
  backend:
    build: .
    ports: ["8000:8000"]
    environment:
      - OLLAMA_URL=http://ollama:11434
      - MODEL_NAME=gemma4:e4b
    depends_on: [ollama]
  frontend:
    build: .
    ports: ["3000:3000"]
    environment:
      - NEXT_PUBLIC_API_URL=http://backend:8000
    depends_on: [backend]
volumes:
  ollama_data:
```

Run: `docker-compose up --build`

---

## 11. Frontend Integration Guide

See `MIGRATION.md` at the repository root for the complete frontend integration guide including:
- All API endpoints with request/response examples
- TypeScript interfaces (`app/lib/api.ts`)
- Frontend integration examples (PR submission, run polling, side-by-side view, fix generator)
- Common error handling
- Where to alter values
- Test commands

---

## 12. Technology Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Frontend | Next.js | 15+ |
| Frontend Styling | Tailwind CSS | v4 |
| Frontend Icons | Lucide React | Latest |
| Backend | FastAPI | 0.128+ |
| Backend Language | Python | 3.9+ |
| Data Validation | Pydantic | 2.7+ |
| Database | SQLite | Built-in |
| Task Queue | asyncio | Built-in |
| HTTP Client | httpx | Latest |
| LLM Provider | Ollama | gemma4:e4b |
| Embedding Model | nomic-embed-text | Latest |
| Lexical Search | rank-bm25 | Latest |
| Diff Parsing | unidiff | Latest |
| Git Processing | gitpython | Latest |
| Testing | pytest | 8.4+ |

---

## 13. Team

**Team Name:** HisenBugs  
**Event:** Hacktoberfest 2026 – Hack Day Sonipat  
**Problem:** Problem 05 – Automated Documentation Drift Detection

| Member | GitHub |
|--------|--------|
| Jaydev Lamror | @jaydevLamror11 |
| Rushan Advani | @rushanadv |
| Daksh Singh | @DakshSingh-GitHub |
| Sahaj Choukhani | @sahajchoukhani |

---

## 14. License

This project is developed for Hacktoberfest 2026 – Hack Day Sonipat.

---

*Last updated: Phase 4 implementation complete*