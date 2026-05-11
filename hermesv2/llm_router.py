"""Route LLM requests between local Llama (Ollama) and Claude Max (CLI).

Routing rules (in order):
1. Prompt > 6000 chars => Claude Max
2. Contains code keywords => Claude Max
3. Contains reasoning keywords => Claude Max
4. Otherwise => Llama
5. On Llama failure => fall back to Claude Max
6. On Claude rate-limit => fall back to Llama

No paid Anthropic API calls. Claude reaches the user's Max subscription via the
`claude` CLI subprocess (see claude_runner.py).
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from hermesv2.claude_runner import (
    ClaudeNotAvailableError,
    ClaudeRateLimitError,
    ClaudeRunner,
)

log = logging.getLogger(__name__)


CODE_KEYWORDS = (
    "write code",
    "implement",
    "function",
    "debug",
    "refactor",
    "stack trace",
    "traceback",
    "compile error",
    "regex",
    "diff this",
)

REASONING_KEYWORDS = (
    "analyze",
    "compare",
    "evaluate",
    "design",
    "architect",
    "trade-off",
    "tradeoff",
    "pros and cons",
    "step by step",
    "reason through",
)


class LLMRouter:
    """Two-LLM router with cost-free fallback semantics."""

    def __init__(
        self,
        claude_runner: ClaudeRunner,
        llama_url: str = "http://localhost:11434",
        llama_model: str = "llama3",
        long_prompt_threshold: int = 6000,
        llama_timeout: int = 120,
    ):
        self.claude = claude_runner
        self.llama_url = llama_url.rstrip("/")
        self.llama_model = llama_model
        self.long_prompt_threshold = long_prompt_threshold
        self.llama_timeout = llama_timeout
        self.stats: dict[str, int] = {
            "llama_calls": 0,
            "claude_max_calls": 0,
            "rate_limited": 0,
            "llama_failures": 0,
            "claude_failures": 0,
            "total_calls": 0,
        }

    def decide(self, prompt: str) -> str:
        """Return 'claude' or 'llama' for a given prompt."""
        if len(prompt) > self.long_prompt_threshold:
            return "claude"
        lowered = prompt.lower()
        if any(kw in lowered for kw in CODE_KEYWORDS):
            return "claude"
        if any(kw in lowered for kw in REASONING_KEYWORDS):
            return "claude"
        return "llama"

    def chat(
        self,
        prompt: str,
        system: str | None = None,
        force: str | None = None,
    ) -> dict[str, Any]:
        """Route the prompt and return {'response': str, 'backend': str}.

        Args:
            prompt: The user prompt.
            system: Optional system prompt (persona).
            force: 'claude' or 'llama' to bypass routing.
        """
        self.stats["total_calls"] += 1
        target = force or self.decide(prompt)

        if target == "claude":
            try:
                resp = self._call_claude(prompt, system)
                return {"response": resp, "backend": "claude_max"}
            except ClaudeRateLimitError as e:
                log.warning("Claude rate-limited, falling back to Llama: %s", e)
                self.stats["rate_limited"] += 1
                resp = self._call_llama(prompt, system)
                return {"response": resp, "backend": "llama (claude rate-limited)"}
            except ClaudeNotAvailableError:
                log.warning("Claude CLI unavailable, using Llama")
                resp = self._call_llama(prompt, system)
                return {"response": resp, "backend": "llama (no claude)"}
            except Exception as e:
                log.exception("Claude failed: %s", e)
                self.stats["claude_failures"] += 1
                resp = self._call_llama(prompt, system)
                return {"response": resp, "backend": "llama (claude error)"}

        try:
            resp = self._call_llama(prompt, system)
            return {"response": resp, "backend": "llama"}
        except Exception as e:
            log.warning("Llama failed (%s), falling back to Claude", e)
            self.stats["llama_failures"] += 1
            try:
                resp = self._call_claude(prompt, system)
                return {"response": resp, "backend": "claude_max (llama down)"}
            except Exception:
                self.stats["claude_failures"] += 1
                raise

    def _call_claude(self, prompt: str, system: str | None) -> str:
        self.stats["claude_max_calls"] += 1
        return self.claude.chat(prompt, system=system)

    def _call_llama(self, prompt: str, system: str | None) -> str:
        self.stats["llama_calls"] += 1
        payload: dict[str, Any] = {
            "model": self.llama_model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system
        r = requests.post(
            f"{self.llama_url}/api/generate",
            json=payload,
            timeout=self.llama_timeout,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("response", "").strip()

    def get_stats(self) -> dict[str, Any]:
        total = max(self.stats["total_calls"], 1)
        return {
            **self.stats,
            "llama_percentage": round(
                100.0 * self.stats["llama_calls"] / total, 1
            ),
            "claude_percentage": round(
                100.0 * self.stats["claude_max_calls"] / total, 1
            ),
            "api_cost_usd": 0.0,
            "billing_note": "$0.00 (using Max subscription)",
        }
