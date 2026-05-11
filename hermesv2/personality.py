"""Loads .md personalities (SOUL.md style) used as Claude system prompts."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Personality:
    name: str
    content: str
    path: Path


class PersonalityManager:
    def __init__(
        self,
        personalities_dir: str | Path,
        default: str = "default",
    ):
        self.dir = Path(personalities_dir)
        self.default = default
        self._cache: dict[str, Personality] = {}
        self.load_all()

    def load_all(self) -> int:
        loaded: dict[str, Personality] = {}
        if not self.dir.exists():
            log.warning("Personalities dir %s does not exist", self.dir)
            self._cache = {}
            return 0
        for path in sorted(self.dir.glob("*.md")):
            name = path.stem
            try:
                content = path.read_text(encoding="utf-8").strip()
            except OSError as e:
                log.error("Cannot read personality %s: %s", path, e)
                continue
            loaded[name] = Personality(name=name, content=content, path=path)
        self._cache = loaded
        log.info("Loaded %d personalities from %s", len(loaded), self.dir)
        return len(loaded)

    def reload(self) -> int:
        return self.load_all()

    def get(self, name: str | None = None) -> Personality | None:
        return self._cache.get(name or self.default)

    def get_system_prompt(self, name: str | None = None) -> str:
        p = self.get(name)
        return p.content if p else ""

    def list_personalities(self) -> list[str]:
        return sorted(self._cache.keys())
