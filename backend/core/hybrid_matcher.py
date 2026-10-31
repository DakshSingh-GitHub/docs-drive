"""Hybrid retrieval engine: BM25 + nomic-embed-text embeddings for linking
code changes to documentation sentences.

Phase 1.4 - Core Engine (Local CLI/Script)

Architecture:
    CodeSymbolChange -> Query Synthesizer -> Query Text
                                 /    \
                                /      \
           Exact Symbol Search    BM25    Nomic-Embed (Ollama)
                                \      /
                                 \    /
                            Reciprocal Rank Fusion (RRF)
                                 |
                                 v
                      Top-K (CodeSymbolChange, DocSentence) pairs

In OFFLINE mode (Ollama not available), the engine falls back to BM25 +
deterministic TF-IDF-like scoring so the full pipeline runs without network.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rank_bm25 import BM25Okapi

from .doc_parser import DocParser
from .diff_parser import DiffParser
from .models import CodeSymbolChange, DocSentence, DriftFinding, Severity
from .embeddings import EmbeddingService, EmbeddingMode, OFFLINE_EMBEDDING_DIM

logger = logging.getLogger(__name__)

# RRF constant (reciprocal rank fusion)
RRF_CONSTANT = 60


class HybridMatcher:
    """Hybrid lexical + dense retrieval linker."""

    def __init__(
        self,
        docs_dir: Optional[str] = None,
        mode: EmbeddingMode = EmbeddingMode.AUTO,
        top_k: int = 10,
        confidence_threshold: float = 0.70,
        exact_symbol_boost: float = 0.5,
    ):
        self.docs_dir = docs_dir
        self.top_k = top_k
        self.confidence_threshold = confidence_threshold
        self.exact_symbol_boost = exact_symbol_boost
        self.embedding_service: Optional[EmbeddingService] = None
        if mode != EmbeddingMode.OFFLINE:
            self.embedding_service = EmbeddingService(mode=mode)

        self.bm25: Optional[BM25Okapi] = None
        self.tokenized_docs: List[List[str]] = []
        self.doc_sentences: List[DocSentence] = []
        self.doc_indexes: Dict[str, int] = {}
        self.bm25_scores: Dict[str, List[float]] = {}
        self.vector_scores: Dict[str, List[float]] = {}
        self._built = False

    def index_documents(self, sentences: List[DocSentence]) -> None:
        """Index a list of DocSentences for retrieval."""
        self.doc_sentences = sentences
        self.doc_indexes = {s.doc_path: i for i, s in enumerate(sentences)}
        self.tokenized_docs = [self._tokenize(s.sentence_text) for s in sentences]
        self.bm25_scores = {s.doc_path: [] for s in sentences}
        self.vector_scores = {s.doc_path: [] for s in sentences}

        if self.tokenized_docs:
            self.bm25 = BM25Okapi(self.tokenized_docs)
        self._built = True

    def synthesize_query(self, change: CodeSymbolChange) -> str:
        """Synthesize a rich retrieval query from a CodeSymbolChange."""
        parts = [
            f"Symbol: {change.symbol_name}",
            f"Type: {change.symbol_type.value}",
            f"Change: {change.change_type.value}",
            f"Description: {change.description}",
        ]
        if change.old_value:
            parts.append(f"Previous value: {change.old_value}")
        if change.new_value:
            parts.append(f"New value: {change.new_value}")
        if change.context_signature:
            parts.append(f"Signature: {change.context_signature}")
        return " | ".join(parts)

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Tokenize document text for BM25."""
        text = re.sub(r"[^a-zA-Z0-9\s]", " ", text)
        return text.lower().split()

    def retrieve_candidates(
        self, change: CodeSymbolChange, top_k: Optional[int] = None
    ) -> List[Tuple[DocSentence, float]]:
        """Retrieve top-K candidate (DocSentence, RRF-score) pairs for a change."""
        top_k = top_k or self.top_k
        query = self.synthesize_query(change)
        query_tokens = self._tokenize(query)

        scores: Dict[str, List[float]] = {}

        # --- Stage 1: BM25 lexical retrieval
        if self.bm25 is not None and query_tokens:
            bm25_scores = self.bm25.get_scores(query_tokens)
            for idx, score in enumerate(bm25_scores):
                doc_path = self.doc_sentences[idx].doc_path
                if doc_path not in scores:
                    scores[doc_path] = []
                scores[doc_path].append(("bm25", float(score))) # type: ignore

        # --- Stage 2: Dense vector retrieval (if embeddings available)
        if self.embedding_service and self.embedding_service.is_available():
            query_embedding = self.embedding_service.embed_text(query)
            for idx, s in enumerate(self.doc_sentences):
                doc_vec = self.embedding_service.embed_text(s.sentence_text)
                sim = self._cosine_similarity(query_embedding, doc_vec)
                doc_path = s.doc_path
                if doc_path not in scores:
                    scores[doc_path] = []
                scores[doc_path].append(("vector", sim)) # type: ignore

        # --- Stage 3: Reciprocal Rank Fusion
        candidates: List[Tuple[DocSentence, float]] = []
        rank_bm25: Dict[str, int] = {}
        rank_vector: Dict[str, int] = {}

        for doc_path, item_list in scores.items():
            for source, score in item_list: # type: ignore
                rank_key = f"{doc_path}::{source}"
                if score > 0:
                    if source == "bm25":
                        rank_bm25[rank_key] = len(rank_bm25) + 1
                    elif source == "vector":
                        rank_vector[rank_key] = len(rank_vector) + 1

        # Sort each source by score descending
        sorted_bm25 = sorted(
            [(k, v) for k, v in scores.items() if any(s == "bm25" for _, s in v)], # type: ignore
            key=lambda x: max(v2 for _, v2 in x[1] if _ == "bm25"), # type: ignore
            reverse=True,
        )
        sorted_vector = sorted(
            [(k, v) for k, v in scores.items() if any(s == "vector" for _, s in v)], # type: ignore
            key=lambda x: max(v2 for _, v2 in x[1] if _ == "vector"), # type: ignore
            reverse=True,
        )

        for rank, (doc_path, _) in enumerate(sorted_bm25, start=1):
            key = f"{doc_path}::bm25"
            if key not in rank_bm25:
                rank_bm25[key] = rank
        for rank, (doc_path, _) in enumerate(sorted_vector, start=1):
            key = f"{doc_path}::vector"
            if key not in rank_vector:
                rank_vector[key] = rank

        fusion_scores: Dict[str, float] = {}
        for doc_path in set(rank_bm25) | set(rank_vector):
            r1 = rank_bm25.get(doc_path, float("inf"))
            r2 = rank_vector.get(doc_path, float("inf"))
            rrf = 1 / (RRF_CONSTANT + r1) + 1 / (RRF_CONSTANT + r2)
            fusion_scores[doc_path] = rrf

        # --- Exact symbol name boost
        symbol_name = change.symbol_name.replace("-", "_").replace(".", "_").lower()
        boosted: List[Tuple[DocSentence, float]] = []
        for doc_path, rrf in sorted(fusion_scores.items(), key=lambda x: -x[1]):
            s = self.doc_sentences[self.doc_indexes.get(doc_path, 0)]
            raw = self._normalize_sentence(s.sentence_text)
            if symbol_name in raw or change.symbol_name in s.sentence_text:
                rrf += self.exact_symbol_boost
            boosted.append((s, rrf))
            if len(boosted) >= top_k:
                break

        return boosted[:top_k]

    @staticmethod
    def _normalize_sentence(text: str) -> str:
        text = text.replace("`", "").replace("|", " ").replace("-", " ")
        return re.sub(r"\s+", " ", text).lower()

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    def link_changes_to_docs(
        self, changes: List[CodeSymbolChange]
    ) -> Dict[CodeSymbolChange, List[Tuple[DocSentence, float]]]:
        """Link all code changes to candidate documentation sentences."""
        if not self._built:
            raise RuntimeError("Index not built. Call index_documents first.")
        results: Dict[CodeSymbolChange, List[Tuple[DocSentence, float]]] = {}
        for change in changes:
            results[change] = self.retrieve_candidates(change)
        return results


# ---------------------------------------------------------------------------
# PR Doc-Update Guard & candidate filtering
# ---------------------------------------------------------------------------

class CandidateFilter:
    """Filter retrieval candidates: PR doc-update guard + confidence threshold."""

    def __init__(self, parser: DiffParser):
        self.parser = parser

    def apply_pr_guard(
        self, candidate: Tuple[DocSentence, float]
    ) -> Tuple[DocSentence, float, bool]:
        """PR Doc-Update Guard: discard doc sentences already updated in PR."""
        doc, score = candidate
        already_updated = self.parser.find_doc_sentence_in_pr_changes(
            doc.doc_path, doc.line_number
        )
        return doc, score, already_updated

    def filter_candidates(
        self,
        candidates: List[Tuple[DocSentence, float]],
        min_score: float = 0.0,
    ) -> List[Tuple[DocSentence, float]]:
        """Filter candidates by min score and PR doc-update guard."""
        filtered: List[Tuple[DocSentence, float]] = []
        for doc, score in candidates:
            if score < min_score:
                continue
            doc, score, already_updated = self.apply_pr_guard((doc, score))
            if already_updated:
                logger.debug(
                    "Suppressed already-updated doc %s line %d",
                    doc.doc_path,
                    doc.line_number,
                )
                continue
            filtered.append((doc, score))
        return filtered


# ---------------------------------------------------------------------------
# End-to-end analysis pipeline
# ---------------------------------------------------------------------------

from dataclasses import dataclass


@dataclass
class DriftLinkageResult:
    """Result of linking code changes to documentation."""

    code_change: CodeSymbolChange
    candidates: List[Tuple[DocSentence, float]]
    matched_sentence: Optional[DocSentence] = None


def run_hybrid_matcher(
    diff_parser: DiffParser,
    doc_parser: DocParser,
    mode: EmbeddingMode = EmbeddingMode.AUTO,
    top_k: int = 5,
) -> Dict[CodeSymbolChange, List[Tuple[DocSentence, float]]]:
    """Run the full hybrid matcher pipeline over a diff + doc index."""
    matcher = HybridMatcher(docs_dir=doc_parser.docs_dir, mode=mode, top_k=top_k)
    all_sentences: List[DocSentence] = []
    for doc in doc_parser.documents.values():
        all_sentences.extend(doc.sentences)
    matcher.index_documents(all_sentences)

    # Extract code changes via AST extractor
    from .ast_extractor import ASTSymbolExtractor
    extractor = ASTSymbolExtractor()
    changes = extractor.extract_from_diff(diff_parser.code_files)

    return matcher.link_changes_to_docs(changes)


if __name__ == "__main__":
    # Demo: link changes to docs in this repo
    import sys

    sample_docs = """# Project Docs

The `--timeout` flag controls the connection timeout. It defaults to `30` seconds.

| Flag | Default | Description |
| --- | --- | --- |
| `--timeout` | `30` | Connection timeout in seconds |
"""
    doc_parser = DocParser()
    doc_parser.parse_text("docs/guide.md", sample_docs)

    sample_diff = """diff --git a/cli.py b/cli.py
index abc..def 100644
--- a/cli.py
+++ b/cli.py
@@ -1,5 +1,5 @@
-def main(timeout=30):
+def main(timeout=60):
     pass
"""
    diff_parser = parse_diff(sample_diff) # type: ignore
    results = run_hybrid_matcher(diff_parser, doc_parser)
    for change, cands in results.items():
        print(f"[{change.symbol_name}] {change.change_type.value}")
        for doc, score in cands:
            print(f"   [{score:.3f}] {doc.doc_path}:{doc.line_number} - {doc.sentence_text[:60]}")
