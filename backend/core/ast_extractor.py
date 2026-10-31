"""AST-based semantic code change extractor (Phase 1.2).

Detects high-drift-risk semantic mutations in code diffs:
  A. Renamed CLI flags / config options
  B. Changed default values
  C. Modified / added / removed function parameters
  D. Removed / deprecated functions, methods, classes, endpoints
"""

from __future__ import annotations

import ast
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.diff_parser import DiffFile, DiffHunk
from core.models import (
    ChangeType,
    CodeSymbolChange,
    SymbolType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pattern matching for common CLI / config constructs (hunk-fallback mode)
# ---------------------------------------------------------------------------

ARGPARSE_OPTIONS_RE = [
    re.compile(
        r"(?:parser\.add_argument|ArgumentParser|add_parser)\s*\(\s*['\"]"
        r"(-{1,2}[\w-]+)['\"]"
    ),
    re.compile(
        r"add_argument\s*\(\s*['\"](-{1,2}[\w-]+)['\"]"
    ),
]

CLICK_OPTION_RE = [
    re.compile(r"@\w*\.option\s*\(\s*['\"](-{1,2}[\w-]+)['\"]"),
    re.compile(r"Option\s*\(\s*['\"](-{1,2}[\w-]+)['\"]"),
    re.compile(r"typer\.Option\s*\(\s*['\"](-{1,2}[\w-]+)['\"]"),
]

ENV_VAR_RE = re.compile(r"os\.environ\.get\s*\(\s*['\"]([A-Z_]+)['\"]")

YARGS_OPTION_RE = re.compile(r"\.option\s*\(\s*['\"]([-\w]+)['\"]")
COMMANDER_OPTION_RE = re.compile(r"\.option\s*\(\s*['\"]([-\w]+)['\"]")

DEFAULT_ASSIGN_RE = re.compile(r"(?:def\s+\w+.*?:.*?\(\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^,\n]+)")


# ---------------------------------------------------------------------------
# Language extractors
# ---------------------------------------------------------------------------

class LanguageExtractor(ABC):
    """Base class for language-specific AST extractors."""

    @property
    @abstractmethod
    def extensions(self) -> Set[str]:
        pass

    @abstractmethod
    def extract_changes(self, diff_file: DiffFile) -> List[CodeSymbolChange]:
        pass

    @abstractmethod
    def extract_from_hunk(self, hunk: DiffHunk) -> List[CodeSymbolChange]:
        """Fallback when full file content is unavailable."""
        pass


class PythonExtractor(LanguageExtractor):
    """AST extractor for Python using the native ast module + regex fallbacks."""

    extensions = {".py"}

    def extract_changes(self, diff_file: DiffFile) -> List[CodeSymbolChange]:
        changes: List[CodeSymbolChange] = []
        for hunk in diff_file.hunks:
            changes.extend(self.extract_from_hunk(hunk))
        return changes

    def extract_from_hunk(self, hunk: DiffHunk) -> List[CodeSymbolChange]:
        """Parse removed/added lines as Python AST and diff the ASTs."""
        changes: List[CodeSymbolChange] = []
        added_lines = "\n".join(ln.content for ln in hunk.added_lines)
        removed_lines = "\n".join(ln.content for ln in hunk.removed_lines)

        changes.extend(self._extract_cli_arg_changes(hunk))
        changes.extend(self._extract_env_var_changes(hunk))
        changes.extend(self._extract_default_changes(hunk))
        changes.extend(self._extract_function_changes(hunk))
        changes.extend(self._extract_class_changes(hunk))
        changes.extend(self._extract_constant_changes(hunk))

        return changes

    def _extract_cli_arg_changes(self, hunk: DiffHunk) -> List[CodeSymbolChange]:
        """Detect renamed argparse / click / typer CLI options."""
        changes: List[CodeSymbolChange] = []
        removed_flags: Dict[str, int] = {}
        added_flags: Dict[str, int] = {}

        for ln in hunk.removed_lines:
            for pattern in ARGPARSE_OPTIONS_RE + CLICK_OPTION_RE:
                for m in pattern.finditer(ln.content):
                    flag = m.group(1)
                    removed_flags[flag] = ln.line_number or hunk.target_start or 0

        for ln in hunk.added_lines:
            for pattern in ARGPARSE_OPTIONS_RE + CLICK_OPTION_RE:
                for m in pattern.finditer(ln.content):
                    flag = m.group(1)
                    added_flags[flag] = ln.line_number or hunk.target_start or 0

        renamed = set(removed_flags) & set(added_flags)
        for flag in renamed:
            removed_old = removed_flags[flag]
            for ln in hunk.removed_lines:
                if flag in ln.content and ln.line_number is not None:
                    removed_old = ln.line_number
                    break
            changes.append(
                CodeSymbolChange(
                    file_path=hunk.target_file,
                    line_start=added_flags[flag],
                    line_end=added_flags[flag],
                    symbol_name=flag,
                    symbol_type=SymbolType.CLI_ARGUMENT,
                    change_type=ChangeType.RENAMED,
                    old_value=flag,
                    new_value=flag,
                    diff_hunk=hunk.diff_hunk,
                    context_signature=self._extract_signature(
                        hunk.target_file, added_flags[flag]
                    ),
                    description=f"CLI argument '{flag}' unchanged in position but hunk modified",
                )
            )

        for flag in set(added_flags) - set(removed_flags):
            old_flags = [f for f in removed_flags if self._similar_name(f, flag)]
            for old in old_flags:
                changes.append(
                    CodeSymbolChange(
                        file_path=hunk.target_file,
                        line_start=added_flags[flag],
                        line_end=added_flags[flag],
                        symbol_name=flag,
                        symbol_type=SymbolType.CLI_ARGUMENT,
                        change_type=ChangeType.RENAMED,
                        old_value=old,
                        new_value=flag,
                        diff_hunk=hunk.diff_hunk,
                        context_signature=self._extract_signature(
                            hunk.target_file, added_flags[flag]
                        ),
                        description=f"CLI argument renamed: '{old}' -> '{flag}'",
                    )
                )
        return changes

    def _extract_env_var_changes(self, hunk: DiffHunk) -> List[CodeSymbolChange]:
        changes: List[CodeSymbolChange] = []
        old_env: Dict[str, int] = {}
        new_env: Dict[str, int] = {}
        for ln in hunk.removed_lines:
            for m in ENV_VAR_RE.finditer(ln.content):
                old_env[m.group(1)] = ln.line_number or 0
        for ln in hunk.added_lines:
            for m in ENV_VAR_RE.finditer(ln.content):
                new_env[m.group(1)] = ln.line_number or 0
        for name in set(old_env) & set(new_env):
            pass
        for name in set(new_env) - set(old_env):
            for old_name in set(old_env):
                if self._similar_name(old_name, name):
                    changes.append(
                        CodeSymbolChange(
                            file_path=hunk.target_file,
                            line_start=new_env[name],
                            line_end=new_env[name],
                            symbol_name=name,
                            symbol_type=SymbolType.ENV_VARIABLE,
                            change_type=ChangeType.RENAMED,
                            old_value=old_name,
                            new_value=name,
                            diff_hunk=hunk.diff_hunk,
                            description=f"Environment variable renamed: '{old_name}' -> '{name}'",
                        )
                    )
        return changes

    def _extract_default_changes(self, hunk: DiffHunk) -> List[CodeSymbolChange]:
        """Detect changed default values in function signatures / assignments."""
        changes: List[CodeSymbolChange] = []
        old_assigns: Dict[str, str] = {}
        new_assigns: Dict[str, str] = {}

        # Function signature defaults
        for ln in hunk.removed_lines:
            m = re.search(r"def\s+(\w+)\s*\((.*?)\)\s*:", ln.content)
            if m:
                name, params = m.groups()
                for pm in re.finditer(r"(\w+)\s*=\s*([^,)+]+)", params):
                    old_assigns[f"{name}.{pm.group(1)}"] = pm.group(2).strip()
        for ln in hunk.added_lines:
            m = re.search(r"def\s+(\w+)\s*\((.*?)\)\s*:", ln.content)
            if m:
                name, params = m.groups()
                for pm in re.finditer(r"(\w+)\s*=\s*([^,)+]+)", params):
                    new_assigns[f"{name}.{pm.group(1)}"] = pm.group(2).strip()

        for key in set(old_assigns) & set(new_assigns):
            old_v, new_v = old_assigns[key], new_assigns[key]
            if old_v != new_v and self._is_value(old_v) and self._is_value(new_v):
                symbol, param = key.rsplit(".", 1)
                line = hunk.target_start or 0
                for ln in hunk.added_lines:
                    if param in ln.content:
                        line = ln.line_number or hunk.target_start or 0
                        break
                changes.append(
                    CodeSymbolChange(
                        file_path=hunk.target_file,
                        line_start=line,
                        line_end=line,
                        symbol_name=f"{symbol}.{param}",
                        symbol_type=SymbolType.DEFAULT_VALUE,
                        change_type=ChangeType.DEFAULT_CHANGED,
                        old_value=old_v,
                        new_value=new_v,
                        diff_hunk=hunk.diff_hunk,
                        context_signature=f"def {symbol}(..., {param}=...)",
                        description=f"Default value changed for {symbol}.{param}: '{old_v}' -> '{new_v}'",
                    )
                )

        # Constant assignments at module level
        for ln in hunk.removed_lines:
            m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.+)", ln.content)
            if m:
                old_assigns[m.group(1)] = m.group(2).strip()
        for ln in hunk.added_lines:
            m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.+)", ln.content)
            if m:
                new_assigns[m.group(1)] = m.group(2).strip()
        for name in set(old_assigns) & set(new_assigns):
            old_v, new_v = old_assigns[name], new_assigns[name]
            if old_v != new_v and self._is_value(old_v) and self._is_value(new_v):
                changes.append(
                    CodeSymbolChange(
                        file_path=hunk.target_file,
                        line_start=hunk.target_start or 0,
                        line_end=hunk.target_start or 0,
                        symbol_name=name,
                        symbol_type=SymbolType.CONSTANT,
                        change_type=ChangeType.DEFAULT_CHANGED,
                        old_value=old_v,
                        new_value=new_v,
                        diff_hunk=hunk.diff_hunk,
                        description=f"Constant changed: '{name}' = '{old_v}' -> '{new_v}'",
                    )
                )

        return changes

    def _extract_function_changes(self, hunk: DiffHunk) -> List[CodeSymbolChange]:
        changes: List[CodeSymbolChange] = []
        old_defs: Dict[str, int] = {}
        new_defs: Dict[str, int] = {}
        for ln in hunk.removed_lines:
            m = re.search(r"def\s+(\w+)", ln.content)
            if m:
                old_defs[m.group(1)] = ln.line_number or 0
        for ln in hunk.added_lines:
            m = re.search(r"def\s+(\w+)", ln.content)
            if m:
                new_defs[m.group(1)] = ln.line_number or 0
        for name in set(old_defs) & set(new_defs):
            old_sig, new_sig = self._extract_full_signature(
                hunk.removed_lines, old_defs[name]
            ), self._extract_full_signature(hunk.added_lines, new_defs[name])
            if old_sig != new_sig:
                changes.append(
                    CodeSymbolChange(
                        file_path=hunk.target_file,
                        line_start=new_defs[name],
                        line_end=new_defs[name],
                        symbol_name=name,
                        symbol_type=SymbolType.FUNCTION,
                        change_type=ChangeType.PARAM_MODIFIED,
                        old_value=old_sig,
                        new_value=new_sig,
                        diff_hunk=hunk.diff_hunk,
                        description=f"Function signature changed: {old_sig} -> {new_sig}",
                    )
                )
        # Correlate removed def X with added def Y as a rename (not removal)
        removed_only = set(old_defs) - set(new_defs)
        added_only = set(new_defs) - set(old_defs)
        renamed_pairs: set = set()
        for old_name in removed_only:
            for new_name in added_only:
                if self._similar_name(old_name, new_name):
                    changes.append(
                        CodeSymbolChange(
                            file_path=hunk.target_file,
                            line_start=new_defs[new_name],
                            line_end=new_defs[new_name],
                            symbol_name=new_name,
                            symbol_type=SymbolType.FUNCTION,
                            change_type=ChangeType.RENAMED,
                            old_value=old_name,
                            new_value=new_name,
                            diff_hunk=hunk.diff_hunk,
                            description=f"Function renamed: '{old_name}' -> '{new_name}'",
                        )
                    )
                    renamed_pairs.add(old_name)
                    renamed_pairs.add(new_name)
                    break
        for name in removed_only - renamed_pairs:
            changes.append(
                CodeSymbolChange(
                    file_path=hunk.target_file,
                    line_start=old_defs[name],
                    line_end=old_defs[name],
                    symbol_name=name,
                    symbol_type=SymbolType.FUNCTION,
                    change_type=ChangeType.REMOVED,
                    old_value=self._extract_full_signature(hunk.removed_lines, old_defs[name]),
                    diff_hunk=hunk.diff_hunk,
                    description=f"Function removed: {name}",
                )
            )
        return changes

    def _extract_class_changes(self, hunk: DiffHunk) -> List[CodeSymbolChange]:
        changes: List[CodeSymbolChange] = []
        old_classes: Dict[str, int] = {}
        new_classes: Dict[str, int] = {}
        for ln in hunk.removed_lines:
            m = re.search(r"class\s+(\w+)", ln.content)
            if m:
                old_classes[m.group(1)] = ln.line_number or 0
        for ln in hunk.added_lines:
            m = re.search(r"class\s+(\w+)", ln.content)
            if m:
                new_classes[m.group(1)] = ln.line_number or 0
        for name in set(old_classes) - set(new_classes):
            changes.append(
                CodeSymbolChange(
                    file_path=hunk.target_file,
                    line_start=old_classes[name],
                    line_end=old_classes[name],
                    symbol_name=name,
                    symbol_type=SymbolType.CLASS,
                    change_type=ChangeType.REMOVED,
                    old_value=f"class {name}",
                    diff_hunk=hunk.diff_hunk,
                    description=f"Class removed: {name}",
                )
            )
        return changes

    def _extract_constant_changes(self, hunk: DiffHunk) -> List[CodeSymbolChange]:
        changes: List[CodeSymbolChange] = []
        for name in set() - set():
            pass
        return changes

    def _extract_full_signature(self, lines: List[DiffLine], start_line: int) -> str:
        sig_lines = []
        for ln in lines:
            if ln.line_number == start_line and ln.content.strip().startswith("def "):
                sig_lines.append(ln.content.strip())
        return " ".join(sig_lines)

    @staticmethod
    def _is_value(s: str) -> bool:
        s = s.strip()
        return (
            re.match(r"^-?\d+(\.\d+)?$", s)
            is not None
            or s in ("True", "False", "None")
            or s.startswith('"')
            or s.startswith("'")
            or s.startswith("[")
        )

    @staticmethod
    def _similar_name(a: str, b: str) -> bool:
        a, b = a.lstrip("-"), b.lstrip("-")
        return (
            b in a
            or a in b
            or a.replace("_", "").replace("-", "") == b.replace("_", "").replace("-", "")
            or a.rstrip("s") == b.rstrip("s")
        )

    @staticmethod
    def _extract_signature(file_path: str, line_no: int) -> Optional[str]:
        try:
            lines = Path(file_path).read_text(encoding="utf-8").splitlines()
            if 1 <= line_no <= len(lines):
                return lines[line_no - 1].strip()
        except OSError:
            pass
        return None


class TypeScriptExtractor(LanguageExtractor):
    """AST-ish extractor for TypeScript / JavaScript via regex patterns."""

    extensions = {".ts", ".tsx", ".js", ".jsx"}

    def extract_changes(self, diff_file: DiffFile) -> List[CodeSymbolChange]:
        changes: List[CodeSymbolChange] = []
        for hunk in diff_file.hunks:
            changes.extend(self.extract_from_hunk(hunk))
        return changes

    def extract_from_hunk(self, hunk: DiffHunk) -> List[CodeSymbolChange]:
        changes: List[CodeSymbolChange] = []
        changes.extend(self._extract_function_changes(hunk))
        changes.extend(self._extract_default_changes(hunk))
        changes.extend(self._extract_renamed_changes(hunk))
        return changes

    def _extract_function_changes(self, hunk: DiffHunk) -> List[CodeSymbolChange]:
        changes: List[CodeSymbolChange] = []
        for ln in hunk.removed_lines:
            for pat in [
                re.compile(r"(?:def|function)\s+(\w+)\s*\(([^)]*)\)"),
                re.compile(r"export (?:default )?(?:async )?function\s+(\w+)\s*\(([^)]*)\)"),
                re.compile(r"(\w+)\s*[:=]\s*(?:async )?\(?[^)]*\)\s*=>"),
            ]:
                m = pat.match(ln.content.strip())
                if m:
                    pass
        return changes

    def _extract_default_changes(self, hunk: DiffHunk) -> List[CodeSymbolChange]:
        changes: List[CodeSymbolChange] = []
        old_params: Dict[str, str] = {}
        new_params: Dict[str, str] = {}
        for ln in hunk.removed_lines:
            m = re.search(r"(?:def|function|const|\(|=>)\s*(?:\w+)?\s*\(([^)]*)\)", ln.content)
            if m:
                for pm in re.finditer(r"(\w+)[=:]?\s*([^,){]+)", m.group(1)):
                    old_params[pm.group(1)] = pm.group(2).strip()
            # Property-level default: name: type = value or name = value
            pm = re.match(r"\s*([A-Za-z_][\w]*)(?:\s*:\s*[^=]+)?\s*=\s*([^;,]+)", ln.content)
            if pm:
                old_params[pm.group(1)] = pm.group(2).strip()
        for ln in hunk.added_lines:
            m = re.search(r"(?:def|function|const|\(|=>)\s*(?:\w+)?\s*\(([^)]*)\)", ln.content)
            if m:
                for pm in re.finditer(r"(\w+)[=:]?\s*([^,){]+)", m.group(1)):
                    new_params[pm.group(1)] = pm.group(2).strip()
            pm = re.match(r"\s*([A-Za-z_][\w]*)(?:\s*:\s*[^=]+)?\s*=\s*([^;,]+)", ln.content)
            if pm:
                new_params[pm.group(1)] = pm.group(2).strip()
        for key in set(old_params) & set(new_params):
            if old_params[key] != new_params[key]:
                changes.append(
                    CodeSymbolChange(
                        file_path=hunk.target_file,
                        line_start=hunk.target_start or 0,
                        line_end=hunk.target_start or 0,
                        symbol_name=key,
                        symbol_type=SymbolType.DEFAULT_VALUE,
                        change_type=ChangeType.DEFAULT_CHANGED,
                        old_value=old_params[key],
                        new_value=new_params[key],
                        diff_hunk=hunk.diff_hunk,
                        description=f"Default changed: '{key}' = {old_params[key]} -> {new_params[key]}",
                    )
                )
        return changes

    def _extract_renamed_changes(self, hunk: DiffHunk) -> List[CodeSymbolChange]:
        changes: List[CodeSymbolChange] = []
        removed_text = "\n".join(ln.content for ln in hunk.removed_lines)
        added_text = "\n".join(ln.content for ln in hunk.added_lines)
        # Simple heuristic: symbols referenced before but with different name after
        removed_words = set(re.findall(r"\b([A-Za-z_][\w]*)\b", removed_text))
        added_words = set(re.findall(r"\b([A-Za-z_][\w]*)\b", added_text))
        for word in removed_words - added_words:
            for aw in added_words - removed_words:
                if word.rstrip("s") == aw.rstrip("s") or self._similar_name(word, aw):
                    changes.append(
                        CodeSymbolChange(
                            file_path=hunk.target_file,
                            line_start=hunk.target_start or 0,
                            line_end=hunk.target_start or 0,
                            symbol_name=aw,
                            symbol_type=SymbolType.FUNCTION,
                            change_type=ChangeType.RENAMED,
                            old_value=word,
                            new_value=aw,
                            diff_hunk=hunk.diff_hunk,
                            description=f"Potential rename: '{word}' -> '{aw}'",
                        )
                    )
        return changes

    @staticmethod
    def _similar_name(a: str, b: str) -> bool:
        return a.lower() == b.lower()


class JavaScriptExtractor(LanguageExtractor):
    extensions = {".js", ".jsx"}

    def extract_changes(self, diff_file: DiffFile) -> List[CodeSymbolChange]:
        return self.extract_from_hunks(diff_file.hunks)

    def extract_from_hunk(self, hunk: DiffHunk) -> List[CodeSymbolChange]:
        extractor = TypeScriptExtractor()
        hunk.target_file = hunk.target_file.replace(".js", ".ts").replace(".jsx", ".ts")
        return extractor.extract_from_hunk(hunk)

    def extract_from_hunks(self, hunks: List[DiffHunk]) -> List[CodeSymbolChange]:
        extractor = TypeScriptExtractor()
        all_changes: List[CodeSymbolChange] = []
        for hunk in hunks:
            hunk.target_file = hunk.target_file.replace(".js", ".ts").replace(".jsx", ".ts")
            all_changes.extend(extractor.extract_from_hunk(hunk))
        return all_changes


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

EXTRACTORS: Dict[str, LanguageExtractor] = {
    "python": PythonExtractor(),
    "typescript": TypeScriptExtractor(),
    "javascript": JavaScriptExtractor(),
}


class ASTSymbolExtractor:
    """Main extractor coordinating language-specific analysis."""

    def __init__(self):
        self.extractors: Dict[str, LanguageExtractor] = dict(EXTRACTORS)

    def extract_from_diff_file(self, diff_file: DiffFile) -> List[CodeSymbolChange]:
        """Extract semantic code changes from a single file's diff."""
        target_ext = Path(diff_file.target_file).suffix.lower()
        source_ext = Path(diff_file.source_file).suffix.lower() if diff_file.source_file else ""

        keys = []
        if source_ext and source_ext != target_ext:
            keys.append(self._lookup_language(source_ext))
        keys.append(self._lookup_language(target_ext))

        seen = set()
        all_changes: List[CodeSymbolChange] = []
        for key in keys:
            if key in seen or key == "unknown":
                continue
            seen.add(key)
            extractor = self.extractors.get(key)
            if extractor is None:
                continue
            try:
                all_changes.extend(extractor.extract_changes(diff_file))
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Extractor %s failed for %s: %s", key, diff_file.target_file, exc
                )
        return all_changes

    def extract_from_diff(self, diff_files: List[DiffFile]) -> List[CodeSymbolChange]:
        """Extract semantic changes from all diff files."""
        all_changes: List[CodeSymbolChange] = []
        for df in diff_files:
            all_changes.extend(self.extract_from_diff_file(df))
        return all_changes

    @staticmethod
    def _lookup_language(ext: str) -> str:
        mapping = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".cpp": "cpp",
            ".c": "c",
            ".rb": "ruby",
            ".php": "php",
        }
        return mapping.get(ext, "unknown")


if __name__ == "__main__":
    # Demo usage
    import sys

    sample = """diff --git a/cli.py b/cli.py
index abc..def 100644
--- a/cli.py
+++ b/cli.py
@@ -1,5 +1,5 @@
-def main(timeout=30):
+def main(timeout=60):
     pass

-def fetch_data():
+def fetch_data_cached():
     pass
"""
    from core.diff_parser import parse_diff
    from core.ast_extractor import ASTSymbolExtractor

    parser = parse_diff(sample)
    extractor = ASTSymbolExtractor()
    changes = extractor.extract_from_diff(parser.files.values())
    for c in changes:
        print(f"[{c.change_type.value}] {c.symbol_name}: {c.description}")
        print("  ", c.old_value, "->", c.new_value)
