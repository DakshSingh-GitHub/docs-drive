# Automated Documentation Drift Detection - Judge Demo Script

## 3-Minute Hackathon Presentation

---

## 1. The Problem (30 seconds)

**"Have you ever read a README, followed the instructions, and the command didn't work?"**

Documentation drifts when developers update code without synchronizing associated docs. Traditional doc-test tools only check executable code blocks — they cannot evaluate natural language prose, configuration tables, CLI parameters, or architectural descriptions.

**Real-world impact:** Users follow outdated docs, commands fail, support tickets increase, developer trust erodes.

**The problem:** We need an automated CI check that catches documentation drift *before* it reaches users.

---

## 2. The CI Check (60 seconds)

**Demo:** Show GitHub Action running on a Pull Request.

1. Open a PR that renames `--timeout-secs` to `--timeout` in `cli.py`
2. The GitHub Action triggers automatically
3. The Action analyzes the PR diff, scans the documentation, and detects that `README.md` still references the old flag
4. The Action posts an inline review comment directly on the doc line with a `suggestion` block
5. The maintainer clicks "Commit suggestion" — one-click fix

**Key capabilities shown:**
- Exact sentence granularity (not whole paragraphs)
- Bi-directional code linkage (doc line → code line)
- False-positive suppression (internal refactors produce zero warnings)
- PR doc-update guard (if docs already fixed in PR, no alert)

---

## 3. The Web Dashboard (60 seconds)

**Demo:** Show the Next.js dashboard at `http://localhost:3000`.

1. **Landing Page:** Overview of drift detection capabilities and system status
2. **PR Analysis:** Submit a GitHub PR URL, watch the analysis run with real-time progress
3. **Findings Board:** List of drift issues with severity badges, confidence scores, and suggested fixes
4. **Side-by-Side View:** Code diff on the left, affected doc sentence on the right — linked by exact line numbers
5. **AI Fix Generator:** Click "Suggest Fix" to get a rewritten sentence, full paragraph, and git patch — copy-paste ready
6. **Settings:** Configure Ollama endpoint, model selection, confidence thresholds

**Tech stack:** Next.js 15 App Router, Tailwind CSS, Lucide icons, FastAPI backend, SQLite persistence

---

## 4. Architecture & Local AI (30 seconds)

**Three-phase architecture:**

- **Phase 1 — Core Engine (Python):** AST parsing, hybrid BM25 + dense embedding retrieval, LLM drift verification
- **Phase 2 — GitHub Action (CI):** Dockerized action, PR comments, check runs, historical benchmarking
- **Phase 3 — Web API (FastAPI):** REST endpoints, async background jobs, SQLite persistence
- **Phase 4 — Frontend (Next.js):** Interactive dashboard, side-by-side diff viewer, one-click fixes

**Local AI (offline-first):**
- `gemma4:e4b` — LLM for drift verification and fix generation (runs on a laptop)
- `nomic-embed-text` — Dense embeddings for indirect semantic relationships
- Falls back to deterministic heuristics when no LLM is available
- **Zero data leaves the machine** — privacy-preserving, no API keys required

---

## 5. Demo Scenarios (quick reference)

| # | Scenario | Code Change | Doc Impact | Severity |
|---|----------|-------------|------------|----------|
| 1 | Renamed Flag | `--timeout-secs` → `--timeout` | README mentions old flag | High |
| 2 | Changed Default | Port 8080 → 3000 | Config table stale | Medium |
| 3 | Removed Param | `verify_ssl` deleted | Tutorial uses removed param | Critical |
| 4 | False Alarm | Internal refactor only | No public API change | None (0 drift) |

---

## 6. Closing (15 seconds)

**"Documentation should never lie about code."**

Automated Documentation Drift Detection catches docs that became wrong, before your users find out the hard way. Local, private, CI-integrated, and ready to drop into any repository.

**Team HisenBugs** — Hacktoberfest 2026, Hack Day Sonipat
**Problem 05:** Automated Documentation Drift Detection

---
