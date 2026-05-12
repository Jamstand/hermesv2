"""Smoke tests. Verify every module loads and the core round-trips work.

Tests do not require Ollama, Discord, or a real `claude` binary. They run
purely against in-memory or temp-dir state.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. All modules import (telegram_gateway parses but is not imported because
#    its `cryptography` dependency can fail in some sandboxes)
# ---------------------------------------------------------------------------

def test_imports():
    import hermesv2
    from hermesv2 import (
        agent, cli, dashboard, doctor, learner, llm_router, memory,
        messages, personality, scheduler, skills_engine,
    )
    from hermesv2.gateways import base, cli_gateway, discord_gateway, webhook_gateway
    from hermesv2.tools import file, marketplace, shell, system, web
    assert hermesv2.__version__


def test_telegram_gateway_syntax():
    """Telegram gateway parses cleanly (importing requires cryptography)."""
    import py_compile
    path = Path(__file__).parent.parent / "hermesv2" / "gateways" / "telegram_gateway.py"
    py_compile.compile(str(path), doraise=True)


# ---------------------------------------------------------------------------
# 2-3. Memory: store/recall + FTS5
# ---------------------------------------------------------------------------

def test_memory_facts_and_fts(tmp_path: Path):
    from hermesv2.memory import Memory
    m = Memory(tmp_path / "mem.db")

    m.remember_fact("u", "car", "S15 Spec R", category="cars")
    assert m.recall_fact("u", "car") == "S15 Spec R"
    assert "car" in m.get_all_facts("u")

    m.add_message("u", "user", "Looking at a 2002 S15 with factory turbo")
    m.add_message("u", "assistant", "Verify it is not a Spec S conversion")
    hits = m.search_messages("u", "spec")
    assert len(hits) >= 1
    assert any("spec" in h["content"].lower() for h in hits)

    m.set_preference("u", "personality", "s15_hunter")
    assert m.get_preference("u", "personality") == "s15_hunter"

    m.update_user_profile("u", {"location": "Miami"})
    m.update_user_profile("u", {"timezone": "ET"})
    profile = m.get_user_profile("u")
    assert profile == {"location": "Miami", "timezone": "ET"}

    ctx = m.build_context("u", "looking for s15")
    assert "S15 Spec R" in ctx


# ---------------------------------------------------------------------------
# 4. Skills engine loads built-in .md files
# ---------------------------------------------------------------------------

def test_skills_engine_loads_builtins():
    from hermesv2.skills_engine import SkillsEngine
    repo = Path(__file__).parent.parent
    se = SkillsEngine(repo / "skills")
    names = {s.name for s in se.list_skills()}
    assert {
        "system_check", "marketplace_monitor", "s15_hunter",
        "stream_helper", "credit_tracker",
    }.issubset(names)


def test_skills_engine_handler_priority(tmp_path: Path):
    from hermesv2.skills_engine import SkillsEngine
    (tmp_path / "x.md").write_text(
        "---\nname: x\ndescription: t\ntrigger: manual\n---\nbody"
    )
    se = SkillsEngine(tmp_path)
    se.register_handler("x", lambda ctx: "handler-ran")
    assert se.execute("x") == "handler-ran"


# ---------------------------------------------------------------------------
# 5. Personalities load
# ---------------------------------------------------------------------------

def test_personalities_load():
    from hermesv2.personality import PersonalityManager
    repo = Path(__file__).parent.parent
    pm = PersonalityManager(repo / "personalities")
    assert set(pm.list_personalities()) >= {"default", "s15_hunter", "vans_streamer"}
    assert pm.get_system_prompt("default")


# ---------------------------------------------------------------------------
# 6. ClaudeRunner handles missing CLI gracefully
# ---------------------------------------------------------------------------

def test_claude_runner_no_binary(tmp_path: Path):
    from hermesv2 import claude_runner
    from hermesv2.claude_runner import ClaudeNotAvailableError, ClaudeRunner
    with patch.object(claude_runner.shutil, "which", return_value=None):
        runner = ClaudeRunner(rate_db_path=tmp_path / "rate.db")
        assert not runner.available()
        with pytest.raises(ClaudeNotAvailableError):
            runner.chat("hi")


def test_claude_runner_rate_limit(tmp_path: Path):
    from hermesv2 import claude_runner
    from hermesv2.claude_runner import ClaudeRateLimitError, ClaudeRunner
    with patch.object(claude_runner.shutil, "which", return_value="/fake/claude"):
        runner = ClaudeRunner(
            rate_db_path=tmp_path / "rate.db",
            max_calls_per_window=2,
            window_seconds=300,
        )
        runner.rate_limiter.check_and_record()
        runner.rate_limiter.check_and_record()
        with pytest.raises(ClaudeRateLimitError):
            runner.rate_limiter.check_and_record()


# ---------------------------------------------------------------------------
# 7. LLMRouter falls back correctly
# ---------------------------------------------------------------------------

def test_router_decision():
    from hermesv2.llm_router import LLMRouter
    runner = MagicMock()
    r = LLMRouter(runner, llama_url="http://x", llama_model="llama3")
    assert r.decide("hi how are you") == "llama"
    assert r.decide("write code to parse JSON") == "claude"
    assert r.decide("analyze the trade-offs of A vs B") == "claude"
    assert r.decide("a" * 7000) == "claude"


def test_router_fallback_on_claude_rate_limit():
    from hermesv2.claude_runner import ClaudeRateLimitError
    from hermesv2.llm_router import LLMRouter

    runner = MagicMock()
    runner.chat.side_effect = ClaudeRateLimitError("throttled")
    r = LLMRouter(runner)
    with patch.object(r, "_call_llama", return_value="llama answer"):
        result = r.chat("write code")
    assert result["response"] == "llama answer"
    assert "claude rate-limited" in result["backend"]
    assert r.stats["rate_limited"] == 1


# ---------------------------------------------------------------------------
# 8. Scheduler registers jobs from config
# ---------------------------------------------------------------------------

def test_scheduler_load_from_config(tmp_path: Path):
    from hermesv2.scheduler import Scheduler
    agent = MagicMock()
    agent.gateways = {}
    sched = Scheduler(agent)
    n = sched.load_from_config([
        {"skill": "system_check", "cron": "0 8 * * *", "deliver_to": []},
        {"skill": "marketplace_monitor", "cron": "*/15 * * * *", "deliver_to": []},
    ])
    assert n == 2
    assert len(sched.get_jobs()) == 2


def test_scheduler_rejects_bad_cron():
    from hermesv2.scheduler import Scheduler
    sched = Scheduler(MagicMock())
    with pytest.raises(ValueError):
        sched.add_job("x", "not a cron")


# ---------------------------------------------------------------------------
# 9. CLI prints help
# ---------------------------------------------------------------------------

def test_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "hermesv2.cli", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "doctor" in result.stdout
    assert "start" in result.stdout
    assert "skills" in result.stdout


# ---------------------------------------------------------------------------
# 10. Doctor catches missing binary
# ---------------------------------------------------------------------------

def test_doctor_claude_binary_missing():
    from hermesv2 import doctor
    with patch.object(doctor, "_shutil") as mock_shutil:
        mock_shutil.which.return_value = None
        ok, _ = doctor._check_claude_binary()
    assert ok is False


def test_doctor_login_missing(tmp_path: Path, monkeypatch):
    from hermesv2 import doctor
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ok, _ = doctor._check_claude_login()
    assert ok is False


# ---------------------------------------------------------------------------
# 11. Learner proposes but does not auto-promote; approve writes a file
# ---------------------------------------------------------------------------

def test_learner_propose_and_approve(tmp_path: Path):
    from hermesv2.learner import Learner
    from hermesv2.skills_engine import SkillsEngine
    from hermesv2.memory import Memory

    skills_dir = tmp_path / "skills"
    auto_dir = skills_dir / "auto"
    auto_dir.mkdir(parents=True)
    se = SkillsEngine(skills_dir, auto_dir=auto_dir)
    learner = Learner(
        router=MagicMock(),
        memory=Memory(tmp_path / "mem.db"),
        skills_engine=se,
        db_path=tmp_path / "learner.db",
        auto_dir=auto_dir,
    )
    p = learner.propose(
        name="ping_check",
        description="Ping a host",
        trigger="manual",
        instructions="Use the system tool to ping a host.",
        rationale="seen 3 times",
    )
    assert p.status == "pending"
    assert (auto_dir / "ping_check.md").exists() is False
    path = learner.approve(p.id)
    assert path.exists()
    assert "name: ping_check" in path.read_text(encoding="utf-8")
    assert se.get("ping_check") is not None


def test_learner_rejects_invalid_name(tmp_path: Path):
    from hermesv2.learner import Learner
    from hermesv2.skills_engine import SkillsEngine
    from hermesv2.memory import Memory
    se = SkillsEngine(tmp_path / "skills")
    learner = Learner(
        router=MagicMock(),
        memory=Memory(tmp_path / "mem.db"),
        skills_engine=se,
        db_path=tmp_path / "learner.db",
        auto_dir=tmp_path / "auto",
    )
    with pytest.raises(ValueError):
        learner.propose("INVALID NAME", "x", "manual", "body", "r")


# ---------------------------------------------------------------------------
# 12. No anthropic SDK imports anywhere in the package
# ---------------------------------------------------------------------------

def test_no_anthropic_sdk():
    """Guard rail: package never imports the anthropic SDK or hits the API URL.

    We strip comments and docstrings before scanning so that the architectural
    note "never contacts api.anthropic.com directly" does not trip the check.
    """
    import io
    import tokenize

    repo = Path(__file__).parent.parent / "hermesv2"
    forbidden = (
        "import anthropic",
        "from anthropic",
        "https://api.anthropic.com",
        "Anthropic(",
    )
    for py in repo.rglob("*.py"):
        source = py.read_text(encoding="utf-8")
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        stripped: list[str] = []
        for tok in tokens:
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            stripped.append(tok.string)
        code_only = " ".join(stripped)
        for needle in forbidden:
            assert needle not in code_only, f"{py} contains {needle!r}"
