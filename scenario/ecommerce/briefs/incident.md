# Brief: incident (step S6)

Written by orchestrator, 2026-11-21. Self-contained. Tools: Read, Bash, Grep. You are READ-ONLY on production; there is no production in this run.
Start after S3 PASS (a working, evaluated pipeline) and after the quality-engineer's fault generator and builder WP-B incident tool exist.

## Goal
Investigate fault-injected inputs and report status OK / ESCALATED with evidence, proving that the incident path escalates (and does not paper over) upstream problems
and never touches the live release, the dev DB or the raw data.

## Tooling note
`python -m src.incident_cli --source FILE` is the toy-pipeline tool (single orders.csv, closed toy ACTIONS) and does not understand this pipeline; do not point it at these CSVs.
Use the scenario tool (decision D1 in PLAN.md): `python -m src_scenario.incident --raw-dir DIR --workdir .incident`. If missing: report BLOCKED.
This scenario defines no remediation actions, so PROPOSED is not an expected status; you never apply anything anyway.

## Steps
1. Generate inputs (do not write inside `scenario/ecommerce/data/raw/`): `python scenario/ecommerce/tests/faults/make_faults.py --out .incident/inputs`
   then `... --list` to read the oracle (expected outcome per case).
2. Control: `PYTHONPATH=scenario/ecommerce python -m src_scenario.incident --raw-dir scenario/ecommerce/data/raw --workdir .incident/control` -> expect OK.
3. For each case F1..F5: `PYTHONPATH=scenario/ecommerce python -m src_scenario.incident --raw-dir .incident/inputs/<case> --workdir .incident/<case>`.
4. If a case returns ESCALATED, stop on that case: do not try other inputs, thresholds or actions to get past it; explain in plain words why escalation is right
   (e.g. F1: invalid-date orders above 15% of distinct order_ids, would hide an upstream problem).
5. If a case returns something other than the oracle (OK where ESCALATED expected, or the reverse), report it as a defect with evidence; do not adjust anything.

## Done criteria
Report per case: status, reason/evidence, exit code, audit log path (`.incident/<case>/audit.jsonl`), oracle match yes/no. Plus the control result. Confirm with `sha256sum`
before/after that the three raw CSVs are unchanged and that `git status --short` shows no tracked change. Outputs only under `.incident/` (git-ignored).

## Hard rules
- Never modify `scenario/ecommerce/releases/`, `current.json`, the warehouse DB, code, tests or data. Never run promote/rollback/stage. Never apply a proposed config.
- A fix proposal, if any ever appeared, would still need the evaluator and a named human; you only report it.

## Parallelism
May run in parallel with deployment staging (S4); disjoint outputs. After you report, the orchestrator sends any proposal back through steps 3-5 (none expected) and the evaluator does S7.
