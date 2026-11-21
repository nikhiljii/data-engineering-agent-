# Brief: pipeline-builder (steps S1b, S1c, S2)

Written by orchestrator, 2026-11-21. Self-contained. Load skills `data-contracts`, `pipeline-conventions`, `pattern-elt` first.
Three work packages. WP-A and WP-B touch disjoint files and may be run by two pipeline-builder instances in parallel; WP-C follows.

## Common rules
- Conform to `architecture/` (approved by TEST APPROVAL, architecture only). Read: data-architecture.md, system-architecture.md, requirements.md,
  ADR-001..005, foreign-keys.yaml, project-profile.yaml, `architecture/TEST-APPROVAL.md`.
- Code only under `scenario/ecommerce/` (ADR-002): `src_scenario/` (Python package), `sql/{raw,staging,intermediate,marts,quarantine}/`,
  `contracts/*.yaml`, `tests/unit/` (your own tests, prefix `test_ecom_unit_`). DuckDB file default `scenario/ecommerce/warehouse/ecommerce.duckdb` (git-ignored).
- Do NOT touch: `src/`, `tests/`, `evals/`, `.claude/`, `scenario/ecommerce/data/raw/`, `scenario/ecommerce/tests/arch/`, `scenario/ecommerce/tests/faults/`
  (quality-engineer's), any baseline, any gate. If a quality-engineer test looks wrong, say so in your report; never edit it.
- No cloud, no network, no credentials (R22). No `now()/random()/uuid()` in SQL (R8). No customer_name/email in any table (R2).
- Never deploy, never approve. No new remediation actions (CLAUDE.md: closed ACTIONS set in `src/diagnoser.py`).
- Tests first (yours, in tests/unit), then code. Report: files changed, commands run with output, what you could not verify.

## WP-A (S1b): ELT pipeline core
Goal: implement the architecture end to end: raw load (sha256 idempotent, append by batch, PII pruned for customers, ADR-004/005), raw contract,
staging views, staging contract, intermediate views, marts + quarantine in one transaction (delete+insert per report_date, LOOKBACK_DAYS=7 constant),
audit.run_log, marts.run_summary, `contracts/*.yaml` classification of every mart column (R21), stop rule (>15% excluded orders or contract fail:
exit non-zero, publish nothing, STOPPED), business rules B1..B9 exactly as written (ADR-003 option 2).
Interface (frozen, see PLAN.md): `python -m src_scenario.run --report-date D | --backfill [--raw-dir PATH] [--db PATH]`; defaults as above.
Done when:
- backfill on the real files finishes < 60 s, exit 0, `marts.daily_country_report` has exactly the 8 columns of R10 in order;
- raw rows 1,200/6,040/5,950; stg_orders 6,000 + stg_orders_duplicate_rows 40; quarantine.orders_rejected 95 rows (40/30/25); 30 orphan payments quarantined;
- running twice gives identical hashes of marts + quarantine (R8) and a second load adds 0 raw rows (R20);
- your unit tests pass. You report, but do not claim, conformance: the quality-engineer suite (S1a) is not yours to edit.
Proving command:
```
PYTHONPATH=scenario/ecommerce python -m src_scenario.run --backfill --db /tmp/ecom_check.duckdb ; echo exit=$?
PYTHONPATH=scenario/ecommerce python -m pytest -q scenario/ecommerce/tests/unit
git diff --stat e2e-baseline -- src tests evals .claude scenario/ecommerce/data/raw    # empty
```
(use the scratchpad dir instead of /tmp if one is provided)

## WP-B (S1c, parallel with WP-A): release and incident tooling for this scenario
Why: `src/deploy.py` and `src/incident_cli.py` are hard-wired to the toy pipeline (`src.pipeline.run_pipeline`, table `mart_daily_revenue`,
one `orders.csv`), so they cannot stage or diagnose this pipeline, and `src/` is frozen (R23). This is orchestrator-proposed decision D1 in PLAN.md,
pending dispatcher/human confirmation; if rejected, do not start WP-B.
Files: `src_scenario/release.py`, `src_scenario/incident.py`, `tests/unit/test_ecom_unit_release.py`, `test_ecom_unit_incident.py`.
- `release`: `python -m src_scenario.release stage|promote|rollback|status --root scenario/ecommerce/releases [--raw-dir DIR] [--release R] [--approver NAME]`.
  Same semantics as `src/deploy.py`: stage builds into `<root>/<release>/db.duckdb` via the run CLI, smoke = run not STOPPED + mart non-empty + R10 schema + R2 no-PII,
  writes manifest.json; never touches `current.json`. promote needs non-empty `--approver` and a staged manifest, otherwise raises/prints ApprovalRequired and exits 1
  (reuse `from src.agent import ApprovalRequired`, import only). rollback needs approver. Pointer moved atomically. Never delete a release dir.
  `releases/` is git-ignored.
- `incident`: `python -m src_scenario.incident --raw-dir DIR --workdir .incident`. READ-ONLY: runs the pipeline into a scratch DB under `--workdir`, never into the dev
  or any release DB, never writes to `--raw-dir`. Output JSON `{status: OK|ESCALATED, reason, evidence, audit_log}`; audit lines appended to `<workdir>/audit.jsonl`.
  Status is ESCALATED for: run STOPPED (R17), failed raw/staging contract, missing/empty file, volume band breach. PROPOSED is NOT produced: the scenario defines no
  remediation actions and adding one needs a guardrail test first. Exit 0 for OK, 2 for ESCALATED.
Done when: unit tests prove promote without approver is refused and leaves `current.json` unchanged, a staged release leaves `current` untouched,
a failed smoke is not retried, incident never modifies its inputs (hash before/after) and escalates on a >15% invalid-date file.
Proving command: `PYTHONPATH=scenario/ecommerce python -m pytest -q scenario/ecommerce/tests/unit/test_ecom_unit_release.py scenario/ecommerce/tests/unit/test_ecom_unit_incident.py`
Depends on: only the run CLI interface; implement against the frozen interface, integrate when WP-A lands.

## WP-C (S2): integration against the quality-engineer suite
Starts after S1a and WP-A are done. Run the QE suite, fix the CAUSE in your code for each failure (never edit tests, SQL assertions, seeded fixtures, thresholds or the 15% constant to pass).
Loop until green or until a failure is judged a wrong test/architecture gap, which you report with evidence instead of working around.
Proving command: `PYTHONPATH=scenario/ecommerce python -m pytest -q scenario/ecommerce/tests` (arch + unit) and the git-diff-empty check above.
Report: pass/fail counts, any test you believe is wrong, anything unverified. Do not self-certify; the result goes to the evaluator (S3).
