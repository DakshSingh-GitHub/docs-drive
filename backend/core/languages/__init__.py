"""Language extractor registry.

Provides the `get_extractor(language)` factory used by ast_extractor.py.
"""

from __future__ import annotations

from typing import Dict, Optional

from core.ast_extractor import (
    JavaScriptExtractor,
    LanguageExtractor,
    PythonExtractor,
    TypeScriptExtractor,
)

extractors: Dict[str, LanguageExtractor] = {
    "python": PythonExtractor(),
    "typescript": TypeScriptExtractor(),
    "javascript": JavaScriptExtractor(),
}


def get_extractor(language: str) -> Optional[LanguageExtractor]:
    return extractors.get(language)
