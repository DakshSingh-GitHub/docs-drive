"""Historical Commit Replay & Benchmark Evaluation Suite (Phase 2.3).

Executes against real open-source project commit histories where a code
commit (Commit A) introduced documentation drift and a subsequent commit
(Commit B) corrected the documentation. Validates that our tool identifies
the drift on Commit A **before** Commit B was authored.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.diff_parser import parse_diff
from core.doc_parser import DocParser
from core.ast_extractor import ASTSymbolExtractor
from core.hybrid_matcher import HybridMatcher
from core.drift_verifier import detect_documentation_drift

logger = logging.getLogger(__name__)


@dataclass
class Scenario:
    """A single historical commit-replay scenario."""

    id: str
    name: str
    project: str
    description: str
    commit_a_diff: str
    pre_fix_docs: Dict[str, str]  # doc_path -> raw markdown text (state at Commit A)
    corrective_commit_b_diff: str
    ground_truth: Dict[str, Any]


@dataclass
class ScenarioResult:
    """Result of running a single historical replay scenario."""

    scenario: Scenario
    findings: List[Dict[str, Any]]
    tp: int = 0
    fp: int = 0
    fn: int = 0
    passed: bool = False
    line_accuracy: Optional[float] = None


class HistoricalReplayer:
    """Replays historical commit scenarios through the drift pipeline."""

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
                name=item.get("description", item["id"]),
                project=item.get("project", "unknown"),
                description=item.get("description", ""),
                commit_a_diff=item["commit_a_diff"],
                pre_fix_docs=item["pre_fix_docs"],
                corrective_commit_b_diff=item.get("corrective_commit_b_diff", ""),
                ground_truth=item["ground_truth"],
            ))
        return len(self.scenarios)

    def run_scenario(self, scenario: Scenario) -> ScenarioResult:
        """Run a single scenario through the full drift pipeline.

        We simulate the state at Commit A by parsing the Commit A diff
        and indexing the pre-fix documentation (the docs *before* the
        corrective commit B was applied).
        """
        # 1. Index documentation as it existed at Commit A (pre-fix)
        doc_parser = DocParser()
        for doc_path, text in scenario.pre_fix_docs.items():
            doc_parser.parse_text(doc_path, text)

        # 2. Parse the Commit A code diff
        parser = parse_diff(scenario.commit_a_diff)

        # 3. Extract code changes
        extractor = ASTSymbolExtractor()
        changes = extractor.extract_from_diff(parser.code_files)

        # 4. Link changes to docs and verify drift
        matcher = HybridMatcher(top_k=10)
        all_sentences: List[Any] = []
        for doc in doc_parser.documents.values():
            all_sentences.extend(doc.sentences)
        matcher.index_documents(all_sentences)

        pairs: List[tuple] = []
        for change in changes:
            cands = matcher.retrieve_candidates(change, top_k=10)
            pairs.extend((change, doc) for doc, score in cands)

        findings = detect_documentation_drift(
            pairs, confidence_threshold=self.confidence
        )

        # 5. Evaluate against ground truth
        result = self._evaluate(scenario, findings)
        return result

    def _evaluate(
        self, scenario: Scenario, findings: List[Any]
    ) -> ScenarioResult:
        gt = scenario.ground_truth
        expect_drift = not bool(gt.get("has_drift", False))  # default: expect drift
        if "has_drift" in gt:
            expect_drift = bool(gt["has_drift"])

        tp, fp, fn = 0, 0, 0
        matched_lines: List[int] = []

        if expect_drift:
            expected_file = gt.get("drift_file")
            expected_line = gt.get("drift_line")
            expected_type = gt.get("drift_type")

            found_match = False
            for f in findings:
                if getattr(f, "drift_type", None) is not None:
                    # Pydantic model
                    f_type = f.drift_type.value if hasattr(f.drift_type, "value") else f.drift_type
                    f_file = f.doc_path
                    f_line = f.doc_line_number
                else:
                    f_type = f.get("drift_type")
                    f_file = f.get("doc_path")
                    f_line = f.get("doc_line_number")

                if f_type == "none":
                    continue

                # Check if this finding matches the expected drift
                file_match = expected_file and f_file == expected_file
                type_match = expected_type and f_type == expected_type

                if file_match and type_match:
                    tp += 1
                    found_match = True
                    if expected_line:
                        matched_lines.append(abs(f_line - expected_line))
                else:
                    fp += 1

            if not found_match:
                fn = 1
            passed = tp > 0

            # Line coordinate accuracy: proportion within ±1 line
            if matched_lines:
                within_one = sum(1 for d in matched_lines if d <= 1)
                result_line_acc = within_one / len(matched_lines)
            else:
                result_line_acc = None

        else:
            # Expect no drift
            any_drift = any(
                getattr(f, "drift_type", None) is None
                or (getattr(f, "drift_type", None).value if hasattr(getattr(f, "drift_type", None), "value") else getattr(f, "drift_type", None)) != "none"
                for f in findings
            )
            fp = sum(
                1 for f in findings
                if getattr(f, "drift_type", None) is not None
                and (getattr(f, "drift_type", None).value if hasattr(getattr(f, "drift_type", None), "value") else getattr(f, "drift_type", None)) != "none"
            )
            fn = 0
            passed = not any_drift
            result_line_acc = None

        return ScenarioResult(
            scenario=scenario,
            findings=findings,
            tp=tp,
            fp=fp,
            fn=fn,
            passed=passed,
            line_accuracy=result_line_acc,
        )

    def run(self) -> None:
        """Run all scenarios."""
        n = self.load_dataset()
        logger.info("Loaded %d historical scenarios", n)
        for i, scenario in enumerate(self.scenarios, start=1):
            logger.info("[%d/%d] Replaying %s ...", i, n, scenario.name)
            result = self.run_scenario(scenario)
            status = "PASS" if result.passed else "FAIL"
            logger.info(
                "[%d/%d] %s - TP=%d FP=%d FN=%d",
                i, n, status, result.tp, result.fp, result.fn,
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

    @property
    def avg_line_accuracy(self) -> Optional[float]:
        accs = [r.line_accuracy for r in self.results if r.line_accuracy is not None]
        if not accs:
            return None
        return sum(accs) / len(accs)

    def print_report(self) -> None:
        """Print the benchmark report to stdout."""
        print("\n" + "=" * 70)
        print("HISTORICAL COMMIT REPLAY BENCHMARK REPORT")
        print("=" * 70)
        for result in self.results:
            s = result.scenario
            print(f"\n[{result.scenario.id}] {s.name}")
            print(f"  Project:        {s.project}")
            print(f"  Description:    {s.description}")
            print(f"  Ground truth:   drift={s.ground_truth.get('has_drift', True)}")
            if s.ground_truth.get("drift_file"):
                print(f"  Expected file:  {s.ground_truth['drift_file']}")
                print(f"  Expected line:  {s.ground_truth['drift_line']}")
                print(f"  Expected type:  {s.ground_truth['drift_type']}")
            print(f"  Result:         TP={result.tp} FP={result.fp} FN={result.fn}")
            if result.line_accuracy is not None:
                print(f"  Line accuracy:  {result.line_accuracy:.2f}")
            print(f"  Status:         {'PASS' if result.passed else 'FAIL'}")

        print("\n" + "-" * 70)
        print(f"OVERALL  Precision={self.precision:.3f} Recall={self.recall:.3f} F1={self.f1:.3f}")
        if self.avg_line_accuracy is not None:
            print(f"Avg line coordinate accuracy: {self.avg_line_accuracy:.2f}")
        print(f"Pass rate: {sum(r.passed for r in self.results)}/{len(self.results)} scenarios")
        print("=" * 70)

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
            "avg_line_accuracy": self.avg_line_accuracy,
            "pass_rate": sum(r.passed for r in self.results) / len(self.results) if self.results else 0.0,
            "scenarios": [
                {
                    "id": r.scenario.id,
                    "name": r.scenario.name,
                    "project": r.scenario.project,
                    "ground_truth": r.scenario.ground_truth,
                    "tp": r.tp,
                    "fp": r.fp,
                    "fn": r.fn,
                    "passed": r.passed,
                    "line_accuracy": r.line_accuracy,
                    "n_findings": len(r.findings),
                }
                for r in self.results
            ],
        }
        Path(output_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report saved to {output_path}")


def main() -> None:
    dataset = os.environ.get(
        "DRIFT_DATASET",
        "evaluation/dataset/real_histories.json",
    )
    runner = HistoricalReplayer(dataset)
    runner.run()
    runner.print_report()
    runner.save_report("backend/evaluation/historical_benchmark_report.json")


if __name__ == "__main__":
    main()
