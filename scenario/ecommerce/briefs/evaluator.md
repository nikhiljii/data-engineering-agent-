# Brief: evaluator (steps S3 and S7)

Written by orchestrator, 2026-11-21. Self-contained. Independent: you edit nothing (your tools are Read, Bash, Grep). You may not be the author of anything you score.
Run after builder WP-A/WP-B/WP-C and the quality-engineer suite are reported done. Never accept the builder's or QE's own claim.

## Goal
Return PASS or VETO on the ecommerce-daily-orders pipeline in `scenario/ecommerce/`, with command output as evidence. Any hard-gate failure is an automatic veto.

## Commands (run all, quote the output)
1. Baseline untouched and regression: `python -m pytest -q tests` (expect the 43 baseline tests pass; do NOT run bare `python -m pytest -q` at root, it also collects scenario/);
   `python -m src.evals --diagnoser rules --no-save`; compare with the last line of `evals/history.jsonl`. These score the TOY incident logic, not this pipeline;
   say so explicitly: a pass here is regression evidence only.
2. Scenario suite: `PYTHONPATH=scenario/ecommerce python -m pytest -q scenario/ecommerce/tests`.
3. Repo guards: `git diff --stat e2e-baseline -- src tests evals .claude scenario/ecommerce/data/raw` must be empty (R23);
   `grep -rEn "boto3|google\.cloud|azure|snowflake|requests|urllib\.request|http" scenario/ecommerce --include=*.py --include=*.sql --include=*.yaml` (R22; investigate every hit, do not hand-wave);
   `grep -rEin "customer_name|email" scenario/ecommerce/sql scenario/ecommerce/contracts` and a DuckDB `information_schema.columns` query on the built DB (R2).
4. Independent re-computation (write nothing to the repo; use the scratchpad dir, build into a scratch DB with
   `PYTHONPATH=scenario/ecommerce python -m src_scenario.run --backfill --db <scratch>.duckdb`): query the DB yourself and check against the architecture's numbers:
   raw 1,200/6,040/5,950; 6,040 = 6,000 + 40; 6,000 = sum(total_orders) + 55; quarantine orders 95 (40/30/25); 30 orphan payments; excluded share < 0.15;
   gross_revenue for 3 spot-checked (report_date, country) rows recomputed directly from the raw CSV with your own DuckDB `read_csv` query (de-dupe, drop invalid, completed only);
   output columns equal R10; data_quality_status equals the ADR-003 derivation (R11). Run the backfill twice and compare row hashes (R8).
5. Release/incident tooling and approver gate (unit evidence only): confirm via the tests that promote without approver is refused and leaves `current.json` unchanged. Do not run
   `promote` or `rollback` yourself, with or without an approver.
6. Hard gates (CLAUDE.md): no false positives, no unsafe fixes, no unapproved production writes, no row-drop > 15% without escalation; nothing was loosened
   (inspect `git log -p` / `git diff e2e-arch-approved --stat -- scenario/ecommerce/tests scenario/ecommerce/data` for edits to tests/thresholds after they were written; the 0.15 constant must be unchanged).
7. Check that conformance rules that cannot be machine-verified are acknowledged, not forgotten. `python -m src.conformance` is NOT applicable (no dbt_project.yml, ADR-002):
   do not count its SKIP/FAIL as a violation, but state that you did not use it.

## Verdict format
PASS or VETO; hard gates table; per-rule R1..R23 result with evidence (rule -> command -> result); at least ONE untested failure mode named
(candidates: late/delta arrival Q12; payment_date vs order_date Q2; customers with changed country SCD1 Q15; two payments per order; unknown status values; unicode/whitespace in country; DST irrelevance; concurrent runs on one DuckDB file).
Propose held-out scenarios in the report; you cannot write them, so the orchestrator records them in PLAN.md. A perfect score on tests written alongside the code is a baseline, not proof.
Do not soften a veto. If PASS, say what the pass does and does not establish.

## S7 (after incident step)
Re-verify that the incident run touched nothing: raw CSV sha256 unchanged, `git status` shows no tracked change, no release `current.json` change, incident outputs only under `.incident/`.
