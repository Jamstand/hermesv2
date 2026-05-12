"""Pre-flight checks. `hermesv2 doctor` invokes run_all()."""

from __future__ import annotations

import importlib
import logging
import os
import shutil as _shutil
import subprocess
import sys
from pathlib import Path

import requests
import yaml
from rich.console import Console
from rich.table import Table

log = logging.getLogger(__name__)


REQUIRED_PACKAGES = (
    "discord",
    "telegram",
    "fastapi",
    "uvicorn",
    "prompt_toolkit",
    "requests",
    "bs4",
    "psutil",
    "yaml",
    "apscheduler",
    "ddgs",
    "aiohttp",
    "click",
    "rich",
    "watchdog",
    "jinja2",
)


def _check_python() -> tuple[bool, str]:
    v = sys.version_info
    ok = v >= (3, 10)
    return ok, f"{v.major}.{v.minor}.{v.micro}"


def _check_packages() -> tuple[bool, str]:
    """Each package check runs in a subprocess so a crashing C extension
    (e.g. cryptography on a broken libffi) does not abort the whole doctor."""
    missing = []
    broken = []
    for pkg in REQUIRED_PACKAGES:
        result = subprocess.run(
            [sys.executable, "-c", f"import {pkg}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            stderr = result.stderr.lower()
            if "no module named" in stderr:
                missing.append(pkg)
            else:
                broken.append(pkg)
    bits = []
    if missing:
        bits.append("missing: " + ", ".join(missing))
    if broken:
        bits.append("broken: " + ", ".join(broken))
    if bits:
        return False, " · ".join(bits)
    return True, f"{len(REQUIRED_PACKAGES)} packages present"


def _check_claude_binary() -> tuple[bool, str]:
    path = _shutil.which("claude")
    if not path:
        return False, "not on PATH — `npm install -g @anthropic-ai/claude-code`"
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0, f"{path} ({r.stdout.strip() or 'no version'})"
    except subprocess.TimeoutExpired:
        return False, f"{path} (--version timed out)"


def _check_claude_login() -> tuple[bool, str]:
    candidates = [
        Path.home() / ".claude" / ".credentials.json",
        Path.home() / ".config" / "claude" / ".credentials.json",
    ]
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            return True, str(c)
    return False, "no credentials file — run `claude login`"


def _check_ollama(url: str) -> tuple[bool, str]:
    try:
        r = requests.get(f"{url.rstrip('/')}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        return True, f"{url} · models: {', '.join(models) or '(none pulled)'}"
    except requests.RequestException as e:
        return False, f"unreachable at {url}: {e}"


def _check_llama_model(url: str, model: str) -> tuple[bool, str]:
    try:
        r = requests.get(f"{url.rstrip('/')}/api/tags", timeout=5)
        r.raise_for_status()
        names = [m["name"] for m in r.json().get("models", [])]
        if any(model in n for n in names):
            return True, f"{model} pulled"
        return False, f"`ollama pull {model}` not run (have: {names})"
    except requests.RequestException as e:
        return False, f"cannot reach ollama: {e}"


def _check_config(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, f"{path} missing — copy from config.example.yaml"
    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
        return True, str(path)
    except yaml.YAMLError as e:
        return False, f"invalid YAML: {e}"


def _check_writable(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".doctor_probe"
        probe.write_text("ok")
        probe.unlink()
        return True, str(path)
    except OSError as e:
        return False, f"{path}: {e}"


def _check_discord_token(cfg: dict) -> tuple[bool, str]:
    d = cfg.get("gateways", {}).get("discord", {})
    if not d.get("enabled"):
        return True, "discord disabled"
    token = d.get("token", "")
    if not token or token.startswith("${"):
        return False, "DISCORD_BOT_TOKEN not set"
    return True, "token present"


def _check_memory_db(cfg: dict) -> tuple[bool, str]:
    db = Path(cfg.get("memory", {}).get("db_path", "data/memory.db"))
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS _probe (x INTEGER)")
        conn.commit()
        conn.close()
        return True, str(db)
    except Exception as e:
        return False, f"{db}: {e}"


def run_all(config_path: str = "config.yaml") -> bool:
    console = Console()
    cfg_path = Path(config_path)
    cfg: dict = {}
    if cfg_path.exists():
        try:
            from hermesv2.agent import load_config
            cfg = load_config(cfg_path)
        except Exception:
            cfg = {}

    llama_url = cfg.get("llm", {}).get("llama_url", "http://localhost:11434")
    llama_model = cfg.get("llm", {}).get("llama_model", "llama3")
    data_dir = Path(cfg.get("agent", {}).get("data_dir", "data"))
    log_dir = Path(cfg.get("agent", {}).get("log_dir", "logs"))

    checks = [
        ("Python 3.10+", _check_python()),
        ("Required packages", _check_packages()),
        ("`claude` CLI", _check_claude_binary()),
        ("`claude` login", _check_claude_login()),
        ("Ollama reachable", _check_ollama(llama_url)),
        (f"Llama model `{llama_model}`", _check_llama_model(llama_url, llama_model)),
        ("config.yaml", _check_config(cfg_path)),
        ("data/ writable", _check_writable(data_dir)),
        ("logs/ writable", _check_writable(log_dir)),
        ("Discord token", _check_discord_token(cfg)),
        ("Memory DB", _check_memory_db(cfg)),
    ]

    table = Table(title="HermesV2 doctor", show_lines=False)
    table.add_column("Check", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Detail", style="dim")
    overall_ok = True
    for label, (ok, detail) in checks:
        if ok:
            table.add_row(label, "[green]PASS[/green]", detail)
        else:
            overall_ok = False
            table.add_row(label, "[red]FAIL[/red]", detail)
    console.print(table)
    if overall_ok:
        console.print("[bold green]All checks passed.[/bold green]")
    else:
        console.print("[bold red]Some checks failed. See details above.[/bold red]")
    return overall_ok
