"""Command-line entry point. `hermesv2 <subcommand>`."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import click
from rich.console import Console

console = Console()


def _setup_logging(config_path: Path) -> None:
    log_dir = Path("logs")
    try:
        if config_path.exists():
            from hermesv2.agent import load_config
            cfg = load_config(config_path)
            log_dir = Path(cfg["agent"]["log_dir"])
    except Exception:
        pass
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "hermesv2.log"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def _load_agent(config_path: Path):
    from hermesv2.agent import HermesV2, load_config
    if not config_path.exists():
        console.print(f"[red]config not found:[/red] {config_path}")
        console.print("Copy [yellow]config.example.yaml[/yellow] to [yellow]config.yaml[/yellow].")
        sys.exit(2)
    return HermesV2(load_config(config_path))


@click.group(invoke_without_command=True)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=Path("config.yaml"),
    show_default=True,
)
@click.pass_context
def main(ctx: click.Context, config_path: Path) -> None:
    """HermesV2 - personal AI agent driven by Claude Max via the CLI."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    _setup_logging(config_path)
    if ctx.invoked_subcommand is None:
        ctx.invoke(cli_interactive)


@main.command("start")
@click.option("--gateway", "-g", multiple=True, help="restrict to specific gateways")
@click.pass_context
def cmd_start(ctx: click.Context, gateway: tuple[str, ...]) -> None:
    """Start all enabled gateways (daemon mode)."""
    agent = _load_agent(ctx.obj["config_path"])
    try:
        asyncio.run(agent.run(list(gateway) if gateway else None))
    except KeyboardInterrupt:
        console.print("[yellow]interrupted[/yellow]")


@main.command("cli")
@click.pass_context
def cli_interactive(ctx: click.Context) -> None:
    """Open the interactive CLI gateway."""
    agent = _load_agent(ctx.obj["config_path"])
    cfg = agent.config.setdefault("gateways", {}).setdefault("cli", {})
    cfg["enabled"] = True
    try:
        asyncio.run(agent.run(["cli"]))
    except KeyboardInterrupt:
        console.print("[yellow]bye[/yellow]")


@main.command("stop")
def cmd_stop() -> None:
    """Stop a running daemon (signals the PID file if present)."""
    pid_file = Path("data/hermesv2.pid")
    if not pid_file.exists():
        console.print("no PID file found")
        return
    try:
        import os, signal
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
        console.print(f"sent SIGTERM to {pid}")
    except (OSError, ValueError) as e:
        console.print(f"[red]could not stop: {e}[/red]")


@main.command("status")
@click.pass_context
def cmd_status(ctx: click.Context) -> None:
    """Show LLM router + Claude usage stats."""
    agent = _load_agent(ctx.obj["config_path"])
    console.print("[bold]LLM router[/bold]")
    console.print_json(data=agent.router.get_stats())
    console.print("[bold]Claude CLI[/bold]")
    console.print_json(data=agent.claude_runner.get_usage_stats())


@main.command("doctor")
@click.pass_context
def cmd_doctor(ctx: click.Context) -> None:
    """Verify installation, dependencies, and configuration."""
    from hermesv2.doctor import run_all
    ok = run_all(str(ctx.obj["config_path"]))
    sys.exit(0 if ok else 1)


@main.command("setup")
@click.pass_context
def cmd_setup(ctx: click.Context) -> None:
    """Guided initial setup."""
    target = ctx.obj["config_path"]
    example = Path("config.example.yaml")
    if not target.exists() and example.exists():
        target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        console.print(f"[green]created[/green] {target} from example")
    console.print("Next steps:")
    console.print("  1. [yellow]claude login[/yellow]   (authenticate Claude Max)")
    console.print(f"  2. edit [yellow]{target}[/yellow] (Discord token etc.)")
    console.print("  3. [yellow]hermesv2 doctor[/yellow]")


@main.command("config")
@click.option("--print", "do_print", is_flag=True)
@click.pass_context
def cmd_config(ctx: click.Context, do_print: bool) -> None:
    """Show or edit the active config."""
    path = ctx.obj["config_path"]
    if do_print or True:
        console.print(path.read_text(encoding="utf-8"))


@main.group("skills")
def grp_skills() -> None:
    """Skill management."""


@grp_skills.command("list")
@click.pass_context
def skills_list(ctx: click.Context) -> None:
    agent = _load_agent(ctx.obj["config_path"])
    for s in agent.skills.list_skills():
        console.print(f"  [bold]{s.name}[/bold] [dim]({s.trigger})[/dim] — {s.description}")


@grp_skills.command("reload")
@click.pass_context
def skills_reload(ctx: click.Context) -> None:
    agent = _load_agent(ctx.obj["config_path"])
    n = agent.skills.reload()
    console.print(f"reloaded {n} skills")


@grp_skills.command("run")
@click.argument("name")
@click.pass_context
def skills_run(ctx: click.Context, name: str) -> None:
    agent = _load_agent(ctx.obj["config_path"])
    result = asyncio.run(agent.run_skill(name))
    console.print(str(result))


@grp_skills.command("proposals")
@click.pass_context
def skills_proposals(ctx: click.Context) -> None:
    agent = _load_agent(ctx.obj["config_path"])
    for p in agent.learner.list_proposals():
        console.print(f"[{p.id}] {p.name} ({p.trigger}) — {p.description}")


@grp_skills.command("approve")
@click.argument("proposal_id", type=int)
@click.pass_context
def skills_approve(ctx: click.Context, proposal_id: int) -> None:
    agent = _load_agent(ctx.obj["config_path"])
    try:
        path = agent.learner.approve(proposal_id)
        console.print(f"[green]promoted -> {path}[/green]")
    except Exception as e:
        console.print(f"[red]error: {e}[/red]")
        sys.exit(1)


@grp_skills.command("reject")
@click.argument("proposal_id", type=int)
@click.pass_context
def skills_reject(ctx: click.Context, proposal_id: int) -> None:
    agent = _load_agent(ctx.obj["config_path"])
    agent.learner.reject(proposal_id)
    console.print("[green]rejected[/green]")


@main.command("search")
@click.argument("query")
@click.option("--user", default="owner", show_default=True)
@click.pass_context
def cmd_search(ctx: click.Context, query: str, user: str) -> None:
    """Search past conversations (FTS5)."""
    agent = _load_agent(ctx.obj["config_path"])
    hits = agent.memory.search_messages(user, query, limit=20)
    for h in hits:
        console.print(f"[dim]{h['role']}[/dim] {h['content'][:200]}")


@main.command("stats")
@click.pass_context
def cmd_stats(ctx: click.Context) -> None:
    """Show usage statistics."""
    agent = _load_agent(ctx.obj["config_path"])
    console.print_json(data={
        "router": agent.router.get_stats(),
        "claude": agent.claude_runner.get_usage_stats(),
    })


@main.command("update")
def cmd_update() -> None:
    """Pull latest HermesV2 (git)."""
    import subprocess
    try:
        subprocess.run(["git", "pull", "--rebase"], check=True)
        console.print("[green]updated[/green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]update failed:[/red] {e}")


if __name__ == "__main__":
    main()
