  # Phase 1: Core Analysis Engine

## Overview

Phase 1 is the **Core Analysis Engine** of the Automated Documentation Drift Detection system. It is a local Python CLI/script that analyzes code changes (git diffs, PR patches, or local repositories) and identifies documentation sentences that have become outdated due to those code changes.

The engine works offline by default — it does not require Ollama, OpenAI, Anthropic, or network access. When those providers are unavailable, it falls back to deterministic heuristic detection that produces comparable results on well-formed change/sentence pairs.

## What It Detects

Phase 1 targets four primary documentation drift categories:

| Category | Description | Example |
|----------|-------------|---------|
| **Renamed CLI flags** | A command-line flag or config option is renamed in code but docs still reference the old name | `--timeout-secs` → `--timeout` in `argparse` |
| **Changed default values** | A default value changes in code but docs state the old default | `timeout=30` → `timeout=60` |
| **Modified API parameters** | Function signatures change (added/removed/reordered params) but tutorials describe old signatures | `retry_count=3` → `retry_count=5` |
| **Removed/deprecated features** | Functions, classes, or endpoints are deleted but docs still describe them | `fetch_legacy()` removed |

It also suppresses false positives when:
- A PR already updated the corresponding documentation sentence (PR doc-update guard)
- The code change is purely internal and does not alter documented behavior
- The documentation sentence is general enough to remain true despite the change

## How It Works (Architecture)

Phase 1 follows a pipeline of six stages, implemented across `backend/core/`:

```
┌─────────────┐    ┌──────────────┐    ┌──────────────┐
│   Input     │───▶│ Diff Parser  │───▶│ AST Extractor│
│  (git diff) │    │              │    │              │
└─────────────┘    └──────────────┘    └──────────────┘
                                             │
                                             ▼
┌─────────────┐    ┌──────────────┐    ┌──────────────┐
│   Output    │◀───│ Drift Verify │◀───│ Hybrid Match │
│   (JSON)    │    │   (LLM)      │    │ (BM25+Emb)   │
└─────────────┘    └──────────────┘    └──────────────┘
                                             │
                                             ▼
                              ┌───────────────────────┐
                              │   Doc Parser          │
                              │ (line-mapped docs)    │
                              └───────────────────────┘
```

### 1.1 Diff Parser (`core/diff_parser.py`)
Ingests unified diffs from three sources:
- Raw unified diff string (`.patch` file or stdin)
- Local git repository (`git diff base...head` via GitPython)
- GitHub PR diff URL (via PyGithub or GitHub REST API)

Classifies each file as code (`.py`, `.ts`, `.js`, `.go`, `.rs`, etc.) or documentation (`.md`, `.mdx`, `.rst`, `.txt`, `.adoc`). Ignores binary files, lock files (`package-lock.json`, `poetry.lock`, etc.), vendor directories, and build artifacts. Records which documentation lines were already modified in the PR (for the doc-update guard).

Output: `DiffFile` objects with hunks, added/removed lines, and exact line numbers.

### 1.2 AST Symbol Extractor (`core/ast_extractor.py`)
Analyzes code diffs to extract semantic code mutations rather than treating them as plain text:

- **Python**: uses native `ast` + regex patterns for argparse, click, typer, `os.environ.get`, function defaults, module constants
- **TypeScript/JavaScript**: regex patterns for `function`, arrow functions, `yargs`, `commander`, property defaults

Detects: renamed CLI flags, changed defaults, parameter modifications/additions/removals, removed/deprecated functions/classes.

Output: `List[CodeSymbolChange]` with old/new values, diff hunk, line ranges, and descriptions.

### 1.3 Doc Parser (`core/doc_parser.py`)
Parses Markdown (`.md`, `.mdx`) and reStructuredText (`.rst`) documentation into individual sentences, each retaining:

- Exact source line number
- Section hierarchy (headings: `["Configuration", "CLI Options"]`)
- Table row metadata (headers, synthesized descriptive text)
- Whether it's a table row, list item, or paragraph prose

Discovers documentation files in `README.md`, `CONTRIBUTING.md`, `docs/`, `doc/`, `documentation/`, `wiki/`, `guide/`.

Output: `List[DocSentence]` per file.

### 1.4 Hybrid Matcher (`core/hybrid_matcher.py`, `core/embeddings.py`)
Links code symbol changes to documentation sentences using a hybrid of:

- **Exact symbol search** (direct references like `--timeout`)
- **BM25 lexical retrieval** (keyword overlap)
- **Dense vector embeddings** via local Ollama `nomic-embed-text` (indirect semantic relationships)

Falls back to deterministic offline pseudo-embeddings when Ollama is unavailable.

Uses Reciprocal Rank Fusion (RRF) to combine lexical and vector scores, with an exact-symbol-name boost. Applies the PR doc-update guard to discard sentences already fixed in the PR.

Output: Top-K `(CodeSymbolChange, DocSentence)` candidate pairs.

### 1.5 Drift Verifier (`core/drift_verifier.py`, `core/llm_client.py`)
Verifies each candidate pair using an LLM:

- **Primary**: local Ollama `gemma4:e4b` (temperature 0.1, strict JSON output)
- **Fallback**: OpenAI / Anthropic via environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`)
- **Offline**: deterministic simulator when no provider is available

Applies chain-of-thought reasoning, assigns:
- `is_drift` (true/false)
- `drift_type`, `severity` (critical/high/medium/low)
- `confidence` (0.0–1.0), threshold 0.70
- `explanation` and `suggested_fix`

Output: `List[DriftFinding]`.

### 1.6 CLI (`backend/cli.py`)
Typer + Rich terminal interface with three commands:

- `check` — analyze a git repo/patch for drift, output table + JSON report
- `index` — index a repository's documentation
- `benchmark` — run Precision/Recall/F1 against historical scenarios

## Directory Structure (backend/)

```
backend/
├── __init__.py
├── cli.py                      # Typer CLI (check/index/benchmark)
├── pyproject.toml
├── requirements.txt
├── core/
│   ├── __init__.py
│   ├── models.py               # Pydantic v2 schemas
│   ├── diff_parser.py          # Stage 1.1: unified diff ingestion
│   ├── ast_extractor.py        # Stage 1.2: semantic change extraction
│   ├── languages/
│   │   ├── __init__.py
│   │   ├── python_extractor.py
│   │   └── typescript_extractor.py
│   ├── doc_parser.py           # Stage 1.3: doc sentence parser
│   ├── embeddings.py           # Stage 1.4: embedding service
│   ├── hybrid_matcher.py       # Stage 1.4: BM25 + embedding linker
│   ├── llm_client.py           # Stage 1.5: LLM client
│   └── drift_verifier.py       # Stage 1.5: drift verification
├── evaluation/
│   ├── benchmark_runner.py     # Stage 1.6: benchmark suite
│   └── dataset/
│       └── sample_cases.json   # 6 ground-truth scenarios
└── tests/
    ├── __init__.py
    ├── test_cli.py
    ├── test_diff_parser.py
    ├── test_ast_extractor.py
    ├── test_doc_parser.py
    └── test_drift_verifier.py
```

## Installation

```bash
cd backend

# Option A: Use the provided virtual environment
.venv/bin/pip install -r requirements.txt

# Option B: Create a fresh virtual environment
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

No Ollama models are required. The engine runs fully offline.

## Usage

### Command: `check`

Analyze a local git repository:

```bash
.venv/bin/python3 -m backend.cli check --repo . --base main --head HEAD
```

Analyze from a patch file:

```bash
.venv/bin/python3 -m backend.cli check --diff pr.patch --docs-dir docs/
```

Export structured JSON:

```bash
.venv/bin/python3 -m backend.cli check --diff pr.patch --json-output report.json --fail-on-drift
```

Options:
- `--repo PATH` — git repo path (default: `.`)
- `--base TEXT` — base revision (default: `main`)
- `--head TEXT` — head revision (default: `HEAD`)
- `--diff PATH` — patch file (bypasses git)
- `--docs-dir PATH` — docs directory (default: auto-detect)
- `--confidence FLOAT` — min confidence threshold (default: `0.75`)
- `--fail-on-drift / --no-fail-on-drift` — exit 1 if drift found (default: on)
- `--json-output PATH` — write `DriftAnalysisReport` JSON

Example output:

```
======================================================================
Documentation Drift Report - . (main..HEAD)
======================================================================
Files analyzed: 1 | Symbols changed: 1 | Doc sentences scanned: 2
Exec time: 0.01s
┏━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ ID          ┃ Severity ┃ Document       ┃ Line ┃ Description    ┃ Confidence ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ # offline-1 │ ⚠ HIGH   │ docs/guide.md  ┃ 1    ┃ Run the tool   ┃ 75%        ┃
└─────────────┴──────────┴────────────────┴──────┴────────────────┴────────────┘

Summary: 1 drift issue(s) detected (1 high).
```

JSON report (`report.json`):

```json
{
  "repo_name": ".",
  "pr_number": null,
  "base_commit": "main",
  "head_commit": "HEAD",
  "timestamp": "2026-10-31T16:55:01.894409+00:00",
  "total_files_analyzed": 1,
  "total_symbols_changed": 1,
  "total_doc_sentences_scanned": 64,
  "findings": [
    {
      "id": "offline-1",
      "doc_path": "docs/guide.md",
      "doc_line_number": 1,
      "affected_sentence": "Run the tool with `--timeout-secs 30` to set the connection timeout.",
      "code_file_path": "cli.py",
      "code_line_start": 5,
      "code_line_end": 5,
      "code_diff_snippet": "-parser.add_argument('--timeout-secs')\n+parser.add_argument('--timeout')",
      "drift_type": "renamed_flag",
      "severity": "high",
      "confidence": 0.75,
      "explanation": "The documentation still references the old flag '--timeout-secs', but the code now uses '--timeout'.",
      "suggested_fix": "Run the tool with `--timeout 30` to set the connection timeout.",
      "is_suppressed": false,
      "suppression_reason": null
    }
  ],
  "execution_time_seconds": 0.0218
}
```

### Command: `index`

Index a repository's documentation for inspection:

```bash
.venv/bin/python3 -m backend.cli index --docs-dir docs/ --json-output index.json
```

### Command: `benchmark`

Run the historical evaluation benchmark:

```bash
.venv/bin/python3 -m backend.cli benchmark --confidence 0.5
```

Computes Precision, Recall, and F1 against `backend/evaluation/dataset/sample_cases.json` (6 curated scenarios covering renamed flags, changed defaults, internal refactors, env var renames, port changes, and already-updated docs).

## Running Tests

```bash
cd backend
.venv/bin/python3 -m pytest tests/ -v
```

All 36 tests pass.

## Programmatic API

The engine can also be used programmatically:

```python
from core.diff_parser import parse_diff
from core.doc_parser import DocParser
from core.ast_extractor import ASTSymbolExtractor
from core.hybrid_matcher import HybridMatcher, run_hybrid_matcher
from core.drift_verifier import detect_documentation_drift

# 1. Parse a diff
parser = parse_diff("diff --git a/cli.py b/cli.py ...")

# 2. Index documentation
doc_parser = DocParser()
doc_parser.parse_text("README.md", "Run with --timeout-secs 30...")

# 3. Link changes to docs
results = run_hybrid_matcher(parser, doc_parser, top_k=5)

# 4. Build candidate pairs and verify drift
pairs = [(change, doc) for change, cands in results.items() for doc, _ in cands]
findings = detect_documentation_drift(pairs, confidence_threshold=0.75)

for f in findings:
    print(f"{f.severity.value}: {f.affected_sentence} -> {f.suggested_fix}")
```

## Offline-First Design

Phase 1 is designed to run entirely offline:

- No Ollama models required (`gemma4:e4b`, `nomic-embed-text` optional)
- No OpenAI/Anthropic API keys required
- No network access needed for core diff parsing, AST extraction, doc parsing, or heuristic drift verification
- When Ollama/OpenAI/Anthropic are available, they are used for LLM-based verification; otherwise the deterministic offline simulator is used

## Limitations (Phase 1)

- Dictionary-style config assignments like `app.config["PORT"] = 3000` require deeper AST analysis (slated for Phase 2)
- Tree-sitter grammars are listed in dependencies but the current extractor uses regex + native `ast` patterns (sufficient for Python/TS drift categories)
- GitHub PR integration (`--github-pr`) requires network access and a GitHub token

## Next Steps

Phase 2 wraps this engine into a GitHub Action (`action.yml`, Dockerfile) that posts inline review comments and check runs on pull requests using the JSON output from Phase 1.
