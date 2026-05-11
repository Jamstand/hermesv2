"""Wrapper around the `claude` CLI subprocess.

Uses the Claude Max subscription via `claude --print`. Never imports the
`anthropic` SDK. Never contacts api.anthropic.com directly.

Rate limiting is persisted in SQLite so that systemd restarts do not silently
reset the window. The 30 calls / 5 min default is a self-imposed throttle, not
the actual Anthropic Max limit (which uses undocumented rolling token + message
windows over hours).
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class ClaudeNotAvailableError(RuntimeError):
    """Raised when the `claude` CLI is not installed or not on PATH."""


class ClaudeNotLoggedInError(RuntimeError):
    """Raised when the `claude` CLI has no credentials available."""


class ClaudeRateLimitError(RuntimeError):
    """Raised when the local self-imposed throttle is exceeded."""


_RATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS claude_calls (
    ts REAL NOT NULL,
    duration_ms INTEGER,
    model TEXT,
    error INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_claude_calls_ts ON claude_calls(ts);
"""


class _RateLimiter:
    def __init__(
        self, db_path: Path, max_calls: int, window_seconds: int
    ):
        self.db_path = db_path
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_RATE_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def check_and_record(self, model: str | None = None) -> None:
        now = time.time()
        cutoff = now - self.window_seconds
        with self._conn() as conn:
            conn.execute("DELETE FROM claude_calls WHERE ts < ?", (cutoff,))
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM claude_calls WHERE ts >= ?", (cutoff,)
            ).fetchone()
            if count >= self.max_calls:
                raise ClaudeRateLimitError(
                    f"Self-imposed throttle: {count} calls in last "
                    f"{self.window_seconds}s (limit {self.max_calls})"
                )
            conn.execute(
                "INSERT INTO claude_calls (ts, model) VALUES (?, ?)", (now, model)
            )
            conn.commit()

    def record_duration(self, duration_ms: int, error: bool = False) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE claude_calls SET duration_ms = ?, error = ? "
                "WHERE rowid = (SELECT MAX(rowid) FROM claude_calls)",
                (duration_ms, 1 if error else 0),
            )
            conn.commit()

    def stats(self) -> dict[str, Any]:
        now = time.time()
        cutoff = now - self.window_seconds
        with self._conn() as conn:
            (window_count,) = conn.execute(
                "SELECT COUNT(*) FROM claude_calls WHERE ts >= ?", (cutoff,)
            ).fetchone()
            (total_count,) = conn.execute(
                "SELECT COUNT(*) FROM claude_calls"
            ).fetchone()
            (errors,) = conn.execute(
                "SELECT COUNT(*) FROM claude_calls WHERE error = 1"
            ).fetchone()
            row = conn.execute(
                "SELECT AVG(duration_ms) FROM claude_calls WHERE duration_ms IS NOT NULL"
            ).fetchone()
        return {
            "calls_in_window": window_count,
            "max_per_window": self.max_calls,
            "window_seconds": self.window_seconds,
            "total_calls": total_count,
            "errors": errors,
            "avg_duration_ms": int(row[0]) if row and row[0] else None,
        }


class ClaudeRunner:
    """Invoke the `claude` CLI as a subprocess.

    Args:
        rate_db_path: SQLite file used to persist rate-limit state.
        model: Default model for chat() calls (`sonnet` or `opus`).
        timeout: Default subprocess timeout in seconds.
        max_calls_per_window: Self-imposed throttle, calls per window.
        window_seconds: Throttle rolling window.
    """

    def __init__(
        self,
        rate_db_path: str | Path = "data/rate.db",
        model: str = "sonnet",
        timeout: int = 300,
        max_calls_per_window: int = 30,
        window_seconds: int = 300,
    ):
        self.binary = shutil.which("claude")
        self.model = model
        self.timeout = timeout
        self.rate_limiter = _RateLimiter(
            Path(rate_db_path), max_calls_per_window, window_seconds
        )

    def available(self) -> bool:
        return self.binary is not None

    def require_available(self) -> None:
        if not self.binary:
            raise ClaudeNotAvailableError(
                "`claude` CLI not found on PATH. Install with "
                "`npm install -g @anthropic-ai/claude-code` and run `claude login`."
            )

    def logged_in(self) -> bool:
        """Cheap check: presence of credentials file. No billing impact."""
        for candidate in (
            Path.home() / ".claude" / ".credentials.json",
            Path.home() / ".config" / "claude" / ".credentials.json",
        ):
            if candidate.exists() and candidate.stat().st_size > 0:
                return True
        return False

    def chat(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        output_format: str | None = None,
        session: str | None = None,
        add_dirs: list[str] | None = None,
        timeout: int | None = None,
    ) -> str:
        """Send a one-shot prompt via `claude --print` and return stdout.

        Raises:
            ClaudeNotAvailableError: if the binary is missing.
            ClaudeRateLimitError: if the self-imposed throttle is exceeded.
            subprocess.TimeoutExpired: if the call exceeds timeout.
        """
        self.require_available()
        chosen_model = model or self.model
        self.rate_limiter.check_and_record(chosen_model)

        full_prompt = prompt
        if system:
            full_prompt = f"<system>{system}</system>\n\n{prompt}"

        cmd: list[str] = [self.binary, "--print", "--model", chosen_model]
        if output_format:
            cmd.extend(["--output-format", output_format])
        if session:
            cmd.extend(["--session", session])
        for d in add_dirs or []:
            cmd.extend(["--add-dir", d])
        cmd.append(full_prompt)

        log.debug("claude command: %s ...", cmd[:5])
        started = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired:
            self.rate_limiter.record_duration(
                int((time.time() - started) * 1000), error=True
            )
            raise
        duration_ms = int((time.time() - started) * 1000)
        if result.returncode != 0:
            self.rate_limiter.record_duration(duration_ms, error=True)
            stderr = result.stderr.strip()
            log.error("claude CLI failed (rc=%s): %s", result.returncode, stderr)
            if "not logged in" in stderr.lower() or "unauthor" in stderr.lower():
                raise ClaudeNotLoggedInError(stderr)
            raise RuntimeError(f"claude CLI failed: {stderr}")
        self.rate_limiter.record_duration(duration_ms)
        return result.stdout.strip()

    def run_skill(
        self,
        skill_name: str,
        prompt: str = "",
        model: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """Invoke a Claude Code skill via `claude --print --skill <name>`."""
        self.require_available()
        chosen_model = model or self.model
        self.rate_limiter.check_and_record(chosen_model)

        cmd: list[str] = [
            self.binary,
            "--print",
            "--model",
            chosen_model,
            "--skill",
            skill_name,
        ]
        if prompt:
            cmd.append(prompt)
        started = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired:
            self.rate_limiter.record_duration(
                int((time.time() - started) * 1000), error=True
            )
            raise
        duration_ms = int((time.time() - started) * 1000)
        if result.returncode != 0:
            self.rate_limiter.record_duration(duration_ms, error=True)
            raise RuntimeError(f"claude skill `{skill_name}` failed: {result.stderr}")
        self.rate_limiter.record_duration(duration_ms)
        return result.stdout.strip()

    def run_command(
        self, args: list[str], timeout: int | None = None
    ) -> subprocess.CompletedProcess:
        """Run an arbitrary `claude <args>` invocation (e.g. `claude --version`).

        Rate-limited and returns the raw CompletedProcess. Caller handles parsing.
        """
        self.require_available()
        self.rate_limiter.check_and_record(self.model)
        started = time.time()
        try:
            result = subprocess.run(
                [self.binary] + args,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired:
            self.rate_limiter.record_duration(
                int((time.time() - started) * 1000), error=True
            )
            raise
        self.rate_limiter.record_duration(
            int((time.time() - started) * 1000),
            error=(result.returncode != 0),
        )
        return result

    def get_usage_stats(self) -> dict[str, Any]:
        s = self.rate_limiter.stats()
        s["binary"] = self.binary
        s["model"] = self.model
        s["api_cost_usd"] = 0.0
        s["billing_note"] = "Using Claude Max subscription via CLI - no API charges"
        return s
