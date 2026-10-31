"""LLM Drift Detection & False Positive Filtering (Phase 1.5).

Verifies candidate (CodeSymbolChange, DocSentence) pairs using either the
local Ollama LLM (gemma4:e4b) or a deterministic OFFLINE simulator. The
verifier performs semantic reasoning to confirm whether a documentation
sentence has become invalid due to a code change, assigns severity +
confidence, and generates a suggested fix.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from core.models import (
    ChangeType,
    CodeSymbolChange,
    DocSentence,
    DriftFinding,
    DriftType,
    Severity,
)
from .llm_client import LLMClient, LLMResult

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.70

# Few-shot exemplars (positive drift case)
POSITVE_EXEMPLAR = {
    "is_drift": True,
    "drift_type": "incorrect_default",
    "severity": "medium",
    "confidence": 0.98,
    "reasoning": (
        "The code changed the default timeout from 30 to 60. The doc sentence "
        "explicitly states the default is 30 seconds, making it factually outdated."
    ),
    "explanation": (
        "The default timeout was changed from 30 to 60 seconds in the code, but "
        "the documentation still claims it is 30 seconds."
    ),
    "suggested_fix": "The default timeout is set to 60 seconds.",
}

# Few-shot exemplar (negative, false alarm suppressed)
NEGATIVE_EXEMPLAR = {
    "is_drift": False,
    "drift_type": "none",
    "severity": "low",
    "confidence": 0.95,
    "reasoning": (
        "The code change modified internal socket connection handling and timeout "
        "defaults, but did not alter the fact that HTTP GET requests are used."
    ),
    "explanation": "The documentation sentence remains accurate despite the internal refactor.",
    "suggested_fix": "",
}


class DriftVerifier:
    """Verify drift candidates using LLM reasoning or OFFLINE simulation."""

    SYSTEM_PROMPT = (
        "You are an expert static analysis and documentation engineering AI.\n"
        "Your role is to detect DOCUMENTATION DRIFT caused by code changes in a "
        "Pull Request.\n"
        "\n"
        "Documentation Drift occurs when:\n"
        "1. A configuration flag or CLI option is renamed, but the doc mentions "
        "the old flag.\n"
        "2. A default value is changed, but the doc still states the old default.\n"
        "3. An API parameter is modified, removed, or added, but the doc gives "
        "outdated instructions.\n"
        "4. A feature or endpoint is removed, but the doc describes it as "
        "available.\n"
        "5. Behavioral logic changes in a way that makes the doc sentence "
        "factually incorrect.\n"
        "\n"
        "CRITICAL RULES:\n"
        "- Minimize false alarms. If the doc sentence is general enough that it "
        "remains true despite the code change, output is_drift: false.\n"
        "- If the code change is purely internal and does NOT alter the behavior "
        "described in the doc sentence, output is_drift: false.\n"
        "- Focus ONLY on the specific sentence provided.\n"
        "- You must output pure JSON matching the requested schema.\n"
    )

    USER_PROMPT_TEMPLATE = """### CODE CHANGE
File: {file} (Lines {start}-{end})
Symbol: {symbol} ({stype})
Change Type: {ctyp}
Old Value: {oldv}
New Value: {newv}

Diff Snippet:
```
{hunk}
```

### DOCUMENTATION SENTENCE
Document: {docpath} (Line {line})
Section Hierarchy: {hier}
Sentence: "{sentence}"
{% if table_headers %}
Table Row Headers: {table_headers}
{% endif %}

Evaluate whether this documentation sentence is now incorrect or misleading
because of the code change.
Output JSON format:
{{
  "is_drift": true | false,
  "drift_type": "outdated_parameter" | "renamed_flag" | "incorrect_default" | "removed_feature" | "behavioral_discrepancy" | "none",
  "severity": "critical" | "high" | "medium" | "low",
  "confidence": 0.0 to 1.0,
  "reasoning": "Step-by-step rationale",
  "explanation": "Clear 1-2 sentence explanation of the drift",
  "suggested_fix": "Rewritten sentence with the accurate new behavior"
}}
"""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        mode: str = "auto",
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
        temperature: float = 0.1,
    ):
        self.confidence_threshold = confidence_threshold
        self.temperature = temperature
        if llm_client is not None:
            self.client = llm_client
            self.mode = llm_client.mode
        else:
            self.client = LLMClient(mode=mode, temperature=temperature)
            self.mode = self.client.mode

    def verify_pair(
        self, change: CodeSymbolChange, doc_sentence: DocSentence
    ) -> LLMResult:
        """Verify a single (code change, doc sentence) candidate pair."""
        prompt = self.build_user_prompt(change, doc_sentence)
        return self.client.chat(prompt, self.SYSTEM_PROMPT, temperature=self.temperature)

    def build_user_prompt(self, change: CodeSymbolChange, doc: DocSentence) -> str:
        """Build the user-facing prompt for drift verification."""
        oldv = change.old_value or "N/A"
        newv = change.new_value or "N/A"
        hier = " > ".join(doc.section_hierarchy)
        table_headers = doc.table_headers
        table_part = ""
        if table_headers:
            table_part = f"Table Row Headers: {', '.join(table_headers)}\n\n"
        return self.USER_PROMPT_TEMPLATE.format(
            file=change.file_path,
            start=change.line_start,
            end=change.line_end,
            symbol=change.symbol_name,
            stype=change.symbol_type.value,
            ctyp=change.change_type.value,
            oldv=oldv,
            newv=newv,
            hunk=change.diff_hunk,
            docpath=doc.doc_path,
            line=doc.line_number,
            hier=hier,
            sentence=doc.sentence_text,
            table_headers=table_part,
        )

    def parse_llm_response(self, result: LLMResult) -> LLMResult:
        """Sanitize and validate the LLM's JSON response."""
        content = result.content or ""
        content = content.strip()
        # Strip markdown code fences
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content, flags=re.IGNORECASE)
        try:
            parsed = json.loads(content)
            result.content = json.dumps(parsed, ensure_ascii=False)
            result.parsed = parsed
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse LLM JSON: %s", exc)
            result.parsed = None
        return result

    def verify_pairs(
        self, pairs: List[Tuple[CodeSymbolChange, DocSentence]]
    ) -> List[LLMResult]:
        """Verify multiple candidate pairs concurrently (sequentially fallback)."""
        return [self.verify_pair(change, doc) for change, doc in pairs]

    def findings_from_results(
        self, pairs: List[Tuple[CodeSymbolChange, DocSentence]], results: List[LLMResult]
    ) -> List[DriftFinding]:
        """Convert verification results into DriftFinding records."""
        findings: List[DriftFinding] = []
        for (change, doc), result in zip(pairs, results):
            parsed = result.parsed
            if not parsed:
                continue
            is_drift = bool(parsed.get("is_drift", False))
            if not is_drift:
                continue
            confidence = float(parsed.get("confidence", 0.0))
            if confidence < self.confidence_threshold:
                logger.debug(
                    "Suppressed finding below confidence threshold: %s",
                    doc.doc_path,
                )
                continue
            drift_type = parsed.get("drift_type", "none")
            if drift_type == "none":
                continue
            severity_str = parsed.get("severity", "low")
            severity = Severity(severity_str) if severity_str in [s.value for s in Severity] else Severity.LOW
            finding = DriftFinding(
                id=str(len(findings) + 1),
                doc_path=doc.doc_path,
                doc_line_number=doc.line_number,
                affected_sentence=doc.sentence_text,
                code_file_path=change.file_path,
                code_line_start=change.line_start,
                code_line_end=change.line_end,
                code_diff_snippet=change.diff_hunk,
                drift_type=DriftType(drift_type),
                severity=severity,
                confidence=confidence,
                explanation=parsed.get("explanation", ""),
                suggested_fix=parsed.get("suggested_fix", ""),
            )
            findings.append(finding)
        return findings

    def detect_drift(
        self,
        pairs: List[Tuple[CodeSymbolChange, DocSentence]],
    ) -> List[DriftFinding]:
        """Run the full verification pipeline: verify pairs -> findings."""
        results = self.verify_pairs(pairs)
        return self.findings_from_results(pairs, results)


# ---------------------------------------------------------------------------
# OFFLINE drift simulator
# ---------------------------------------------------------------------------

class OfflineDriftDetector:
    """Deterministic drift detector used when no LLM is available.

    Uses lexical + semantic heuristics to simulate drift detection with
    chain-of-thought-style scoring. Designed to produce results comparable
    to the LLM verifier on well-formed (change, sentence) pairs.
    """

    # Keywords that indicate documentation mentions the old code concept
    OLD_CONCEPT_PATTERNS = [
        re.compile(r"--\s*[A-Za-z][\w-]*", re.IGNORECASE),
        re.compile(r"[`'\"]?[A-Z_][A-Z0-9_]*[`'\"]?", re.IGNORECASE),
        re.compile(r"\b\w*timeout\w*\b", re.IGNORECASE),
        re.compile(r"\b\w*port\w*\b", re.IGNORECASE),
        re.compile(r"\b\w*secret\w*\b", re.IGNORECASE),
        re.compile(r"\b\w*deprecated\w*\b", re.IGNORECASE),
        re.compile(r"\b\w*removed\w*\b", re.IGNORECASE),
        re.compile(r"\b\w*async\w*\b", re.IGNORECASE),
        re.compile(r"\b\w*new\w*\b", re.IGNORECASE),
    ]

    def __init__(
        self,
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
        verbose: bool = False,
    ):
        self.confidence_threshold = confidence_threshold
        self.verbose = verbose

    def simulate_drift(
        self, change: CodeSymbolChange, doc: DocSentence
    ) -> Tuple[bool, float, DriftType, Severity, str]:
        """Simulate drift verdict for a (change, doc) pair."""
        score = 0.0
        drift_type = DriftType.NONE
        reasons: List[str] = []

        doc_text = doc.sentence_text.replace("`", "").replace('"', "").replace("'", "")
        symbol = change.symbol_name.replace("-", "_").replace(".", "_").lower()

        # 1. Exact symbol / flag match
        if symbol in doc_text.lower() or change.symbol_name in doc_text:
            score += 0.35
            reasons.append(f"document mentions symbol '{change.symbol_name}'")

        # 2. Value-based drift (changed defaults)
        if change.change_type == ChangeType.DEFAULT_CHANGED:
            old_v = (change.old_value or "").strip()
            new_v = (change.new_value or "").strip()
            if self._contains_value(doc_text, old_v):
                score += 0.55
                reasons.append(f"document states old default value {old_v}")
                drift_type = DriftType.INCORRECT_DEFAULT
            if self._contains_value(doc_text, new_v):
                score -= 0.2
                reasons.append(f"document already states new value {new_v}")

        # 3. Renamed flag drift
        if change.change_type == ChangeType.RENAMED:
            if self._contains_value(doc_text, change.old_value):
                score += 0.4
                reasons.append(f"document still references old flag '{change.old_value}'")
                drift_type = DriftType.RENAMED_FLAG
            elif self._contains_value(doc_text, change.new_value):
                score -= 0.25
                reasons.append("document already references new flag name")

        # 4. Removed feature
        if change.change_type in (ChangeType.REMOVED, ChangeType.DEPRECATED):
            if any(p.search(doc_text) for p in self.OLD_CONCEPT_PATTERNS):
                score += 0.25
                reasons.append("document describes a feature no longer present")
                drift_type = DriftType.REMOVED_FEATURE
            else:
                score += 0.1
                drift_type = DriftType.REMOVED_FEATURE

        # 5. Behavioral change
        if change.change_type == ChangeType.BEHAVIOR_CHANGED:
            score += 0.2
            reasons.append("behavioral change detected; doc semantics may be stale")
            drift_type = DriftType.BEHAVIORAL_DISCREPANCY

        # General-prose false-alarm suppression
        if score < 0.15 and len(doc_text.split()) > 12 and not any(
            self._contains_value(doc_text, v) for v in [change.old_value, change.new_value] if v
        ):
            score = 0.0
            reasons.append("general prose not tied to the changed symbol")

        confidence = min(max(score, 0.0), 1.0)
        is_drift = confidence >= self.confidence_threshold

        severity = self._infer_severity(change, is_drift)
        reasoning = " + ".join(reasons) if reasons else "no drift indicators found"

        return is_drift, confidence, drift_type, severity, reasoning

    def _contains_value(self, text: str, value: str) -> bool:
        if not value:
            return False
        clean = value.strip().strip("`").strip('"').strip("'")
        return re.search(re.escape(clean.lower()), text.lower()) is not None

    @staticmethod
    def _infer_severity(change: CodeSymbolChange, is_drift: bool) -> Severity:
        if not is_drift:
            return Severity.LOW
        if change.change_type in (
            ChangeType.RENAMED,
            ChangeType.REMOVED,
            ChangeType.PARAM_REMOVED,
        ):
            return Severity.HIGH
        if change.change_type == ChangeType.DEFAULT_CHANGED:
            return Severity.MEDIUM
        return Severity.LOW

    def simulate_drift_batch(
        self, pairs: List[Tuple[CodeSymbolChange, DocSentence]]
    ) -> List[Tuple[CodeSymbolChange, DocSentence, bool, float, DriftType, Severity, str]]:
        """Simulate drift for all pairs."""
        results = [self.simulate_drift(c, d) for c, d in pairs]
        return [(c, d) + r for (c, d), r in zip(pairs, results)]

    def build_offline_findings(
        self,
        pairs: List[Tuple[CodeSymbolChange, DocSentence]],
    ) -> List[DriftFinding]:
        """Build DriftFinding records from offline simulation."""
        findings: List[DriftFinding] = []
        for change, doc, is_drift, confidence, drift_type, severity, reasoning in self.simulate_drift_batch(pairs):
            if not is_drift:
                continue
            if confidence < self.confidence_threshold:
                continue
            explanation = self._generate_offline_explanation(change, doc, drift_type)
            suggested_fix = self._generate_offline_fix(change, doc)
            findings.append(
                DriftFinding(
                    id=f"offline-{len(findings)+1}",
                    doc_path=doc.doc_path,
                    doc_line_number=doc.line_number,
                    affected_sentence=doc.sentence_text,
                    code_file_path=change.file_path,
                    code_line_start=change.line_start,
                    code_line_end=change.line_end,
                    code_diff_snippet=change.diff_hunk,
                    drift_type=drift_type,
                    severity=severity,
                    confidence=confidence,
                    explanation=explanation,
                    suggested_fix=suggested_fix,
                )
            )
        return findings

    @staticmethod
    def _generate_offline_explanation(change: CodeSymbolChange, doc: DocSentence, drift_type: DriftType) -> str:
        explanations = {
            DriftType.RENAMED_FLAG: (
                f"The documentation still references the old flag '{change.old_value}', "
                f"but the code now uses '{change.new_value}'."
            ),
            DriftType.INCORRECT_DEFAULT: (
                f"The code's default value changed from {change.old_value} to {change.new_value}, "
                f"but the documentation still states the old default."
            ),
            DriftType.REMOVED_FEATURE: (
                f"The feature '{change.symbol_name}' has been removed/deprecated in the code, "
                f"but documentation still describes it."
            ),
            DriftType.BEHAVIORAL_DISCREPANCY: (
                "The code's behavior has changed and the documentation no longer reflects it accurately."
            ),
            DriftType.OUTDATED_PARAMETER: (
                f"The parameter '{change.symbol_name}' was modified in the code but the documentation "
                f"describes the outdated signature."
            ),
        }
        return explanations.get(drift_type, "Documentation drift detected.")

    @staticmethod
    def _generate_offline_fix(change: CodeSymbolChange, doc: DocSentence) -> str:
        if change.change_type == ChangeType.RENAMED:
            return doc.sentence_text.replace(change.old_value, change.new_value)
        if change.change_type == ChangeType.DEFAULT_CHANGED:
            return doc.sentence_text.replace(change.old_value or "", change.new_value or "")
        if change.change_type == ChangeType.REMOVED:
            return f"Remove reference to '{change.symbol_name}'; it has been removed from the code."
        return "Update this sentence to reflect the current code behavior."


# ---------------------------------------------------------------------------
# Unified drift detection orchestrator
# ---------------------------------------------------------------------------

def detect_documentation_drift(
    pairs: List[Tuple[CodeSymbolChange, DocSentence]],
    llm_mode: str = "offline",
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> List[DriftFinding]:
    """High-level drift detection: uses LLM verifier when available,
    falls back to the offline simulator otherwise."""
    if llm_mode == "offline" or not LLMClient(mode=llm_mode).is_available():
        logger.info("Using OFFLINE drift simulation (no LLM available)")
        detector = OfflineDriftDetector(confidence_threshold=confidence_threshold)
        return detector.build_offline_findings(pairs)
    verifier = DriftVerifier(mode=llm_mode, confidence_threshold=confidence_threshold)
    return verifier.detect_drift(pairs)


if __name__ == "__main__":
    # Demo: verify a sample pair
    from core.models import ChangeType, SymbolType
    from core.doc_parser import DocParser

    change = CodeSymbolChange(
        file_path="cli.py",
        line_start=1,
        line_end=1,
        symbol_name="timeout",
        symbol_type=SymbolType.DEFAULT_VALUE,
        change_type=ChangeType.DEFAULT_CHANGED,
        old_value="30",
        new_value="60",
        diff_hunk="-def main(timeout=30):\n+def main(timeout=60):",
        description="Default timeout changed from 30 to 60",
    )
    doc = DocSentence(
        doc_path="docs/guide.md",
        line_number=5,
        sentence_index=0,
        sentence_text="The default timeout is set to 30 seconds.",
        section_hierarchy=["Configuration"],
        raw_markdown="The default timeout is set to 30 seconds.",
    )
    findings = detect_documentation_drift([(change, doc)])
    for f in findings:
        print(f"[{f.severity.value}] {f.affected_sentence}")
        print(f"   explanation: {f.explanation}")
        print(f"   fix: {f.suggested_fix}")
