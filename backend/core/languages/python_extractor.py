"""Python-specific AST extractor using the native `ast` module.

Extends regex-based hunk extraction with full-file AST comparison when both
before/after file contents are available.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.diff_parser import DiffHunk
from core.models import (
    ChangeType,
    CodeSymbolChange,
    SymbolType,
)

logger = logging.getLogger(__name__)


class PythonASTExtractor:
    """Full-file AST comparison for Python semantics."""

    def extract_from_files(
        self, source_path: str, source_text: Optional[str], target_text: str
    ) -> List[CodeSymbolChange]:
        """Compare before/after Python file contents via AST."""
        changes: List[CodeSymbolChange] = []
        try:
            before_tree = ast.parse(source_text) if source_text else None
        except SyntaxError:
            before_tree = None
        try:
            after_tree = ast.parse(target_text)
        except SyntaxError:
            logger.warning("Cannot parse target Python file: %s", source_path)
            return changes

        before_tree = before_tree or ast.parse("")
        after_mods = self._compare_trees(before_tree, after_tree)
        changes.extend(after_mods)

        # Detect renamed CLI args via AST walk
        changes.extend(self._find_cli_arg_renames(after_tree, target_text))

        return changes

    def _compare_trees(
        self, before: ast.AST, after: ast.AST
    ) -> List[CodeSymbolChange]:
        """Compare two ASTs and emit CodeSymbolChange records."""
        changes: List[CodeSymbolChange] = []

        before_funcs = {
            n.name: n
            for n in ast.walk(before)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        after_funcs = {
            n.name: n
            for n in ast.walk(after)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        for name, func in after_funcs.items():
            if name in before_funcs:
                old = before_funcs[name]
                delta = self._compare_functions(old, func)
                if delta:
                    changes.append(delta)
            else:
                changes.append(
                    CodeSymbolChange(
                        file_path="unknown",
                        line_start=func.lineno or 0,
                        line_end=func.end_lineno or func.lineno or 0,
                        symbol_name=name,
                        symbol_type=SymbolType.FUNCTION,
                        change_type=ChangeType.RENAMED,
                        old_value=f"<removed: {name}>",
                        new_value=name,
                        diff_hunk="",
                        context_signature=self._sig_to_str(func),
                        description=f"Function '{name}' added",
                    )
                )

        for name in set(before_funcs) - set(after_funcs):
            changes.append(
                CodeSymbolChange(
                    file_path="unknown",
                    line_start=before_funcs[name].lineno or 0,
                    line_end=before_funcs[name].end_lineno or before_funcs[name].lineno or 0,
                    symbol_name=name,
                    symbol_type=SymbolType.FUNCTION,
                    change_type=ChangeType.REMOVED,
                    old_value=name,
                    new_value=f"<removed: {name}>",
                    diff_hunk="",
                    context_signature=self._sig_to_str(before_funcs[name]),
                    description=f"Function '{name}' removed",
                )
            )

        # Module-level constants
        before_consts = {
            n.name: n.value
            for n in ast.walk(before)
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].name.isupper()
        }
        after_consts = {
            n.name: n.value
            for n in ast.walk(after)
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].name.isupper()
        }
        for name in set(before_consts) & set(after_consts):
            ov, nv = before_consts[name], after_consts[name]
            ov_s, nv_s = ast.literal_eval(ov) if isinstance(ov, ast.Constant) else None, ast.literal_eval(nv) if isinstance(nv, ast.Constant) else None
            if ov_s is not None and nv_s is not None and ov_s != nv_s:
                changes.append(
                    CodeSymbolChange(
                        file_path="unknown",
                        line_start=nv.lineno or 0,
                        line_end=nv.lineno or 0,
                        symbol_name=name,
                        symbol_type=SymbolType.CONSTANT,
                        change_type=ChangeType.DEFAULT_CHANGED,
                        old_value=str(ov_s),
                        new_value=str(nv_s),
                        diff_hunk="",
                        description=f"Constant '{name}' changed from {ov_s} to {nv_s}",
                    )
                )

        return changes

    def _compare_functions(
        self, old: ast.FunctionDef, new: ast.FunctionDef
    ) -> Optional[CodeSymbolChange]:
        old_params = self._params_to_dict(old.args)
        new_params = self._params_to_dict(new.args)

        if old_params == new_params:
            return None

        changes: List[CodeSymbolChange] = []
        for key in set(old_params) & set(new_params):
            ov, nv = old_params[key], new_params[key]
            if ov != nv:
                ov_s = ast.literal_eval(ov) if isinstance(ov, ast.Constant) else None
                nv_s = ast.literal_eval(nv) if isinstance(nv, ast.Constant) else None
                changes.append(
                    CodeSymbolChange(
                        file_path="unknown",
                        line_start=new.lineno or 0,
                        line_end=new.lineno or 0,
                        symbol_name=f"{new.name}.{key}",
                        symbol_type=SymbolType.DEFAULT_VALUE,
                        change_type=ChangeType.DEFAULT_CHANGED,
                        old_value=ov_s if ov_s is not None else ov.s if hasattr(ov, "s") else str(ov),
                        new_value=nv_s if nv_s is not None else nv.s if hasattr(nv, "s") else str(nv),
                        diff_hunk="",
                        context_signature=self._sig_to_str(new),
                        description=f"Default '{key}' changed in {new.name}",
                    )
                )

        for key in set(old_params) - set(new_params):
            changes.append(
                CodeSymbolChange(
                    file_path="unknown",
                    line_start=new.lineno or 0,
                    line_end=new.lineno or 0,
                    symbol_name=f"{new.name}.{key}",
                    symbol_type=SymbolType.FUNCTION,
                    change_type=ChangeType.PARAM_REMOVED,
                    old_value=key,
                    new_value=None,
                    diff_hunk="",
                    context_signature=self._sig_to_str(new),
                    description=f"Parameter '{key}' removed from {new.name}",
                )
            )

        for key in set(new_params) - set(old_params):
            nv = new_params[key]
            if not (hasattr(nv, "default") and nv.default is None) and not isinstance(nv, ast.NameConstant) and nv.default is not None:
                pass
            kind = ChangeType.PARAM_MODIFIED if isinstance(nv, ast.Constant) else ChangeType.PARAM_ADDED
            changes.append(
                CodeSymbolChange(
                    file_path="unknown",
                    line_start=new.lineno or 0,
                    line_end=new.lineno or 0,
                    symbol_name=f"{new.name}.{key}",
                    symbol_type=SymbolType.FUNCTION,
                    change_type=kind,
                    old_value=None,
                    new_value=ast.literal_eval(nv) if isinstance(nv, ast.Constant) else None,
                    diff_hunk="",
                    context_signature=self._sig_to_str(new),
                    description=f"Parameter '{key}' added/modified in {new.name}",
                )
            )

        return changes[0] if len(changes) == 1 else None

    def _params_to_dict(self, args: ast.arguments) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        defaults_offset = len(args.posonlyargs) + len(args.args) - len(args.defaults)
        for i, arg in enumerate(args.args):
            idx = i - defaults_offset
            val = args.defaults[idx] if 0 <= idx < len(args.defaults) else None
            result[arg.arg] = val
        for arg, val in zip(args.kwonlyargs, args.kw_defaults):
            result[arg.arg] = val
        if args.vararg:
            result["*" + args.vararg.arg] = None
        if args.kwarg:
            result["**" + args.kwarg.arg] = None
        return result

    @staticmethod
    def _sig_to_str(node: ast.FunctionDef) -> str:
        parts = []
        for arg in node.args.args:
            s = arg.arg
            if arg.annotation:
                s += f": {ast.get_source_segment}"
            if arg.default is not None:
                s += f"={ast.get_source_segment}"
            parts.append(s)
        return f"def {node.name}({', '.join(parts)})"

    def _find_cli_arg_renames(self, tree: ast.AST, source: str) -> List[CodeSymbolChange]:
        changes: List[CodeSymbolChange] = []
        flags: List[ast.arg] = []

        class FlagVisitor(ast.NodeVisitor):
            def visit_Call(self, node: ast.Call) -> None:
                func_name = ""
                if isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    func_name = node.func.id
                if "add_argument" in func_name or "argument" in func_name:
                    for kw in node.keywords:
                        if kw.arg == "dest" and isinstance(kw.value, ast.Constant):
                            flags.append(ast.arg(arg=kw.value.s, lineno=node.lineno))
                self.generic_visit(node)

        FlagVisitor().visit(tree)
        if len(flags) == 2:
            f1, f2 = flags
            changes.append(
                CodeSymbolChange(
                    file_path="unknown",
                    line_start=max(f1.lineno, f2.lineno),
                    line_end=max(f1.lineno, f2.lineno),
                    symbol_name=f2.arg,
                    symbol_type=SymbolType.CLI_ARGUMENT,
                    change_type=ChangeType.RENAMED,
                    old_value=f1.arg,
                    new_value=f2.arg,
                    diff_hunk="",
                    description=f"CLI argument renamed: '{f1.arg}' -> '{f2.arg}'",
                )
            )
        return changes


if __name__ == "__main__":
    extractor = PythonASTExtractor()
    before = 'def main(timeout=30):\n    pass\n'
    after = 'def main(timeout=60):\n    pass\n'
    changes = extractor.extract_from_files("sample.py", before, after)
    for c in changes:
        print(c.description, "->", c.old_value, c.new_value)
