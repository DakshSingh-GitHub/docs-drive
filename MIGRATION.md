# MIGRATION.md — Frontend Integration Guide

**Automated Documentation Drift Detection**
**Team:** HisenBugs | **Event:** Hacktoberfest 2026 – Hack Day Sonipat

This document is the **complete reference for frontend developers** integrating the Next.js dashboard with the backend API. It covers every API endpoint, workflow, data schema, environment setup, and integration step needed to build the Phase 4 Next.js frontend.

---

## 1. Project Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Next.js Frontend (Port 3000)                │
│  app/lib/api.ts — Typed fetch client for backend API           │
└─────────────────────────────────────────────────────────────────┘
                            │ HTTPS / JSON
                            │ (CORS enabled for localhost:3000)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI Backend (Port 8000)                 │
│  backend/api/main.py — FastAPI app with CORS + lifespan       │
│  backend/api/routes.py — 10 REST endpoints                     │
│  backend/api/worker.py — Async + sync job manager              │
│  backend/api/database.py — SQLite persistence                  │
│  backend/api/schemas.py — Pydantic v2 request/response models  │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
                    Phase 1 Core Engine (Python)
                    backend/core/
                    ├── diff_parser.py    # Unified diff parsing
                    ├── ast_extractor.py  # Semantic change extraction
                    ├── doc_parser.py     # Markdown sentence parser
                    ├── hybrid_matcher.py # BM25 + embeddings linker
                    ├── drift_verifier.py # LLM drift verification
                    └── models.py         # Pydantic v2 schemas
```

---

## 2. Environment Setup

### 2.1 Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Node.js | 18+ | Frontend runtime |
| npm / yarn | Latest | Package manager |
| Python | 3.9+ | Backend runtime |
| pip | Latest | Python package manager |

### 2.2 Backend Setup

```bash
cd /Users/dakshdts/Documents/Hackathons/docs-drive/backend

# Create virtual environment (if not already created)
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install fastapi uvicorn httpx aiosqlite

# Verify installation
python3 -m pytest tests/ -v   # All 75 tests should pass
```

### 2.3 Frontend Setup

```bash
cd /Users/dakshdts/Documents/Hackathons/docs-drive

# Install Node dependencies
npm install
# or: yarn install

# Verify
npm run build   # Should compile without errors
```

### 2.4 Environment Variables

Create a `.env.local` file in the project root:

```bash
# Frontend environment variables
NEXT_PUBLIC_API_URL=http://localhost:8000

# Optional: Override default API base URL
# NEXT_PUBLIC_API_URL=https://your-backend.vercel.app
```

### 2.5 Start Services (Local Development)

**Option A: Manual start**
```bash
# Terminal 1 — Backend
cd backend
source .venv/bin/activate
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — Frontend
cd /Users/dakshdts/Documents/Hackathons/docs-drive
npm run dev   # Starts on http://localhost:3000
```

**Option B: Unified start script**
```bash
cd /Users/dakshdts/Documents/Hackathons/docs-drive
chmod +x start.sh
./start.sh   # Starts both backend and frontend
```

**Option C: Docker**
```bash
cd /Users/dakshdts/Documents/Hackathons/docs-drive
docker-compose up --build
```

### 2.6 Verify Services

| Service | URL | Expected Response |
|---------|-----|-------------------|
| Backend Health | `http://localhost:8000/api/health` | `{"status": "ok", ...}` |
| API Docs | `http://localhost:8000/docs` | Swagger UI |
| Frontend | `http://localhost:3000` | Next.js landing page |

---

## 3. Complete API Reference

All endpoints are prefixed with `/api/`. Base URL: `http://localhost:8000`

### 3.1 Health Check

**`GET /api/health`**

Returns service health and Ollama connectivity status.

**Request:** None

**Response:**
```json
{
  "status": "ok",
  "ollama": {
    "available": true,
    "models": ["gemma4:e4b", "nomic-embed-text"],
    "error": null
  },
  "version": "1.0.0"
}
```

**Frontend usage:** Poll this on app mount to show Ollama connectivity indicator.

---

### 3.2 Analyze PR

**`POST /api/analyze-pr`**

Triggers drift analysis for a GitHub Pull Request. Enqueues a background job and returns immediately with a `run_id`.

**Request:**
```json
{
  "repo_url": "https://github.com/owner/repo",
  "pr_number": 42,
  "github_token": "ghp_xxxxxxxxxxxx",
  "confidence_threshold": 0.75
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `repo_url` | string (URL) | Yes | GitHub repository URL |
| `pr_number` | integer | Yes | Pull request number |
| `github_token` | string | No | GitHub token for private repos |
| `confidence_threshold` | float (0.0–1.0) | No | Minimum confidence (default: 0.75) |

**Response (202 Accepted):**
```json
{
  "run_id": "run_abc123def456",
  "status": "queued",
  "message": "PR analysis queued. Poll /api/runs/{run_id} for progress."
}
```

**Frontend flow:**
1. Call this endpoint when user submits PR URL
2. Store `run_id` in component state
3. Poll `GET /api/runs/{run_id}` every 3 seconds until status is `completed` or `failed`

---

### 3.3 Analyze Diff (Playground Mode)

**`POST /api/analyze-diff`**

Analyzes a raw unified diff and documentation content synchronously. Returns findings immediately.

**Request:**
```json
{
  "diff_text": "diff --git a/cli.py b/cli.py\n-    parser.add_argument('--timeout-secs')\n+    parser.add_argument('--timeout')\n",
  "docs": [
    {
      "path": "README.md",
      "content": "# CLI Tool\n\nRun the tool with `--timeout-secs 30` to configure timeout."
    }
  ],
  "confidence_threshold": 0.75
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `diff_text` | string | Yes | Unified diff text |
| `docs` | array of `{path, content}` | No | Documentation files to scan |
| `confidence_threshold` | float (0.0–1.0) | No | Minimum confidence (default: 0.75) |

**Response (200 OK):**
```json
{
  "run_id": "run_abc123",
  "status": "completed",
  "findings_count": 1,
  "report": {
    "repo_name": "playground",
    "pr_number": null,
    "total_files_analyzed": 1,
    "total_symbols_changed": 1,
    "total_doc_sentences_scanned": 5,
    "findings": [...],
    "execution_time_seconds": 0.5
  }
}
```

---

### 3.4 List Runs

**`GET /api/runs?limit=50`**

Returns recent analysis runs ordered by created_at DESC.

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | 50 | Maximum number of runs to return |

**Response:**
```json
{
  "runs": [
    {
      "id": "run_abc123",
      "repo_name": "owner/repo",
      "pr_number": 42,
      "base_commit": "abc123",
      "head_commit": "def456",
      "status": "completed",
      "progress": 100,
      "current_step": null,
      "total_findings": 3,
      "report_json": "...",
      "error_message": null,
      "created_at": "2026-10-31T10:00:00Z",
      "completed_at": "2026-10-31T10:02:30Z"
    }
  ],
  "total": 5
}
```

---

### 3.5 Get Run Status

**`GET /api/runs/{run_id}`**

Returns real-time status and progress. When completed, includes the full `DriftAnalysisReport`.

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `run_id` | string | Run ID returned from `/api/analyze-pr` or `/api/analyze-diff` |

**Response (completed):**
```json
{
  "run_id": "run_abc123",
  "status": "completed",
  "progress": 100,
  "current_step": null,
  "report": {
    "repo_name": "owner/repo",
    "pr_number": 42,
    "base_commit": "abc123",
    "head_commit": "def456",
    "timestamp": "2026-10-31T10:00:00Z",
    "total_files_analyzed": 3,
    "total_symbols_changed": 5,
    "total_doc_sentences_scanned": 42,
    "findings": [
      {
        "id": "offline-xyz789",
        "doc_path": "docs/guide.md",
        "doc_line_number": 42,
        "affected_sentence": "Run the tool with `--timeout-secs 30` to configure timeout.",
        "code_file_path": "cli.py",
        "code_line_start": 5,
        "code_line_end": 5,
        "code_diff_snippet": "-    parser.add_argument('--timeout-secs', type=int, default=30)\n+    parser.add_argument('--timeout', type=int, default=60)",
        "drift_type": "renamed_flag",
        "severity": "high",
        "confidence": 0.95,
        "explanation": "The CLI flag `--timeout-secs` was renamed to `--timeout` in `cli.py`.",
        "suggested_fix": "Run the tool with `--timeout` to configure timeout."
      }
    ],
    "execution_time_seconds": 2.34
  },
  "error_message": null
}
```

**Response (processing):**
```json
{
  "run_id": "run_abc123",
  "status": "processing",
  "progress": 50,
  "current_step": "Scanning documentation & computing embeddings",
  "report": null,
  "error_message": null
}
```

---

### 3.6 Generate Fix

**`POST /api/generate-fix`**

Invokes Ollama gemma4:e4b to produce a high-fidelity documentation correction.

**Request:**
```json
{
  "finding_id": "optional-finding-id",
  "doc_sentence": "The default timeout is 30 seconds.",
  "code_diff": "-def main(timeout=30):\n+def main(timeout=60):",
  "tone": "concise"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `finding_id` | string | No | ID of existing drift finding |
| `doc_sentence` | string | Yes | Outdated documentation sentence |
| `code_diff` | string | Yes | Responsible code diff snippet |
| `tone` | string | No | Fix tone: `concise`, `detailed`, or `tutorial` (default: `concise`) |

**Response:**
```json
{
  "original_sentence": "The default timeout is 30 seconds.",
  "suggested_sentence": "The default timeout is 60 seconds.",
  "suggested_paragraph": "The default connection timeout is 60 seconds.",
  "git_patch": "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-The default timeout is 30 seconds.\n+The default timeout is 60 seconds.",
  "explanation": "Timeout default changed from 30 to 60 seconds."
}
```

---

### 3.7 Finding Detail (Side-by-Side)

**`GET /api/runs/{run_id}/findings/{finding_id}`**

Returns isolated finding with code diff hunk and doc sentence for side-by-side comparison view.

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `run_id` | string | Run ID |
| `finding_id` | string | Finding ID from the report |

**Response:**
```json
{
  "finding": {
    "id": "offline-xyz789",
    "doc_path": "docs/guide.md",
    "doc_line_number": 42,
    "affected_sentence": "Run the tool with `--timeout-secs 30` to configure timeout.",
    "code_file_path": "cli.py",
    "code_line_start": 5,
    "code_line_end": 5,
    "code_diff_snippet": "-    parser.add_argument('--timeout-secs')\n+    parser.add_argument('--timeout')",
    "drift_type": "renamed_flag",
    "severity": "high",
    "confidence": 0.95,
    "explanation": "The CLI flag `--timeout-secs` was renamed to `--timeout` in `cli.py`.",
    "suggested_fix": "Run the tool with `--timeout` to configure timeout."
  },
  "code_diff_hunk": "-    parser.add_argument('--timeout-secs', type=int, default=30)\n+    parser.add_argument('--timeout', type=int, default=60)\n     parser.add_argument('--verbose', action='store_true')",
  "doc_sentence": "Run the tool with `--timeout-secs 30` to configure timeout.",
  "doc_line_number": 42
}
```

---

### 3.8 Apply Fix (Git Patch)

**`POST /api/fix/apply`**

Generates a downloadable unified git patch for applying the suggested fix.

**Request:**
```json
{
  "finding_id": "offline-xyz789",
  "fix_type": "sentence"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `finding_id` | string | Yes | ID of drift finding |
| `fix_type` | string | No | `sentence` or `paragraph` (default: `sentence`) |

**Response:**
```json
{
  "git_patch": "--- a/docs/guide.md\n+++ b/docs/guide.md\n@@ -42,1 +42,1 @@\n-Run the tool with `--timeout-secs 30` to configure timeout.\n+Run the tool with `--timeout 30` to configure timeout.",
  "filename": "fix-drift-docs_guide_md-L42.patch"
}
```

---

### 3.9 Playground Scenarios

**`GET /api/playground/scenarios`**

Returns curated demo scenarios loaded from external `demo/scenarios/*/scenario.json` files.

**Response:**
```json
{
  "scenarios": [
    {
      "id": "scenario-01-renamed-flag",
      "name": "CLI Flag Renamed",
      "description": "--timeout-secs renamed to --timeout in CLI argument parser",
      "category": "renamed_flag",
      "severity": "high",
      "diff": "diff --git a/cli.py b/cli.py\n-    parser.add_argument('--timeout-secs', type=int, default=30)\n+    parser.add_argument('--timeout', type=int, default=60)",
      "docs": {
        "README.md": "# CLI Tool\n\nRun the tool with `--timeout-secs 30` to configure timeout."
      },
      "expected_drift": true,
      "expected_file": "README.md",
      "expected_symbol": "--timeout-secs",
      "drift_type": "renamed_flag",
      "severity": "high",
      "description_for_judges": "The CLI flag `--timeout-secs` was renamed to `--timeout` in the code, but the README still tells users to use the old flag name."
    },
    {
      "id": "scenario-02-changed-default",
      "name": "Default Port Changed",
      "description": "Default server port changed from 8080 to 3000",
      "category": "incorrect_default",
      "severity": "medium",
      "diff": "diff --git a/config.py b/config.py\n-DEFAULT_PORT = 8080\n+DEFAULT_PORT = 3000",
      "docs": {
        "docs/setup.md": "# Setup\n\nThe development server listens on port 8080 by default."
      },
      "expected_drift": true,
      "expected_file": "docs/setup.md",
      "expected_symbol": "8080",
      "drift_type": "incorrect_default",
      "severity": "medium",
      "description_for_judges": "The default server port was changed from 8080 to 3000 in config.py, but the documentation still states port 8080."
    },
    {
      "id": "scenario-03-removed-parameter",
      "name": "API Parameter Removed",
      "description": "Deprecated parameter verify_ssl removed from Client.connect()",
      "category": "param_removed",
      "severity": "critical",
      "diff": "diff --git a/client.py b/client.py\n-    def connect(self, host, port, verify_ssl=True):\n+    def connect(self, host, port):",
      "docs": {
        "docs/client.md": "# Client API\n\nTo connect, call `client.connect(host, port, verify_ssl=False)` to disable SSL verification."
      },
      "expected_drift": true,
      "expected_file": "docs/client.md",
      "expected_symbol": "verify_ssl",
      "drift_type": "param_removed",
      "severity": "critical",
      "description_for_judges": "The `verify_ssl` parameter was removed from the `connect()` method, but the documentation still instructs users to pass it."
    },
    {
      "id": "scenario-04-false-alarm-refactor",
      "name": "Internal Refactor (No Drift)",
      "description": "Internal socket refactoring that leaves public API unchanged",
      "category": "no_drift",
      "severity": "none",
      "diff": "diff --git a/utils/socket.py b/utils/socket.py\n-import socket\n+import selectors\n\ndef process_data(data):\n     return data",
      "docs": {
        "README.md": "# Architecture\n\nOur utility functions provide efficient data processing and network communication."
      },
      "expected_drift": false,
      "expected_file": null,
      "expected_symbol": null,
      "drift_type": "none",
      "severity": "none",
      "description_for_judges": "Internal implementation changed but the public API and behavior remain identical. The system correctly produces ZERO drift findings."
    }
  ]
}
```

---

### 3.10 Repository Tracking

**`GET /api/repositories`**

Returns tracked/monitored repositories.

**Response:**
```json
{
  "repositories": [
    { "owner": "tiangolo", "repo": "fastapi" },
    { "owner": "psf", "repo": "requests" }
  ]
}
```

**`POST /api/repositories`**

Adds a repository to track.

**Request:**
```json
{
  "owner": "tiangolo",
  "repo": "fastapi"
}
```

**Response:**
```json
{
  "repositories": [
    { "owner": "tiangolo", "repo": "fastapi" }
  ]
}
```

---

## 4. Data Schemas (TypeScript)

Create `app/lib/api.ts` with these TypeScript interfaces:

```typescript
// API base URL
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Helper for API calls
export async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

// Drift Finding
export interface DriftFinding {
  id: string;
  doc_path: string;
  doc_line_number: number;
  affected_sentence: string;
  code_file_path: string;
  code_line_start: number;
  code_line_end: number;
  code_diff_snippet: string;
  drift_type: 'outdated_parameter' | 'renamed_flag' | 'incorrect_default' | 'removed_feature' | 'behavioral_discrepancy' | 'none';
  severity: 'critical' | 'high' | 'medium' | 'low';
  confidence: number;
  explanation: string;
  suggested_fix: string;
  is_suppressed: boolean;
  suppression_reason: string | null;
}

// Drift Analysis Report
export interface DriftAnalysisReport {
  repo_name: string;
  pr_number: number | null;
  base_commit: string;
  head_commit: string;
  timestamp: string;
  total_files_analyzed: number;
  total_symbols_changed: number;
  total_doc_sentences_scanned: number;
  findings: DriftFinding[];
  execution_time_seconds: number;
}

// Analysis Run
export interface AnalysisRun {
  id: string;
  repo_name: string;
  pr_number: number | null;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  progress: number;
  current_step: string | null;
  total_findings: number;
  report: DriftAnalysisReport | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

// Scenario
export interface Scenario {
  id: string;
  name: string;
  description: string;
  category: string;
  severity: string;
  diff: string;
  docs: Record<string, string>;
  expected_drift: boolean;
  expected_file: string | null;
  expected_symbol: string | null;
  drift_type: string | null;
  severity_level: string | null;
  description_for_judges: string;
}

// Repository Track
export interface RepositoryTrack {
  owner: string;
  repo: string;
}

// Side-by-Side Result
export interface SideBySideResult {
  finding: DriftFinding;
  code_diff_hunk: string;
  doc_sentence: string;
  doc_line_number: number;
}

// Fix Apply Result
export interface FixApplyResult {
  git_patch: string;
  filename: string;
}
```

---

## 5. Frontend Integration Examples

### 5.1 PR Submission Form

```tsx
// app/prs/page.tsx
import { apiRequest, AnalysisRun } from '@/lib/api';

export default function PRPage() {
  const [repoUrl, setRepoUrl] = useState('');
  const [prNumber, setPrNumber] = useState('');
  const [runId, setRunId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await apiRequest<{ run_id: string; status: string }>(
        '/api/analyze-pr',
        {
          method: 'POST',
          body: JSON.stringify({
            repo_url: repoUrl,
            pr_number: parseInt(prNumber, 10),
            confidence_threshold: 0.75,
          }),
        }
      );
      setRunId(res.run_id);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <input
        placeholder="GitHub PR URL"
        value={repoUrl}
        onChange={(e) => setRepoUrl(e.target.value)}
      />
      <input
        placeholder="PR Number"
        type="number"
        value={prNumber}
        onChange={(e) => setPrNumber(e.target.value)}
      />
      <button type="submit" disabled={loading}>
        {loading ? 'Analyzing...' : 'Start Drift Analysis'}
      </button>
    </form>
  );
}
```

### 5.2 Poll Run Status

```tsx
// app/prs/[owner]/[repo]/[pr_number]/page.tsx
import { apiRequest, AnalysisRun, DriftFinding } from '@/lib/api';
import { useEffect, useState } from 'react';

export default function PRDetailPage({
  params,
}: { params: { owner: string; repo: string; pr_number: string } }) {
  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [pollInterval, setPollInterval] = useState<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const fetchRun = async () => {
      const run = await apiRequest<AnalysisRun>(`/api/runs/${runId}`);
      setRun(run);
      if (run.status === 'completed' || run.status === 'failed') {
        clearInterval(pollInterval);
      }
    };

    fetchRun();
    const interval = setInterval(fetchRun, 3000);
    setPollInterval(interval);

    return () => clearInterval(interval);
  }, [runId]);

  if (!run) return <div>Loading...</div>;
  if (run.status === 'failed') return <div>Analysis failed: {run.error_message}</div>;
  if (run.status !== 'completed') return <div>Analyzing... {run.progress}%</div>;

  return <FindingsBoard findings={run.report?.findings || []} />;
}
```

### 5.3 Side-by-Side Comparison

```tsx
// app/components/SideBySideComparison.tsx
import { SideBySideResult } from '@/lib/api';
import { apiRequest } from '@/lib/api';

export default function SideBySideComparison({
  runId,
  findingId,
}: { runId: string; findingId: string }) {
  const [result, setResult] = useState<SideBySideResult | null>(null);

  useEffect(() => {
    apiRequest<SideBySideResult>(
      `/api/runs/${runId}/findings/${findingId}`
    ).then(setResult);
  }, [runId, findingId]);

  if (!result) return <div>Loading...</div>;

  return (
    <div className="flex flex-col md:flex-row gap-4">
      <div className="w-full bg-gray-900 p-4 rounded">
        <h3>Code Change</h3>
        <pre className="text-sm font-mono text-red-200">
          {result.code_diff_hunk}
        </pre>
      </div>
      <div className="w-full bg-gray-900 p-4 rounded">
        <h3>Documentation</h3>
        <pre className="text-sm font-mono text-amber-200">
          {result.doc_sentence}
        </pre>
      </div>
    </div>
  );
}
```

### 5.4 Generate Fix

```tsx
// app/components/FixSuggesterModal.tsx
import { apiRequest, FixApplyResult } from '@/lib/api';

export default function FixSuggesterModal({
  finding,
}: { finding: DriftFinding }) {
  const [fix, setFix] = useState<FixApplyResult | null>(null);

  const generateFix = async () => {
    const res = await apiRequest<FixApplyResult>('/api/generate-fix', {
      method: 'POST',
      body: JSON.stringify({
        doc_sentence: finding.affected_sentence,
        code_diff: finding.code_diff_snippet,
        tone: 'concise',
      }),
    });
    setFix(res);
  };

  return (
    <div className="p-4 bg-gray-900 rounded">
      <h3>AI Suggested Fix</h3>
      <p>Original: {finding.affected_sentence}</p>
      <p>Fix: {fix?.suggested_sentence}</p>
      <button onClick={generateFix}>Generate Fix</button>
      {fix && (
        <pre className="text-sm font-mono text-green-200">
          {fix.git_patch}
        </pre>
      )}
    </div>
  );
}
```

---

## 5.5 Playground Scenarios

```tsx
// app/prs/page.tsx - Playground tab
import { apiRequest, Scenario } from '@/lib/api';

export default function Playground() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);

  useEffect(() => {
    apiRequest<{ scenarios: Scenario[] }>('/api/playground/scenarios')
      .then((data) => setScenarios(data.scenarios));
  }, []);

  return (
    <div>
      <h2>Demo Scenarios</h2>
      {scenarios.map((s) => (
        <div key={s.id}>
          <h3>{s.name}</h3>
          <p>{s.description}</p>
          <p>Expected drift: {s.expected_drift ? 'Yes' : 'No'}</p>
        </div>
      ))}
    </div>
  );
}
```

---

## 5.6 Repository Tracking

```tsx
// app/repositories/page.tsx
import { apiRequest, RepositoryTrack } from '@/lib/api';

export default function RepositoriesPage() {
  const [repos, setRepos] = useState<RepositoryTrack[]>([]);

  useEffect(() => {
    apiRequest<{ repositories: RepositoryTrack[] }>('/api/repositories')
      .then((data) => setRepos(data.repositories));
  }, []);

  const addRepo = async (owner: string, repo: string) => {
    await apiRequest<{ repositories: RepositoryTrack[] }>(
      '/api/repositories',
      {
        method: 'POST',
        body: JSON.stringify({ owner, repo }),
      }
    );
    // Refresh list
  };

  return (
    <div>
      <h2>Tracked Repositories</h2>
      <ul>
        {repos.map((r) => (
          <li key={`${r.owner}/${r.repo}`}>
            {r.owner}/{r.repo}
          </li>
        ))}
      </ul>
    </div>
  );
}
```

---

## 6. Common Error Handling

| Error | Cause | Fix |
|-------|-------|-----|
| `404 Not Found` (run) | Invalid `run_id` | Verify `run_id` was returned from `/api/analyze-pr` |
| `404 Not Found` (finding) | Invalid `finding_id` | Verify finding ID exists in the run's report |
| `500 Internal Server Error` | Ollama unavailable | Check `/api/health`; fix generation will use fallback |
| `422 Unprocessable Entity` | Invalid request body | Validate request schema matches TypeScript interfaces |
| `CORS error` | Frontend on different port | Add origin to CORS in `backend/api/main.py` |

---

## 7. Demo Scenarios for Testing

| Scenario | ID | Expected Drift | Severity |
|----------|----|---------------|----------|
| Renamed CLI flag | `scenario-01-renamed-flag` | Yes | High |
| Changed default port | `scenario-02-changed-default` | Yes | Medium |
| Removed API parameter | `scenario-03-removed-parameter` | Yes | Critical |
| Internal refactor (no drift) | `scenario-04-false-alarm-refactor` | No | None |

Fetch scenarios via `GET /api/playground/scenarios` and use them to test the frontend end-to-end.

---

## 8. Quick Start Checklist

- [ ] Install Node.js dependencies: `npm install`
- [ ] Install Python dependencies: `cd backend && .venv/bin/pip install -r requirements.txt`
- [ ] Create `.env.local` with `NEXT_PUBLIC_API_URL=http://localhost:8000`
- [ ] Start backend: `.venv/bin/python3 -m uvicorn api.main:app --reload --port 8000`
- [ ] Start frontend: `npm run dev`
- [ ] Verify health: `curl http://localhost:8000/api/health`
- [ ] Test analyze-diff: `POST /api/analyze-diff` with sample diff
- [ ] Test generate-fix: `POST /api/generate-fix` with sample text
- [ ] Test playground scenarios: `GET /api/playground/scenarios`
- [ ] Build frontend: `npm run build`
- [ ] Deploy: `vercel` or `docker-compose up --build`

---

## 9. Support

- **Backend logs:** Check terminal running `uvicorn`
- **Frontend logs:** Check browser console and Next.js terminal
- **Ollama issues:** Run `ollama list` to verify models; run `ollama pull gemma4:e4b nomic-embed-text` to install models
- **Git issues:** Ensure `git` is installed and repository is accessible

---

*Documentation version: 1.0*
*Last updated: Phase 4 implementation complete*