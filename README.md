# HermesV2

Personal AI agent platform for a Raspberry Pi 5, driving every Claude call
through your existing **Claude Max subscription** via the `claude` CLI —
**zero extra API spend**.

Inspired by Hermes Agent (NousResearch), but rebuilt around the CLI instead
of paid API keys. Tailored to Josh's actual workflow (Discord bot
automation, S15 Spec R import hunting, streaming setup, Pi monitoring).

## Why

You already pay $200/mo for Claude Max. Every other agent platform asks you
to pay *again* for API tokens. HermesV2 reuses the subscription you have —
the `claude` CLI authenticates against the same account, so requests go
through that quota instead of API billing.

| | Original Hermes | HermesV2 |
|---|---|---|
| Claude access | Pay API tokens | Uses Max subscription |
| Cost / month | $30–70 API | $0 extra |
| Skills | Generic | Pre-built for Josh's life |
| Pi optimization | Heavy | Lightweight |
| Customization | Limited | You own the code |

## Features

- **LLM router**: Routes simple prompts to local Llama (Ollama), heavy
  reasoning / code to Claude Max. Falls back bidirectionally if one is
  unavailable.
- **Persistent memory**: SQLite + FTS5. Facts, preferences, conversations,
  user profile. Build-context for every reply.
- **Skills**: Markdown files with YAML frontmatter; Python handlers
  optional. Hot-reloaded via watchdog.
- **Personalities**: System-prompt personas, switchable per-user.
- **Self-improving learner**: Watches conversations, proposes new skills.
  Owner approves via `hermesv2 skills approve <id>`. Capped at 50 auto
  skills, 5 proposals/24h, validated frontmatter.
- **Multi-platform gateways**: Discord, Telegram, CLI (`prompt_toolkit` +
  rich), HTTP webhook + Slack adapter.
- **Cron scheduler**: APScheduler. Skills on cron, results delivered to
  any configured gateway.
- **Web dashboard**: HTMX status page (live stats, claude usage, skill list,
  pending proposals) mounted on the webhook gateway.
- **Tools**: web search (`ddgs`), fetch + extract, sandboxed file IO,
  allowlist shell, Cars and Bids/eBay scrapers, Pi system metrics.
- **Pi-first**: systemd service, journald caps in INSTALL.md, lazy Ollama
  loading guidance.

## The architectural rule

**Never import the `anthropic` SDK. Never hit `api.anthropic.com`.**
All Claude requests go through `subprocess.run(["claude", "--print", ...])`,
which uses your Max-account credentials at `~/.claude/.credentials.json`.

A test (`tests/test_smoke.py::test_no_anthropic_sdk`) enforces this.

## Quickstart

```bash
git clone <repo> hermesv2
cd hermesv2
bash setup.sh
claude login                       # authenticate Claude Max
cp config.example.yaml config.yaml
$EDITOR config.yaml                # set DISCORD_BOT_TOKEN etc.
hermesv2 doctor                    # verify install (expect 11 checks)
hermesv2                           # interactive CLI gateway
```

Full Pi 5 deployment: see [INSTALL.md](INSTALL.md).

## CLI

```
hermesv2                # interactive CLI gateway
hermesv2 start          # all enabled gateways (daemon mode)
hermesv2 doctor         # verify install
hermesv2 setup          # guided setup
hermesv2 status         # quick stats
hermesv2 stats          # full router + claude stats
hermesv2 search "s15"   # FTS5 search past convos
hermesv2 skills list
hermesv2 skills run <name>
hermesv2 skills proposals
hermesv2 skills approve <id>
hermesv2 skills reject <id>
hermesv2 skills reload  # hot reload .md files
hermesv2 update         # git pull
```

## Repo layout

```
hermesv2/
├── README.md             INSTALL.md          config.example.yaml
├── requirements.txt      pyproject.toml      setup.sh
├── hermesv2.service      .gitignore
├── personalities/        # .md personas (default, vans_streamer, s15_hunter)
├── skills/               # .md skills + skills/auto/ for learner-promoted
├── tests/                # smoke tests
└── hermesv2/             # package
    ├── agent.py          # orchestrator
    ├── claude_runner.py  # subprocess wrapper + rate limiter
    ├── llm_router.py     # Llama vs Claude routing
    ├── memory.py         # SQLite + FTS5
    ├── skills_engine.py  # .md loader, hot reload
    ├── personality.py
    ├── learner.py        # propose-then-promote
    ├── scheduler.py      # APScheduler
    ├── doctor.py         # `hermesv2 doctor`
    ├── dashboard.py      # HTMX status page
    ├── messages.py
    ├── cli.py            # `hermesv2` entry point
    ├── gateways/         # discord, telegram, cli, webhook
    └── tools/            # web, file, shell, marketplace, system
```

## License

MIT
