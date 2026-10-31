"""Documentation Drift Detection - Core Analysis Engine.

Modules:
    diff_parser   : Git diff ingestion and hunk parsing
    ast_extractor : AST-based semantic change extraction
    doc_parser    : Markdown documentation sentence indexing
    hybrid_matcher: BM25 + dense embedding retrieval linker
    drift_verifier: LLM drift verification & false-positive filtering
    embeddings    : nomic-embed-text embedding service
    llm_client    : Ollama/OpenAI/Anthropic LLM client
    models        : Universal Pydantic data contracts
"""

from .models import (
    SymbolType,
    ChangeType,
    CodeSymbolChange,
    DocSentence,
    DriftType,
    Severity,
    DriftFinding,
    DriftAnalysisReport,
)

__all__ = [
    "SymbolType",
    "ChangeType",
    "CodeSymbolChange",
    "DocSentence",
    "DriftType",
    "Severity",
    "DriftFinding",
    "DriftAnalysisReport",
]
