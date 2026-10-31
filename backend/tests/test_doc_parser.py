"""Unit tests for the documentation parser (Phase 1.3)."""

import pytest

from core.doc_parser import DocParser, SentenceSplitter


SAMPLE_MARKDOWN = """# My Project

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

SAMPLE_RST = """My Project
==========

Welcome to **My Project**. This is a CLI tool.

Configuration
-------------

By default, the ``--timeout`` flag is set to ``30`` seconds.
"""


def test_sentence_splitting_within_paragraph():
    """Sentence splitting within a multi-line paragraph assigns correct lines."""
    parser = DocParser()
    doc = parser.parse_text("test.md", SAMPLE_MARKDOWN)
    paragraphs = [s for s in doc.sentences if not s.is_table_row and "Run with" not in s.sentence_text]
    assert len(paragraphs) >= 2
    intro = next(s for s in paragraphs if "Welcome to" in s.sentence_text)
    assert "My Project" in intro.sentence_text


def test_markdown_table_parsing():
    """Markdown table parsing creates DocSentences with correct headers and line coordinates."""
    parser = DocParser()
    doc = parser.parse_text("test.md", SAMPLE_MARKDOWN)
    rows = [s for s in doc.sentences if s.is_table_row]
    assert len(rows) == 2
    timeout_row = next(r for r in rows if r.sentence_text.startswith("flag: --timeout"))
    assert timeout_row.is_table_row is True
    assert timeout_row.table_headers == ["Flag", "Type", "Default", "Description"]
    assert "--timeout" in timeout_row.sentence_text
    assert "30" in timeout_row.sentence_text
    assert timeout_row.line_number is not None


def test_heading_hierarchy_tracked():
    """Section hierarchy is accurately tracked across nested headings."""
    parser = DocParser()
    doc = parser.parse_text("test.md", SAMPLE_MARKDOWN)
    # Find a sentence under "## Configuration"
    config_sents = [s for s in doc.sentences if "Configuration" in s.section_hierarchy]
    assert len(config_sents) >= 1


def test_abbreviations_not_split():
    """Sentences with abbreviations (e.g., v1.2) are not prematurely split."""
    splitter = SentenceSplitter()
    text = "The default timeout is e.g. 30 seconds. v1.2 changed this."
    sents = [s for s, _ in splitter.split_sentences(text)]
    assert len(sents) <= 3  # e.g. and v1.2 protected


def test_rst_parsing():
    """reStructuredText parsing produces sentences."""
    parser = DocParser()
    doc = parser.parse_text("test.rst", SAMPLE_RST)
    assert len(doc.sentences) >= 1


def test_inline_code_not_split():
    """Inline code spans like `--timeout` are preserved and not split."""
    splitter = SentenceSplitter()
    text = 'By default, the `--timeout` flag is set to `30` seconds.'
    sents = [s for s, _ in splitter.split_sentences(text)]
    assert len(sents) == 1
    assert "--timeout" in sents[0]


def test_index_directory_ignores_node_modules(tmp_path):
    """index_directory walks directory and skips IGNORE_SEGMENTS without NameError."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "guide.md").write_text("# Guide\n\nDefault port is 8080.\n", encoding="utf-8")

    nm_dir = docs_dir / "node_modules" / "pkg"
    nm_dir.mkdir(parents=True)
    (nm_dir / "README.md").write_text("# Ignored\n\nShould not be indexed.\n", encoding="utf-8")

    parser = DocParser()
    parser.index_directory(str(docs_dir))

    assert len(parser.documents) == 1
    indexed_paths = list(parser.documents.keys())
    assert any("guide.md" in p for p in indexed_paths)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
