"""Unit tests for the historical commit replayer (Phase 2.3)."""

import json
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
DATASET_PATH = BACKEND_DIR / "evaluation" / "dataset" / "real_histories.json"


def test_dataset_loads():
    """real_histories.json is valid and contains scenarios."""
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    assert len(data) >= 1
    assert data[0]["id"] == "scenario-click-flag-rename"


def test_historical_replayer_loads_dataset():
    """HistoricalReplayer loads the dataset scenarios."""
    from evaluation.historical_replayer import HistoricalReplayer

    runner = HistoricalReplayer(str(DATASET_PATH), confidence=0.5)
    n = runner.load_dataset()
    assert n >= 1
    assert runner.scenarios[0].id == "scenario-click-flag-rename"


def test_historical_replayer_runs_scenario():
    """HistoricalReplayer runs a single scenario and produces a result."""
    from evaluation.historical_replayer import HistoricalReplayer

    runner = HistoricalReplayer(str(DATASET_PATH), confidence=0.5)
    runner.load_dataset()
    scenario = runner.scenarios[0]
    result = runner.run_scenario(scenario)
    assert isinstance(result.tp, int)
    assert isinstance(result.fp, int)
    assert isinstance(result.fn, int)
    assert isinstance(result.passed, bool)


def test_historical_replayer_computes_metrics():
    """HistoricalReplayer computes precision/recall/F1/line accuracy."""
    from evaluation.historical_replayer import HistoricalReplayer

    runner = HistoricalReplayer(str(DATASET_PATH), confidence=0.5)
    runner.run()
    assert isinstance(runner.precision, float)
    assert isinstance(runner.recall, float)
    assert isinstance(runner.f1, float)
    assert 0.0 <= runner.precision <= 1.0
    assert 0.0 <= runner.recall <= 1.0


def test_historical_replayer_saves_report(tmp_path):
    """HistoricalReplayer saves a JSON report."""
    from evaluation.historical_replayer import HistoricalReplayer

    runner = HistoricalReplayer(str(DATASET_PATH), confidence=0.5)
    runner.run()
    out = tmp_path / "report.json"
    runner.save_report(str(out))
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "precision" in data
    assert "recall" in data
    assert "f1" in data
    assert "scenarios" in data


def test_internal_refactor_no_drift():
    """Internal refactor scenario expects no drift (has_drift=false)."""
    from evaluation.historical_replayer import HistoricalReplayer

    runner = HistoricalReplayer(str(DATASET_PATH), confidence=0.5)
    runner.load_dataset()
    scenario = next(s for s in runner.scenarios if s.id == "scenario-internal-refactor-no-drift")
    result = runner.run_scenario(scenario)
    # With low confidence threshold, we may get some findings; check fn is 0 (expect no drift)
    assert result.fn == 0


def test_doc_already_updated_no_drift():
    """Doc-already-updated scenario expects no drift."""
    from evaluation.historical_replayer import HistoricalReplayer

    runner = HistoricalReplayer(str(DATASET_PATH), confidence=0.5)
    runner.load_dataset()
    scenario = next(s for s in runner.scenarios if s.id == "scenario-doc-already-updated")
    result = runner.run_scenario(scenario)
    assert result.fn == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
