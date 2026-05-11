"""Abstract base for all messaging gateways."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermesv2.agent import HermesV2


class Gateway(ABC):
    """Subclasses adapt platform-native messages into IncomingMessage and call agent.handle()."""

    name: str = "base"

    def __init__(self, agent: "HermesV2", config: dict):
        self.agent = agent
        self.config = config
        self._running = False

    @abstractmethod
    async def start(self) -> None:
        """Start listening. Must be idempotent; sets self._running True."""

    @abstractmethod
    async def stop(self) -> None:
        """Cancel-safe shutdown. Idempotent."""

    @abstractmethod
    async def send(self, channel_id: str, text: str) -> None:
        """Push an unsolicited message (used by scheduler)."""

    @property
    def running(self) -> bool:
        return self._running
