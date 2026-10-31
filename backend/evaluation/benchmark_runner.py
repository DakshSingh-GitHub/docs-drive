"""Benchmark evaluation suite for the documentation drift detection engine.

Phase 1.6 - Core Engine (Local CLI/Script)

The benchmark replays historical drift scenarios and computes Precision,
Recall, and F1 score by comparing detected findings against ground-truth
expectations.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.doc_parser import DocParser
from core.diff_parser import parse_diff
from core.models import ChangeType, CodeSymbolChange, DocSentence, DriftFinding, DriftType, Severity
from core.drift_verifier import detect_documentation_drift

logger = logging.getLogger(__name__)


@dataclass
class Scenario:
    """A single benchmark scenario."""

    id: str
    name: str
    diff: str
    docs_content: Dict[str, str]  # doc_path -> raw markdown text
    ground_truth: Dict[str, Any]


@dataclass
class ScenarioResult:
    """Result of running a single scenario."""

    scenario: Scenario
    findings: List[DriftFinding]
    tp: int = 0
    fp: int = 0
    fn: int = 0
    passed: bool = False


class BenchmarkRunner:
    """Runs the benchmark dataset and computes Precision/Recall/F1."""

    def __init__(
        self,
        dataset_path: str,
        confidence: float = 0.75,
        llm_mode: str = "offline",
    ):
        self.dataset_path = dataset_path
        self.confidence = confidence
        self.llm_mode = llm_mode
        self.scenarios: List[Scenario] = []
        self.results: List[ScenarioResult] = []

    def load_dataset(self) -> int:
        """Load benchmark scenarios from the dataset JSON."""
        path = Path(self.dataset_path)
        if not path.exists():
            raise FileNotFoundError(f"Benchmark dataset not found: {self.dataset_path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data:
            self.scenarios.append(Scenario(
                id=item["id"],
                name=item["name"],
                diff=item["drift_commit_diff"],
                docs_content=item["docs_content"],
                ground_truth=item["ground_truth"],
            ))
        return len(self.scenarios)

    def run_scenario(self, scenario: Scenario) -> ScenarioResult:
        """Run a single scenario through the full drift pipeline."""
        # Index documentation
        doc_parser = DocParser()
        for doc_path, text in scenario.docs_content.items():
            doc_parser.parse_text(doc_path, text)

        # Parse the drift commit diff
        parser = parse_diff(scenario.diff)

        # Extract code changes
        from core.ast_extractor import ASTSymbolExtractor
        extractor = ASTSymbolExtractor()
        changes = extractor.extract_from_diff(parser.code_files)

        # Link + verify
        pairs: List[Tuple[CodeSymbolChange, DocSentence]] = self._build_matcher(doc_parser, changes)

        findings = detect_documentation_drift(pairs, confidence_threshold=self.confidence)

        # Evaluate against ground truth
        result = self._evaluate(scenario, findings)
        return result

    def _build_matcher(self, doc_parser: DocParser, changes: List[CodeSymbolChange]) -> List[Tuple[CodeSymbolChange, DocSentence]]:
        all_sentences: List[DocSentence] = []
        for doc in doc_parser.documents.values():
            all_sentences.extend(doc.sentences)
        # Pair each code change with every doc sentence (small dataset, cross product is fine)
        pairs: List[Tuple[CodeSymbolChange, DocSentence]] = []
        for change in changes:
            for sentence in all_sentences:
                pairs.append((change, sentence))
        return pairs

    def _evaluate(
        self, scenario: Scenario, findings: List[DriftFinding]
    ) -> ScenarioResult:
        gt = scenario.ground_truth
        expect_drift = bool(gt.get("has_drift", False))

        tp, fp, fn = 0, 0, 0

        if expect_drift:
            # Expect at least one finding
            found_expected_file = False
            for f in findings:
                if f.drift_type == DriftType.NONE:
                    continue
                affected_file = f.doc_path
                if gt.get("affected_file") and affected_file != gt["affected_file"]:
                    fp += 1
                    continue
                found_expected_file = True
                tp += 1
            if not found_expected_file:
                fn = 1 if not findings else 0
                for f in findings:
                    if f.drift_type != DriftType.NONE:
                        fp += 1
            passed = tp > 0
        else:
            # Expect zero findings
            any_findings = any(f.drift_type != DriftType.NONE for f in findings)
            fp = len([f for f in findings if f.drift_type != DriftType.NONE])
            fn = 0
            passed = not any_findings

        return ScenarioResult(
            scenario=scenario,
            findings=findings,
            tp=tp,
            fp=fp,
            fn=fn,
            passed=passed,
        )

    def run(self) -> None:
        """Run all scenarios."""
        n = self.load_dataset()
        logger.info("Loaded %d scenarios", n)
        for i, scenario in enumerate(self.scenarios, start=1):
            logger.info("[%d/%d] Running %s ...", i, n, scenario.name)
            start = time.perf_counter()
            result = self.run_scenario(scenario)
            elapsed = time.perf_counter() - start
            status = "PASS" if result.passed else "FAIL"
            logger.info(
                "[%d/%d] %s - TP=%d FP=%d FN=%d (took %.2fs)",
                i, n, status, result.tp, result.fp, result.fn, elapsed,
            )
            self.results.append(result)

    # ------------------------------------------------ metrics

    @property
    def precision(self) -> float:
        tp = sum(r.tp for r in self.results)
        fp = sum(r.fp for r in self.results)
        if tp + fp == 0:
            return 0.0
        return tp / (tp + fp)

    @property
    def recall(self) -> float:
        tp = sum(r.tp for r in self.results)
        fn = sum(r.fn for r in self.results)
        if tp + fn == 0:
            return 0.0
        return tp / (tp + fn)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        if p + r == 0:
            return 0.0
        return 2 * (p * r) / (p + r)

    def print_report(self) -> None:
        """Print the benchmark report to stdout."""
        print("\n" + "=" * 60)
        print("BENCHMARK REPORT")
        print("=" * 60)
        for result in self.results:
            s = result.scenario
            print(f"\n[{result.scenario.id}] {s.name}")
            print(f"  Ground truth: drift={s.ground_truth.get('has_drift')}")
            print(f"  Result:       TP={result.tp} FP={result.fp} FN={result.fn} -> {'PASS' if result.passed else 'FAIL'}")
        print("\n" + "-" * 60)
        print(f"OVERALL  Precision={self.precision:.3f} Recall={self.recall:.3f} F1={self.f1:.3f}")
        print(f"Pass rate: {sum(r.passed for r in self.results)}/{len(self.results)} scenarios")
        print("=" * 60)

    def save_report(self, output_path: str) -> None:
        """Save the benchmark report as JSON."""
        report = {
            "dataset": self.dataset_path,
            "confidence_threshold": self.confidence,
            "llm_mode": self.llm_mode,
            "n_scenarios": len(self.scenarios),
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "pass_rate": sum(r.passed for r in self.results) / len(self.results) if self.results else 0.0,
            "scenarios": [
                {
                    "id": r.scenario.id,
                    "name": r.scenario.name,
                    "ground_truth": r.scenario.ground_truth,
                    "tp": r.tp,
                    "fp": r.fp,
                    "fn": r.fn,
                    "passed": r.passed,
                    "n_findings": len(r.findings),
                }
                for r in self.results
            ],
        }
        Path(output_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report saved to {output_path}")


def main() -> None:
    dataset = os.environ.get("DRIFT_DATASET", "backend/evaluation/dataset/sample_cases.json")
    runner = BenchmarkRunner(dataset)
    runner.run()
    runner.print_report()


if __name__ == "__main__":
    main()
