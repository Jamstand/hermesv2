"""Allowlist-based shell command runner."""

from __future__ import annotations

import logging
import shlex
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class ShellResult:
    returncode: int
    stdout: str
    stderr: str
    command: str


class ShellTool:
    def __init__(
        self,
        allowlist: list[str] | None = None,
        require_approval: bool = True,
        approval_callback=None,
    ):
        self.allowlist = list(allowlist or [])
        self.require_approval = require_approval
        self.approval_callback = approval_callback

    def _is_allowed(self, cmd: str) -> bool:
        stripped = cmd.strip()
        for prefix in self.allowlist:
            if stripped == prefix or stripped.startswith(prefix + " "):
                return True
        return False

    def run_command(self, cmd: str, timeout: int = 30) -> ShellResult:
        if not self._is_allowed(cmd):
            return ShellResult(
                returncode=126,
                stdout="",
                stderr=f"command not in allowlist: {cmd!r}",
                command=cmd,
            )
        if self.require_approval and self.approval_callback is not None:
            if not self.approval_callback(cmd):
                return ShellResult(
                    returncode=125,
                    stdout="",
                    stderr="approval denied",
                    command=cmd,
                )
        try:
            parts = shlex.split(cmd)
            result = subprocess.run(
                parts,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return ShellResult(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                command=cmd,
            )
        except FileNotFoundError as e:
            return ShellResult(returncode=127, stdout="", stderr=str(e), command=cmd)
        except subprocess.TimeoutExpired:
            return ShellResult(
                returncode=124, stdout="", stderr="timeout", command=cmd
            )
