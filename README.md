# pipeline-guardian

A Claude Code agent team for end-to-end data engineering: from a pipeline request to a safely
deployed, monitored, self-checked pipeline, with humans approving only what touches production.

Flow: **architect** (system, then data architecture, ADR for ELT/ETL/ETLT) -> human approves ->
**orchestrator** plans -> **pipeline-builder** + **quality-engineer** build and test ->
**evaluator** (independent, can veto) -> **deployment** stages and smoke-tests -> human approves
promotion -> **incident** diagnoses failures and proposes fixes that loop back through the evaluator.

## Layout
- `CLAUDE.md` team, workflow, hard rules, commands (Claude Code reads this first)
- `PLAN.md` state between sessions
- `.claude/agents/` the seven agents; `.claude/skills/` architecture, ELT/ETL patterns, contracts, conventions, runbook
- `project-profile.yaml` + `profiles/` the ELT / ETL / ETLT choice inputs
- `architecture/` the approved pack (stubs until the architect runs) and `decisions/` ADRs
- `src/` paradigm scorer, conformance checker (R1-R19, ratchet baseline), toy pipeline, incident loop, deploy gate, eval harness
- `evals/` scorecard history, failure log, conformance baseline
- `tests/`

## Use
```bash
pip install -r requirements.txt
python -m pytest -q
python -m src.paradigm project-profile.yaml            # ELT/ETL/ETLT recommendation
python -m src.evals --diagnoser rules                  # scorecard + hard gates
python -m src.conformance --project <dbt project> --baseline evals/conformance/baseline.json
```
Open the folder in Claude Code and start with: "Run the architect for <your pipeline goal>."

## Honest limits
Agent and skill files are written but not yet exercised end to end by the Claude Code CLI;
the diagnoser is rule-based (no LLM yet); the incident agent has no dbt adapter yet.
