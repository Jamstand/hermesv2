"""HTMX status page mounted on the webhook gateway's FastAPI app."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

if TYPE_CHECKING:
    from hermesv2.agent import HermesV2


_BASE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>HermesV2</title>
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif;
           background: #0e1116; color: #e6e6e6; margin: 0; padding: 2em; }
    h1 { color: #7aa2f7; margin-top: 0; }
    h2 { color: #bb9af7; border-bottom: 1px solid #292e3a; padding-bottom: .3em; }
    .panel { background: #161b22; border: 1px solid #292e3a; border-radius: 8px;
             padding: 1em; margin-bottom: 1em; }
    .stat { display: inline-block; min-width: 180px; padding: .5em 1em;
            background: #1c2230; border-radius: 6px; margin: .2em; }
    .stat .label { color: #889099; font-size: .85em; }
    .stat .value { color: #f7768e; font-size: 1.4em; font-weight: 600; }
    pre { background: #0a0d12; padding: 1em; border-radius: 6px; overflow-x: auto; }
    .skill, .pers { padding: .3em 0; }
    .skill .name { color: #9ece6a; font-weight: 600; }
    .pill { display: inline-block; padding: .1em .5em; border-radius: 999px;
            background: #292e3a; color: #889099; font-size: .75em; margin-left: .5em; }
  </style>
</head>
<body>
  <h1>HermesV2 dashboard</h1>
  <p style="color:#889099">owner: {owner} · version: {version} · live status below</p>

  <div class="panel">
    <h2>LLM router</h2>
    <div id="stats" hx-get="/dashboard/stats" hx-trigger="load, every 5s" hx-swap="innerHTML"></div>
  </div>

  <div class="panel">
    <h2>Claude CLI usage</h2>
    <div id="claude" hx-get="/dashboard/claude" hx-trigger="load, every 5s" hx-swap="innerHTML"></div>
  </div>

  <div class="panel">
    <h2>Skills ({skill_count})</h2>
    <div id="skills" hx-get="/dashboard/skills" hx-trigger="load" hx-swap="innerHTML"></div>
  </div>

  <div class="panel">
    <h2>Personalities ({pers_count})</h2>
    <div id="pers" hx-get="/dashboard/personalities" hx-trigger="load" hx-swap="innerHTML"></div>
  </div>

  <div class="panel">
    <h2>Pending skill proposals</h2>
    <div id="proposals" hx-get="/dashboard/proposals" hx-trigger="load, every 30s" hx-swap="innerHTML"></div>
  </div>
</body>
</html>"""


def _stat(label: str, value) -> str:
    return f'<div class="stat"><div class="label">{label}</div><div class="value">{value}</div></div>'


def attach(app: FastAPI, agent: "HermesV2") -> None:
    @app.get("/", response_class=HTMLResponse)
    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> str:
        from hermesv2 import __version__
        return _BASE.format(
            owner=agent.config.get("agent", {}).get("owner", "owner"),
            version=__version__,
            skill_count=len(agent.skills.list_skills()),
            pers_count=len(agent.personality.list_personalities()),
        )

    @app.get("/dashboard/stats", response_class=HTMLResponse)
    async def stats_frag() -> str:
        s = agent.router.get_stats()
        return "".join([
            _stat("Total calls", s["total_calls"]),
            _stat("Llama", f'{s["llama_calls"]} ({s["llama_percentage"]}%)'),
            _stat("Claude Max", f'{s["claude_max_calls"]} ({s["claude_percentage"]}%)'),
            _stat("Rate-limited", s["rate_limited"]),
            _stat("API cost", s["billing_note"]),
        ])

    @app.get("/dashboard/claude", response_class=HTMLResponse)
    async def claude_frag() -> str:
        s = agent.claude_runner.get_usage_stats()
        return "".join([
            _stat("CLI binary", s.get("binary") or "MISSING"),
            _stat("Default model", s["model"]),
            _stat("In window",
                  f'{s["calls_in_window"]}/{s["max_per_window"]}'),
            _stat("Total calls", s["total_calls"]),
            _stat("Errors", s["errors"]),
            _stat("Avg duration",
                  f'{s["avg_duration_ms"]} ms' if s["avg_duration_ms"] else "n/a"),
        ])

    @app.get("/dashboard/skills", response_class=HTMLResponse)
    async def skills_frag() -> str:
        lines = []
        for s in agent.skills.list_skills():
            lines.append(
                f'<div class="skill"><span class="name">{s.name}</span>'
                f'<span class="pill">{s.trigger}</span> — {s.description}</div>'
            )
        return "".join(lines) or "<em>no skills loaded</em>"

    @app.get("/dashboard/personalities", response_class=HTMLResponse)
    async def pers_frag() -> str:
        names = agent.personality.list_personalities()
        return "".join(f'<div class="pers">{n}</div>' for n in names) or "<em>none</em>"

    @app.get("/dashboard/proposals", response_class=HTMLResponse)
    async def proposals_frag() -> str:
        ps = agent.learner.list_proposals()
        if not ps:
            return "<em>no pending proposals</em>"
        lines = []
        for p in ps:
            lines.append(
                f'<div class="skill">[{p.id}] <span class="name">{p.name}</span>'
                f'<span class="pill">{p.trigger}</span> — {p.description}</div>'
            )
        return "".join(lines)
