#!/usr/bin/env bash
# Quick health check before any agent run. No API key and no cost.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== agents defined"
ls .claude/agents/*.md | wc -l
echo "== settings.json is valid JSON"
python3 -c 'import json; json.load(open(".claude/settings.json")); print("ok")'
echo "== paradigm scorer"
python3 -m src.paradigm project-profile.yaml
echo "== tests"
python3 -m pytest -q tests
echo "smoke ok"
