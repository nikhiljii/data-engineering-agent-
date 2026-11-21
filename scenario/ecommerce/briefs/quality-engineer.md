# Brief: quality-engineer (step S1a)

Written by orchestrator, 2026-11-21. Self-contained. Read nothing under `scenario/ecommerce/src_scenario/` or `sql/`:
your independence from the builder is the point (CLAUDE.md, quality-engineer definition).

## Goal
Turn the approved architecture into an executable test suite that is RED until the builder's code exists and
GREEN only when the pipeline really conforms. Cover every conformance rule R1..R23 and business rule B1..B9 with at
least one test, and prove each test can fail (seeded failing case).

## Inputs (read in full)
`architecture/data-architecture.md` (R1-R23, B1-B9, quality contracts, quarantine design), `architecture/requirements.md`
(D1-D7 defect counts, A-n), `architecture/foreign-keys.yaml`, `architecture/decisions/ADR-001..005.md`,
`architecture/system-architecture.md`, `scenario/ecommerce/data/raw/*.csv` (read-only).

## Interface you may rely on (frozen in PLAN.md "Interface freeze"; taken from the architecture)
- Run: `python -m src_scenario.run --report-date D | --backfill`, plus `--raw-dir PATH` and `--db PATH` (the last two are an
  orchestrator proposal needed so tests can use temp dirs; pending dispatcher confirmation D2). Exit code non-zero when run_status = STOPPED.
- Package root for imports: `PYTHONPATH=scenario/ecommerce`.
- DuckDB schemas/tables exactly as in data-architecture.md "Layers": raw.{customers,orders,payments};
  staging.{stg_customers,stg_orders,stg_orders_duplicate_rows,stg_payments}; intermediate.{int_orders_enriched,int_order_payments};
  marts.{daily_country_report,daily_country_report_dq,run_summary}; quarantine.{orders_rejected,payments_rejected}; audit.run_log.
- Column names for dq/run_summary/quarantine as listed in data-architecture.md. If a name you need is not listed there, do NOT guess:
  list it under "Architecture gaps" in your report.
- Contracts at `scenario/ecommerce/contracts/*.yaml` (R21).
Do not invent behaviour. Where the architecture says UNKNOWN (e.g. payments of negative-quantity orders), assert only invariants, not a number.

## Files in scope (create only these)
- `scenario/ecommerce/tests/arch/test_ecom_*.py` (pytest, prefix `test_ecom_` to avoid module-name clashes with baseline `tests/`)
- `scenario/ecommerce/tests/arch/conftest.py` (fixture that runs the pipeline into a tmp db from tmp copies of the raw CSVs)
- `scenario/ecommerce/tests/arch/sql/*.sql` (independent assertion queries, e.g. R12/R13/R14/R15 reconciliations)
- `scenario/ecommerce/tests/arch/seeded/` (small mutated CSV fixtures proving failure: >15% invalid dates, missing header column,
  altered payment_amount for R15 mutation test, duplicated file for R20, PII column present)
- `scenario/ecommerce/tests/faults/make_faults.py` (generator that writes fault-injected copies of the three CSVs into a directory
  given on the command line, for the incident step; NEVER writes into `scenario/ecommerce/data/raw/`). Cases: F1 >15% invalid order_date
  (expect STOPPED/escalate, R17); F2 orders.csv header column renamed (raw contract fail); F3 payments.csv missing/empty; F4 orders volume cut by >50% (R19 volume band); F5 customers.csv with PII columns kept and a duplicated key.
  Print the expected outcome per case in the generator's `--list` output so the incident step has an oracle.
Never edit: any model/SQL, `src_scenario/`, `src/`, `tests/`, `evals/`, `.claude/`, raw data, any baseline file. Never add a rule to a baseline.

## Done criteria
1. Every R1..R23 and B1..B9 maps to >= 1 test (table in your report: rule -> test id). Rules not machine-testable are named with reason.
   R23 and R22 are repo-level checks (`git diff --stat e2e-baseline -- src tests evals .claude scenario/ecommerce/data/raw` empty; grep for cloud/network libs).
2. Expected numbers asserted exactly where the architecture states them: raw rows 1,200/6,040/5,950; orders 6,040 = 6,000 + 40; 6,000 = sum(total_orders) + 55;
   quarantine orders 95 rows (40 duplicate_order_surplus, 30 invalid_order_date, 25 negative_quantity); orphan payments 30; excluded share about 0.9% (< 0.15).
   Raw customers table has no customer_name/email (R2, ADR-004).
3. Each test has a failing counterpart (seeded fixture or mutation) demonstrated in your report by command output showing it failing for the right reason.
4. The suite is RED against an empty `src_scenario` (collection works; tests fail or error because the pipeline is missing), and you state this honestly.
5. Report: rules covered, tests added, rules not testable and why, "Architecture gaps" list, conformance/pytest output. No claim of green.

## Proving command
```
PYTHONPATH=scenario/ecommerce python -m pytest -q scenario/ecommerce/tests/arch --collect-only   # collects, no import errors from your own files
PYTHONPATH=scenario/ecommerce python -m pytest -q scenario/ecommerce/tests/arch                  # expected RED before builder, GREEN after S2
python scenario/ecommerce/tests/faults/make_faults.py --out .incident/inputs --list
git diff --stat e2e-baseline -- src tests evals .claude scenario/ecommerce/data/raw               # must be empty
```
NOTE: bare `python -m pytest -q` at repo root also collects `scenario/`; always pass explicit paths so baseline (43 tests) and scenario suites are not mixed.

## Parallelism and handoff
Runs in parallel with builder WP-A and WP-B (disjoint files). One-way dependency: the builder may read your tests; you may not read the builder's code.
Report goes to the orchestrator, who records it in PLAN.md. Escalate (do not work around) if the architecture is ambiguous.
