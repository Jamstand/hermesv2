# HermesV2 install guide (Raspberry Pi 5)

Tested on a Pi 5 8GB, Raspberry Pi OS Bookworm 64-bit. Works on any Debian/
Ubuntu-based system with Python 3.10+.

## 1. Prerequisites

- Raspberry Pi 5 (4 GB minimum, 8 GB recommended)
- 64-bit Raspberry Pi OS Bookworm (or any Debian/Ubuntu ≥ Bullseye)
- Active internet connection
- A Claude Max subscription (you're already paying for it)
- Optional: Discord bot token, Telegram bot token

## 2. Install Claude Code CLI

```bash
# Node 20 (required for the Claude Code CLI)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Claude Code itself
sudo npm install -g @anthropic-ai/claude-code

# Verify
claude --version
```

Log in with your Max account:

```bash
claude login
```

This opens a browser flow and writes credentials to
`~/.claude/.credentials.json`. **No API key needed.** HermesV2 reads from
this credential file via the `claude` subprocess.

## 3. Install Ollama and pull Llama 3

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3
```

Ollama listens on `http://localhost:11434` by default — keep that.

## 4. Clone and bootstrap HermesV2

```bash
git clone <your-repo> ~/hermesv2
cd ~/hermesv2
bash setup.sh
```

`setup.sh` installs Python deps with `--break-system-packages` (PEP 668
workaround on Bookworm), installs the package editable, and creates a
default `config.yaml` if one isn't present.

## 5. Configure

```bash
$EDITOR config.yaml
```

Minimum changes:

- Set `gateways.discord.token` (or use `${DISCORD_BOT_TOKEN}` env var)
- Replace `REPLACE_ME` channel IDs under `scheduler.jobs`
- Enable Telegram and webhook if you want them

For secrets, prefer environment variables. Add them to
`/etc/hermesv2.env` (chmod 600) and uncomment the `EnvironmentFile=` line
in `hermesv2.service`:

```bash
sudo install -m 600 /dev/null /etc/hermesv2.env
sudo tee -a /etc/hermesv2.env <<EOF
DISCORD_BOT_TOKEN=your_real_token
TELEGRAM_BOT_TOKEN=your_real_token
WEBHOOK_API_KEY=long_random_string
EOF
```

## 6. Verify

```bash
hermesv2 doctor
```

Expect all 11 checks to pass. If something fails, the table tells you
exactly what's missing.

## 7. Test interactively

```bash
hermesv2
```

This opens the CLI gateway. Try:

```
> hi
> /skills
> /skill system_check
> /search s15
> /stats
```

Quit with `/quit`.

## 8. Run as a service (24/7)

```bash
sudo cp hermesv2.service /etc/systemd/system/
# adjust User=, WorkingDirectory=, PATH= for your username (default is `pi`)
sudo systemctl daemon-reload
sudo systemctl enable --now hermesv2
sudo systemctl status hermesv2
```

Live logs:

```bash
sudo journalctl -u hermesv2 -f
# or
tail -f ~/hermesv2/logs/service.log
```

Cap journald so it doesn't fill the SD card:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
echo -e "[Journal]\nSystemMaxUse=200M" | sudo tee /etc/systemd/journald.conf.d/hermesv2.conf
sudo systemctl restart systemd-journald
```

## 9. Dashboard

If you enabled the webhook gateway in config (`gateways.webhook.enabled:
true`), browse to `http://<pi-ip>:8080/dashboard` to see live stats. The
page polls HTMX fragments every few seconds.

## 10. Adding custom skills

Drop a new `.md` file into `skills/`:

```markdown
---
name: my_skill
description: Does the thing
trigger: manual
---
Instructions for the LLM go here.
```

`watchdog` reloads on file change automatically (if `skills.hot_reload:
true` in config). To run it once:

```bash
hermesv2 skills run my_skill
```

To schedule it, add a stanza under `scheduler.jobs`:

```yaml
scheduler:
  jobs:
    - skill: my_skill
      cron: "*/30 * * * *"
      deliver_to:
        - { gateway: discord, channel_id: "1234567890" }
```

## 11. Adding a new gateway

1. Subclass `hermesv2.gateways.base.Gateway`
2. Implement `async start/stop/send`
3. Adapt platform messages into `IncomingMessage` and call `agent.handle()`
4. Register in `HermesV2.build_gateways()`

## 12. Auto-skill proposals

The Learner runs every 6 hours (configurable) and may propose new skills
based on patterns it sees. Proposals are NOT auto-promoted — they stay in
SQLite until you approve them:

```bash
hermesv2 skills proposals
hermesv2 skills approve 3
hermesv2 skills reject 5
```

Caps: 50 total auto-skills, 5 proposals per 24 hours.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `claude` binary not found | npm global bin not on PATH | Add `~/.npm-global/bin` to PATH in service file |
| Rate-limit errors | Self-throttle (30/5min by default) | Raise `llm.claude.max_calls_per_window` in config |
| Ollama unreachable | Service not running | `sudo systemctl status ollama` |
| Discord doesn't respond | Missing `Message Content` intent | Enable in Discord developer portal |
| FTS5 search empty | Old SQLite without FTS5 | `sqlite3 --version` ≥ 3.20 |
| systemd keeps restarting | Crash on startup | `journalctl -u hermesv2 -e` |
