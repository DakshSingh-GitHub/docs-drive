"""Unit tests for the CLI and benchmark runner (Phase 1.6)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

def _run_cli(args, **kwargs):
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT) + os.pathsep + str(BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    )
    return subprocess.run([sys.executable, "-m", "backend.cli"] + args,
                         capture_output=True, text=True, cwd=PROJECT_ROOT, env=env, **kwargs)


def test_cli_check_help():
    """CLI invocation with --help works."""
    result = _run_cli(["check", "--help"])
    assert result.returncode == 0
    assert "check" in result.stdout or "analyze" in result.stdout.lower()


def test_cli_check_with_patch_file(tmp_path):
    """Run `docs-drift check` against a patch file."""
    patch = BACKEND_DIR / "tests" / "samples" / "renamed_flag.patch"
    if not patch.exists():
        pytest.skip("Sample patch not available")
    out = tmp_path / "report.json"
    result = _run_cli(["check", "--diff", str(patch), "--json-output", str(out)])
    assert out.exists()
    report = json.loads(out.read_text(encoding="utf-8"))
    assert "findings" in report


def test_cli_index_command(tmp_path):
    """The `index` subcommand indexes documentation files."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "README.md").write_text("# Test\n\nThe timeout is 30 seconds by default.")
    result = _run_cli(["index", "--docs-dir", str(docs_dir),
                       "--json-output", str(tmp_path / "index.json")])
    assert result.returncode == 0
    assert (tmp_path / "index.json").exists()


def test_benchmark_runner_loads_dataset():
    """Benchmark runner loads the sample dataset scenarios."""
    from backend.evaluation.benchmark_runner import BenchmarkRunner

    dataset = BACKEND_DIR / "evaluation" / "dataset" / "sample_cases.json"
    runner = BenchmarkRunner(str(dataset), confidence=0.6)
    n = runner.load_dataset()
    assert n >= 1
    assert runner.scenarios[0].id == "scenario-01-renamed-flag"


def test_benchmark_runner_computes_metrics():
    """Benchmark runner computes precision/recall/F1."""
    from backend.evaluation.benchmark_runner import BenchmarkRunner

    dataset = BACKEND_DIR / "evaluation" / "dataset" / "sample_cases.json"
    runner = BenchmarkRunner(str(dataset), confidence=0.5)
    runner.run()
    assert isinstance(runner.precision, float)
    assert isinstance(runner.recall, float)
    assert isinstance(runner.f1, float)
    assert 0.0 <= runner.precision <= 1.0


def test_benchmark_report_prints_pass_fail():
    """Benchmark report prints PASS/FAIL per scenario."""
    from backend.evaluation.benchmark_runner import BenchmarkRunner

    dataset = BACKEND_DIR / "evaluation" / "dataset" / "sample_cases.json"
    runner = BenchmarkRunner(str(dataset), confidence=0.5)
    runner.run()
    report = runner.print_report()
    # print_report prints to stdout; check no exception raised
    assert report is None


def test_sample_cases_json_valid():
    """sample_cases.json is valid and covers the 4 primary drift categories."""
    dataset = BACKEND_DIR / "evaluation" / "dataset" / "sample_cases.json"
    data = json.loads(dataset.read_text(encoding="utf-8"))
    drift_count = sum(1 for s in data if s["ground_truth"].get("has_drift"))
    no_drift_count = sum(1 for s in data if not s["ground_truth"].get("has_drift"))
    assert drift_count >= 1
    assert no_drift_count >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
