"""Telegram gateway via python-telegram-bot v21."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from hermesv2.gateways.base import Gateway
from hermesv2.messages import IncomingMessage

if TYPE_CHECKING:
    from hermesv2.agent import HermesV2

log = logging.getLogger(__name__)

MAX_MSG = 4000


def _chunk(text: str, size: int = MAX_MSG) -> list[str]:
    if len(text) <= size:
        return [text]
    parts = []
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


class TelegramGateway(Gateway):
    name = "telegram"

    def __init__(self, agent: "HermesV2", config: dict):
        super().__init__(agent, config)
        self.allowed_chat_ids = set(str(c) for c in config.get("allowed_chat_ids", []))
        self.app: Application | None = None

    def _build_app(self) -> Application:
        token = self.config.get("token")
        if not token or token.startswith("$"):
            raise RuntimeError("Telegram token missing; set TELEGRAM_BOT_TOKEN")
        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("remember", self._cmd_remember))
        app.add_handler(CommandHandler("recall", self._cmd_recall))
        app.add_handler(CommandHandler("skill", self._cmd_skill))
        app.add_handler(CommandHandler("search", self._cmd_search))
        app.add_handler(CommandHandler("stats", self._cmd_stats))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        return app

    async def start(self) -> None:
        if self._running:
            return
        self.app = self._build_app()
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        self._running = True
        log.info("Telegram gateway started")

    async def stop(self) -> None:
        if not self.app:
            return
        self._running = False
        if self.app.updater:
            await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

    async def send(self, channel_id: str, text: str) -> None:
        if not self.app:
            return
        for chunk in _chunk(text):
            await self.app.bot.send_message(
                chat_id=int(channel_id),
                text=chunk,
                parse_mode=ParseMode.MARKDOWN,
            )

    def _allowed(self, chat_id: int) -> bool:
        if not self.allowed_chat_ids:
            return True
        return str(chat_id) in self.allowed_chat_ids

    async def _on_message(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_chat:
            return
        chat_id = update.effective_chat.id
        if not self._allowed(chat_id):
            return
        user_id = str(update.effective_user.id) if update.effective_user else str(chat_id)

        async def _reply(text: str) -> None:
            for chunk in _chunk(text):
                await update.message.reply_text(chunk)

        msg = IncomingMessage(
            user_id=user_id,
            text=update.message.text or "",
            gateway=self.name,
            channel_id=str(chat_id),
            session_id=str(chat_id),
            is_dm=update.effective_chat.type == "private",
            reply=_reply,
            raw=update,
        )
        try:
            await self.agent.handle(msg)
        except Exception:
            log.exception("telegram handler failed")
            await update.message.reply_text("Sorry, something broke. Check logs.")

    async def _cmd_status(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        stats = self.agent.claude_runner.get_usage_stats()
        await update.message.reply_text(
            f"online · claude {stats['calls_in_window']}/{stats['max_per_window']} · $0.00"
        )

    async def _cmd_remember(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args or len(ctx.args) < 2:
            await update.message.reply_text("usage: /remember <key> <value>")
            return
        user_id = str(update.effective_user.id)
        key, value = ctx.args[0], " ".join(ctx.args[1:])
        self.agent.memory.remember_fact(user_id, key, value)
        await update.message.reply_text(f"remembered: {key} = {value}")

    async def _cmd_recall(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await update.message.reply_text("usage: /recall <key>")
            return
        user_id = str(update.effective_user.id)
        v = self.agent.memory.recall_fact(user_id, ctx.args[0])
        await update.message.reply_text(v or f"no fact for {ctx.args[0]!r}")

    async def _cmd_skill(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await update.message.reply_text("usage: /skill <name>")
            return
        try:
            result = await self.agent.run_skill(ctx.args[0])
            for chunk in _chunk(str(result)):
                await update.message.reply_text(chunk)
        except Exception as e:
            await update.message.reply_text(f"error: {e}")

    async def _cmd_search(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await update.message.reply_text("usage: /search <query>")
            return
        user_id = str(update.effective_user.id)
        hits = self.agent.memory.search_messages(user_id, " ".join(ctx.args), limit=5)
        lines = [f"[{h['role']}] {h['content'][:200]}" for h in hits]
        await update.message.reply_text("\n".join(lines) or "no matches")

    async def _cmd_stats(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        stats = self.agent.router.get_stats()
        lines = [f"{k}: {v}" for k, v in stats.items()]
        await update.message.reply_text("\n".join(lines))
