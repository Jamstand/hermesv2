"""File IO restricted to a working directory."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


class FileTool:
    def __init__(self, working_dir: str | Path):
        self.working_dir = Path(working_dir).resolve()
        self.working_dir.mkdir(parents=True, exist_ok=True)

    def _safe(self, path: str | Path) -> Path:
        p = (self.working_dir / path).resolve()
        try:
            p.relative_to(self.working_dir)
        except ValueError as e:
            raise PermissionError(f"path escapes working dir: {path}") from e
        return p

    def read_file(self, path: str | Path) -> str:
        p = self._safe(path)
        return p.read_text(encoding="utf-8")

    def write_file(self, path: str | Path, content: str) -> Path:
        p = self._safe(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def list_dir(self, path: str | Path = ".") -> list[str]:
        p = self._safe(path)
        if not p.exists():
            return []
        return sorted(c.name + ("/" if c.is_dir() else "") for c in p.iterdir())

    def exists(self, path: str | Path) -> bool:
        try:
            return self._safe(path).exists()
        except PermissionError:
            return False
