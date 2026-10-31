"""Command-line interface for the documentation drift detection engine.

Uses Typer + Rich for an elegant terminal experience. Commands:

  docs-drift check          Analyze a git repo/patch for documentation drift
  docs-drift index          Index a repository's documentation
  docs-drift run            Run a full analysis over a diff + docs dir
  docs-drift benchmark      Run the historical evaluation benchmark
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional

import typer

from .core.diff_parser import DiffParser, parse_diff_from_patch, parse_diff_from_repo
from .core.doc_parser import DocParser
from .core.hybrid_matcher import run_hybrid_matcher
from .core.llm_client import LLMClient
from .core.models import (
    DriftAnalysisReport,
    DriftFinding,
    DriftType,
    Severity,
)
from .core.drift_verifier import detect_documentation_drift

app = typer.Typer(help="docs-drift: Automated Documentation Drift Detection")

logger = logging.getLogger(__name__)
typer_colors = None


# ---------------------------------------------------------------------------
# Rich-ish terminal output (fallback when rich unavailable)
# ---------------------------------------------------------------------------

def _print(msg: str, **kwargs) -> None:
    """Print a message (uses Rich if available, else plain print)."""
    try:
        from rich.console import Console
        from rich.text import Text

        console = Console()
        text = Text(msg)
        console.print(text)
    except Exception:
        print(msg)


def _print_table(headers: List[str], rows: List[List[str]]) -> None:
    """Print a simple table with borders."""
    try:
        from rich.console import Console
        from rich.table import Table

        table = Table(show_header=True, header_style="bold magenta")
        for h in headers:
            table.add_column(h)
        for row in rows:
            table.add_row(*row)
        Console().print(table)
    except Exception:
        widths = [max(len(h), len(str(r[i]))) for i, h in enumerate(headers) for r in [row] for j in range(len(row))]
        header_line = " | ".join(h.ljust(max(8, len(h))) for h in headers)
        _print("=" * len(header_line))
        _print(header_line)
        _print("=" * len(header_line))
        for row in rows:
            _print(" | ".join(str(c).ljust(max(8, len(str(h))))) for h, c in zip(headers, row))


SEVERITY_EMOJI = {
    Severity.CRITICAL: "\u2757 CRITICAL",
    Severity.HIGH: "\u26a0 HIGH",
    Severity.MEDIUM: "\uf0a8 MEDIUM",
    Severity.LOW: "\uf054 LOW",
}


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

@app.command()
def check(
    repo: str = typer.Option(".", "--repo", help="Path to local git repository"),
    base: str = typer.Option("main", "--base", help="Base git revision / branch"),
    head: str = typer.Option("HEAD", "--head", help="Head git revision / branch"),
    diff: Optional[str] = typer.Option(None, "--diff", help="Path to unified diff / patch file"),
    docs_dir: Optional[str] = typer.Option(None, "--docs-dir", help="Documentation directory"),
    confidence: float = typer.Option(0.75, "--confidence", help="Minimum confidence threshold"),
    fail_on_drift: bool = typer.Option(True, "--fail-on-drift/--no-fail-on-drift", help="Exit 1 if drift detected"),
    json_output: Optional[str] = typer.Option(None, "--json-output", help="Write DriftAnalysisReport JSON"),
) -> None:
    """Analyze a git repository or diff for documentation drift."""
    start = time.perf_counter()

    # --- 1. Ingest diff
    if diff:
        parser = parse_diff_from_patch(diff)
    else:
        parser = parse_diff_from_repo(repo, base, head)

    # --- 2. Index documentation
    doc_parser = DocParser(docs_dir=docs_dir)
    repo_path = Path(repo).resolve()
    if docs_dir:
        docs_dir_path = Path(docs_dir)
        if not docs_dir_path.is_absolute():
            docs_dir_path = repo_path / docs_dir_path
        if docs_dir_path.is_dir():
            doc_parser.index_directory(str(docs_dir_path))
        else:
            # Fallback to repository root documentation discovery if docs_dir does not exist
            doc_paths = doc_parser.find_documentation_files(str(repo_path))
            for rel in doc_paths:
                doc_parser.parse_file(str(repo_path / rel))
    elif not parser.doc_files:
        doc_paths = doc_parser.find_documentation_files(str(repo_path))
        for rel in doc_paths:
            doc_parser.parse_file(str(repo_path / rel))

    total_docs = sum(len(d.sentences) for d in doc_parser.documents.values())

    # --- 3. Extract code changes
    from .core.ast_extractor import ASTSymbolExtractor

    extractor = ASTSymbolExtractor()
    changes = extractor.extract_from_diff(parser.code_files)

    # --- 4. Link changes to docs
    matcher = run_hybrid_matcher(parser, doc_parser, top_k=5)
    pairs: List[tuple] = []
    for change, cands in matcher.items():
        filtered = [(doc, score) for doc, score in cands if score >= 0.0]
        pairs.extend((change, doc) for doc, score in filtered)

    # --- 5. Verify drift
    findings = detect_documentation_drift(pairs, confidence_threshold=confidence)

    elapsed = time.perf_counter() - start

    # --- 6. Build report
    report = DriftAnalysisReport(
        repo_name=repo,
        base_commit=base,
        head_commit=head,
        timestamp=DriftAnalysisReport.now_iso(),
        total_files_analyzed=len(parser.files),
        total_symbols_changed=len(changes),
        total_doc_sentences_scanned=total_docs,
        findings=[f.model_dump() for f in findings],
        execution_time_seconds=elapsed,
    )

    # --- 7. Report to terminal
    _print(f"\n{'=' * 70}")
    _print(f"Documentation Drift Report - {repo} ({base}..{head})")
    _print(f"{'=' * 70}")
    _print(f"Files analyzed: {report.total_files_analyzed} | "
           f"Symbols changed: {report.total_symbols_changed} | "
           f"Doc sentences scanned: {report.total_doc_sentences_scanned}")
    _print(f"Exec time: {report.execution_time_seconds:.2f}s")

    if not findings:
        _print("\nNo documentation drift detected.")
    else:
        headers = ["ID", "Severity", "Document", "Line", "Drift Description", "Confidence"]
        rows: List[List[str]] = []
        counts = {s: 0 for s in Severity}
        for f in findings:
            counts[f.severity] += 1
            desc = f"{f.affected_sentence[:45]} -> {f.suggested_fix[:30]}"
            rows.append([
                f"# {f.id}",
                SEVERITY_EMOJI.get(f.severity, str(f.severity.value)),
                f"{f.doc_path}:{f.doc_line_number}",
                str(f.doc_line_number),
                desc,
                f"{f.confidence:.0%}",
            ])
        _print_table(headers, rows)

        summary = ", ".join(
            f"{c} {s.value}" for s, c in counts.items() if c > 0
        )
        _print(f"\nSummary: {len(findings)} drift issue(s) detected ({summary}).")

    if json_output:
        Path(json_output).write_text(report.model_dump_json(indent=2), encoding="utf-8")
        _print(f"\nJSON report written to {json_output}")

    if fail_on_drift and findings:
        sys.exit(1)


@app.command()
def index(
    repo: str = typer.Option(".", "--repo", help="Path to local git repository"),
    docs_dir: Optional[str] = typer.Option(None, "--docs-dir", help="Documentation directory"),
    json_output: Optional[str] = typer.Option(None, "--json-output", help="Write index as JSON"),
) -> None:
    """Index a repository's documentation for drift analysis."""
    doc_parser = DocParser(docs_dir=docs_dir)
    repo_path = Path(repo).resolve()
    if docs_dir:
        doc_parser.index_directory(docs_dir)
    else:
        doc_paths = doc_parser.find_documentation_files(str(repo_path))
        for rel in doc_paths:
            doc_parser.parse_file(str(repo_path / rel))
    total = sum(len(d.sentences) for d in doc_parser.documents.values())
    _print(f"Indexed {len(doc_parser.documents)} files, {total} sentences.")
    if json_output:
        index_data = {
            p: [s.model_dump() for s in d.sentences]
            for p, d in doc_parser.documents.items()
        }
        Path(json_output).write_text(json.dumps(index_data, indent=2), encoding="utf-8")


@app.command()
def benchmark(
    dataset: str = typer.Option("backend/evaluation/dataset/sample_cases.json", "--dataset", help="Benchmark dataset JSON"),
    confidence: float = typer.Option(0.75, "--confidence", help="Minimum confidence threshold"),
    llm_mode: str = typer.Option("offline", "--llm-mode", help="LLM provider (offline|ollama|openai|anthropic)"),
) -> None:
    """Run the historical drift-detection benchmark."""
    from .evaluation.benchmark_runner import BenchmarkRunner

    runner = BenchmarkRunner(dataset, confidence=confidence, llm_mode=llm_mode)
    runner.run()
    runner.print_report()
    runner.save_report("backend/evaluation/benchmark_report.json")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
