"""Universal Pydantic data contracts for the documentation drift detection engine.

All modules must adhere strictly to these shared data contracts as defined in
prompts/base.md.
"""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class SymbolType(str, Enum):
    """Type of a code symbol that changed."""

    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    CONFIG_FLAG = "config_flag"
    CLI_ARGUMENT = "cli_argument"
    ENV_VARIABLE = "env_variable"
    DEFAULT_VALUE = "default_value"
    CONSTANT = "constant"
    ENDPOINT = "endpoint"


class ChangeType(str, Enum):
    """Type of semantic change detected."""

    RENAMED = "renamed"
    DEFAULT_CHANGED = "default_changed"
    PARAM_MODIFIED = "param_modified"
    PARAM_REMOVED = "param_removed"
    PARAM_ADDED = "param_added"
    DEPRECATED = "deprecated"
    REMOVED = "removed"
    BEHAVIOR_CHANGED = "behavior_changed"


class CodeSymbolChange(BaseModel):
    """Code Symbol Change - AST-level semantic mutation."""

    file_path: str
    line_start: int
    line_end: int
    symbol_name: str
    symbol_type: SymbolType
    change_type: ChangeType
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    diff_hunk: str
    context_signature: Optional[str] = None
    description: str

    model_config = ConfigDict(frozen=True)


class DocSentence(BaseModel):
    """Document Sentence Reference - a single sentence from documentation."""

    doc_path: str
    line_number: int
    sentence_index: int
    sentence_text: str
    section_hierarchy: List[str]
    is_table_row: bool = False
    table_headers: Optional[List[str]] = None
    raw_markdown: str

    model_config = ConfigDict(frozen=True)


class DriftType(str, Enum):
    """Category of detected documentation drift."""

    OUTDATED_PARAMETER = "outdated_parameter"
    RENAMED_FLAG = "renamed_flag"
    INCORRECT_DEFAULT = "incorrect_default"
    REMOVED_FEATURE = "removed_feature"
    BEHAVIORAL_DISCREPANCY = "behavioral_discrepancy"
    NONE = "none"


class Severity(str, Enum):
    """Severity of the drift finding."""

    CRITICAL = "critical"  # Code will break if user follows doc
    HIGH = "high"  # Configuration or API signature incorrect
    MEDIUM = "medium"  # Default value or non-breaking behavior changed
    LOW = "low"  # Minor wording or cosmetic description drift

    @classmethod
    def from_change_type(cls, change_type: ChangeType) -> "Severity":
        """Infer severity from the type of code change."""
        breaking = {
            ChangeType.RENAMED,
            ChangeType.REMOVED,
            ChangeType.PARAM_REMOVED,
            ChangeType.DEPRECATED,
        }
        if change_type in breaking:
            return cls.HIGH
        if change_type == ChangeType.DEFAULT_CHANGED:
            return cls.MEDIUM
        return cls.LOW


class DriftFinding(BaseModel):
    """A verified documentation drift incident."""

    id: str
    doc_path: str
    doc_line_number: int
    affected_sentence: str
    code_file_path: str
    code_line_start: int
    code_line_end: int
    code_diff_snippet: str
    drift_type: DriftType
    severity: Severity
    confidence: float  # 0.0 to 1.0
    explanation: str
    suggested_fix: str
    is_suppressed: bool = False
    suppression_reason: Optional[str] = None

    model_config = ConfigDict(frozen=True)


class DriftAnalysisReport(BaseModel):
    """Aggregated analysis report for CI and API output."""

    repo_name: str
    pr_number: Optional[int] = None
    base_commit: str
    head_commit: str
    timestamp: str
    total_files_analyzed: int
    total_symbols_changed: int
    total_doc_sentences_scanned: int
    findings: List[DriftFinding]
    execution_time_seconds: float

    @staticmethod
    def now_iso() -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    model_config = ConfigDict(frozen=True)


class SymbolChangeCandidate(BaseModel):
    """Intermediate representation of a candidate code symbol change."""

    symbol_name: str
    symbol_type: SymbolType
    change_type: ChangeType
    old_value: Optional[str]
    new_value: Optional[str]
    context_signature: Optional[str]
    description: str


class DocIndexDocument(BaseModel):
    """Document-level index document with source text and offsets."""

    doc_path: str
    sentences: List[DocSentence]
    total_lines: int
    encoding: str = "utf-8"
