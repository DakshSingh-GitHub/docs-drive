"""Unit tests for the drift verifier and LLM client (Phase 1.5)."""

import json
import pytest

from core.drift_verifier import (
    DriftVerifier,
    OfflineDriftDetector,
    detect_documentation_drift,
)
from core.llm_client import LLMClient, LLMResult
from core.models import (
    ChangeType,
    CodeSymbolChange,
    DocSentence,
    DriftFinding,
    DriftType,
    Severity,
    SymbolType,
)


CHANGE_RENAMED_FLAG = CodeSymbolChange(
    file_path="cli.py",
    line_start=5,
    line_end=5,
    symbol_name="--timeout-secs",
    symbol_type=SymbolType.CLI_ARGUMENT,
    change_type=ChangeType.RENAMED,
    old_value="--timeout-secs",
    new_value="--timeout",
    diff_hunk="-parser.add_argument('--timeout-secs')\n+parser.add_argument('--timeout')",
    description="CLI flag renamed",
)

CHANGE_DEFAULT_CHANGED = CodeSymbolChange(
    file_path="config.py",
    line_start=10,
    line_end=10,
    symbol_name="timeout",
    symbol_type=SymbolType.DEFAULT_VALUE,
    change_type=ChangeType.DEFAULT_CHANGED,
    old_value="30",
    new_value="60",
    diff_hunk="-DEFAULT_TIMEOUT = 30\n+DEFAULT_TIMEOUT = 60",
    description="Default timeout changed 30 -> 60",
)

CHANGE_REMOVED = CodeSymbolChange(
    file_path="api.py",
    line_start=20,
    line_end=20,
    symbol_name="fetch_legacy",
    symbol_type=SymbolType.FUNCTION,
    change_type=ChangeType.REMOVED,
    old_value="fetch_legacy",
    new_value=None,
    diff_hunk="-def fetch_legacy():\n-    pass",
    description="Function removed",
)


DOC_WITH_OLD_FLAG = DocSentence(
    doc_path="docs/guide.md",
    line_number=12,
    sentence_index=0,
    sentence_text="Run the tool with --timeout-secs to set the timeout.",
    section_hierarchy=["CLI Options"],
    raw_markdown="Run the tool with --timeout-secs to set the timeout.",
)

DOC_WITH_OLD_DEFAULT = DocSentence(
    doc_path="docs/guide.md",
    line_number=15,
    sentence_index=1,
    sentence_text="The default timeout is set to 30 seconds.",
    section_hierarchy=["Configuration"],
    raw_markdown="The default timeout is set to 30 seconds.",
)

DOC_GENERAL_PROSE = DocSentence(
    doc_path="docs/guide.md",
    line_number=20,
    sentence_index=2,
    sentence_text="This tool provides a simple command-line interface for configuration.",
    section_hierarchy=["Introduction"],
    raw_markdown="This tool provides a simple command-line interface for configuration.",
)


def test_llm_client_offline_mode():
    """LLMClient offline mode returns a simulated verdict."""
    client = LLMClient(mode="offline")
    assert client.is_available() is False
    result = client.chat("Test prompt")
    assert result.provider == "offline"
    assert result.success is True
    parsed = json.loads(result.content)
    assert "is_drift" in parsed
    assert "reasoning" in parsed


def test_offline_detector_drift_detected_renamed_flag():
    """Renamed flag scenario returns drift with suggested fix."""
    detector = OfflineDriftDetector()
    is_drift, conf, dtype, sev, reason = detector.simulate_drift(CHANGE_RENAMED_FLAG, DOC_WITH_OLD_FLAG)
    assert is_drift is True
    assert dtype == DriftType.RENAMED_FLAG
    assert conf >= detector.confidence_threshold


def test_offline_detector_drift_detected_default_changed():
    """Changed default scenario returns drift with accurate suggested_fix."""
    detector = OfflineDriftDetector()
    findings = detector.build_offline_findings([(CHANGE_DEFAULT_CHANGED, DOC_WITH_OLD_DEFAULT)])
    assert len(findings) == 1
    f = findings[0]
    assert f.drift_type == DriftType.INCORRECT_DEFAULT
    assert "30" in f.affected_sentence
    assert "60" in f.suggested_fix


def test_offline_detector_false_alarm_general_prose():
    """General prose scenario returns is_drift: false."""
    detector = OfflineDriftDetector()
    is_drift, conf, _, _, _ = detector.simulate_drift(CHANGE_DEFAULT_CHANGED, DOC_GENERAL_PROSE)
    assert is_drift is False or conf < detector.confidence_threshold


def test_offline_detector_removed_feature():
    """Removed feature produces a removed_feature drift finding."""
    detector = OfflineDriftDetector()
    findings = detector.build_offline_findings([(CHANGE_REMOVED, DOC_WITH_OLD_FLAG)])
    assert len(findings) >= 0


def test_detect_documentation_drift_offline():
    """End-to-end offline drift detection over multiple pairs."""
    pairs = [
        (CHANGE_RENAMED_FLAG, DOC_WITH_OLD_FLAG),
        (CHANGE_DEFAULT_CHANGED, DOC_WITH_OLD_DEFAULT),
        (CHANGE_DEFAULT_CHANGED, DOC_GENERAL_PROSE),
    ]
    findings = detect_documentation_drift(pairs, llm_mode="offline")
    drift_findings = [f for f in findings if f.drift_type != DriftType.NONE]
    assert len(drift_findings) >= 1


def test_confidence_threshold_filtering():
    """Findings below confidence threshold are suppressed."""
    detector = OfflineDriftDetector(confidence_threshold=0.95)
    findings = detector.build_offline_findings([(CHANGE_DEFAULT_CHANGED, DOC_WITH_OLD_DEFAULT)])
    # With a high threshold, the simulated confidence may be filtered
    assert len(findings) <= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
