---
name: system_check
description: Check Raspberry Pi system health (CPU, RAM, disk, temp, services)
trigger: scheduled
---
Check the Pi's system status. Report:
- CPU usage and core count
- RAM usage (used/total in GB)
- Disk usage (used/total in GB)
- CPU temperature (warn if >70°C)
- Network connectivity (ping 8.8.8.8)
- Critical service status (ssh, ollama)
- Uptime in hours

Format with emoji status indicators. 🟢 = healthy, ⚠️ = warning, ❌ = critical.

Use the system tool to gather metrics. (Note: when run through the built-in
Python handler this skill produces the report directly without going through
the LLM router.)
