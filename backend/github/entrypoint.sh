#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# GitHub Action entrypoint for the docs-drift action.
#
# Coordinates Ollama availability, runs the Phase 1 CLI against the PR diff,
# exports step outputs, and invokes the PR commenter (Phase 2.2).
# ---------------------------------------------------------------------------

echo "=== docs-drift GitHub Action ==="

# 1. Validate required environment variables (with fallbacks for push / workflow_dispatch events)
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"
BASE_REF="${BASE_REF:-${GITHUB_BASE_REF:-HEAD~1}}"
HEAD_REF="${HEAD_REF:-${GITHUB_HEAD_REF:-${GITHUB_SHA:-HEAD}}}"

# Handle initial push event where before SHA is all zeros
if [ "$BASE_REF" = "0000000000000000000000000000000000000000" ]; then
    BASE_REF="HEAD~1"
fi

CONFIDENCE_THRESHOLD="${CONFIDENCE_THRESHOLD:-0.75}"
FAIL_ON_DRIFT="${FAIL_ON_DRIFT:-true}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
MODEL_NAME="${MODEL_NAME:-gemma4:e4b}"
DOCS_DIR="${DOCS_DIR:-docs}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-}"
GITHUB_EVENT_PATH="${GITHUB_EVENT_PATH:-}"

# Mark the git working directory as safe (GitHub Actions runners) and cd into workspace
if [ -n "${GITHUB_WORKSPACE:-}" ] && [ -d "$GITHUB_WORKSPACE" ]; then
    cd "$GITHUB_WORKSPACE"
    git config --global --add safe.directory "$GITHUB_WORKSPACE" 2>/dev/null || true
fi

# Ensure /app and workspace modules are available on python path
export PYTHONPATH="/app:${GITHUB_WORKSPACE:-}:${PYTHONPATH:-}"

# Fetch the base commit if needed (shallow fetch)
if [ -n "$BASE_REF" ]; then
    echo "Fetching base ref: $BASE_REF"
    git fetch origin "+refs/heads/$BASE_REF:refs/remotes/origin/$BASE_REF" --depth=50 2>/dev/null || \
    git fetch origin "$BASE_REF" --depth=50 2>/dev/null || true
fi

# Fetch the head ref if needed
if [ -n "$HEAD_REF" ] && [ "$HEAD_REF" != "HEAD" ]; then
    echo "Fetching head ref: $HEAD_REF"
    git fetch origin "+refs/heads/$HEAD_REF:refs/remotes/origin/$HEAD_REF" --depth=50 2>/dev/null || true
fi

# Determine PR metadata for the commenter
OWNER=""
REPO=""
PR_NUMBER=""

if [ -n "$GITHUB_REPOSITORY" ]; then
    OWNER="$(echo "$GITHUB_REPOSITORY" | cut -d'/' -f1)"
    REPO="$(echo "$GITHUB_REPOSITORY" | cut -d'/' -f2)"
fi

if [ -n "$GITHUB_EVENT_PATH" ] && [ -f "$GITHUB_EVENT_PATH" ]; then
    PR_NUMBER="$(python3 -c "import json; d=json.load(open('$GITHUB_EVENT_PATH')); print(d.get('pull_request',{}).get('number',''))" 2>/dev/null || echo "")"
fi

echo "Repository: $OWNER/$REPO"
echo "PR Number: $PR_NUMBER"
echo "Base Ref: $BASE_REF -> Head Ref: $HEAD_REF"

# 2. Run the Phase 1 CLI against the diff between BASE_REF and HEAD_REF
echo "Running docs-drift check..."
REPORT_FILE="/tmp/drift_report.json"

set +e
python3 -m backend.cli check \
    --repo "$GITHUB_WORKSPACE" \
    --base "$BASE_REF" \
    --head "$HEAD_REF" \
    --docs-dir "$DOCS_DIR" \
    --confidence "$CONFIDENCE_THRESHOLD" \
    --fail-on-drift \
    --json-output "$REPORT_FILE"
CLI_EXIT_CODE=$?
set -e

DRIFT_DETECTED="false"
DRIFT_COUNT="0"

if [ -f "$REPORT_FILE" ]; then
    DRIFT_COUNT="$(python3 -c "import json; d=json.load(open('$REPORT_FILE')); print(len(d.get('findings',[])))" 2>/dev/null || echo "0")"
    if [ "$DRIFT_COUNT" -gt 0 ] 2>/dev/null; then
        DRIFT_DETECTED="true"
    fi
fi

echo "Drift detected: $DRIFT_DETECTED"
echo "Drift count: $DRIFT_COUNT"

# 3. Export GitHub Action step outputs
if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "drift_detected=$DRIFT_DETECTED" >> "$GITHUB_OUTPUT"
    echo "drift_count=$DRIFT_COUNT" >> "$GITHUB_OUTPUT"
    echo "report_json=$REPORT_FILE" >> "$GITHUB_OUTPUT"
fi

# 4. Invoke the PR commenter (Phase 2.2) to publish results
if [ -n "$OWNER" ] && [ -n "$REPO" ] && [ -n "$PR_NUMBER" ] && [ -n "$GITHUB_TOKEN" ]; then
    echo "Invoking PR commenter..."
    python3 -m backend.github.pr_commenter \
        --owner "$OWNER" \
        --repo "$REPO" \
        --pr-number "$PR_NUMBER" \
        --token "$GITHUB_TOKEN" \
        --report "$REPORT_FILE" \
        --fail-on-drift "$FAIL_ON_DRIFT"
else
    echo "Skipping PR commenter (missing repo/pr/token context - local dry run)"
fi

# 5. Exit with appropriate code
if [ "$FAIL_ON_DRIFT" = "true" ] && [ "$CLI_EXIT_CODE" -ne 0 ]; then
    echo "Failing CI due to drift detection (exit code $CLI_EXIT_CODE)"
    exit "$CLI_EXIT_CODE"
fi

exit 0
