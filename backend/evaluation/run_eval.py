"""Benchmark execution script computing Precision, Recall, and Accuracy
for the Historical Commit Replay evaluation suite (Phase 2.3).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from evaluation.historical_replayer import HistoricalReplayer

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the historical commit replay benchmark"
    )
    parser.add_argument(
        "--dataset",
        default="evaluation/dataset/real_histories.json",
        help="Path to historical scenarios JSON",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.75,
        help="Minimum confidence threshold for drift findings",
    )
    parser.add_argument(
        "--llm-mode",
        default="offline",
        choices=["offline", "ollama", "openai", "anthropic"],
        help="LLM provider mode (default: offline)",
    )
    parser.add_argument(
        "--output",
        default="evaluation/historical_benchmark_report.json",
        help="Path to save the JSON report",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    runner = HistoricalReplayer(
        dataset_path=args.dataset,
        confidence=args.confidence,
        llm_mode=args.llm_mode,
    )
    runner.run()
    runner.print_report()
    runner.save_report(args.output)


if __name__ == "__main__":
    main()
