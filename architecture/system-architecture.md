# System architecture: ecommerce-daily-orders

STATUS: PROPOSED. Written by the `architect`. Not approved. No pipeline code is written before a human approves this pack
(system-architecture.md, data-architecture.md, ADR-001 to ADR-005, foreign-keys.yaml). Replaces the earlier demo project's pack.
Inputs: `architecture/requirements.md`, `project-profile.yaml`.

## Goal

Every day, turn three raw CSV files (customers, orders, payments) with known data-quality defects into one
reporting table of orders and payments per (report_date, country), plus an honest quality status per row, locally in
DuckDB. Consumers (assumed, requirements A-1): finance/business analysts who track order volume, revenue and payment
health. If the table is wrong they misread revenue or miss a payment-failure spike; if it is late they lose the day's view
(no hard consequence stated). The table must say when a figure is not trustworthy instead of hiding it.

## Constraints

- Local execution only, DuckDB, no cloud, no spend, no real credentials (cost ceiling: 0).
- Daily batch. No streaming.
- Source files are read-only; `scenario/ecommerce/data/raw/` is never modified.
- Regulation: customers.csv has name and email (synthetic PII). Residency: local disk. Legal basis for real data: UNKNOWN (open question Q5).
- Team skills: SQL medium (placeholder), moderate Python (assumed).
- Code location: all pipeline code lives under `scenario/ecommerce/` (Python package `src_scenario`). The baseline `src/`, `tests/`, `evals/` and `.claude/` are not modified (ADR-002).
- Hard rule from CLAUDE.md: if a fix would drop more than 15% of rows, escalate (encoded as a run-stop in data-architecture R17).

## Context diagram (text)

```
 customers.csv --\
 orders.csv    ---+--> [loader: Python + DuckDB] --> raw -> staging -> intermediate -> marts --> daily_country_report (+ _dq, run_summary)
 payments.csv  --/                                          \--> quarantine (rejected rows with reason)
                                         audit.run_log <----/        |
                                                                     v
                                              analysts (read DuckDB file or export) 
```
One process, one DuckDB file `scenario/ecommerce/warehouse/ecommerce.duckdb` (git-ignored via `*.duckdb`). No network.

## Integrations

| Party | Role | Owner | Interface | Volume | Change rate | If it breaks |
|---|---|---|---|---|---|---|
| customers.csv | source | UNKNOWN (fictional e-commerce company) | local CSV file | 1,200 rows | UNKNOWN (static snapshot observed) | UNKNOWN |
| orders.csv | source | UNKNOWN | local CSV file | 6,040 rows (about 200 orders/day, 30 days) | UNKNOWN | UNKNOWN |
| payments.csv | source | UNKNOWN | local CSV file | 5,950 rows | UNKNOWN | UNKNOWN |
| daily_country_report | consumer: analysts (A-1, unnamed) | UNKNOWN | DuckDB table, read by SQL | at most 30 x 9 rows for the current month | daily | UNKNOWN |

Arrival pattern (full snapshot vs new day only) is UNKNOWN (Q12); ADR-005 assumes full snapshots.

## Security

- Classification: customer_name, email = PII (not landed, ADR-004); customer_id = pseudonymous key (internal); everything else internal. The report has no PII.
- Access: filesystem permissions on the DuckDB file; no accounts or roles exist locally. Secrets: none; rule R22 forbids credentials in the repo.
- Encryption: none at rest or in transit (local, synthetic). Acceptable only because the data is synthetic; a real deployment needs a review (open question).
- Audit: `audit.run_log` records every run (run_id, report_dates, input batch ids, status, counts).

## Non-functional requirements

| NFR | Target | How measured |
|---|---|---|
| Freshness | report for day D available within 24 h of D's files arriving (profile `freshness_hours: 24`; placeholder from "daily", Q1) | `audit.run_log.finished_at - raw._loaded_at` check, R19 |
| Run time | full 30-day backfill under 60 s on a laptop (proposed; volume is about 18k rows) | run_log duration; test asserts |
| Correctness | the 8-column contract holds, reconciliation R12-R14 pass on every run | conformance queries |
| Idempotency | same input, same mart rows, run twice = identical hash | R8 |
| Recovery | RPO = 0 (inputs are kept and read-only), RTO = one re-run of the backfill (under 60 s) | rebuild from raw files test |
| Availability | none required (offline batch) | n/a |
| Volume growth | none stated; design is valid to about 100x (millions of rows) on one DuckDB file without change | not measured, stated as assumption |
| Retention | UNKNOWN (Q3): raw files kept untouched; warehouse is rebuildable | n/a |

## Technology decisions

| Component | Chosen | Rejected alternative | Why | ADR |
|---|---|---|---|---|
| Paradigm | ELT | ETL, ETLT | raw must be kept, SQL transforms, embedded engine | ADR-001 |
| Engine | DuckDB (local file) | any cloud warehouse | constraint: local only, no cloud | profile |
| Transform tooling | plain SQL files + small Python runner, pytest | dbt-core + dbt-duckdb | no dependency available, tiny volume, rules stay readable SQL | ADR-002 |
| Defects and status semantics | quarantine schema, OK/WARN/FAIL with contradictions informational | analyst A-12 as written (contradictions drive WARN) | WARN would be about 99% of rows | ADR-003 |
| PII | prune name and email at raw landing | land all columns | no consumer needs them | ADR-004 |
| Loading and idempotency | append raw by batch, per-date delete+insert in marts | truncate-reload, incremental merge | audit of loads, simplest correct | ADR-005 |
| Orchestration | one CLI command `python -m src_scenario.run --report-date D` (or `--backfill`), schedulable by cron later | Airflow/Dagster | one flow, one daily job; not needed | this doc |

## Environments

Local only: `dev` = the developer's DuckDB file. A "staging then promote" path exists for the toy pipeline (`src/deploy.py`), but nothing
is deployed anywhere for this scenario; any promotion would need a named human approver (CLAUDE.md). Approval of this
architecture pack is required before the build starts.

## Cost

Zero by constraint (local CPU). Biggest driver: none. Time cost: the builder and evaluator.

## Risks (ranked)

1. Semantics of business rules are assumptions (report_date basis A-2, revenue definition A-9, negative quantity A-7, DQ status A-12). A wrong assumption silently misstates revenue. Mitigation: each is a numbered rule plus a test, and open questions below.
2. The status column could be useless (about 263/264 rows WARN under the analyst's original rule, ADR-003). Mitigation: ADR-003 option 2; needs owner sign-off.
3. Arrival pattern unknown (Q12): wrong assumption breaks the staging "latest batch" logic.
4. No currency column: revenue is in an unspecified single currency (Q8).
5. PII: raw copy of the CSV exists on disk regardless; real-data handling not decided (Q5).

## Open questions

See the list in data-architecture.md (they are the requirements open questions Q1..Q12 plus architect additions Q13..Q16); none was guessed.
