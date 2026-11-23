# Setup: Data Engineering Agent on Claude Code with DeepSeek

## 1. Install
```bash
npm install -g @anthropic-ai/claude-code
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt dbt-core dbt-duckdb
```

## 2. Add your key
```bash
cp .env.example .env      # then edit .env and set DEEPSEEK_API_KEY
```
`.env` is git-ignored and denied to the agents in `.claude/settings.json`. Never commit it.
If a key is ever exposed, revoke it on the DeepSeek platform and create a new one.

## 3. Check health (free)
```bash
scripts/smoke.sh
```

## 4. Start
```bash
scripts/claude-deepseek.sh                       # normal session
AGENT=orchestrator scripts/claude-deepseek.sh    # orchestrator as the main session
```
Confirm your Claude Code version supports `--agent` with `claude --help`. If it does not,
start a normal session and ask it to follow the orchestrator's instructions from the main thread.

Inside the session, run `/agents`. All 17 agents should be listed.

## Model mapping
The agent files say `opus` or `sonnet`. The launcher maps `opus` to `STRONG_MODEL`
(orchestrator, architect, evaluator) and `sonnet`/`haiku` to `FAST_MODEL` (everyone else).
Do not set `CLAUDE_CODE_SUBAGENT_MODEL`: it would force every agent, including the evaluator,
onto one model.

## Permissions (`.claude/settings.json`)
- Allowed without asking: tests, evals, conformance, `deploy stage`, dbt build/test/parse.
- Always asks you: `deploy promote`, `deploy rollback`, `git push`.
- Denied: reading `.env` files, `rm -rf`.

## Cost
DeepSeek charges per token. Set a small balance or spending limit on the platform, start with a small task,
and read usage from the DeepSeek dashboard. Repeat each experiment 3 times because LLM output varies.

## Known limits
- `src/pipeline.py`, `src/deploy.py` and `src/incident_cli.py` handle only the toy `orders.csv`. dbt work needs a dbt-aware adapter.
- The approver check in `src.deploy promote` is a non-empty string. The `ask` permission above adds a real human prompt.
