"""Unified Diff Parser for the documentation drift detection engine.

Supports three input types:
  1. A raw unified diff string (sys.stdin or .patch file).
  2. Local git repository path with base_ref and head_ref.
  3. GitHub PR diff URL or raw patch content.

File classification separates code files from documentation files (.md, .mdx,
.rst, .txt, .adoc), and ignores binary / lock files / build artifacts.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Prevent GitPython from raising ImportError at import time in serverless containers
# where the git binary is not installed in $PATH.
os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")
try:
    import git
except Exception:  # pragma: no cover
    git = None  # type: ignore

from unidiff import PatchSet

from .models import DocSentence
from .serverless import is_serverless

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File Classification
# ---------------------------------------------------------------------------

CODE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
}

DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".txt", ".adoc", ".markdown", ".wiki"}

IGNORE_PATTERNS = [
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "Gemfile.lock",
    "Cargo.lock",
    "composer.lock",
    "pnpm-lock.yaml",
    "go.sum",
    "requirements.txt",
    "Pipfile.lock",
    "Dockerfile",
    ".dockerignore",
]

IGNORE_DIRS = {
    "node_modules",
    "vendor",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    "target",
    ".idea",
    ".vscode",
    ".tox",
    ".eggs",
    "*.egg-info",
}


def classify_file(path: str) -> str:
    """Classify a file as 'code', 'doc', or 'other'.

    Returns one of: 'code', 'doc', 'other'.
    """
    p = Path(path)
    name = p.name if p.is_absolute() else Path(path).name

    # Ignore files in vendor / build / node_modules directories
    parts = Path(path).parts
    if any(part in IGNORE_DIRS for part in parts):
        return "other"

    if name in IGNORE_PATTERNS:
        return "other"

    suffix = p.suffix.lower()
    if suffix in CODE_EXTENSIONS:
        return "code"
    if suffix in DOC_EXTENSIONS:
        return "doc"

    name_lower = name.lower()
    if any(name_lower.endswith(ext) for ext in CODE_EXTENSIONS):
        return "code"
    if name_lower in ("readme", "contributing", "license", "changelog", "history"):
        return "doc"
    return "other"


def is_code_file(path: str) -> bool:
    return classify_file(path) == "code"


def is_doc_file(path: str) -> bool:
    return classify_file(path) == "doc"


# ---------------------------------------------------------------------------
# Diff Hunk Representation
# ---------------------------------------------------------------------------

@dataclass
class DiffLine:
    """A single line in a diff hunk."""

    line_number: int  # line number in the target (new) file
    source_line_number: Optional[int] = None  # line number in the source (old) file
    kind: str = " "  # ' ', '+', '-', '\\', '@'
    content: str = ""  # content without the leading +/- marker
    raw_line: str = ""  # raw line including marker

    @property
    def is_addition(self) -> bool:
        return self.kind == "+"

    @property
    def is_deletion(self) -> bool:
        return self.kind == "-"

    @property
    def is_context(self) -> bool:
        return self.kind == " "


@dataclass
class DiffHunk:
    """A single hunk from a unified diff."""

    source_file: str
    target_file: str
    source_start: Optional[int] = None
    source_count: int = 0
    target_start: Optional[int] = None
    target_count: int = 0
    target_pos: int = 0
    source_pos: int = 0

    added_lines: List[DiffLine] = field(default_factory=list)
    removed_lines: List[DiffLine] = field(default_factory=list)
    context_lines: List[DiffLine] = field(default_factory=list)
    lines: List[DiffLine] = field(default_factory=list)

    @property
    def diff_hunk(self) -> str:
        """Reconstruct the unified diff hunk text."""
        lines = []
        header = (
            f"@@ -{self.source_start},{self.source_count} "
            f"+{self.target_start},{self.target_count} @@"
        )
        lines.append(header)
        for ln in self.lines:
            lines.append(f"{ln.kind}{ln.content}")
        return "\n".join(lines)

    @property
    def total_added_lines(self) -> int:
        return len(self.added_lines)

    @property
    def total_removed_lines(self) -> int:
        return len(self.removed_lines)


@dataclass
class DiffFile:
    """A single file's diff (may contain multiple hunks)."""

    source_file: str
    target_file: str
    change_type: str = "modified"
    hunks: List[DiffHunk] = field(default_factory=list)
    binary: bool = False

    @property
    def is_added(self) -> bool:
        return self.change_type == "added"

    @property
    def is_deleted(self) -> bool:
        return self.change_type == "deleted"

    @property
    def is_modified(self) -> bool:
        return self.change_type == "modified"

    @property
    def is_renamed(self) -> bool:
        return self.change_type == "renamed"

    @property
    def all_lines(self) -> List[DiffLine]:
        lines: List[DiffLine] = []
        for hunk in self.hunks:
            lines.extend(hunk.lines)
        return lines

    @property
    def total_added(self) -> int:
        return sum(h.total_added_lines for h in self.hunks)

    @property
    def total_removed(self) -> int:
        return sum(h.total_removed_lines for h in self.hunks)

    @property
    def first_hunk(self) -> Optional[DiffHunk]:
        return self.hunks[0] if self.hunks else None


@dataclass
class DiffSummary:
    """Summary of all document lines already modified in the PR."""

    doc_files_modified: Dict[str, Set[int]] = field(default_factory=dict)

    def has_doc_updates(self) -> bool:
        return bool(self.doc_files_modified)

    def doc_lines_changed(self, doc_path: str) -> Set[int]:
        return self.doc_files_modified.get(doc_path, set())


# ---------------------------------------------------------------------------
# Unified Diff Parser
# ---------------------------------------------------------------------------

HUNK_HEADER_RE = re.compile(
    r"^@@\s*-([0-9]+)(?:,([0-9]+))?\s+\+([0-9]+)(?:,([0-9]+))?\s+@@",
    re.IGNORECASE,
)


class DiffParser:
    """Parse unified diffs from multiple sources."""

    def __init__(self, docs_dir: Optional[str] = None):
        self.docs_dir = docs_dir
        self.files: Dict[str, DiffFile] = {}
        self.doc_updates = DiffSummary()

    def parse_diff(self, diff_text: str) -> None:
        """Parse a unified diff string into DiffFile objects."""
        lines = diff_text.splitlines()
        current_file: Optional[DiffFile] = None
        current_hunk: Optional[DiffHunk] = None

        for raw_line in lines:
            if raw_line.startswith("diff --git"):
                match = re.search(r"diff --git a/(.*) b/(.*)", raw_line)
                if match:
                    a_path = match.group(1).replace('\\"', '"').replace("\\ ", " ")
                    b_path = match.group(2).replace('\\"', '"').replace("\\ ", " ")
                    rename_match = re.search(r"rename (from|to) (.*)", raw_line, re.IGNORECASE)
                    if rename_match:
                        current_file = DiffFile(source_file=a_path, target_file=b_path, change_type="renamed")
                    else:
                        current_file = DiffFile(source_file=a_path, target_file=b_path)
                    self.files[a_path] = current_file
                    if a_path != b_path:
                        self.files[b_path] = current_file
                    current_hunk = None
                continue

            if raw_line.startswith("index "):
                continue
            if raw_line.startswith("new file mode") or raw_line.startswith("deleted file mode"):
                if current_file:
                    current_file.change_type = "added" if "new" in raw_line else "deleted"
                continue
            if raw_line.startswith("Binary files"):
                if current_file:
                    current_file.binary = True
                    current_file.change_type = "modified"
                continue
            if raw_line.startswith("--- "):
                continue
            if raw_line.startswith("+++ "):
                m = re.search(r"^\+\+\+ b/(.*)", raw_line)
                if m:
                    b_path = m.group(1).replace('\\"', '"').replace("\\ ", " ")
                    if current_file:
                        current_file.target_file = b_path
                continue
            if raw_line.startswith("@@") or raw_line.startswith("@@ "):
                match = HUNK_HEADER_RE.match(raw_line)
                if match:
                    s_start = int(match.group(1))
                    s_count = int(match.group(2)) if match.group(2) else 1
                    t_start = int(match.group(3))
                    t_count = int(match.group(4)) if match.group(4) else 1
                    current_hunk = DiffHunk(
                        source_file=current_file.source_file if current_file else "",
                        target_file=current_file.target_file if current_file else "",
                        source_start=s_start,
                        source_count=s_count,
                        target_start=t_start,
                        target_count=t_count,
                        target_pos=t_start,
                        source_pos=s_start,
                    )
                    if current_file:
                        current_file.hunks.append(current_hunk)
                continue
            if raw_line.startswith("\\ No newline"):
                continue

            if raw_line and current_hunk is not None:
                kind = raw_line[0]
                content = raw_line[1:]
                ln, src_ln = self._map_line_numbers(kind, current_hunk.target_pos, current_hunk.source_pos)
                diff_line = DiffLine(
                    line_number=ln,
                    source_line_number=src_ln,
                    kind=kind,
                    content=content,
                    raw_line=raw_line,
                )
                current_hunk.lines.append(diff_line)
                if kind == "+":
                    current_hunk.added_lines.append(diff_line)
                    current_hunk.target_pos += 1
                elif kind == "-":
                    current_hunk.removed_lines.append(diff_line)
                    current_hunk.source_pos += 1
                else:
                    current_hunk.context_lines.append(diff_line)
                    current_hunk.target_pos += 1
                    current_hunk.source_pos += 1

        self._finalize_doc_updates()

    @staticmethod
    def _map_line_numbers(kind: str, target_pos: int, source_pos: int) -> Tuple[int, Optional[int]]:
        """Map diff line kind to target/source file line numbers."""
        if kind == " ":
            return target_pos, source_pos
        if kind == "+":
            return target_pos, None
        if kind == "-":
            return None, source_pos
        return 0, 0

    def _finalize_doc_updates(self) -> None:
        """Record which doc lines were already modified in the PR."""
        for diff_file in self.files.values():
            if not is_doc_file(diff_file.target_file):
                continue
            changed_lines: Set[int] = set()
            for hunk in diff_file.hunks:
                for ln in hunk.added_lines + hunk.context_lines:
                    if ln.line_number is not None:
                        changed_lines.add(ln.line_number)
            if changed_lines:
                self.doc_updates.doc_files_modified[diff_file.target_file] = changed_lines

    def parse_diff_string(self, diff_text: str) -> "DiffParser":
        """Parse a diff string and return self for chaining."""
        self.parse_diff(diff_text)
        return self

    def _resolve_ref(self, repo: "git.Repo", ref: str, is_head: bool = False) -> str:
        """Resolve a git ref name to a resolvable git reference or object.

        Handles:
          - Direct local branches/tags/commits (e.g. 'main', 'HEAD')
          - Remote tracking branches (e.g. 'origin/main', 'refs/remotes/origin/main')
          - Unfetched refs that need to be fetched from 'origin'
          - CI detached HEAD (when head_ref is a PR branch name like 'phase-4-completion',
            fallback to 'HEAD')
          - Fallbacks for base_ref ('origin/main', 'origin/master', 'HEAD~1')
        """
        # 1. Direct match locally
        try:
            repo.rev_parse(ref)
            return ref
        except Exception:
            pass

        # 2. Check origin/ or refs/remotes/origin/ prefix
        for prefix in [f"origin/{ref}", f"refs/remotes/origin/{ref}"]:
            try:
                repo.rev_parse(prefix)
                return prefix
            except Exception:
                pass

        # 3. Try fetching from origin
        try:
            repo.git.fetch("origin", f"+refs/heads/{ref}:refs/remotes/origin/{ref}", "--depth=50")
        except Exception:
            try:
                repo.git.fetch("origin", f"{ref}:{ref}", "--depth=50")
            except Exception:
                try:
                    repo.git.fetch("origin", "+refs/heads/*:refs/remotes/origin/*")
                except Exception:
                    pass

        # 4. Check again after fetching
        for cand in [ref, f"origin/{ref}", f"refs/remotes/origin/{ref}"]:
            try:
                repo.rev_parse(cand)
                return cand
            except Exception:
                pass

        # 5. Head-specific fallback:
        # In GitHub Actions (actions/checkout), the PR head commit is already checked out as HEAD!
        # If head_ref was passed as a branch name (e.g. phase-4-completion) that doesn't exist locally,
        # HEAD is the exact checked-out commit we want to analyze.
        if is_head:
            try:
                repo.rev_parse("HEAD")
                logger.info("head_ref '%s' not found locally; falling back to current HEAD", ref)
                return "HEAD"
            except Exception:
                pass

        # 6. Base-specific fallback:
        if not is_head:
            for fallback in ["origin/main", "origin/master", "main", "master", "HEAD~1"]:
                try:
                    repo.rev_parse(fallback)
                    logger.info("base_ref '%s' not found locally; falling back to '%s'", ref, fallback)
                    return fallback
                except Exception:
                    pass

        raise RuntimeError(
            f"Cannot resolve git ref '{ref}' in repository {repo.working_dir}. "
            "Ensure the branch exists locally or on remote."
        )

    def from_local_repo(self, repo_path: str, base_ref: str = "main", head_ref: str = "HEAD") -> "DiffParser":
        """Ingest a diff from a local git repository.

        In serverless environments (Vercel, AWS Lambda, etc.) the ``git``
        binary and ``GitPython`` may not be available. In that case this
        method raises a clear error suggesting the use of
        :meth:`from_patch_file` or :meth:`from_github_pr` instead, which
        work without a local git installation.
        """
        if is_serverless():
            raise RuntimeError(
                "from_local_repo() is not supported in serverless environments "
                "(no git binary / GitPython). Use from_patch_file() or "
                "from_github_pr() instead, or pass a pre-parsed diff string "
                "via parse_diff()."
            )
        repo = git.Repo(repo_path)

        resolved_base = self._resolve_ref(repo, base_ref, is_head=False)
        resolved_head = self._resolve_ref(repo, head_ref, is_head=True)

        diff_text = ""
        # Try three-dot diff first (merge-base to head), fall back to two-dot, then merge-base
        try:
            diff_text = repo.git.diff(f"{resolved_base}...{resolved_head}")
        except Exception:
            try:
                diff_text = repo.git.diff(f"{resolved_base}..{resolved_head}")
            except Exception:
                try:
                    merge_base = repo.git.merge_base(resolved_base, resolved_head)
                    mb_str = merge_base[0].hexsha if hasattr(merge_base[0], "hexsha") else str(merge_base[0]).strip()
                    diff_text = repo.git.diff(f"{mb_str}..{resolved_head}")
                except Exception:
                    try:
                        diff_text = repo.git.diff(resolved_base, resolved_head)
                    except Exception as err:
                        raise RuntimeError(
                            f"Failed to generate git diff between {resolved_base} and {resolved_head}: {err}"
                        ) from err

        self.parse_diff(diff_text)
        return self

    def from_patch_file(self, patch_path: str) -> "DiffParser":
        """Ingest a diff from a unified patch file."""
        patch_text = Path(patch_path).read_text(encoding="utf-8")
        self.parse_diff(patch_text)
        return self

    def from_github_pr(self, owner: str, repo: str, pr_number: int, token: Optional[str] = None) -> "DiffParser":
        """Ingest a diff from a GitHub PR (uses PyGithub if available)."""
        try:
            import github
            gh = github.Github(token) if token else github.Github()
            pr = gh.get_repo(f"{owner}/{repo}").get_pull(pr_number)
            diff_text = "".join(patch.decode("utf-8") for patch in pr.get_patches())
        except ImportError:
            import httpx
            url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
            headers = {"Accept": "application/vnd.github+json"}
            if token:
                headers["Authorization"] = f"token {token}"
            resp = httpx.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            pr_data = resp.json()
            diff_text = pr_data.get("diff_url", "")
            if diff_text:
                diff_resp = httpx.get(diff_text, headers=headers, timeout=30)
                diff_text = diff_resp.text
        self.parse_diff(diff_text)
        return self

    @property
    def code_files(self) -> List[DiffFile]:
        return [f for f in self.files.values() if is_code_file(f.target_file)]

    @property
    def doc_files(self) -> List[DiffFile]:
        return [f for f in self.files.values() if is_doc_file(f.target_file)]

    @property
    def has_code_changes(self) -> bool:
        return bool(self.code_files)

    @property
    def has_doc_changes(self) -> bool:
        return self.doc_updates.has_doc_updates()

    def get_file(self, path: str) -> Optional[DiffFile]:
        return self.files.get(path)

    def find_doc_sentence_in_pr_changes(self, doc_path: str, line_number: int) -> bool:
        """PR Doc-Update Guard: is this doc sentence already updated in the PR?"""
        return line_number in self.doc_updates.doc_lines_changed(doc_path)


# ---------------------------------------------------------------------------
# Public Convenience Functions
# ---------------------------------------------------------------------------

def parse_diff(diff_text: str) -> DiffParser:
    """Parse a raw unified diff string."""
    return DiffParser().parse_diff_string(diff_text)


def parse_diff_from_repo(repo_path: str, base_ref: str = "main", head_ref: str = "HEAD") -> DiffParser:
    """Parse a diff between two git revisions in a local repository."""
    return DiffParser().from_local_repo(repo_path, base_ref, head_ref)


def parse_diff_from_patch(patch_path: str) -> DiffParser:
    """Parse a unified diff from a patch file."""
    return DiffParser().from_patch_file(patch_path)


def parse_diff_from_github_pr(owner: str, repo: str, pr_number: int, token: Optional[str] = None) -> DiffParser:
    """Parse a diff from a GitHub PR."""
    return DiffParser().from_github_pr(owner, repo, pr_number, token)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        parser = parse_diff_from_patch(sys.argv[1])
    else:
        parser = parse_diff(sys.stdin.read())

    for path, diff_file in sorted(parser.files.items()):
        print(f"\n{'=' * 70}")
        print(f"FILE: {path} ({diff_file.change_type})")
        for hunk in diff_file.hunks:
            print(hunk.diff_hunk)
