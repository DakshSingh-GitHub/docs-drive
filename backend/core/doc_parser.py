"""Documentation parser: Markdown sentence splitter with source line mapping.

Phase 1.3 - Core Engine (Local CLI/Script)
- Extracts individual natural-language sentences from Markdown (.md, .mdx),
  reStructuredText (.rst), and plaintext documentation.
- Every sentence retains its exact source line number, section hierarchy
  (headings), and whether it originates from a table row, list, or prose.
- Synthesizes descriptive text from configuration tables for semantic search.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel

try:
    import markdown_it
    from markdown_it.main import MarkdownIt
    MARKDOWN_IT_AVAILABLE = True
except ImportError:
    MARKDOWN_IT_AVAILABLE = False

from .models import DocSentence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Non-splitter abbreviations and patterns
# ---------------------------------------------------------------------------

NON_SPLIT_ABBREVS = {
    "e",  "e.g",  "i",  "i.e",  "vs",  "vs",  "etc",  "al",
    "approx",  "col",  "dr",  "fig",  "inc",  "incl",  "ltd",
    "max",  "me",  "min",  "mr",  "mrs",  "ms",  "no",  "pp",
    "prof",  "repr",  "resp",  "sec",  "sq",  "sr",  "st",
    "usr",  "vol",  "vol",  "v",  "rev",  "ed",  "ed",
}

# Version / dotted-number patterns that must NOT trigger splitting
VERSION_PATTERN = re.compile(r"\b\d+(?:\.\d+){1,3}(?:\.\d+)?\b")

# Inline code spans: preserve as a single unit
INLINE_CODE_PATTERN = re.compile(r"(`{1,3})((?:(?!\1).)+)\1")

# URLs
URL_PATTERN = re.compile(r"https?://[^\s<>\]]+")


# ---------------------------------------------------------------------------
# Sentence splitter (line-aware, abbreviation-aware)
# ---------------------------------------------------------------------------

class SentenceSplitter:
    """Line-aware sentence splitter with abbreviation protection."""

    def __init__(self, keep_code_inline: bool = True):
        self.keep_code_inline = keep_code_inline

    def split_sentences(self, text: str, start_line: int = 1) -> List[Tuple[str, int]]:
        """Split text into sentences, returning (sentence, line_number).

        Sentences with inline code keep the code span as a single token.
        Abbreviations (e.g. "e.g.", "v1.2", "Python 3.11") are protected.
        """
        lines = text.splitlines()
        sentences: List[Tuple[str, int]] = []
        buffer = ""
        first_line = start_line

        for idx, raw_line in enumerate(lines):
            line_no = start_line + idx
            stripped = raw_line.strip()
            if not stripped:
                continue

            if buffer:
                buffer += " " + stripped
            else:
                buffer = stripped
                first_line = line_no

            parts = self._tokenize_sentence(buffer)
            if parts:
                sentences.append((parts[0], first_line))
                buffer = parts[1] if len(parts) > 1 else ""

        if buffer:
            sentences.append((buffer.strip(), first_line))

        return sentences

    def _tokenize_sentence(self, text: str) -> Tuple[str, str]:
        """Tokenize text into (leading_sentence, trailing_remainder)."""
        # Strip inline code for sentence-termination decisions only;
        # the returned sentence text preserves the original code spans.
        clean = self._strip_inline_code(text)
        pos = self._find_sentence_end(clean)
        if pos == -1:
            return text, ""
        # Map the clean-text position back to the original text position
        orig_pos = self._map_clean_to_original(pos, text)
        leading = text[:orig_pos].strip()
        trailing = text[orig_pos + 1 :].strip()
        return leading, trailing

    @staticmethod
    def _map_clean_to_original(clean_pos: int, text: str) -> int:
        """Map a position in inline-code-stripped text to original text position."""
        orig_pos = 0
        clean_idx = 0
        while clean_idx < clean_pos and orig_pos < len(text):
            m = INLINE_CODE_PATTERN.match(text, orig_pos)
            if m:
                # Skip the entire code span in original (it vanishes in clean)
                orig_pos = m.end()
            else:
                orig_pos += 1
                clean_idx += 1
        return orig_pos

    @staticmethod
    def _strip_inline_code(text: str) -> str:
        """Remove inline code spans from text for split decisions."""
        return INLINE_CODE_PATTERN.sub("", text)

    def _find_sentence_end(self, text: str) -> int:
        """Find the first valid sentence-ending punctuation position."""
        # First, split out URL regions
        url_regions: List[Tuple[int, int]] = []
        for m in URL_PATTERN.finditer(text):
            url_regions.append((m.start(), m.end()))

        def is_in_url(i: int) -> bool:
            return any(s <= i < e for s, e in url_regions)

        # Split on known non-sentence-end dots (abbreviations)
        for i, ch in enumerate(text):
            if ch in ".!?":
                if is_in_url(i):
                    continue
                if self._is_no_split(i, text):
                    continue
                return i
        return -1

    def _is_no_split(self, pos: int, text: str) -> bool:
        """Check if punctuation at pos should NOT end a sentence."""
        before = text[:pos].rstrip()
        after = text[pos + 1 : pos + 5].strip()

        # Abbreviations followed by space + capitalized word
        match = re.search(r"\s+([A-Z])", after)
        if match:
            prefix = before.split()[-1] if before else ""
            if "." in prefix:
                core = prefix.replace(".", "")
                if core.lower() in NON_SPLIT_ABBREVS:
                    return True
        # URL right after
        if URL_PATTERN.match(after) or after.startswith(("http://", "https://")):
            return True
        # Version numbers like "3.11.4." at sentence boundary are still splits
        # but "Python 3.11." alone - keep split
        if VERSION_PATTERN.match(after):
            return True
        # Line end
        return len(after) == 0

    @staticmethod
    def _is_valid_word(text: str) -> bool:
        """Check if a candidate word is a known abbreviation."""
        return text.lower() in NON_SPLIT_ABBREVS


class DocParser:
    """Parse documentation files into line-mapped DocSentences."""

    EXTENSIONS = {".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc"}
    DOC_FOLDERS = {"docs", "doc", "documentation", "wiki", "guide", "manual"}

    def __init__(self, docs_dir: Optional[str] = None):
        self.docs_dir = docs_dir
        self.splitter = SentenceSplitter()
        self.documents: Dict[str, DocIndexDocument] = {}

    def parse_file(self, path: str) -> DocIndexDocument:
        """Parse a single documentation file into DocSentences."""
        path = str(path)
        if not Path(path).exists():
            raise FileNotFoundError(f"Documentation file not found: {path}")
        text = Path(path).read_text(encoding="utf-8")
        return self.parse_text(path, text)

    def parse_text(self, path: str, text: str) -> DocIndexDocument:
        """Parse documentation text into a DocIndexDocument."""
        ext = Path(path).suffix.lower()
        if ext == ".rst":
            sentences = self._parse_rst(text, path)
        else:
            sentences = self._parse_markdown(text, path)
        doc = DocIndexDocument(
            doc_path=path,
            sentences=sentences,
            total_lines=len(text.splitlines()),
        )
        self.documents[path] = doc
        return doc

    def _parse_markdown(self, text: str, doc_path: str = "") -> List[DocSentence]:
        sentences: List[DocSentence] = []
        headings: List[Tuple[int, str]] = []  # (line_number, heading_text)
        section_stack: List[str] = []

        lines = text.splitlines()
        i = 0
        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()

            # Heading
            if stripped.startswith("#"):
                level = 0
                while level < len(stripped) and stripped[level] == "#":
                    level += 1
                heading = stripped[level:].strip()
                # Remove trailing # for ATX headings
                heading = heading.rstrip("#").strip()
                headings.append((i + 1, heading))
                # Build hierarchy properly: all headings up to current level
                section_stack = [h for _, h in headings[:level]]
                i += 1
                continue

            # Table
            if stripped.startswith("|"):
                table_lines, row_start = self._read_table_block(lines, i)
                table_sentences = self._parse_table_row(
                    table_lines, row_start, i + 1, section_stack, doc_path
                )
                sentences.extend(table_sentences)
                i += len(table_lines)
                continue

            # List item
            if re.match(r"^\s*[-*+]\s", stripped) or re.match(r"^\s*\d+\.\s", stripped):
                sentence = self._parse_list_item(stripped, i + 1, section_stack, doc_path)
                if sentence:
                    sentences.append(sentence)
                i += 1
                continue

            # Regular paragraph
            if stripped:
                para_lines, para_start = self._read_paragraph_block(lines, i)
                para_text = " ".join(p.strip() for p in para_lines)
                para_lines_s = self.splitter.split_sentences(para_text)
                for sent, line_no in para_lines_s:
                    if sent.strip():
                        sentences.append(
                            DocSentence(
                                doc_path=doc_path,
                                line_number=line_no,
                                sentence_index=len(sentences),
                                sentence_text=sent,
                                section_hierarchy=section_stack[:],
                                raw_markdown="\n".join(para_lines),
                            )
                        )
                i += len(para_lines)
                continue

            i += 1

        return sentences

    def _read_table_block(self, lines: List[str], start: int) -> Tuple[List[str], int]:
        table_lines = []
        i = start
        while i < len(lines) and lines[i].strip().startswith("|"):
            table_lines.append(lines[i])
            i += 1
        return table_lines, i

    def _parse_table_row(self, table_lines: List[str], row_start: int, base_line: int, section_stack: List[str], doc_path: str = "") -> List[DocSentence]:
        # Find header row
        header_line = None
        header_idx = -1
        for idx, ln in enumerate(table_lines):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            cells = [c for c in cells if c]
            if idx == 0 and header_line is None:
                header_line = cells
                header_idx = idx
        if header_line is None or len(table_lines) < 2:
            return None
        # Determine separator row
        sep_idx = -1
        for idx, ln in enumerate(table_lines[1:], start=1):
            if re.match(r"^\s*\|?\s*[-:]+\s*(\|\s*[-:]+\s*)*\.?\s*$", ln):
                sep_idx = idx
                break
        if sep_idx == -1:
            sep_idx = 1
        # Data rows after separator
        data_rows: List[Tuple[str, int]] = []
        for idx in range(sep_idx + 1, len(table_lines)):
            cells = [c.strip() for c in table_lines[idx].strip().strip("|").split("|")]
            cells = [re.sub(r"``", "", c) for c in cells]
            cells = [re.sub(r"`", "", c) for c in cells]
            cells = [c for c in cells if c]
            if len(cells) >= len(header_line) and cells:
                data_rows.append((cells, base_line + idx))
        sentences: List[DocSentence] = []
        for cells, line_no in data_rows:
            synthesized_parts = []
            for cell, header in zip(cells, header_line):
                synthesized_parts.append(f"{header.lower()}: {cell}")
            synthesized = " ".join(synthesized_parts)
            sentences.append(
                DocSentence(
                    doc_path=doc_path,
                    line_number=line_no,
                    sentence_index=len(sentences),
                    sentence_text=synthesized,
                    section_hierarchy=section_stack[:],
                    is_table_row=True,
                    table_headers=header_line,
                    raw_markdown="\n".join(table_lines),
                )
            )
        return sentences

    def _parse_list_item(self, item: str, line_no: int, section_stack: List[str], doc_path: str = "") -> Optional[DocSentence]:
        cleaned = re.sub(r"^\s*[-*+]\s+", "", item)
        cleaned = re.sub(r"^\s*\d+\.\s+", "", cleaned)
        if cleaned.strip():
            return DocSentence(
                doc_path=doc_path,
                line_number=line_no,
                sentence_index=0,
                sentence_text=cleaned.strip(),
                section_hierarchy=section_stack[:],
                raw_markdown=item,
            )
        return None

    def _read_paragraph_block(self, lines: List[str], start: int) -> Tuple[List[str], int]:
        para_lines = []
        i = start
        while i < len(lines):
            if lines[i].strip():
                para_lines.append(lines[i])
                i += 1
            else:
                break
        return para_lines, i

    def _parse_rst(self, text: str, doc_path: str = "") -> List[DocSentence]:
        """Parse reStructuredText: split on blank lines / directive boundaries."""
        sentences: List[DocSentence] = []
        section_stack: List[str] = []
        blocks = re.split(r"\n\s*\n", text)
        idx = 0
        for block in blocks:
            if not block.strip():
                continue
            idx += block.count("\n") + 1
            clean = " ".join(block.strip().split())
            clean = clean.replace(":ref:", "").replace(":doc:", "")
            for sent, line_no in self.splitter.split_sentences(clean):
                if sent.strip() and len(sent.split()) > 2:
                    sentences.append(
                        DocSentence(
                            doc_path=doc_path,
                            line_number=idx,
                            sentence_index=len(sent),
                            sentence_text=sent,
                            section_hierarchy=section_stack[:],
                            raw_markdown=block,
                        )
                    )
        return sentences

    # ------------------------------------------------ repository discovery

    def find_documentation_files(self, repo_path: str) -> List[str]:
        """Discover documentation files in a repository."""
        repo = Path(repo_path)
        docs: List[str] = []
        seen: Set[str] = set()

        def add_candidate(p: Path) -> None:
            if str(p) in seen:
                return
            if not p.exists():
                return
            if p.suffix.lower() in self.EXTENSIONS:
                rel = str(p.relative_to(repo))
                if any(seg in IGNORE_SEGMENTS for seg in rel.split(os.sep)):
                    return
                docs.append(rel)
                seen.add(str(p))

        IGNORE_SEGMENTS = {"node_modules", "vendor", ".git", ".venv", "venv", "__pycache__", ".next", "dist", "build"}
        # Top-level docs
        for name in ("README.md", "CONTRIBUTING.md", "ARCHITECTURE.md", "CHANGELOG.md", "LICENSE", "HACKATHON.md", "PLAN.md", "README.txt"):
            add_candidate(repo / name)
        # Docs folders
        for folder in self.DOC_FOLDERS:
            folder_path = repo / folder
            if folder_path.is_dir():
                for root, dirs, files in os.walk(folder_path):
                    dirs[:] = [d for d in dirs if d not in IGNORE_SEGMENTS]
                    for f in files:
                        add_candidate(Path(root) / f)
        return docs

    def index_directory(self, repo_path: str) -> None:
        """Index all documentation files in a repository."""
        paths = self.find_documentation_files(repo_path)
        # Also walk the directory directly for doc files (handles --docs-dir pointing at a docs folder)
        repo = Path(repo_path)
        if repo.is_dir():
            for root, dirs, files in os.walk(repo):
                dirs[:] = [d for d in dirs if d not in IGNORE_SEGMENTS]
                for f in files:
                    p = Path(root) / f
                    if p.suffix.lower() in self.EXTENSIONS:
                        rel = str(p.relative_to(repo))
                        if rel not in paths:
                            paths.append(rel)
        for rel in paths:
            try:
                self.parse_file(os.path.join(repo_path, rel))
            except Exception as exc:  # pragma: no cover
                logger.warning("Failed to index %s: %s", rel, exc)


class DocIndexDocument(BaseModel):
    """Container holding all DocSentences for one documentation file."""

    doc_path: str
    sentences: List[DocSentence]
    total_lines: int
    encoding: str = "utf-8"


if __name__ == "__main__":
    # Demo: parse a sample README
    sample = """# My Project

Welcome to **My Project**. This is a CLI tool for managing tasks.

## Configuration

By default, the `--timeout` flag is set to `30` seconds. You can override
this with the `--timeout-secs` environment variable.

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--timeout` | int | `30` | Connection timeout in seconds |
| `--port` | int | `8080` | Server listening port |

- Run with `python main.py`
- Use `--help` for usage

The default port is 8080 unless changed in `config.py`.
"""
    parser = DocParser()
    doc = parser.parse_text("sample.md", sample)
    for s in doc.sentences:
        print(f"[{s.line_number}] {s.section_hierarchy} | {s.sentence_text[:80]}")
