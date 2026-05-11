"""Interactive terminal gateway using prompt_toolkit."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown

from hermesv2.gateways.base import Gateway
from hermesv2.messages import IncomingMessage

if TYPE_CHECKING:
    from hermesv2.agent import HermesV2

log = logging.getLogger(__name__)


class CLIGateway(Gateway):
    name = "cli"

    def __init__(self, agent: "HermesV2", config: dict):
        super().__init__(agent, config)
        self.console = Console()
        history_path = agent.data_dir / "cli_history"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        self.session: PromptSession = PromptSession(history=FileHistory(str(history_path)))
        self.user_id = config.get("user_id", "owner")
        self.session_id = "cli"
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.console.print(
            f"[bold cyan]HermesV2[/bold cyan] CLI ready. "
            "Type [yellow]/help[/yellow] for commands, [yellow]/quit[/yellow] to exit.\n"
        )
        with patch_stdout():
            while not self._stop.is_set():
                try:
                    text = await self.session.prompt_async("> ")
                except (EOFError, KeyboardInterrupt):
                    break
                text = text.strip()
                if not text:
                    continue
                if text.startswith("/"):
                    await self._handle_command(text)
                    continue
                await self._dispatch(text)
        self._running = False

    async def stop(self) -> None:
        self._stop.set()
        self._running = False

    async def send(self, channel_id: str, text: str) -> None:
        self.console.print(f"[dim]({channel_id})[/dim] ")
        self.console.print(Markdown(text))

    async def _dispatch(self, text: str) -> None:
        async def _reply(msg: str) -> None:
            self.console.print(Markdown(msg))

        msg = IncomingMessage(
            user_id=self.user_id,
            text=text,
            gateway=self.name,
            channel_id=None,
            session_id=self.session_id,
            is_dm=True,
            reply=_reply,
        )
        try:
            await self.agent.handle(msg)
        except Exception as e:
            log.exception("dispatch failed")
            self.console.print(f"[red]error:[/red] {e}")

    async def _handle_command(self, text: str) -> None:
        parts = text[1:].split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("quit", "exit", "q"):
            self._stop.set()
        elif cmd == "help":
            self.console.print(
                "[bold]Commands:[/bold]\n"
                "  /new            start a new session\n"
                "  /reset          wipe memory for this user\n"
                "  /model <name>   override Claude model (sonnet|opus)\n"
                "  /personality <name>  switch persona\n"
                "  /skills         list available skills\n"
                "  /skill <name>   run a skill\n"
                "  /search <q>     FTS search past messages\n"
                "  /usage          show Claude rate-limiter stats\n"
                "  /stats          show LLM router stats\n"
                "  /proposals      list pending skill proposals\n"
                "  /approve <id>   approve a proposal\n"
                "  /reject <id>    reject a proposal\n"
                "  /quit           exit\n"
            )
        elif cmd == "new":
            from uuid import uuid4
            self.session_id = uuid4().hex[:8]
            self.console.print(f"[green]new session:[/green] {self.session_id}")
        elif cmd == "reset":
            self.console.print("[yellow]Memory reset not implemented (would wipe DB rows).[/yellow]")
        elif cmd == "model":
            if arg:
                self.agent.claude_runner.model = arg
                self.console.print(f"[green]claude model:[/green] {arg}")
        elif cmd == "personality":
            self.agent.memory.set_preference(self.user_id, "personality", arg)
            self.console.print(f"[green]personality:[/green] {arg}")
        elif cmd == "skills":
            for s in self.agent.skills.list_skills():
                self.console.print(f"  [bold]{s.name}[/bold] [dim]({s.trigger})[/dim] — {s.description}")
        elif cmd == "skill":
            if not arg:
                self.console.print("[red]usage: /skill <name>[/red]")
                return
            try:
                result = await self.agent.run_skill(arg)
                self.console.print(Markdown(str(result)))
            except Exception as e:
                self.console.print(f"[red]error:[/red] {e}")
        elif cmd == "search":
            hits = self.agent.memory.search_messages(self.user_id, arg, limit=10)
            for h in hits:
                self.console.print(f"  [dim]{h['role']}[/dim] {h['content'][:120]}")
        elif cmd == "usage":
            self.console.print_json(data=self.agent.claude_runner.get_usage_stats())
        elif cmd == "stats":
            self.console.print_json(data=self.agent.router.get_stats())
        elif cmd == "proposals":
            for p in self.agent.learner.list_proposals():
                self.console.print(
                    f"  [{p.id}] [bold]{p.name}[/bold] [dim]({p.trigger})[/dim] — {p.description}"
                )
        elif cmd == "approve":
            try:
                path = self.agent.learner.approve(int(arg))
                self.console.print(f"[green]approved -> {path}[/green]")
            except Exception as e:
                self.console.print(f"[red]error:[/red] {e}")
        elif cmd == "reject":
            try:
                self.agent.learner.reject(int(arg))
                self.console.print("[green]rejected[/green]")
            except Exception as e:
                self.console.print(f"[red]error:[/red] {e}")
        else:
            self.console.print(f"[red]unknown command:[/red] {cmd}")
