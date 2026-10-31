"""Embedding service for nomic-embed-text via local Ollama.

Provides a pluggable embedding backend with an OFFLINE fallback that generates
deterministic pseudo-embeddings when Ollama (or any remote provider) is
unavailable. This keeps the full pipeline runnable without network access.
"""

from __future__ import annotations

import hashlib
import logging
import math
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class EmbeddingMode(str, Enum):
    """Embedding provider selection mode."""

    AUTO = "auto"  # Try Ollama, then OpenAI, then Anthropic, then OFFLINE
    OFFLINE = "offline"  # Deterministic pseudo-embeddings, no network
    OLLAMA = "ollama"  # Local nomic-embed-text
    OPENAI = "openai"  # OpenAI embeddings via API key
    ANTHROPIC = "anthropic"  # Anthropic embeddings via API key (bedrock)


OFFLINE_EMBEDDING_DIM = 768


def _offline_hash_embedding(text: str) -> List[float]:
    """Generate a deterministic pseudo-embedding from the text hash.

    Uses a keyed hash over the text to produce a stable vector. This is NOT a
    semantic embedding — it is only used so the retrieval pipeline runs
    offline without network access. Ranking quality is degraded in this mode.
    """
    seed = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)
    values: List[float] = []
    for i in range(OFFLINE_EMBEDDING_DIM):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        values.append((seed / 0x7FFFFFFF - 0.5) * 2)
    # Normalize
    mag = math.sqrt(sum(v * v for v in values))
    if mag == 0:
        return values
    return [v / mag for v in values]


class EmbeddingService:
    """Embedding service wrapping Ollama / OpenAI / Anthropic / offline."""

    def __init__(
        self,
        model_name: str = "nomic-embed-text",
        host: str = "http://localhost:11434",
        mode: EmbeddingMode = EmbeddingMode.AUTO,
        openai_key: Optional[str] = None,
        anthropic_key: Optional[str] = None,
        ollama_available_check: bool = True,
    ):
        self.model_name = model_name
        self.host = host
        self.mode = mode
        self.openai_key = openai_key
        self.anthropic_key = anthropic_key
        self._cache: Dict[str, List[float]] = {}

        self._ollama_client = None
        if OLLAMA_AVAILABLE and (mode == EmbeddingMode.AUTO or mode == EmbeddingMode.OLLAMA):
            try:
                self._ollama_client = ollama.Client(host=host)
            except Exception as exc:
                logger.warning("Could not connect to Ollama at %s: %s", host, exc)
                self._ollama_client = None

        self._httpx_available = HTTPX_AVAILABLE

    @property
    def available(self) -> bool:
        return self.is_available()

    def is_available(self) -> bool:
        """Check whether a real embedding provider is available."""
        if self.mode == EmbeddingMode.OFFLINE:
            return False
        if self.mode == EmbeddingMode.OLLAMA and self._ollama_client is not None:
            return True
        if self.mode == EmbeddingMode.OPENAI and self.openai_key:
            return True
        if self.mode == EmbeddingMode.ANTHROPIC and self.anthropic_key:
            return True
        return bool(self._ollama_client is not None)

    def _check_ollama_health(self) -> bool:
        """Probe Ollama for model availability."""
        if self._ollama_client is None or not OLLAMA_AVAILABLE:
            return False
        try:
            self._ollama_client.ps()
            return True
        except Exception:
            return False

    def embed_text(self, text: str) -> List[float]:
        """Return a dense embedding vector for the given text."""
        if text in self._cache:
            return self._cache[text]

        if self.mode == EmbeddingMode.OFFLINE:
            vec = _offline_hash_embedding(text)
        elif self.mode == EmbeddingMode.OLLAMA or self.mode == EmbeddingMode.AUTO:
            if self.mode == EmbeddingMode.AUTO and not self._check_ollama_health():
                logger.info("Ollama not available, falling back to OFFLINE mode")
                self.mode = EmbeddingMode.OFFLINE
                return self.embed_text(text)
            try:
                response = self._ollama_client.embeddings(model=self.model_name, prompt=text)
                vec = response["embedding"]
            except Exception as exc:  # pragma: no cover
                logger.warning("Ollama embedding failed: %s", exc)
                if self.mode == EmbeddingMode.AUTO:
                    logger.info("Falling back to OFFLINE embedding mode")
                    self.mode = EmbeddingMode.OFFLINE
                    return self.embed_text(text)
                vec = _offline_hash_embedding(text)
        elif self.mode == EmbeddingMode.OPENAI:
            vec = self._embed_openai(text)
        elif self.mode == EmbeddingMode.ANTHROPIC:
            vec = self._embed_anthropic(text)
        else:
            vec = _offline_hash_embedding(text)

        self._cache[text] = vec
        return vec

    def _embed_openai(self, text: str) -> List[float]:
        """Embed via OpenAI API."""
        if not self._httpx_available:
            raise RuntimeError("httpx not installed for OpenAI mode")
        import httpx

        headers = {"Authorization": f"Bearer {self.openai_key}", "Content-Type": "application/json"}
        payload = {"input": text, "model": "text-embedding-ada-002"}
        resp = httpx.post("https://api.openai.com/v1/embeddings", headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]

    def _embed_anthropic(self, text: str) -> List[float]:
        """Embed via Anthropic Bedrock (us-east-1)."""
        if not self._httpx_available:
            raise RuntimeError("httpx not installed for Anthropic mode")
        import boto3

        bedrock = boto3.client("bedrock", region_name="us-east-1", aws_access_key_id=self.anthropic_key or "", aws_secret_access_key="")
        body = {"inputText": text}
        resp = bedrock.invoke_model(modelId="amazon.titan-embed-text-v2:0", body=json.dumps(body))
        return json.loads(resp["body"].read())["embedding"]


if __name__ == "__main__":
    svc = EmbeddingService(mode=EmbeddingMode.OFFLINE)
    vec = svc.embed_text("hello world")
    print(f"Embedding dim: {len(vec)}, first 5: {vec[:5]}")
