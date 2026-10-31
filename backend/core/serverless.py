"""Serverless environment detection utility.

Detects whether the application is running in a serverless environment
(Vercel, AWS Lambda, Google Cloud Functions, etc.) so that code paths
can prefer serverless-friendly alternatives while keeping all original
functionality intact for local/machine execution.
"""

from __future__ import annotations

import os
from typing import Dict


def is_serverless() -> bool:
    """Return True if running in a known serverless environment.

    Detects via common environment variables set by serverless platforms:
    - Vercel: VERCEL=1
    - AWS Lambda: AWS_LAMBDA_FUNCTION_NAME
    - Google Cloud Functions: FUNCTION_NAME / GOOGLE_CLOUD_PROJECT
    - Azure Functions: FUNCTIONS_WORKER_RUNTIME
    - Netlify: NETLIFY
    """
    serverless_vars = [
        "VERCEL",
        "VERCEL_ENV",
        "AWS_LAMBDA_FUNCTION_NAME",
        "FUNCTION_NAME",
        "GOOGLE_CLOUD_PROJECT",
        "FUNCTIONS_WORKER_RUNTIME",
        "NETLIFY",
    ]
    return any(os.environ.get(var) for var in serverless_vars)


def is_vercel() -> bool:
    """Return True if running on Vercel."""
    return bool(os.environ.get("VERCEL"))


def get_serverless_tmp_path(filename: str) -> str:
    """Return a writable temporary path for serverless environments.

    On Vercel/AWS Lambda, only /tmp is writable. On local machines,
    returns the filename as-is (or a local temp dir).
    """
    if is_serverless():
        return f"/tmp/{filename}"
    return filename


def get_platform_info() -> Dict[str, str]:
    """Return a dict of detected platform environment variables."""
    info: Dict[str, str] = {}
    for var in [
        "VERCEL",
        "VERCEL_ENV",
        "AWS_LAMBDA_FUNCTION_NAME",
        "FUNCTION_NAME",
        "GOOGLE_CLOUD_PROJECT",
        "FUNCTIONS_WORKER_RUNTIME",
        "NETLIFY",
        "GITHUB_ACTIONS",
        "CI",
    ]:
        val = os.environ.get(var)
        if val:
            info[var] = val
    return info
