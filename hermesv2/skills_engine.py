"""Markdown-based skills with YAML frontmatter.

Each `.md` file in skills/ defines one skill. Frontmatter shape:

    ---
    name: skill_name
    description: What it does
    trigger: manual | scheduled | event
    ---
    <markdown body becomes LLM instructions when the skill is invoked>

Skills can also be registered with a Python handler (callable) which takes
priority over the markdown body.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

log = logging.getLogger(__name__)

VALID_TRIGGERS = {"manual", "scheduled", "event"}


@dataclass
class Skill:
    name: str
    description: str
    trigger: str
    content: str
    path: Path
    metadata: dict[str, Any] = field(default_factory=dict)
    handler: Callable[..., Any] | None = None

    def render(self, context: dict[str, Any] | None = None) -> str:
        """Substitute {{var}} placeholders from `context` into the body."""
        body = self.content
        if context:
            for key, value in context.items():
                body = body.replace("{{" + key + "}}", str(value))
        return body


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n?(.*)$", re.DOTALL)


def parse_skill_file(path: Path) -> Skill | None:
    """Parse a `.md` file with YAML frontmatter into a Skill. Returns None on error."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        log.error("Cannot read skill %s: %s", path, e)
        return None

    match = _FRONTMATTER_RE.match(raw)
    if not match:
        log.warning("Skill %s missing YAML frontmatter, skipping", path.name)
        return None

    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        log.error("Invalid YAML in %s: %s", path.name, e)
        return None

    name = meta.get("name")
    if not name:
        log.error("Skill %s has no `name` in frontmatter", path.name)
        return None

    trigger = meta.get("trigger", "manual")
    if trigger not in VALID_TRIGGERS:
        log.warning("Skill %s has invalid trigger %r, defaulting to manual", name, trigger)
        trigger = "manual"

    return Skill(
        name=name,
        description=meta.get("description", ""),
        trigger=trigger,
        content=match.group(2).strip(),
        path=path,
        metadata=meta,
    )


class SkillsEngine:
    """Loads, registers, and dispatches skills."""

    def __init__(self, skills_dir: str | Path, auto_dir: str | Path | None = None):
        self.skills_dir = Path(skills_dir)
        self.auto_dir = Path(auto_dir) if auto_dir else self.skills_dir / "auto"
        self._skills: dict[str, Skill] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._lock = threading.Lock()
        self.load_skills()

    def load_skills(self) -> int:
        """Scan skills_dir (recursive) and replace the in-memory registry."""
        with self._lock:
            loaded: dict[str, Skill] = {}
            if not self.skills_dir.exists():
                log.warning("Skills dir %s does not exist", self.skills_dir)
                self._skills = {}
                return 0
            for path in sorted(self.skills_dir.rglob("*.md")):
                skill = parse_skill_file(path)
                if skill is None:
                    continue
                if skill.name in loaded:
                    log.warning(
                        "Duplicate skill name %s (in %s); keeping first",
                        skill.name,
                        path,
                    )
                    continue
                if skill.name in self._handlers:
                    skill.handler = self._handlers[skill.name]
                loaded[skill.name] = skill
            self._skills = loaded
            log.info("Loaded %d skills from %s", len(loaded), self.skills_dir)
            return len(loaded)

    def reload(self) -> int:
        return self.load_skills()

    def register_handler(self, name: str, handler: Callable[..., Any]) -> None:
        with self._lock:
            self._handlers[name] = handler
            if name in self._skills:
                self._skills[name].handler = handler

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_skills(self, trigger_type: str | None = None) -> list[Skill]:
        with self._lock:
            skills = list(self._skills.values())
        if trigger_type:
            skills = [s for s in skills if s.trigger == trigger_type]
        return sorted(skills, key=lambda s: s.name)

    def execute(self, name: str, context: dict[str, Any] | None = None) -> Any:
        """Run a skill. If a handler is registered, call it; else return rendered body."""
        skill = self.get(name)
        if skill is None:
            raise KeyError(f"Skill `{name}` not found")
        if skill.handler is not None:
            log.debug("Executing %s via Python handler", name)
            return skill.handler(context or {})
        return skill.render(context)
