"""Discord gateway. Responds to mentions, DMs, and built-in ! commands."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from hermesv2.gateways.base import Gateway
from hermesv2.messages import IncomingMessage

if TYPE_CHECKING:
    from hermesv2.agent import HermesV2

log = logging.getLogger(__name__)

MAX_MSG = 1900


def _chunk(text: str, size: int = MAX_MSG) -> list[str]:
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    buf = ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > size:
            if buf:
                parts.append(buf)
            buf = line
        else:
            buf += line
    if buf:
        parts.append(buf)
    return parts


class DiscordGateway(Gateway):
    name = "discord"

    def __init__(self, agent: "HermesV2", config: dict):
        super().__init__(agent, config)
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        self.client = discord.Client(intents=intents)
        self.prefix = config.get("prefix", "!")
        self.allowed_users = set(str(u) for u in config.get("allowed_users", []))
        self._task: asyncio.Task | None = None
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    async def on_ready(self) -> None:
        await self.client.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="for marketplace deals",
            )
        )
        log.info("Discord gateway connected as %s", self.client.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.client.user or message.author.bot:
            return
        is_dm = isinstance(message.channel, discord.DMChannel)
        mentions_bot = self.client.user in message.mentions if self.client.user else False
        if not (is_dm or mentions_bot or message.content.startswith(self.prefix)):
            return

        author_id = str(message.author.id)
        if self.allowed_users and author_id not in self.allowed_users:
            return

        content = message.content
        if mentions_bot and self.client.user:
            content = content.replace(f"<@{self.client.user.id}>", "").strip()
            content = content.replace(f"<@!{self.client.user.id}>", "").strip()

        if content.startswith(self.prefix):
            await self._handle_command(message, content[len(self.prefix):].strip())
            return

        async def _reply(text: str) -> None:
            for chunk in _chunk(text):
                await message.channel.send(chunk)

        msg = IncomingMessage(
            user_id=author_id,
            text=content,
            gateway=self.name,
            channel_id=str(message.channel.id),
            session_id=str(message.channel.id),
            is_dm=is_dm,
            mentions_bot=mentions_bot,
            reply=_reply,
            raw=message,
        )
        try:
            await self.agent.handle(msg)
        except Exception:
            log.exception("discord handler failed")
            await message.channel.send("Sorry, something broke. Check logs.")

    async def _handle_command(self, message: discord.Message, raw: str) -> None:
        parts = raw.split(maxsplit=2)
        cmd = parts[0].lower() if parts else ""
        user_id = str(message.author.id)

        if cmd == "status":
            stats = self.agent.claude_runner.get_usage_stats()
            await message.channel.send(
                f"online · claude calls in window: "
                f"{stats['calls_in_window']}/{stats['max_per_window']} · "
                f"api cost: $0.00"
            )
        elif cmd == "remember" and len(parts) >= 3:
            self.agent.memory.remember_fact(user_id, parts[1], parts[2])
            await message.channel.send(f"remembered: {parts[1]} = {parts[2]}")
        elif cmd == "recall" and len(parts) >= 2:
            v = self.agent.memory.recall_fact(user_id, parts[1])
            await message.channel.send(v or f"no fact for {parts[1]!r}")
        elif cmd == "skill" and len(parts) >= 2:
            try:
                result = await self.agent.run_skill(parts[1])
                for chunk in _chunk(str(result)):
                    await message.channel.send(chunk)
            except Exception as e:
                await message.channel.send(f"error: {e}")
        elif cmd == "search" and len(parts) >= 2:
            query = " ".join(parts[1:])
            hits = self.agent.memory.search_messages(user_id, query, limit=5)
            lines = [f"[{h['role']}] {h['content'][:200]}" for h in hits]
            await message.channel.send("\n".join(lines) or "no matches")
        elif cmd == "stats":
            stats = self.agent.router.get_stats()
            lines = [f"{k}: {v}" for k, v in stats.items()]
            await message.channel.send("\n".join(lines))
        else:
            await message.channel.send(
                f"commands: {self.prefix}status, {self.prefix}remember <key> <value>, "
                f"{self.prefix}recall <key>, {self.prefix}skill <name>, "
                f"{self.prefix}search <query>, {self.prefix}stats"
            )

    async def start(self) -> None:
        if self._running:
            return
        token = self.config.get("token")
        if not token or token.startswith("$"):
            raise RuntimeError("Discord token missing; set DISCORD_BOT_TOKEN")
        self._running = True
        self._task = asyncio.create_task(self.client.start(token))

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        await self.client.close()
        if self._task:
            try:
                await self._task
            except Exception:
                pass

    async def send(self, channel_id: str, text: str) -> None:
        try:
            channel = self.client.get_channel(int(channel_id)) or await self.client.fetch_channel(int(channel_id))
        except (ValueError, discord.NotFound, discord.Forbidden) as e:
            log.error("cannot deliver to discord channel %s: %s", channel_id, e)
            return
        for chunk in _chunk(text):
            await channel.send(chunk)
