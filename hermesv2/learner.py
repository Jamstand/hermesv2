"""Self-improving learning loop.

Hermes-style: analyze recent conversations, extract facts and skill ideas,
*propose* new skills (never write them directly). Owner approves via
`/skills approve <id>` to promote a proposal into `skills/auto/`.

Guardrails:
- Hard cap on auto-skills (default 50)
- Daily proposal cap (default 5)
- Frontmatter validated before promote
- Never overwrites existing skill file (auto or manual)
- Name must match `[a-z0-9_-]+`
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermesv2.llm_router import LLMRouter
from hermesv2.memory import Memory
from hermesv2.skills_engine import SkillsEngine, parse_skill_file

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


@dataclass
class SkillProposal:
    id: int
    name: str
    description: str
    trigger: str
    instructions: str
    rationale: str
    status: str
    created_at: float


_PROPOSAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    trigger TEXT NOT NULL,
    instructions TEXT NOT NULL,
    rationale TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    decided_at REAL
);
CREATE INDEX IF NOT EXISTS idx_proposal_status ON skill_proposals(status);
"""


class Learner:
    def __init__(
        self,
        router: LLMRouter,
        memory: Memory,
        skills_engine: SkillsEngine,
        db_path: str | Path = "data/learner.db",
        max_auto_skills: int = 50,
        max_proposals_per_day: int = 5,
        auto_dir: str | Path | None = None,
        enabled: bool = True,
    ):
        self.router = router
        self.memory = memory
        self.skills = skills_engine
        self.enabled = enabled
        self.max_auto_skills = max_auto_skills
        self.max_proposals_per_day = max_proposals_per_day
        self.auto_dir = Path(auto_dir) if auto_dir else skills_engine.auto_dir
        self.auto_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_PROPOSAL_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    async def analyze_conversation(
        self, user_id: str, recent_messages: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Ask the LLM to extract facts + skill candidates from recent activity."""
        if not self.enabled:
            return {"facts": [], "proposals": []}
        if recent_messages is None:
            recent_messages = self.memory.get_recent_messages(user_id, limit=30)
        if not recent_messages:
            return {"facts": [], "proposals": []}

        convo = "\n".join(
            f"[{m['role']}] {m['content'][:500]}" for m in recent_messages
        )
        prompt = (
            "You are reviewing a personal AI agent's recent conversation history "
            "with its owner. Extract:\n\n"
            "1. New durable FACTS about the owner (preferences, goals, equipment, "
            "people, places). Skip ephemeral stuff.\n"
            "2. Up to 2 SKILL PROPOSALS for repeated patterns that could be "
            "automated. Only propose if the pattern appears 2+ times.\n\n"
            "Respond as JSON ONLY (no prose). Schema:\n"
            "{\n"
            '  "facts": [{"key": "<snake_case>", "value": "<string>", "category": "<string|null>"}],\n'
            '  "proposals": [{"name": "<snake_case>", "description": "<one line>", '
            '"trigger": "manual|scheduled|event", "instructions": "<markdown body>", '
            '"rationale": "<why this should exist>"}]\n'
            "}\n\n"
            "Conversation:\n" + convo
        )
        result = self.router.chat(prompt, force="claude")
        extracted = _safe_json(result["response"])
        if not extracted:
            return {"facts": [], "proposals": []}

        accepted_facts: list[dict[str, Any]] = []
        for f in extracted.get("facts", []):
            key, value = f.get("key"), f.get("value")
            if key and value:
                self.memory.remember_fact(user_id, key, value, f.get("category"))
                accepted_facts.append(f)

        accepted_proposals: list[SkillProposal] = []
        for p in extracted.get("proposals", []):
            try:
                proposal = self.propose(
                    name=p.get("name", ""),
                    description=p.get("description", ""),
                    trigger=p.get("trigger", "manual"),
                    instructions=p.get("instructions", ""),
                    rationale=p.get("rationale", ""),
                )
                accepted_proposals.append(proposal)
            except ValueError as e:
                log.info("Rejected proposal %r: %s", p.get("name"), e)

        return {
            "facts": accepted_facts,
            "proposals": [_proposal_dict(p) for p in accepted_proposals],
        }

    def propose(
        self,
        name: str,
        description: str,
        trigger: str,
        instructions: str,
        rationale: str = "",
    ) -> SkillProposal:
        """Insert a pending proposal row. Validates name + cap."""
        if not NAME_RE.match(name):
            raise ValueError(f"name {name!r} must match [a-z0-9_-]")
        if trigger not in {"manual", "scheduled", "event"}:
            raise ValueError(f"invalid trigger {trigger!r}")
        if not instructions.strip():
            raise ValueError("instructions empty")
        if self.skills.get(name):
            raise ValueError(f"skill {name!r} already exists")

        cutoff = time.time() - 86400
        with self._conn() as conn:
            (today_count,) = conn.execute(
                "SELECT COUNT(*) FROM skill_proposals WHERE created_at >= ?",
                (cutoff,),
            ).fetchone()
            if today_count >= self.max_proposals_per_day:
                raise ValueError(
                    f"daily proposal cap reached ({self.max_proposals_per_day})"
                )
            cur = conn.execute(
                "INSERT INTO skill_proposals "
                "(name, description, trigger, instructions, rationale, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, description, trigger, instructions, rationale, time.time()),
            )
            conn.commit()
            return SkillProposal(
                id=cur.lastrowid,
                name=name,
                description=description,
                trigger=trigger,
                instructions=instructions,
                rationale=rationale,
                status="pending",
                created_at=time.time(),
            )

    def list_proposals(self, status: str | None = "pending") -> list[SkillProposal]:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM skill_proposals WHERE status = ? "
                    "ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM skill_proposals ORDER BY created_at DESC"
                ).fetchall()
        return [
            SkillProposal(
                id=r["id"],
                name=r["name"],
                description=r["description"],
                trigger=r["trigger"],
                instructions=r["instructions"],
                rationale=r["rationale"] or "",
                status=r["status"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def approve(self, proposal_id: int) -> Path:
        """Promote a pending proposal into skills/auto/<name>.md."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM skill_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        if not row:
            raise KeyError(f"proposal {proposal_id} not found")
        if row["status"] != "pending":
            raise ValueError(f"proposal {proposal_id} is {row['status']}, not pending")

        existing = list(self.auto_dir.glob("*.md"))
        if len(existing) >= self.max_auto_skills:
            raise ValueError(
                f"auto-skill cap reached ({self.max_auto_skills}); "
                "prune skills/auto/ before approving more"
            )

        target = self.auto_dir / f"{row['name']}.md"
        if target.exists():
            raise ValueError(f"file already exists: {target}")
        if self.skills.get(row["name"]):
            raise ValueError(f"skill {row['name']!r} already exists in registry")

        body = (
            "---\n"
            f"name: {row['name']}\n"
            f"description: {row['description']}\n"
            f"trigger: {row['trigger']}\n"
            "source: auto-learner\n"
            f"created_at: {row['created_at']}\n"
            "---\n"
            f"{row['instructions']}\n"
        )
        target.write_text(body, encoding="utf-8")

        if parse_skill_file(target) is None:
            target.unlink()
            raise ValueError("validation of written file failed; rolled back")

        with self._conn() as conn:
            conn.execute(
                "UPDATE skill_proposals SET status='approved', decided_at=? WHERE id=?",
                (time.time(), proposal_id),
            )
            conn.commit()
        self.skills.reload()
        log.info("Approved skill proposal %s -> %s", proposal_id, target)
        return target

    def reject(self, proposal_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE skill_proposals SET status='rejected', decided_at=? "
                "WHERE id=? AND status='pending'",
                (time.time(), proposal_id),
            )
            conn.commit()

    def improve_skill(self, skill_name: str, feedback: str) -> None:
        """Record feedback against a skill. Future: ask LLM for revised body."""
        skill = self.skills.get(skill_name)
        if not skill:
            raise KeyError(skill_name)
        log_path = self.auto_dir.parent / "_feedback.log"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "skill": skill_name,
                "feedback": feedback,
            }) + "\n")


def _safe_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("Learner output was not valid JSON: %s", text[:200])
        return None


def _proposal_dict(p: SkillProposal) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "trigger": p.trigger,
        "status": p.status,
    }
