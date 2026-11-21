# Brief: deployment (step S4)

Written by orchestrator, 2026-11-21. Self-contained. Load skill `deploy-runbook`. Tools: Read, Bash.
Start only after the evaluator verdict is PASS (S3) and builder WP-B has landed `src_scenario/release.py`. If either is missing, stop and report; do not improvise.

## Goal
Stage ONE local release of the ecommerce-daily-orders pipeline from `scenario/ecommerce/data/raw/` and smoke-test it. Staging never changes the live release.
Return the staged release id and evidence to the orchestrator. Nothing is promoted in this run.

## Tooling note (read this)
The runbook names `python -m src.deploy stage --source FILE --root releases`. That tool only builds the toy pipeline (single orders.csv, table `mart_daily_revenue`) and is not
valid evidence for this pipeline; do not run it against the ecommerce CSVs. Use the scenario release tool (decision D1 in PLAN.md):
```
PYTHONPATH=scenario/ecommerce python -m src_scenario.release stage --raw-dir scenario/ecommerce/data/raw --root scenario/ecommerce/releases
PYTHONPATH=scenario/ecommerce python -m src_scenario.release status --root scenario/ecommerce/releases
```
If D1 was rejected and the tool does not exist: report BLOCKED, do not run `src.deploy`.

## Files in scope
Read-only everywhere. Staging writes only under `scenario/ecommerce/releases/<id>/` (git-ignored). No edits to code, config, data or PLAN.md.

## Done criteria
1. `stage` exits 0 and prints a release id; `manifest.json` exists with smoke result and row counts.
2. `status` shows `current` unchanged (null if nothing was ever promoted) and lists the staged release as not live.
3. Evidence reported: release id, rows in/out per layer, rows quarantined by reason (expected 40/30/25 orders, 30 orphan payments), dates by OK/WARN/FAIL, config diff vs live ("no live release").
4. If smoke fails: stop, report the failing checks, do NOT retry with altered inputs.

## Hard rules for this run
- You NEVER run `promote` or `rollback`. The promotion approver for this test is UNSET (`architecture/TEST-APPROVAL.md`); this brief contains no approver name, therefore no promote may run.
  Do not invent, borrow or infer an approver name, and do not pass a placeholder such as "test" or "harness".
- No cloud, no real environment. "Release" here means a local directory plus manifest.

## Proving command
`PYTHONPATH=scenario/ecommerce python -m src_scenario.release stage --raw-dir scenario/ecommerce/data/raw --root scenario/ecommerce/releases` then `... status ...`

## Parallelism
Runs after S3 PASS. May run in parallel with incident (S6): disjoint outputs (`releases/` vs `.incident/`).
Report to the orchestrator, which presents release + evidence to the human at gate S5 and records the gate state in PLAN.md.
