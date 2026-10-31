"""Unit tests for the PR commenter module (Phase 2.2)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.github.pr_commenter import (
    PRCommenter,
    SUMMARY_SIGNATURE,
    build_check_annotations,
    build_inline_comments,
    determine_check_conclusion,
    render_summary_body,
)


def make_commenter() -> PRCommenter:
    return PRCommenter(owner="owner", repo="repo", pr_number=1, token="fake-token")


def test_summary_signature_present():
    """The summary template includes the hidden signature tag."""
    body = render_summary_body([])
    assert SUMMARY_SIGNATURE in body


def test_render_summary_body_with_findings():
    """Rendering summary with findings produces valid markdown with suggestion blocks."""
    findings = [
        {
            "doc_path": "docs/cli.md",
            "doc_line_number": 42,
            "drift_type": "renamed_flag",
            "severity": "high",
            "code_file_path": "src/cli.py",
            "code_line_start": 88,
            "code_line_end": 90,
            "affected_sentence": "Run the command with --timeout-secs.",
            "code_diff_snippet": "-parser.add_argument('--timeout-secs')\n+parser.add_argument('--timeout')",
            "explanation": "The flag was renamed.",
            "suggested_fix": "Run the command with --timeout.",
        }
    ]
    body = render_summary_body(findings)
    assert "docs/cli.md" in body
    assert "L42" in body
    assert "renamed_flag" in body
    assert "```suggestion" in body
    assert "Run the command with --timeout." in body
    assert SUMMARY_SIGNATURE in body


def test_render_summary_body_no_findings():
    """Rendering summary with no findings still includes signature."""
    body = render_summary_body([])
    assert SUMMARY_SIGNATURE in body
    assert "0 issue" in body


def test_find_existing_summary_comment_found():
    """Existing comment with signature is detected."""
    commenter = make_commenter()
    with patch.object(commenter.client, "request") as mock_request:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.json.return_value = [
            {"id": 10, "body": f"{SUMMARY_SIGNATURE}\nOld summary"},
            {"id": 11, "body": "Unrelated comment"},
        ]
        mock_request.return_value = mock_resp

        result = commenter.find_existing_summary_comment()
        assert result is not None
        assert result["id"] == 10


def test_find_existing_summary_comment_not_found():
    """No comment with signature returns None."""
    commenter = make_commenter()
    with patch.object(commenter.client, "request") as mock_request:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.json.return_value = [
            {"id": 11, "body": "Unrelated comment"},
        ]
        mock_request.return_value = mock_resp

        result = commenter.find_existing_summary_comment()
        assert result is None


def test_build_inline_comments_only_touched_lines():
    """Inline comments are only built for doc lines touched in the PR."""
    findings = [
        {
            "doc_path": "docs/cli.md",
            "doc_line_number": 42,
            "drift_type": "renamed_flag",
            "severity": "high",
            "explanation": "The flag was renamed.",
            "suggested_fix": "Use --timeout.",
        },
        {
            "doc_path": "docs/other.md",
            "doc_line_number": 10,
            "drift_type": "incorrect_default",
            "severity": "medium",
            "explanation": "Default changed.",
            "suggested_fix": "New default.",
        },
    ]
    touched = {"docs/cli.md": {42}}
    comments = build_inline_comments(findings, touched)
    assert len(comments) == 1
    assert comments[0]["path"] == "docs/cli.md"
    assert comments[0]["line"] == 42
    assert "```suggestion" in comments[0]["body"]


def test_build_check_annotations_levels():
    """Check annotations assign correct annotation levels by severity."""
    findings = [
        {"doc_path": "a.md", "doc_line_number": 1, "drift_type": "renamed_flag",
         "severity": "high", "code_file_path": "b.py", "code_line_start": 2,
         "explanation": "e", "suggested_fix": "f"},
        {"doc_path": "a.md", "doc_line_number": 2, "drift_type": "incorrect_default",
         "severity": "medium", "code_file_path": "b.py", "code_line_start": 2,
         "explanation": "e", "suggested_fix": "f"},
        {"doc_path": "a.md", "doc_line_number": 3, "drift_type": "renamed_flag",
         "severity": "low", "code_file_path": "b.py", "code_line_start": 2,
         "explanation": "e", "suggested_fix": "f"},
    ]
    anns = build_check_annotations(findings)
    assert anns[0]["annotation_level"] == "failure"
    assert anns[1]["annotation_level"] == "warning"
    assert anns[2]["annotation_level"] == "notice"


def test_determine_check_conclusion():
    """Check-run conclusion logic."""
    assert determine_check_conclusion([], fail_on_drift=True) == "success"
    assert (
        determine_check_conclusion(
            [{"severity": "high"}], fail_on_drift=True
        )
        == "failure"
    )
    assert (
        determine_check_conclusion(
            [{"severity": "low"}], fail_on_drift=True
        )
        == "success"
    )
    assert (
        determine_check_conclusion(
            [{"severity": "high"}], fail_on_drift=False
        )
        == "success"
    )


def test_main_handles_403_gracefully(tmp_path, monkeypatch):
    """When GitHub token lacks write permissions (HTTP 403), main() should log
    a warning and write to GITHUB_STEP_SUMMARY instead of crashing."""
    import json
    import httpx
    from github import pr_commenter

    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"findings": []}), encoding="utf-8")
    summary_file = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    def fake_request(self, method, path, json_body=None):
        req = httpx.Request(method, f"https://api.github.com{path}")
        if method == "GET" and "/comments" in path:
            return httpx.Response(200, json=[], request=req)
        if method == "GET" and "/pulls/" in path:
            return httpx.Response(200, json={"head": {"sha": "abc1234"}}, request=req)
        # Return 403 on POST (creating comment or check run)
        return httpx.Response(
            403,
            json={"message": "Resource not accessible by integration"},
            request=req,
        )

    monkeypatch.setattr(pr_commenter.PRCommenter, "_request", fake_request)
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_commenter.py",
            "--owner", "DakshSingh-GitHub",
            "--repo", "docs-drive",
            "--pr-number", "1",
            "--token", "ghs_readonly_token",
            "--report", str(report_path),
            "--fail-on-drift", "false",
        ],
    )

    # Should complete without raising HTTPStatusError
    pr_commenter.main()
    assert summary_file.exists()
    assert "<!-- docs-drift-bot-summary -->" in summary_file.read_text(encoding="utf-8")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
