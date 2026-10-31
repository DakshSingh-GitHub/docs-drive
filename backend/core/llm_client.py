"""LLM client supporting Ollama (gemma4:e4b), OpenAI, and Anthropic providers.

Provides a uniform chat interface with retry handling, JSON schema enforcement,
and pluggable providers. In OFFLINE mode it returns a deterministic simulated
verdict so the pipeline never breaks when no LLM is available.
"""

from __future__ import annotations

import os

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL_NAME = os.environ.get("MODEL_NAME", "gemma4:e4b")


@dataclass
class LLMResult:
    """Result of an LLM chat request."""

    content: str
    parsed: Optional[Dict[str, Any]] = None
    provider: str = "unknown"
    model: str = "unknown"
    success: bool = True
    error: Optional[str] = None
    duration_seconds: float = 0.0


class LLMClient:
    """Unified LLM client for Ollama / OpenAI / Anthropic."""

    MODELS = {
        "auto": MODEL_NAME,
        "ollama": MODEL_NAME,
        "openai": "gpt-4o",
        "anthropic": "claude-3-5-sonnet-20240620",
    }

    def __init__(
        self,
        mode: str = "auto",
        host: str = OLLAMA_URL,
        temperature: float = 0.1,
        max_retries: int = 3,
        openai_key: Optional[str] = None,
        anthropic_key: Optional[str] = None,
    ):
        self.mode = mode
        self.host = host
        self.temperature = temperature
        self.max_retries = max_retries
        self.openai_key = openai_key
        self.anthropic_key = anthropic_key
        self._ollama_client = None

        if OLLAMA_AVAILABLE and (mode == "auto" or mode == "ollama"):
            try:
                self._ollama_client = ollama.Client(host=host) # type: ignore
            except Exception as exc:
                logger.warning("Could not connect to Ollama at %s: %s", host, exc)
                self._ollama_client = None

        self._httpx_available = HTTPX_AVAILABLE

    @property
    def is_available(self) -> bool: # type: ignore
        return self.is_available()

    def is_available(self) -> bool:
        """Check whether a real LLM provider is available."""
        if self.mode == "offline":
            return False
        if self.mode == "ollama" and self._ollama_client is not None:
            try:
                self._ollama_client.ps()
                return True
            except Exception:
                return False
        if self.mode == "openai" and self.openai_key:
            return True
        if self.mode == "anthropic" and self.anthropic_key:
            return True
        return self._ollama_client is not None

    def chat(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> LLMResult: # type: ignore
        """Send a chat request and return the LLMResult."""
        temperature = temperature if temperature is not None else self.temperature
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        start = time.monotonic()
        for attempt in range(self.max_retries + 1):
            try:
                if self.mode == "offline":
                    content = self._simulate_response(user_prompt, system_prompt)
                    return LLMResult(
                        content=content,
                        provider="offline",
                        model="offline-simulator",
                        duration_seconds=time.monotonic() - start,
                    )
                if self.mode == "ollama" or (self.mode == "auto" and self._ollama_client):
                    resp = self._call_ollama(messages, temperature)
                    return LLMResult(
                        content=resp,
                        provider="ollama",
                        model=self.MODELS["ollama"],
                        duration_seconds=time.monotonic() - start,
                    )
                if self.mode == "openai" and self.openai_key:
                    resp = self._call_openai(messages, temperature)
                    return LLMResult(
                        content=resp,
                        provider="openai",
                        model=self.MODELS["openai"],
                        duration_seconds=time.monotonic() - start,
                    )
                if self.mode == "anthropic" and self.anthropic_key:
                    resp = self._call_anthropic(messages, temperature)
                    return LLMResult(
                        content=resp,
                        provider="anthropic",
                        model=self.MODELS["anthropic"],
                        duration_seconds=time.monotonic() - start,
                    )
                return LLMResult(
                    content="",
                    provider="unknown",
                    error="No LLM provider configured",
                    success=False,
                    duration_seconds=time.monotonic() - start,
                )
            except Exception as exc:
                logger.warning("LLM call attempt %d failed: %s", attempt + 1, exc)
                if attempt == self.max_retries:
                    return LLMResult(
                        content="",
                        provider=self.mode,
                        error=str(exc),
                        success=False,
                        duration_seconds=time.monotonic() - start,
                    )
                time.sleep(0.5 * (attempt + 1))

    def _call_ollama(self, messages: List[Dict[str, str]], temperature: float) -> str:
        """Call the local Ollama gemma4:e4b model."""
        resp = self._ollama_client.chat( # type: ignore
            model=self.MODELS["ollama"],
            messages=messages,
            options={"temperature": temperature},
        )
        return resp["message"]["content"]

    def _call_openai(self, messages: List[Dict[str, str]], temperature: float) -> str:
        """Call the OpenAI API."""
        if not self._httpx_available:
            raise RuntimeError("httpx not installed for OpenAI mode")
        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.MODELS["openai"],
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        resp = httpx.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=120) # type: ignore
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _call_anthropic(self, messages: List[Dict[str, str]], temperature: float) -> str:
        """Call the Anthropic Claude API."""
        if not self._httpx_available:
            raise RuntimeError("httpx not installed for Anthropic mode")
        headers = {
            "x-api-key": self.anthropic_key,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        last_msg = messages[-1]["content"]
        payload = {
            "model": self.MODELS["anthropic"],
            "max_tokens": 4096,
            "temperature": temperature,
            "messages": [{"role": "user", "content": last_msg}],
        }
        resp = httpx.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=120) # type: ignore
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]

    def _simulate_response(self, user_prompt: str, system_prompt: Optional[str] = None) -> str:
        """Simulate an LLM response with a deterministic JSON verdict."""
        import hashlib

        seed = hashlib.md5(user_prompt.encode("utf-8")).hexdigest()
        is_drift = int(seed[:8], 16) % 100 > 35  # ~65% drift rate for testing
        drift_type = "incorrect_default" if "default" in user_prompt.lower() else "renamed_flag"
        confidence = round(0.75 + (int(seed[:4], 16) % 20) / 100, 2)
        if not is_drift:
            drift_type = "none"
            confidence = 0.95
        return json.dumps(
            {
                "is_drift": is_drift,
                "drift_type": drift_type,
                "severity": "high" if drift_type == "renamed_flag" else "medium",
                "confidence": confidence,
                "reasoning": "Simulated chain-of-thought verification: code change affects the documented behavior.",
                "explanation": (
                    f"The documentation sentence no longer matches the code change "
                    f"({user_prompt[:60]}...)."
                    if is_drift
                    else "The documentation sentence remains accurate despite the code change."
                ),
                "suggested_fix": "Update the documentation sentence to reflect the current code behavior."
                if is_drift
                else "",
            },
            ensure_ascii=False,
        )


if __name__ == "__main__":
    client = LLMClient(mode="offline")
    result = client.chat("Test offline mode")
    print(f"Provider: {result.provider}, success: {result.success}")
    print(result.content[:300])
