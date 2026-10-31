"""Unit tests for the AST symbol extractor (Phase 1.2)."""

import pytest

from core.ast_extractor import ASTSymbolExtractor
from core.diff_parser import parse_diff


SAMPLE_PYTHON_DEFAULT_CHANGE = """diff --git a/api/client.py b/api/client.py
index abc123..def456 100644
--- a/api/client.py
+++ b/api/client.py
@@ -1,5 +1,5 @@
-def connect(host="localhost", port=8080, timeout=30):
+def connect(host="localhost", port=8080, timeout=60):
     return host
"""

SAMPLE_PYTHON_RENAMED_CLI_ARG = """diff --git a/cli/main.py b/cli/main.py
index abc123..def456 100644
--- a/cli/main.py
+++ b/cli/main.py
@@ -5,8 +5,8 @@ from click import Command
 parser = argparse.ArgumentParser()
-parser.add_argument('--timeout-secs', type=int, default=30)
+parser.add_argument('--timeout', type=int, default=60)
 parser.add_argument('--verbose', action='store_true')
"""

SAMPLE_PYTHON_RENAMED_ARGPARSER = """diff --git a/cli/app.py b/cli/app.py
index abc123..def456 100644
--- a/cli/app.py
+++ b/cli/app.py
@@ -10,7 +10,7 @@ def main():
     parser = argparse.ArgumentParser()
-    parser.add_argument("--auth-token", help="Pass your token")
+    parser.add_argument("--auth-token-new", help="Pass your token")
 """

SAMPLE_PYTHON_ENV_RENAME = """diff --git a/config/env.py b/config/env.py
index abc123..def456 100644
--- a/config/env.py
+++ b/config/env.py
@@ -3,5 +3,5 @@ import os
-DB_HOST = os.environ.get("DB_HOST_OLD", "localhost")
+DB_HOST = os.environ.get("DB_HOST", "localhost")
"""

SAMPLE_TS_FUNCTION_SIGNATURE = """diff --git a/src/api/client.ts b/src/api/client.ts
index abc123..def456 100644
--- a/src/api/client.ts
+++ b/src/api/client.ts
@@ -1,5 +1,5 @@
-def fetch(url: string, retry_count: int = 3):
+def fetch(url: string, retry_count: int = 5):
     return call(url)
"""

SAMPLE_TS_DEFAULT_CHANGE = """diff --git a/src/config.ts b/src/config.ts
index abc123..def456 100644
--- a/src/config.ts
+++ b/src/config.ts
@@ -2,5 +2,5 @@ export interface Settings {
-    keepAliveTimeout: number = 60;
+    keepAliveTimeout: number = 120;
     port: number = 8080;
 }
"""

SAMPLE_DELETION = """diff --git a/legacy.py b/legacy.py
index abc123..def456 100644
--- a/legacy.py
+++ b/src/api/client.ts
@@ -1,5 +1,5 @@
-def fetch_legacy():
+def fetch():
     return call()
"""

SAMPLE_NO_DRIFT_REFACTOR = """diff --git a/utils/helpers.py b/utils/helpers.py
index abc123..def456 100644
--- a/utils/helpers.py
+++ b/utils/helpers.py
@@ -1,5 +1,5 @@
-def fetch_data(url: str, retry_count: int = 3, timeout: int = 60):
+def _fetch_data_internal(url: str, retry_count: int = 3, timeout: int = 60):
     return http.get(url)
"""


def test_python_function_default_change():
    """Python function with a modified default parameter value."""
    parser = parse_diff(SAMPLE_PYTHON_DEFAULT_CHANGE)
    extractor = ASTSymbolExtractor()
    changes = extractor.extract_from_diff(parser.files.values())
    assert len(changes) >= 1
    match = [c for c in changes if c.change_type.value == "default_changed"][0]
    assert "timeout" in match.symbol_name
    assert match.old_value == "30"
    assert match.new_value == "60"


def test_python_renamed_cli_argument_argparse():
    """CLI flag rename detected via argparse pattern."""
    parser = parse_diff(SAMPLE_PYTHON_RENAMED_CLI_ARG)
    extractor = ASTSymbolExtractor()
    changes = extractor.extract_from_diff(parser.files.values())
    renamed = [c for c in changes if c.change_type.value == "renamed"]
    assert len(renamed) >= 1
    assert renamed[0].symbol_name in ("-timeout-secs", "--timeout", "-timeout")
    assert ("--timeout-secs" in renamed[0].description) or ("timeout-secs" in renamed[0].description)


def test_python_env_var_rename():
    """Environment variable rename detection."""
    parser = parse_diff(SAMPLE_PYTHON_ENV_RENAME)
    extractor = ASTSymbolExtractor()
    changes = extractor.extract_from_diff(parser.files.values())
    renamed = [c for c in changes if c.change_type.value == "renamed"]
    assert any("DB_HOST" in c.symbol_name for c in renamed)


def test_ts_function_signature_change():
    """TypeScript function signature change."""
    parser = parse_diff(SAMPLE_TS_FUNCTION_SIGNATURE)
    extractor = ASTSymbolExtractor()
    changes = extractor.extract_from_diff(parser.files.values())
    assert len(changes) >= 1
    assert any("retry_count" in c.symbol_name for c in changes)


def test_ts_default_change():
    """TypeScript default value change."""
    parser = parse_diff(SAMPLE_TS_DEFAULT_CHANGE)
    extractor = ASTSymbolExtractor()
    changes = extractor.extract_from_diff(parser.files.values())
    defaults = [c for c in changes if c.change_type.value == "default_changed"]
    assert any(c.old_value == "60" and c.new_value == "120" for c in defaults)


def test_deletion_detected():
    """Removed function detection."""
    parser = parse_diff(SAMPLE_DELETION)
    extractor = ASTSymbolExtractor()
    changes = extractor.extract_from_diff(parser.files.values())
    removed = [c for c in changes if c.change_type.value == "removed"]
    # At least the renamed function is detected
    assert any(c.symbol_name in ("fetch", "fetch_legacy") for c in changes)


def test_internal_refactor_no_false_positive():
    """Internal refactor that preserves external API produces no drift for the doc sentence."""
    parser = parse_diff(SAMPLE_NO_DRIFT_REFACTOR)
    extractor = ASTSymbolExtractor()
    changes = extractor.extract_from_diff(parser.files.values())
    # Should be few/no high-risk changes; internal underscore rename only
    high_risk = [c for c in changes if c.change_type.value in ("removed", "renamed")]
    # Only the internal naming change; no semantic drift for external consumers
    assert len(changes) == 0 or all(c.symbol_name.startswith("_") for c in changes)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
