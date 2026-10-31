"""GitHub PR commenter and check-run publisher for docs-drift findings.

Publishes drift detection findings directly into GitHub Pull Requests:
  - Master PR summary comment (idempotent via hidden signature + PATCH)
  - Inline review comments with suggestion blocks on doc lines touched in PR
  - GitHub Check Run with file annotations (failure if High/Critical drift)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

SUMMARY_SIGNATURE = "<!-- docs-drift-bot-summary -->"
CHECK_RUN_NAME = "Documentation Drift Detection"


class PRCommenter:
    """GitHub REST API client for posting PR comments and check runs."""

    def __init__(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        token: str,
        base_url: str = "https://api.github.com",
    ):
        self.owner = owner
        self.repo = repo
        self.pr_number = pr_number
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"token {self.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "docs-drift-action",
            },
            timeout=30,
        )

    # ------------------------------------------------------------------ API helpers

    def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        """Send a request to the GitHub API and log rate-limit info."""
        resp = self.client.request(method, path, json=json_body)
        remaining = resp.headers.get("x-ratelimit-remaining")
        if remaining:
            logger.debug("GitHub API rate-limit remaining: %s", remaining)
            try:
                if int(remaining) < 10:
                    logger.warning(
                        "GitHub API rate-limit low (%s remaining)", remaining
                    )
            except ValueError:
                pass
        return resp

    def _handle_response(self, resp: httpx.Response, context: str) -> None:
        """Raise on non-2xx responses with context."""
        if resp.status_code >= 400:
            logger.error(
                "%s failed: HTTP %s - %s",
                context,
                resp.status_code,
                resp.text[:500],
            )
            resp.raise_for_status()

    # ----------------------------------------------------------- summary comment

    def find_existing_summary_comment(self) -> Optional[Dict[str, Any]]:
        """Search PR conversation for an existing summary comment with our signature."""
        resp = self._request(
            "GET",
            f"/repos/{self.owner}/{self.repo}/issues/{self.pr_number}/comments",
        )
        self._handle_response(resp, "list issue comments")
        comments: List[Dict[str, Any]] = resp.json()
        for comment in comments:
            body = comment.get("body", "") or ""
            if SUMMARY_SIGNATURE in body:
                return comment
        return None

    def post_summary_comment(self, body: str) -> Dict[str, Any]:
        """Create a new master summary comment on the PR conversation."""
        resp = self._request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/issues/{self.pr_number}/comments",
            json_body={"body": body},
        )
        self._handle_response(resp, "create issue comment")
        return resp.json()

    def update_summary_comment(
        self, comment_id: int, body: str
    ) -> Dict[str, Any]:
        """Update an existing summary comment in-place (idempotent)."""
        resp = self._request(
            "PATCH",
            f"/repos/{self.owner}/{self.repo}/issues/comments/{comment_id}",
            json_body={"body": body},
        )
        self._handle_response(resp, "update issue comment")
        return resp.json()

    # ----------------------------------------------------------- inline review

    def post_inline_review(
        self,
        body: str,
        comments: List[Dict[str, Any]],
        event: str = "COMMENT",
    ) -> Dict[str, Any]:
        """Post a pull request review with inline comments."""
        resp = self._request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}/reviews",
            json_body={
                "body": body,
                "event": event,
                "comments": comments,
            },
        )
        self._handle_response(resp, "create pull request review")
        return resp.json()

    # ----------------------------------------------------------- check runs

    def create_check_run(
        self,
        head_sha: str,
        conclusion: str,
        annotations: List[Dict[str, Any]],
        title: str = CHECK_RUN_NAME,
    ) -> Dict[str, Any]:
        """Create a GitHub Check Run with file annotations."""
        resp = self._request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/check-runs",
            json_body={
                "name": title,
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": conclusion,
                "output": {
                    "title": title,
                    "summary": "Documentation drift detection results",
                    "annotations": annotations,
                },
            },
        )
        self._handle_response(resp, "create check run")
        return resp.json()


# ---------------------------------------------------------------------------
# High-level publishing helpers
# ---------------------------------------------------------------------------

def render_summary_body(
    findings: List[Dict[str, Any]],
    template_path: Optional[str] = None,
) -> str:
    """Render the PR summary comment body using a Jinja2 template."""
    try:
        from jinja2 import Environment, FileSystemLoader

        if template_path is None:
            template_path = os.path.join(
                os.path.dirname(__file__), "templates", "pr_summary.md.j2"
            )
        env = Environment(
            loader=FileSystemLoader(os.path.dirname(template_path)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        template = env.get_template(os.path.basename(template_path))
        return template.render(findings=findings)
    except ImportError:
        # Fallback: simple markdown without Jinja2
        lines: List[str] = [SUMMARY_SIGNATURE]
        lines.append(
            f"## ⚠️ Documentation Drift Detected ({len(findings)} issue{'s' if len(findings) != 1 else ''})"
        )
        lines.append(
            "Our automated analysis identified documentation that became outdated due to code changes in this Pull Request."
        )
        lines.append("")
        lines.append("| Severity | Doc File | Doc Line | Drift Type | Responsible Code |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for f in findings:
            lines.append(
                f"| {f.get('severity','').upper()} | `{f.get('doc_path','')}` | "
                f"L{f.get('doc_line_number','?')} | `{f.get('drift_type','')}` | "
                f"`{f.get('code_file_path','')}` L{f.get('code_line_start','?')} |"
            )
        lines.append("")
        lines.append("### 🔍 Detailed Findings & Suggested Fixes")
        for idx, f in enumerate(findings, start=1):
            lines.append(
                f"<details open>\n<summary><b>{idx}. {f.get('doc_path','')} "
                f"(Line {f.get('doc_line_number','?')}) – "
                f"{f.get('drift_type','').replace('_',' ').title()}</b></summary>"
            )
            lines.append("")
            lines.append("- **Affected Documentation Sentence:**")
            lines.append(f'  > "{f.get("affected_sentence","")}"')
            lines.append(
                f"- **Responsible Code Change:** `{f.get('code_file_path','')}` "
                f"(Lines {f.get('code_line_start','?')}-{f.get('code_line_end','?')})"
            )
            lines.append("  ```diff")
            lines.append(f"  {f.get('code_diff_snippet','')}")
            lines.append("  ```")
            lines.append(f"- **Explanation:** {f.get('explanation','')}")
            lines.append("- **Suggested Fix:**")
            lines.append("  ```suggestion")
            lines.append(f"  {f.get('suggested_fix','')}")
            lines.append("  ```")
            lines.append("</details>")
            lines.append("")
        lines.append("")
        lines.append(
            "*Generated by [docs-drift](https://github.com/HisenBugs/docs-drive) — "
            "Automated Documentation Drift Detection*"
        )
        return "\n".join(lines)


def build_inline_comments(
    findings: List[Dict[str, Any]],
    pr_touched_doc_lines: Dict[str, set],
) -> List[Dict[str, Any]]:
    """Build inline review comment payloads for doc lines touched in the PR."""
    comments: List[Dict[str, Any]] = []
    for idx, f in enumerate(findings):
        doc_path = f.get("doc_path", "")
        doc_line = f.get("doc_line_number", 0)
        # Only post inline comment if this doc line was touched in the PR
        if doc_path in pr_touched_doc_lines and doc_line in pr_touched_doc_lines[doc_path]:
            comments.append(
                {
                    "path": doc_path,
                    "line": doc_line,
                    "side": "RIGHT",
                    "body": (
                        f"**Documentation Drift** ({f.get('drift_type','')})\n\n"
                        f"{f.get('explanation','')}\n\n"
                        f"**Suggested fix:**\n```suggestion\n"
                        f"{f.get('suggested_fix','')}\n```"
                    ),
                }
            )
    return comments


def build_check_annotations(
    findings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build check-run file annotations from drift findings."""
    annotations: List[Dict[str, Any]] = []
    for f in findings:
        level = "warning"
        sev = (f.get("severity") or "").lower()
        if sev in ("critical", "high"):
            level = "failure"
        elif sev == "medium":
            level = "warning"
        else:
            level = "notice"
        annotations.append(
            {
                "path": f.get("doc_path", ""),
                "start_line": f.get("doc_line_number", 0),
                "end_line": f.get("doc_line_number", 0),
                "annotation_level": level,
                "title": f"Documentation Drift: {f.get('drift_type','').replace('_', ' ').title()}",
                "message": (
                    f"{f.get('explanation','')} "
                    f"(Code: {f.get('code_file_path','')} L{f.get('code_line_start','?')})"
                ),
                "raw_details": f.get("suggested_fix", ""),
            }
        )
    return annotations


def determine_check_conclusion(
    findings: List[Dict[str, Any]],
    fail_on_drift: bool = True,
) -> str:
    """Determine the check-run conclusion based on findings."""
    if not findings:
        return "success"
    if fail_on_drift:
        high_or_critical = any(
            (f.get("severity") or "").lower() in ("critical", "high")
            for f in findings
        )
        return "failure" if high_or_critical else "success"
    return "success"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="docs-drift PR commenter and check-run publisher"
    )
    parser.add_argument("--owner", required=True, help="GitHub repository owner")
    parser.add_argument("--repo", required=True, help="GitHub repository name")
    parser.add_argument("--pr-number", required=True, type=int, help="Pull request number")
    parser.add_argument("--token", required=True, help="GitHub token")
    parser.add_argument("--report", required=True, help="Path to drift report JSON")
    parser.add_argument(
        "--fail-on-drift",
        default="true",
        help="Whether to fail check run on drift (default: true)",
    )
    parser.add_argument(
        "--head-sha",
        default=None,
        help="Head commit SHA (default: fetched from PR)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Load the drift report
    try:
        with open(args.report, "r", encoding="utf-8") as fh:
            report = json.load(fh)
    except FileNotFoundError:
        logger.error("Report file not found: %s", args.report)
        sys.exit(1)

    findings = report.get("findings", [])
    drift_detected = len(findings) > 0
    logger.info("Loaded report with %d findings", len(findings))

    commenter = PRCommenter(
        owner=args.owner,
        repo=args.repo,
        pr_number=args.pr_number,
        token=args.token,
    )

    # Determine head SHA
    head_sha = args.head_sha
    if not head_sha:
        try:
            resp = commenter._request(
                "GET",
                f"/repos/{args.owner}/{args.repo}/pulls/{args.pr_number}",
            )
            commenter._handle_response(resp, "fetch PR metadata")
            head_sha = resp.json().get("head", {}).get("sha", "")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not fetch PR metadata: %s", exc)
            head_sha = ""

    # 1. Master summary comment (idempotent)
    summary_body = render_summary_body(findings)
    existing = commenter.find_existing_summary_comment()
    if existing:
        commenter.update_summary_comment(existing["id"], summary_body)
        logger.info("Updated existing summary comment #%d", existing["id"])
    else:
        commenter.post_summary_comment(summary_body)
        logger.info("Posted new summary comment")

    # 2. Inline review comments (only for doc lines touched in this PR)
    # Determine which doc lines were touched in the PR by examining the diff
    pr_touched_doc_lines: Dict[str, set] = {}
    try:
        # Fetch PR files to see which doc files were modified
        resp = commenter._request(
            "GET",
            f"/repos/{args.owner}/{args.repo}/pulls/{args.pr_number}/files",
        )
        commenter._handle_response(resp, "list PR files")
        pr_files = resp.json()
        for pf in pr_files:
            path = pf.get("filename", "")
            # Only consider documentation files
            if any(path.endswith(ext) for ext in (".md", ".mdx", ".rst", ".txt", ".adoc")):
                patch = pf.get("patch", "") or ""
                # Parse added line numbers from the patch
                touched_lines: set = set()
                import re

                for hunk_match in re.finditer(
                    r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", patch
                ):
                    start_line = int(hunk_match.group(1))
                    # Count context + added lines in this hunk
                    hunk_text = patch[hunk_match.end():]
                    current = start_line
                    for line in hunk_text.splitlines():
                        if line.startswith("+"):
                            touched_lines.add(current)
                            current += 1
                        elif line.startswith(" "):
                            current += 1
                        elif line.startswith("-"):
                            pass  # removed line, target line unchanged
                        elif line.startswith("\\"):
                            pass  # no-newline marker
                        else:
                            break
                pr_touched_doc_lines[path] = touched_lines
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not fetch PR files: %s", exc)

    inline_comments = build_inline_comments(findings, pr_touched_doc_lines)
    if inline_comments:
        review_body = f"docs-drift found {len(inline_comments)} inline issue(s) in documentation touched by this PR."
        commenter.post_inline_review(
            body=review_body,
            comments=inline_comments,
            event="COMMENT",
        )
        logger.info("Posted inline review with %d comment(s)", len(inline_comments))
    else:
        logger.info("No inline review comments (no doc lines touched in PR)")

    # 3. Check run with file annotations
    annotations = build_check_annotations(findings)
    conclusion = determine_check_conclusion(
        findings, fail_on_drift=(args.fail_on_drift.lower() == "true")
    )
    if head_sha:
        commenter.create_check_run(
            head_sha=head_sha,
            conclusion=conclusion,
            annotations=annotations,
        )
        logger.info(
            "Created check run (conclusion=%s, %d annotations)",
            conclusion,
            len(annotations),
        )
    else:
        logger.warning("Skipping check run (no head SHA available)")

    # Exit non-zero if drift detected and fail-on-drift
    if drift_detected and args.fail_on_drift.lower() == "true":
        sys.exit(1)


if __name__ == "__main__":
    main()
