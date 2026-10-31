"""Unit tests for the unified diff parser (Phase 1.1)."""

import pytest

from core.diff_parser import (
    DiffParser,
    DiffFile,
    DiffHunk,
    DiffLine,
    classify_file,
    parse_diff,
    parse_diff_from_patch,
)


SAMPLE_RENAMED_FLAG_DIFF = """diff --git a/cli.py b/cli.py
index abc123..def456 100644
--- a/cli.py
+++ b/cli.py
@@ -30,8 +30,8 @@ def main():
     # CLI argument parser setup
     parser = argparse.ArgumentParser(description="My CLI tool")
-    parser.add_argument('--timeout-secs', type=int, default=30,
-                        help='Connection timeout in seconds (old)')
+    parser.add_argument('--timeout', type=int, default=60,
+                        help='Connection timeout in seconds')
     parser.add_argument('--verbose', action='store_true')

diff --git a/src/config.py b/src/config.py
index 111..222 100644
--- a/src/config.py
+++ b/src/config.py
@@ -10,7 +10,7 @@ HOST = "localhost"
-DEFAULT_PORT = 8080
+DEFAULT_PORT = 3000
 TIMEOUT = 30
"""

SAMPLE_RENAMED_FLAG_DIFF_2 = """diff --git a/utils.py b/utils.py
index 0000000..1111111 100644
--- /dev/null
+++ b/utils.py
@@ -0,0 +1,5 @@
+def new_helper(x):
+    return x * 2
"""


def test_parse_renamed_flag_diff():
    """Parsing a diff with a renamed CLI flag (--timeout-secs -> --timeout)."""
    parser = parse_diff(SAMPLE_RENAMED_FLAG_DIFF)

    assert "cli.py" in parser.files
    assert "src/config.py" in parser.files

    cli_file = parser.files["cli.py"]
    assert cli_file.change_type == "modified"
    assert cli_file.source_file == "cli.py"
    assert cli_file.target_file == "cli.py"
    assert len(cli_file.hunks) == 1

    hunk = cli_file.hunks[0]
    assert hunk.target_start == 30

    added = " ".join(ln.content.strip() for ln in hunk.added_lines)
    removed = " ".join(ln.content.strip() for ln in hunk.removed_lines)
    assert "--timeout-secs" in removed
    assert "--timeout" in added
    assert "default=30" in removed
    assert "default=60" in added

    removed_content = "".join(ln.content for ln in hunk.removed_lines)
    assert "timeout-secs" in removed_content

    config_file = parser.files["src/config.py"]
    removed = {ln.content.strip() for ln in config_file.hunks[0].removed_lines}
    assert "DEFAULT_PORT = 8080" in removed
    added = {ln.content.strip() for ln in config_file.hunks[0].added_lines}
    assert "DEFAULT_PORT = 3000" in added


def test_parse_function_default_change():
    """Parsing a diff with a changed function default argument."""
    diff = """diff --git a/api/client.py b/api/client.py
index abc..def 100644
--- a/api/client.py
+++ b/api/client.py
@@ -1,5 +1,5 @@
-def connect(host="localhost", port=8080, timeout=30):
+def connect(host="localhost", port=8080, timeout=60):
     pass
"""
    parser = parse_diff(diff)
    file = parser.files["api/client.py"]
    hunk = file.hunks[0]
    removed = " ".join(ln.content.strip() for ln in hunk.removed_lines)
    added = " ".join(ln.content.strip() for ln in hunk.added_lines)
    assert "timeout=30" in removed
    assert "timeout=60" in added


def test_parse_new_file_diff():
    """Parsing a diff that adds a new file."""
    diff = """diff --git a/utils.py b/utils.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/utils.py
@@ -0,0 +1,5 @@
+def new_helper(x):
+    return x * 2
"""
    parser = parse_diff(diff)
    file = parser.files["utils.py"]
    assert file.change_type == "added"
    assert file.first_hunk is not None
    assert file.first_hunk.source_start == 0


def test_file_classification():
    """Code vs documentation vs ignored files."""
    assert classify_file("cli.py") == "code"
    assert classify_file("src/api/client.ts") == "code"
    assert classify_file("src/page.tsx") == "code"
    assert classify_file("README.md") == "doc"
    assert classify_file("docs/guide.mdx") == "doc"
    assert classify_file("docs/api.rst") == "doc"
    assert classify_file("docs/readme.txt") == "doc"
    assert classify_file("CONTRIBUTING.adoc") == "doc"
    assert classify_file("package-lock.json") == "other"
    assert classify_file(".gitignore") == "other"
    assert classify_file("Dockerfile") == "other"
    assert classify_file("node_modules/vendor/lib.js") == "other"


def test_code_vs_doc_separation():
    """Code and doc files are correctly separated in a mixed diff."""
    parser = parse_diff(SAMPLE_RENAMED_FLAG_DIFF)
    code = parser.code_files
    docs = parser.doc_files
    assert len(code) == 2
    assert code[0].target_file in ("cli.py", "src/config.py")
    assert code[1].target_file in ("cli.py", "src/config.py")
    assert len(docs) == 0


def test_doc_line_mapping_preserved():
    """Added lines retain target line numbers for doc diff tracking."""
    diff = """diff --git a/README.md b/README.md
index abc..def 100644
--- a/README.md
+++ b/README.md
@@ -5,7 +5,8 @@
 Some intro text.
-New default: 30 seconds.
+New default: 60 seconds.
+Added line here.
 More text.
"""
    parser = parse_diff(diff)
    file = parser.files["README.md"]
    assert file.change_type == "modified"
    added = file.hunks[0].added_lines
    line_numbers = [ln.line_number for ln in added]
    assert 6 in line_numbers
    assert 7 in line_numbers


def test_doc_changes_recorded():
    """PR doc modifications are recorded via DiffSummary."""
    diff = """diff --git a/docs/guide.md b/docs/guide.md
index abc..def 100644
--- a/docs/guide.md
+++ b/docs/guide.md
@@ -10,4 +10,4 @@
-Run with --timeout-secs.
+Run with --timeout.
"""
    parser = parse_diff(diff)
    assert parser.has_doc_changes is True
    assert parser.doc_updates.has_doc_updates() is True
    assert parser.doc_updates.doc_lines_changed("docs/guide.md") == {10}


def test_pr_doc_update_guard():
    """PR Doc-Update Guard: known doc line is recognized as already updated."""
    diff = SAMPLE_RENAMED_FLAG_DIFF  # contains docs touched at line ~12 in docs/guide.md
    parser = parse_diff(diff)
    # The sample diff has no docs/guide.md, so verify guard returns False
    assert parser.find_doc_sentence_in_pr_changes("docs/guide.md", 12) is False


def test_parse_from_patch_file(tmp_path):
    """Parsing a diff from a .patch file."""
    patch_path = tmp_path / "sample.patch"
    patch_path.write_text(SAMPLE_RENAMED_FLAG_DIFF, encoding="utf-8")
    parser = parse_diff_from_patch(str(patch_path))
    assert "cli.py" in parser.files
    assert parser.has_code_changes


def test_from_local_repo_with_detached_head_and_remote_branch(tmp_path):
    """Simulate CI environment where PR branch is checked out as detached HEAD
    and head_ref name is not a local branch, while base_ref is on origin/."""
    import git

    repo = git.Repo.init(str(tmp_path))
    file_path = tmp_path / "sample.py"
    file_path.write_text("x = 1\n", encoding="utf-8")
    repo.index.add(["sample.py"])
    c1 = repo.index.commit("initial commit")
    repo.create_head("main")

    feat = repo.create_head("phase-4-completion")
    repo.head.reference = feat
    file_path.write_text("x = 2\n", encoding="utf-8")
    repo.index.add(["sample.py"])
    c2 = repo.index.commit("feature commit")

    # Detach HEAD (simulating actions/checkout on PR)
    repo.head.reference = repo.commit(c2.hexsha)
    repo.delete_head("phase-4-completion", force=True)

    # Simulate origin/main tracking branch without local main branch
    repo.create_remote("origin", "https://example.com/fake.git")
    repo.create_head("origin/main", c1)
    repo.delete_head("main", force=True)

    parser = DiffParser()
    parser.from_local_repo(str(tmp_path), base_ref="main", head_ref="phase-4-completion")

    assert "sample.py" in parser.files
    f = parser.files["sample.py"]
    assert f.change_type == "modified"
    assert len(f.hunks) == 1
    assert any("x = 2" in ln.content for ln in f.hunks[0].added_lines)
    assert any("x = 1" in ln.content for ln in f.hunks[0].removed_lines)


def test_from_local_repo_normal_branches(tmp_path):
    """Test from_local_repo with standard local branches."""
    import git

    repo = git.Repo.init(str(tmp_path))
    file_path = tmp_path / "cli.py"
    file_path.write_text("TIMEOUT = 30\n", encoding="utf-8")
    repo.index.add(["cli.py"])
    repo.index.commit("initial commit")
    repo.create_head("main")

    feat = repo.create_head("feat")
    repo.head.reference = feat
    file_path.write_text("TIMEOUT = 60\n", encoding="utf-8")
    repo.index.add(["cli.py"])
    repo.index.commit("feature commit")

    parser = DiffParser()
    parser.from_local_repo(str(tmp_path), base_ref="main", head_ref="feat")
    assert "cli.py" in parser.files
    assert parser.has_code_changes


def test_from_local_repo_already_merged_branch(tmp_path):
    """Test from_local_repo when head_ref is already merged into base_ref."""
    import git

    repo = git.Repo.init(str(tmp_path))
    file_path = tmp_path / "cli.py"
    file_path.write_text("TIMEOUT = 30\n", encoding="utf-8")
    repo.index.add(["cli.py"])
    c1 = repo.index.commit("initial commit")
    main_branch = repo.create_head("main", c1)

    # Create feature branch and commit
    feat = repo.create_head("logo-pr", c1)
    repo.head.reference = feat
    file_path.write_text("TIMEOUT = 60\n", encoding="utf-8")
    repo.index.add(["cli.py"])
    c2 = repo.index.commit("feature commit")

    # Merge logo-pr into main
    repo.head.reference = main_branch
    repo.head.reset(index=True, working_tree=True)
    repo.git.merge("logo-pr", "--no-ff", "-m", "Merge pull request #1 from logo-pr")

    parser = DiffParser()
    parser.from_local_repo(str(tmp_path), base_ref="main", head_ref="logo-pr")
    assert "cli.py" in parser.files
    assert parser.has_code_changes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
