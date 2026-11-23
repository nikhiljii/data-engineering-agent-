#!/usr/bin/env bash
# Start Claude Code with DeepSeek as the model provider.
# Usage:  scripts/claude-deepseek.sh                 (normal session)
#         AGENT=orchestrator scripts/claude-deepseek.sh   (orchestrator as the main session)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "DEEPSEEK_API_KEY is not set. Copy .env.example to .env and add your key." >&2
  exit 1
fi

STRONG_MODEL="${STRONG_MODEL:-deepseek-v4-pro}"
FAST_MODEL="${FAST_MODEL:-deepseek-flash}"

export ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"
export ANTHROPIC_AUTH_TOKEN="$DEEPSEEK_API_KEY"
export ANTHROPIC_MODEL="$STRONG_MODEL"
export ANTHROPIC_DEFAULT_OPUS_MODEL="$STRONG_MODEL"
export ANTHROPIC_DEFAULT_SONNET_MODEL="$FAST_MODEL"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="$FAST_MODEL"
unset ANTHROPIC_API_KEY

if [ -n "${AGENT:-}" ]; then
  exec claude --agent "$AGENT" "$@"
fi
exec claude "$@"
