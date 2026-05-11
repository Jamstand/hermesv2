"""Main orchestrator. Wires every subsystem together."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
from pathlib import Path
from typing import Any

import yaml

from hermesv2.claude_runner import ClaudeRunner
from hermesv2.gateways.base import Gateway
from hermesv2.learner import Learner
from hermesv2.llm_router import LLMRouter
from hermesv2.memory import Memory
from hermesv2.messages import IncomingMessage
from hermesv2.personality import PersonalityManager
from hermesv2.scheduler import Scheduler
from hermesv2.skills_engine import SkillsEngine

log = logging.getLogger(__name__)

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(obj: Any) -> Any:
    if isinstance(obj, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), obj)
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    return obj


def load_config(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    return _expand_env(yaml.safe_load(raw))


class HermesV2:
    """Top-level agent. Build with `HermesV2(config)`; start with `await run()`."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        agent_cfg = config.get("agent", {})
        self.name = agent_cfg.get("name", "HermesV2")
        self.owner = agent_cfg.get("owner", "owner")
        self.data_dir = Path(agent_cfg.get("data_dir", "data"))
        self.log_dir = Path(agent_cfg.get("log_dir", "logs"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        mem_cfg = config.get("memory", {})
        self.memory = Memory(mem_cfg.get("db_path", str(self.data_dir / "memory.db")))

        llm_cfg = config.get("llm", {})
        claude_cfg = llm_cfg.get("claude", {})
        self.claude_runner = ClaudeRunner(
            rate_db_path=self.data_dir / "rate.db",
            model=claude_cfg.get("model", "sonnet"),
            timeout=claude_cfg.get("timeout", 300),
            max_calls_per_window=claude_cfg.get("max_calls_per_window", 30),
            window_seconds=claude_cfg.get("window_seconds", 300),
        )
        self.router = LLMRouter(
            claude_runner=self.claude_runner,
            llama_url=llm_cfg.get("llama_url", "http://localhost:11434"),
            llama_model=llm_cfg.get("llama_model", "llama3"),
        )

        skills_cfg = config.get("skills", {})
        self.skills = SkillsEngine(
            skills_cfg.get("dir", "skills"),
            auto_dir=skills_cfg.get("auto_dir", "skills/auto"),
        )
        self._register_skill_handlers()
        self._hot_reload_observer = None
        if skills_cfg.get("hot_reload", True):
            self._start_hot_reload()

        pers_cfg = config.get("personality", {})
        self.personality = PersonalityManager(
            pers_cfg.get("dir", "personalities"),
            default=pers_cfg.get("default", "default"),
        )

        learn_cfg = config.get("learner", {})
        self.learner = Learner(
            router=self.router,
            memory=self.memory,
            skills_engine=self.skills,
            db_path=self.data_dir / "learner.db",
            max_auto_skills=skills_cfg.get("max_auto_skills", 50),
            max_proposals_per_day=skills_cfg.get("max_proposals_per_day", 5),
            auto_dir=skills_cfg.get("auto_dir", "skills/auto"),
            enabled=learn_cfg.get("enabled", True),
        )

        self.scheduler = Scheduler(self)
        sched_cfg = config.get("scheduler", {})
        if sched_cfg.get("enabled", True):
            self.scheduler.load_from_config(sched_cfg.get("jobs", []))

        self.gateways: dict[str, Gateway] = {}
        self._claude_semaphore = asyncio.Semaphore(1)

    def _register_skill_handlers(self) -> None:
        """Hook Python implementations to a few built-in skills."""
        from hermesv2.tools import system as sys_tool

        def _system_check_handler(_ctx: dict[str, Any]) -> str:
            status = sys_tool.get_pi_status()
            lines = ["**System status**"]
            cpu_emoji = "🟢" if status["cpu_percent"] < 80 else "⚠️"
            mem_emoji = "🟢" if status["memory_percent"] < 80 else "⚠️"
            disk_emoji = "🟢" if status["disk_percent"] < 85 else "⚠️"
            temp_v = status["temperature_c"]
            if temp_v is None:
                temp_emoji = "❔"
                temp_str = "n/a"
            elif temp_v < 70:
                temp_emoji = "🟢"
                temp_str = f"{temp_v}°C"
            else:
                temp_emoji = "❌"
                temp_str = f"{temp_v}°C"
            net_emoji = "🟢" if status["network_ok"] else "❌"
            lines.append(f"{cpu_emoji} CPU: {status['cpu_percent']}% ({status['cpu_count']} cores)")
            lines.append(f"{mem_emoji} RAM: {status['memory_used_gb']}/{status['memory_total_gb']} GB ({status['memory_percent']}%)")
            lines.append(f"{disk_emoji} Disk: {status['disk_used_gb']}/{status['disk_total_gb']} GB ({status['disk_percent']}%)")
            lines.append(f"{temp_emoji} Temp: {temp_str}")
            lines.append(f"{net_emoji} Network: {'up' if status['network_ok'] else 'down'}")
            lines.append(f"⏱️  Uptime: {status['uptime_hours']}h")
            for svc in ("ssh", "ollama"):
                state = sys_tool.get_service_status(svc)
                icon = "🟢" if state == "active" else "❌"
                lines.append(f"{icon} {svc}: {state}")
            return "\n".join(lines)

        self.skills.register_handler("system_check", _system_check_handler)

    def _start_hot_reload(self) -> None:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            log.info("watchdog not installed; hot reload disabled")
            return

        engine = self.skills

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory:
                    return
                if str(event.src_path).endswith(".md"):
                    log.info("skills changed (%s); reloading", event.src_path)
                    try:
                        engine.reload()
                    except Exception:
                        log.exception("hot reload failed")

        observer = Observer()
        observer.schedule(_Handler(), str(self.skills.skills_dir), recursive=True)
        observer.daemon = True
        observer.start()
        self._hot_reload_observer = observer
        log.info("hot reload watching %s", self.skills.skills_dir)

    async def handle(self, msg: IncomingMessage) -> None:
        """Main message handler. Routes through memory + LLM + skills."""
        self.memory.add_message(
            msg.user_id, "user", msg.text,
            channel_id=msg.channel_id, session_id=msg.session_id,
            metadata={"gateway": msg.gateway},
        )
        context = self.memory.build_context(msg.user_id, msg.text)
        pers_pref = self.memory.get_preference(msg.user_id, "personality")
        system_prompt = self.personality.get_system_prompt(pers_pref)
        full_prompt = msg.text
        if context:
            full_prompt = f"{context}\n\n# Current message\n{msg.text}"

        loop = asyncio.get_running_loop()
        async with self._claude_semaphore:
            result = await loop.run_in_executor(
                None,
                lambda: self.router.chat(full_prompt, system=system_prompt or None),
            )
        response = result["response"]
        self.memory.add_message(
            msg.user_id, "assistant", response,
            channel_id=msg.channel_id, session_id=msg.session_id,
            metadata={"gateway": msg.gateway, "backend": result["backend"]},
        )
        if msg.reply:
            await msg.reply(response)

    async def run_skill(
        self, name: str, context: dict[str, Any] | None = None
    ) -> Any:
        """Run a skill. If it returns markdown (no handler), pass through the LLM."""
        result = self.skills.execute(name, context or {})
        if isinstance(result, str) and not self.skills.get(name).handler:
            loop = asyncio.get_running_loop()
            async with self._claude_semaphore:
                routed = await loop.run_in_executor(
                    None, lambda: self.router.chat(result)
                )
            return routed["response"]
        return result

    def register_gateway(self, gateway: Gateway) -> None:
        self.gateways[gateway.name] = gateway

    def build_gateways(self) -> None:
        cfg = self.config.get("gateways", {})

        if cfg.get("cli", {}).get("enabled", False):
            from hermesv2.gateways.cli_gateway import CLIGateway
            self.register_gateway(CLIGateway(self, cfg["cli"]))

        if cfg.get("discord", {}).get("enabled", False):
            from hermesv2.gateways.discord_gateway import DiscordGateway
            self.register_gateway(DiscordGateway(self, cfg["discord"]))

        if cfg.get("telegram", {}).get("enabled", False):
            from hermesv2.gateways.telegram_gateway import TelegramGateway
            self.register_gateway(TelegramGateway(self, cfg["telegram"]))

        if cfg.get("webhook", {}).get("enabled", False):
            from hermesv2.gateways.webhook_gateway import WebhookGateway
            self.register_gateway(WebhookGateway(self, cfg["webhook"]))

    async def run(self, gateways: list[str] | None = None) -> None:
        """Start every enabled gateway + the scheduler, run until SIGINT."""
        self.build_gateways()
        targets = [g for n, g in self.gateways.items() if not gateways or n in gateways]
        if not targets:
            raise RuntimeError("no gateways enabled")
        self.scheduler.start()

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass

        await asyncio.gather(*(g.start() for g in targets), return_exceptions=True)
        try:
            await stop_event.wait()
        finally:
            await asyncio.gather(
                *(g.stop() for g in targets), return_exceptions=True
            )
            self.scheduler.stop()
            if self._hot_reload_observer is not None:
                self._hot_reload_observer.stop()
                self._hot_reload_observer.join(timeout=2)
