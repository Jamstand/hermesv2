"""Normalized message shape that every gateway adapts into."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class IncomingMessage:
    user_id: str
    text: str
    gateway: str
    channel_id: str | None = None
    session_id: str | None = None
    is_dm: bool = False
    mentions_bot: bool = False
    reply: Callable[[str], Awaitable[None]] | None = None
    raw: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
