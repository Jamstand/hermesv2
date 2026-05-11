"""APScheduler wrapper. Loads scheduled skill jobs from config."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from hermesv2.agent import HermesV2

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, agent: "HermesV2"):
        self.agent = agent
        self.scheduler = AsyncIOScheduler()
        self._jobs: list[dict[str, Any]] = []

    def add_job(
        self,
        skill_name: str,
        cron_expression: str,
        context: dict[str, Any] | None = None,
        delivery_targets: list[dict[str, str]] | None = None,
    ) -> str:
        trigger = _parse_cron(cron_expression)
        job_id = f"{skill_name}_{len(self._jobs)}"
        self.scheduler.add_job(
            self._run,
            trigger=trigger,
            args=[skill_name, context or {}, delivery_targets or []],
            id=job_id,
            replace_existing=True,
        )
        self._jobs.append({
            "id": job_id,
            "skill": skill_name,
            "cron": cron_expression,
            "delivery_targets": delivery_targets or [],
        })
        log.info("scheduled %s [%s]", skill_name, cron_expression)
        return job_id

    def load_from_config(self, jobs: list[dict[str, Any]]) -> int:
        count = 0
        for job in jobs or []:
            try:
                self.add_job(
                    job["skill"],
                    job["cron"],
                    job.get("context"),
                    job.get("deliver_to", []),
                )
                count += 1
            except Exception as e:
                log.error("failed to register job %r: %s", job, e)
        return count

    async def _run(
        self,
        skill_name: str,
        context: dict[str, Any],
        delivery: list[dict[str, str]],
    ) -> None:
        log.info("running scheduled skill %s", skill_name)
        try:
            result = await self.agent.run_skill(skill_name, context)
        except Exception:
            log.exception("scheduled skill %s failed", skill_name)
            return
        if not result:
            return
        for target in delivery:
            gateway_name = target.get("gateway")
            channel_id = target.get("channel_id") or target.get("chat_id")
            if not gateway_name or not channel_id:
                continue
            gateway = self.agent.gateways.get(gateway_name)
            if gateway is None or not gateway.running:
                log.warning(
                    "skipping delivery: gateway %r not running", gateway_name
                )
                continue
            try:
                await gateway.send(str(channel_id), str(result))
            except Exception:
                log.exception("delivery to %s failed", gateway_name)

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def get_jobs(self) -> list[dict[str, Any]]:
        return list(self._jobs)


def _parse_cron(expr: str) -> CronTrigger:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron expression must have 5 fields: {expr!r}")
    minute, hour, day, month, weekday = parts
    return CronTrigger(
        minute=minute, hour=hour, day=day, month=month, day_of_week=weekday
    )
