#!/bin/bash
# HermesV2 setup. Designed for Raspberry Pi 5 (Bookworm 64-bit) but works on
# any modern Debian/Ubuntu. Idempotent: safe to re-run.
set -e

echo ">> HermesV2 setup"

if ! command -v python3 >/dev/null; then
    echo "!! python3 not found" >&2
    exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
echo "   python3 ${PY_VERSION}"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || { echo "!! python 3.10+ required" >&2; exit 1; }

if ! command -v node >/dev/null; then
    echo ">> installing Node.js 20"
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi
echo "   node $(node --version)"

if ! command -v claude >/dev/null; then
    echo ">> installing Claude Code CLI"
    if command -v npm >/dev/null; then
        sudo npm install -g @anthropic-ai/claude-code
    else
        echo "!! npm missing, cannot install claude" >&2
        exit 1
    fi
fi
echo "   claude $(claude --version 2>/dev/null || echo 'installed')"
echo "   (run \`claude login\` after this script to authenticate Max)"

if ! command -v ollama >/dev/null; then
    echo ">> installing Ollama"
    curl -fsSL https://ollama.com/install.sh | sh
fi
echo "   ollama $(ollama --version 2>/dev/null || echo 'installed')"

if command -v ollama >/dev/null; then
    if ! ollama list 2>/dev/null | grep -q "^llama3"; then
        echo ">> pulling llama3 (this can take several minutes)"
        ollama pull llama3 || echo "!! ollama pull failed; run manually later"
    fi
fi

echo ">> installing Python deps"
if pip3 install --help 2>&1 | grep -q break-system-packages; then
    pip3 install -r requirements.txt --break-system-packages
    pip3 install -e . --break-system-packages
else
    pip3 install -r requirements.txt
    pip3 install -e .
fi

mkdir -p data logs skills/auto personalities
[ ! -f config.yaml ] && cp config.example.yaml config.yaml && echo "   created config.yaml"

echo ""
echo "== Done =="
echo "Next:"
echo "  1. claude login        (auth Claude Max)"
echo "  2. \$EDITOR config.yaml  (set DISCORD_BOT_TOKEN etc.)"
echo "  3. hermesv2 doctor     (verify)"
echo "  4. hermesv2            (start interactive CLI)"
echo "  5. sudo cp hermesv2.service /etc/systemd/system/ && sudo systemctl enable --now hermesv2"
