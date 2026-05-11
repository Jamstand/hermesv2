"""FastAPI HTTP gateway. Generic /webhook/<source> endpoints + Slack adapter."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request

from hermesv2.gateways.base import Gateway
from hermesv2.messages import IncomingMessage

if TYPE_CHECKING:
    from hermesv2.agent import HermesV2

log = logging.getLogger(__name__)


class WebhookGateway(Gateway):
    name = "webhook"

    def __init__(self, agent: "HermesV2", config: dict):
        super().__init__(agent, config)
        self.port = int(config.get("port", 8080))
        self.api_key = config.get("api_key", "")
        self._task: asyncio.Task | None = None
        self.app = FastAPI(title="HermesV2 webhook gateway")
        self._setup_routes()
        if config.get("dashboard", True):
            from hermesv2.dashboard import attach as attach_dashboard

            attach_dashboard(self.app, agent)

    def _check_key(self, header_value: str | None) -> None:
        if not self.api_key or self.api_key.startswith("$"):
            return  # auth disabled
        if header_value != self.api_key:
            raise HTTPException(status_code=401, detail="bad api key")

    def _setup_routes(self) -> None:
        @self.app.get("/health")
        async def health():
            return {"ok": True}

        @self.app.post("/webhook/{source}")
        async def hook(
            source: str,
            request: Request,
            x_api_key: str | None = Header(default=None),
        ):
            self._check_key(x_api_key)
            body = await request.json()
            return await self._dispatch_generic(source, body)

        @self.app.post("/slack/events")
        async def slack_events(
            request: Request,
            x_api_key: str | None = Header(default=None),
        ):
            body = await request.json()
            if body.get("type") == "url_verification":
                return {"challenge": body.get("challenge")}
            self._check_key(x_api_key)
            event = body.get("event", {})
            if event.get("type") != "message" or event.get("bot_id"):
                return {"ok": True}
            return await self._dispatch_generic(
                "slack",
                {
                    "user_id": event.get("user", "unknown"),
                    "text": event.get("text", ""),
                    "channel_id": event.get("channel"),
                },
            )

    async def _dispatch_generic(self, source: str, body: dict) -> dict:
        text = body.get("text") or body.get("message") or ""
        user_id = str(body.get("user_id") or body.get("from") or "webhook")
        channel_id = body.get("channel_id")
        replies: list[str] = []

        async def _reply(msg: str) -> None:
            replies.append(msg)

        msg = IncomingMessage(
            user_id=user_id,
            text=text,
            gateway=f"webhook:{source}",
            channel_id=channel_id,
            session_id=channel_id or user_id,
            reply=_reply,
            raw=body,
        )
        await self.agent.handle(msg)
        return {"ok": True, "replies": replies}

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        config = uvicorn.Config(
            self.app, host="0.0.0.0", port=self.port, log_level="warning"
        )
        server = uvicorn.Server(config)
        self._server = server
        self._task = asyncio.create_task(server.serve())
        log.info("Webhook gateway listening on :%s", self.port)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if hasattr(self, "_server"):
            self._server.should_exit = True
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def send(self, channel_id: str, text: str) -> None:
        log.info(
            "Webhook gateway has no outbound channel for %s; "
            "delivery for scheduled jobs via webhook not supported", channel_id
        )
