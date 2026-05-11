"""Raspberry Pi (and generic Linux) system metrics."""

from __future__ import annotations

import logging
import shutil as _shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil

log = logging.getLogger(__name__)


def get_pi_status() -> dict[str, Any]:
    """Return a snapshot of CPU, RAM, disk, temperature, uptime, and network."""
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "cpu_count": psutil.cpu_count(logical=False) or psutil.cpu_count(),
        "load_avg": psutil.getloadavg() if hasattr(psutil, "getloadavg") else None,
        "memory_used_gb": round(mem.used / 1024**3, 2),
        "memory_total_gb": round(mem.total / 1024**3, 2),
        "memory_percent": mem.percent,
        "disk_used_gb": round(disk.used / 1024**3, 2),
        "disk_total_gb": round(disk.total / 1024**3, 2),
        "disk_percent": disk.percent,
        "temperature_c": check_temperature(),
        "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1),
        "network_ok": _ping("8.8.8.8"),
    }


def get_service_status(name: str) -> str:
    """Return systemctl is-active output, or 'unknown' if systemd unavailable."""
    if not _shutil.which("systemctl"):
        return "unknown"
    try:
        r = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=10
        )
        return r.stdout.strip() or "unknown"
    except subprocess.TimeoutExpired:
        return "timeout"


def check_disk_space(path: str = "/") -> dict[str, Any]:
    d = psutil.disk_usage(path)
    return {
        "used_gb": round(d.used / 1024**3, 2),
        "total_gb": round(d.total / 1024**3, 2),
        "free_gb": round(d.free / 1024**3, 2),
        "percent": d.percent,
    }


def check_memory() -> dict[str, Any]:
    m = psutil.virtual_memory()
    return {
        "used_gb": round(m.used / 1024**3, 2),
        "total_gb": round(m.total / 1024**3, 2),
        "available_gb": round(m.available / 1024**3, 2),
        "percent": m.percent,
    }


def check_temperature() -> float | None:
    """Read CPU temp. Tries vcgencmd, then /sys, then psutil."""
    if _shutil.which("vcgencmd"):
        try:
            r = subprocess.run(
                ["vcgencmd", "measure_temp"],
                capture_output=True, text=True, timeout=5
            )
            out = r.stdout.strip()
            if "=" in out:
                return float(out.split("=")[1].rstrip("'C"))
        except (subprocess.TimeoutExpired, ValueError):
            pass
    thermal = Path("/sys/class/thermal/thermal_zone0/temp")
    if thermal.exists():
        try:
            return round(int(thermal.read_text().strip()) / 1000.0, 1)
        except (ValueError, OSError):
            pass
    if hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures() or {}
            for entries in temps.values():
                for t in entries:
                    if t.current:
                        return t.current
        except Exception:
            pass
    return None


def _ping(host: str) -> bool:
    if not _shutil.which("ping"):
        return False
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", "2", host],
            capture_output=True, text=True, timeout=5
        )
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False


__all__ = [
    "get_pi_status",
    "get_service_status",
    "check_disk_space",
    "check_memory",
    "check_temperature",
]
