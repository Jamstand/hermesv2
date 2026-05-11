# HermesV2

Personal AI agent platform that runs on a Raspberry Pi 5 and drives all
Claude work through your **Claude Max subscription** via the `claude` CLI —
zero extra API spend.

Inspired by Hermes Agent (NousResearch), but rebuilt around the CLI instead
of paid API keys.

## What it does

- **Hybrid LLM routing**: simple tasks go to local Llama (Ollama), reasoning
  and code go to Claude Max via the `claude` CLI subprocess
- **Persistent memory**: SQLite + FTS5 for full-text search across every
  conversation
- **Skills system**: Markdown files with YAML frontmatter, hot-reloaded
- **Personalities**: Per-user system-prompt personas
- **Self-improving learner**: Proposes new skills from observed patterns
  (owner-approved before promotion)
- **Multi-platform gateways**: Discord, Telegram, CLI, webhook (Slack/HTTP)
- **Cron scheduler**: Runs skills on a schedule, delivers results to any
  gateway
- **Web dashboard**: HTMX status page over FastAPI
- **Toolset**: web search/fetch, file IO, sandboxed shell, marketplace scrapers
  (Cars and Bids, eBay), Pi system metrics

## Quickstart

```bash
git clone <repo> hermesv2
cd hermesv2
bash setup.sh
claude login                 # authenticate Claude Max
cp config.example.yaml config.yaml
$EDITOR config.yaml          # set DISCORD_BOT_TOKEN etc.
hermesv2 doctor              # verify install
hermesv2                     # interactive CLI
```

See [INSTALL.md](INSTALL.md) for the full Pi 5 deployment guide.

## Architecture rule

**Never import the `anthropic` SDK. Never hit `api.anthropic.com`.** All
Claude calls go through `subprocess.run(["claude", "--print", ...])`, which
uses the Max subscription you already pay for.

## License

MIT
