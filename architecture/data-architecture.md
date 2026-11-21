# Data architecture: ecommerce-daily-orders

STATUS: PROPOSED. Written by the `architect` after `system-architecture.md`. Not approved; no build until a human approves.
Conforms to `project-profile.yaml` and `requirements.md` (assumptions A-n are cited; where this document decides differently it says so and links an ADR).
Pipeline code location: `scenario/ecommerce/` (package `src_scenario`), not `src/` (ADR-002).

## Paradigm

ELT (ADR-001; scorer output ELT 7 / ETL 0, architect review agrees). One light pre-landing step: PII columns of customers are pruned at load (ADR-004).

## Layers

All in one DuckDB file `scenario/ecommerce/warehouse/ecommerce.duckdb`, one schema per layer (R4).

| Schema | Content | Materialization | Rule |
|---|---|---|---|
| `raw` | `customers`, `orders`, `payments`: all columns VARCHAR exactly as in the CSV, plus `_batch_id`, `_loaded_at`, `_source_file`, `_source_sha256`, `_source_row_number`. customers has only customer_id, country, created_at (ADR-004). Append-only by batch. | table | no logic (R1) |
| `staging` | `stg_customers`, `stg_orders`, `stg_orders_duplicate_rows`, `stg_payments`. Typed (`try_cast`), trimmed, latest batch per source, orders de-duplicated to one row per order_id, validity flags and reasons added. Invalid rows are kept here with `is_valid = false`, not deleted. | view | one model per source table (R5) |
| `intermediate` | `int_orders_enriched` (order + country + validity + totals), `int_order_payments` (order + its payment + flags) | view | refs staging only |
| `marts` | `daily_country_report` (the output), `daily_country_report_dq` (per-row issue counts), `run_summary` (one row per run) | table | keyed (R6) |
| `quarantine` | `orders_rejected`, `payments_rejected`: the rejected source rows with `reason`, `batch_id`, and the full raw values | table | R16 |
| `audit` | `run_log` (run_id, started_at, finished_at, report_dates, batch ids per source, status, counts) | table, append | run metadata only |

Dependency direction: raw -> staging -> intermediate -> marts and quarantine; audit is written by the runner. Only staging reads raw (R3).

## Model

Modeling approach: **flat aggregate table** for the single, stable consumer (daily country table). Kimball dimensions and Data Vault are rejected: three small tables, one output, no history
requirement; a star schema adds models nobody queries (data-modeling skill: "flat wide tables only for a single, stable use"). `customers` is used as a lookup, not published as a dimension.

| Table | Grain | Primary key | Foreign keys | Owner |
|---|---|---|---|---|
| `staging.stg_customers` | one row per customer | customer_id | none | UNKNOWN (data) / pipeline team |
| `staging.stg_orders` | one row per distinct order_id after de-duplication | order_id | customer_id -> stg_customers (nullable) | same |
| `staging.stg_orders_duplicate_rows` | one row per surplus duplicate source row | (order_id, _source_row_number) | order_id -> stg_orders | same |
| `staging.stg_payments` | one row per payment | payment_id | order_id -> stg_orders (orphans exist) | same |
| `marts.daily_country_report` | one row per (report_date, country) with at least one attributed order | (report_date, country) | none | analysts consume; pipeline team owns |
| `marts.daily_country_report_dq` | same grain and key | (report_date, country) | -> daily_country_report | same |
| `marts.run_summary` | one row per run | run_id | none | pipeline team |
| `quarantine.orders_rejected` | one row per rejected source order row | (order_id, _source_row_number) | none | pipeline team |
| `quarantine.payments_rejected` | one row per rejected source payment row | (payment_id) | none | pipeline team |

Foreign keys are declared in `architecture/foreign-keys.yaml` (R7). Two are known-violated by design and carry `severity: warn`.

### Derivations (each is a numbered business rule; A-n = requirements assumption)

- **B1 report_date** = order_date parsed strictly as ISO `YYYY-MM-DD` (A-2). Payment metrics are attributed to the order's order_date, not payment_date. Open question Q2.
- **B2 country** = customers.country joined on orders.customer_id; NULL customer_id, or a customer_id missing from customers, gives `UNKNOWN` (A-5; an unknown id is a generic guard, none observed).
- **B3 de-duplication**: one row per order_id. Choose the version with non-null customer_id first, then a deterministic tie-break on the remaining columns (order_date, product_id, quantity, unit_price, order_status, source row number). Surplus rows go to quarantine reason `duplicate_order_surplus`; the kept order has `had_duplicate = true`. Never sum copies (A-4).
- **B4 order validity**: invalid if order_date does not parse (`invalid_order_date`) or quantity < 0 (`negative_quantity`); both reasons are recorded if both apply. Invalid orders are excluded from all measures and quarantined (A-7). Zero or non-integer quantity: not observed; treated as invalid with reason `invalid_quantity` (generic guard).
- **B5 total_orders** = count of valid orders in the row, any status (A-8). **completed_orders** = valid orders with `order_status = 'completed'` (A-6). Unknown statuses fail the staging contract (R5, accepted values).
- **B6 gross_revenue** = sum(quantity x unit_price) over valid completed orders, rounded to 2 decimals, DECIMAL(18,2), from order lines not payments, before refunds, in one unspecified currency (A-9, A-17, Q8).
- **B7 successful_payments / failed_payments** = count of payment rows with status `success` / `failed` whose order is valid in the row. `pending` is in neither column (A-10). Payments of invalid orders and orphans are quarantined (`order_excluded`, `orphan_payment`).
- **B8 data_quality_status**: worst of OK < WARN < FAIL per row, with triggers exactly as in ADR-003 (this deviates from analyst A-12 on one point: status/payment contradictions are informational).
- **B9 sparse grain**: a row exists only if at least one order with a valid date (valid or excluded for quantity) maps to it; a FAIL row can show total_orders = 0. No zero-filled calendar (Q13).

## History

- No SCD2. `country` is overwritten (SCD1): a rerun uses the customer's current country, so an old date can change if a customer moves; no customer-change history exists in the source (Q15).
- Deletes: sources are snapshots; an order that disappears from a later snapshot disappears from the report on the next run of its date. Not observed.
- Late-arriving data: a late payment changes its order's report_date row; handled by the lookback window and explicit backfill (ADR-005). Late or corrected orders are handled the same way.
- Raw keeps every loaded batch (history of loads); staging reads the latest.

## Physical design

- Volume is about 18k rows total: no partitioning, no clustering. Full recompute is cheaper than incremental logic.
- Staging and intermediate are views; marts and quarantine are tables (deterministic rebuild in one transaction).
- The mart is written by `DELETE ... WHERE report_date IN (target dates)` then `INSERT ... SELECT` inside one transaction (ADR-005).
- Tuning levers if ever needed: materialize intermediate, pre-aggregate by order_date.

## Orchestration

Single flow `run(report_date | backfill)`, dependency order: load raw -> raw contract -> build staging views -> staging contract -> build intermediate -> transform to marts/quarantine (one transaction) -> mart contract -> write audit and run_summary.

- Schedule: daily, triggered by cron or a human for now (none provisioned).
- Retries: none automatic; a failed run publishes nothing (transaction rolls back) and is safe to re-run.
- Stop rule: a failed contract at a boundary stops before the next stage (data-contracts skill); all checks of the failing flow are still reported.
- Backfill: `--backfill` recomputes every report_date present in the data; `--report-date D` recomputes D and `LOOKBACK_DAYS` before it (proposed 7, Q14).
- Idempotency strategy per report_date: described in ADR-005, enforced by R8, R9, R20.

## Quality contracts (checks are named by what they assert; each run reports every failure)

### Raw layer (stop the run on failure)
| Source | Check |
|---|---|
| customers | exact header = customer_id, customer_name, email, country, created_at; row count > 0; row count within volume band of previous batch (proposed +/-50%, constant at top of the runner) |
| orders | exact header = order_id, customer_id, order_date, product_id, quantity, unit_price, order_status; row count > 0; volume band |
| payments | exact header = payment_id, order_id, payment_date, payment_amount, payment_status; row count > 0; volume band |
| all | all data columns are VARCHAR; load metadata present; file sha256 recorded; freshness: newest `_loaded_at` within 24 h at run time |

### Data contracts per source (staging boundary)

**customers**: customer_id not null and unique; country not null and matches `^[A-Z]{2}$`; created_at parses as ISO date (observed clean; a failure is a contract failure, not a quarantine, because customers is a lookup).
**orders**: order_id not null and unique after de-duplication; order_status in {completed, refunded, pending, cancelled}; unit_price numeric and > 0; quantity integer (negative values are a quarantine case, not a contract failure); order_date unparseable -> quarantine; customer_id empty -> flagged, not failure; raw duplicate rate reported. A missing or non-numeric order_id fails the contract (not observed).
**payments**: payment_id not null and unique; order_id not null; payment_status in {success, failed, pending}; payment_amount numeric and > 0; payment_date parses as ISO date (all valid today; a bad one is a contract failure); at most one payment per order (observed; more than one fails the contract because B7 and D7 assume it); orphan order_id -> quarantine.

### Marts (output contract: `marts.daily_country_report`)
| Column | Type | Rule | Classification |
|---|---|---|---|
| report_date | DATE | not null, part of key | internal |
| country | VARCHAR | not null, part of key; in {DE, ES, FR, GB, IN, IT, NL, US, UNKNOWN} (set extended only by an ADR) | internal |
| total_orders | INTEGER | >= 0 | internal |
| completed_orders | INTEGER | 0 <= completed_orders <= total_orders | internal |
| gross_revenue | DECIMAL(18,2) | >= 0 (negative quantities are excluded) | confidential (financial) |
| successful_payments | INTEGER | >= 0 | internal |
| failed_payments | INTEGER | >= 0 | internal |
| data_quality_status | VARCHAR | in {OK, WARN, FAIL} | internal |

Exactly these 8 columns in this order; no extra columns in this table (explanations live in `_dq`).

`marts.daily_country_report_dq` columns (all INTEGER except key): report_date, country, `duplicates_collapsed`, `unknown_country_orders`, `excluded_orders` (negative or invalid quantity), `amount_mismatch_orders`, `completed_without_payment`, `no_payment_orders`, `payments_excluded`, `status_payment_contradictions`. Status derives from these counts only (R11).

`marts.run_summary` columns: run_id, report_dates, raw rows per source, quarantined order rows by reason, quarantined payment rows by reason, distinct excluded orders, excluded_share, orphan_payment_count, run_status (`PUBLISHED` | `STOPPED`), dates by status (OK/WARN/FAIL counts). Unattributable data (invalid dates, orphans) is visible only here and in quarantine.

### Quarantine design
- Accepted reasons: orders `duplicate_order_surplus`, `invalid_order_date`, `negative_quantity`, `invalid_quantity`; payments `orphan_payment`, `order_excluded`. A row with several reasons carries all of them (pipe separated, sorted) and one `primary_reason` by this precedence: invalid_order_date, negative_quantity, invalid_quantity, duplicate_order_surplus.
- Each quarantine row holds the original raw values (all VARCHAR) plus `_batch_id` and `_source_row_number`, so it can be traced and replayed.
- Rebuilt every run from the latest batch (no manual edits). Nothing is deleted from raw.
- Expected counts on the current files (from the analyst's profiling, the builder's tests must reproduce them): orders 40 surplus + 30 invalid date + 25 negative quantity = 95 rows; payments 30 orphan + 28 for invalid-date orders + payments of the 25 negative-quantity orders (count not profiled; UNKNOWN) .
- Threshold: excluded distinct orders (invalid date + invalid quantity types) / distinct order_ids > 15% stops the run, publishes nothing, and is escalated to a human (R17). Today about 0.9%.
- Owner and retention of the quarantine: UNKNOWN (Q16).

## Governance and PII

| Column | Classification | Masking |
|---|---|---|
| customers.customer_name, customers.email | PII | not landed anywhere in DuckDB (ADR-004; rule R2) |
| customers.customer_id, orders.customer_id | internal (pseudonymous key) | none |
| customers.country, created_at | internal | none |
| orders.* (other columns), payments.* | internal; unit_price, payment_amount confidential (financial) | none |
| marts columns | per table above | none needed (no PII) |
| quarantine columns | same as the source columns | none (no PII present) |

Retention: UNKNOWN (Q3); raw CSVs untouched; the warehouse is rebuildable. Access: filesystem. Lineage: table-level lineage is this document's layer/model tables plus `ref`-style naming in SQL file headers; no lineage tool.

## Observability

- Per run, in `audit.run_log` and `marts.run_summary`: rows in per source, rows kept, rows quarantined by reason, excluded share, orphan count, per-status row counts, duration.
- Monitors: freshness (newest raw batch age vs 24 h), volume (row count vs previous batch band), quarantine rate vs 15%, WARN/FAIL share of report rows.
- Alerting: none provisioned (local); a stopped run exits with a non-zero code and writes the reason. Who is alerted: UNKNOWN (Q4). Runbook: to be written by the `documentation` agent after build.

## Cost

Zero spend. Volume about 18k rows; a full backfill is expected under 60 s. Biggest driver: none. Tuning lever per layer: raw (none), staging (materialize), marts (none needed).

## Conformance rules (numbered, testable; the evaluator checks each by command or query)

`src.conformance` only understands dbt projects, so these are verified by tests and SQL assertions under `scenario/ecommerce/tests/` (ADR-002). Where a number below in `[ ]` appears it is the value on the current files.

- **R1**: raw tables hold source values unmodified: every data column is VARCHAR, no business logic; raw row counts equal CSV data row counts [customers 1,200; orders 6,040; payments 5,950]. Check: information_schema + count query.
- **R2**: no column named customer_name or email (case-insensitive) exists in any table or view of the DuckDB file. Check: information_schema query returns 0 rows.
- **R3**: only staging models reference the `raw` schema; intermediate, marts, quarantine SQL reference only the layer(s) upstream of them. Check: grep over `scenario/ecommerce/sql/`.
- **R4**: layers live in separate schemas: raw, staging, intermediate, marts, quarantine, audit.
- **R5**: there is exactly one staging model per source table (stg_customers, stg_orders, stg_payments; plus stg_orders_duplicate_rows as a declared helper), each with a unique and not-null test on its key, and accepted-values tests on order_status and payment_status.
- **R6**: every mart table has a unique and a not-null test on its key (daily_country_report and _dq: (report_date, country); run_summary: run_id).
- **R7**: every foreign key in `architecture/foreign-keys.yaml` has a relationships test; `severity: warn` ones must also report the violation count into run_summary. Check: command comparing the YAML with the test list.
- **R8**: every model and run is idempotent: running the pipeline twice on the same raw batch yields byte-identical `marts.daily_country_report`, `_dq` and quarantine tables (compare a hash of ordered rows); SQL under `sql/` contains no `now()`, `current_timestamp`, `random()` or `uuid()` (only the loader and audit code may).
- **R9**: rerunning one report_date changes no mart rows for dates outside the run's recompute window. Check: hash rows of other dates before and after.
- **R10**: `marts.daily_country_report` has exactly the columns report_date DATE, country VARCHAR, total_orders INTEGER, completed_orders INTEGER, gross_revenue DECIMAL(18,2), successful_payments INTEGER, failed_payments INTEGER, data_quality_status VARCHAR, in that order.
- **R11**: data_quality_status is in {OK, WARN, FAIL} and equals the value derived from `daily_country_report_dq` per ADR-003 for every row: FAIL if excluded_orders > 0; else WARN if duplicates_collapsed + unknown_country_orders + amount_mismatch_orders + completed_without_payment > 0; else OK.
- **R12**: order reconciliation: count(distinct raw order_id) = sum(total_orders) + count(distinct order_ids quarantined as invalid_order_date or negative_quantity or invalid_quantity). [6,000 = sum(total_orders) + 55]
- **R13**: order row accounting: raw order rows = stg_orders rows + stg_orders_duplicate_rows rows [6,040 = 6,000 + 40]; stg_orders rows = valid + invalid; invalid orders = quarantined orders with an invalid reason [55].
- **R14**: payment row accounting: raw payment rows = payments counted into the report (success + failed + pending on valid orders) + quarantine.payments_rejected rows [5,950]. `pending` payments are counted in neither report column but are in the first term.
- **R15**: gross_revenue per row equals an independently written query: sum(quantity x unit_price) over valid completed orders from staging; and does not change if payment_amount values are altered (mutation test).
- **R16**: every quarantine row has a non-null `primary_reason` in the accepted set (see Quarantine design); no source row is absent from both its staging/valid set and quarantine (follows from R13, R14).
- **R17**: if distinct excluded orders / distinct order_ids exceeds 0.15 (constant `MAX_EXCLUDED_SHARE`), or any raw/staging contract fails, the run exits non-zero, publishes nothing and records `STOPPED` in audit. Check: seeded fault test.
- **R18**: no silent coercion: every `try_cast` in staging that yields NULL from a non-empty source value produces an invalid flag with a reason that ends in quarantine or a contract failure. Check: query for non-empty raw value with NULL typed value and no flag = 0 rows.
- **R19**: every source has a freshness check (newest `_loaded_at` age <= 24 h) and a volume check (row count band against the previous batch); both are recorded in run_log.
- **R20**: the raw load is idempotent: loading a file whose sha256 already exists for that source adds 0 rows. Check: load twice, compare counts.
- **R21**: every column of every mart table appears in the contract file `scenario/ecommerce/contracts/*.yaml` with a classification (public, internal, confidential, PII); PII columns have a masking rule; a test fails on an unclassified column.
- **R22**: no credentials in the repo and no cloud client library or network call in `scenario/ecommerce/` (grep for boto3, google.cloud, azure, snowflake, requests, urllib.request, http).
- **R23**: the build changes nothing under `src/`, `tests/`, `evals/`, `.claude/` or `scenario/ecommerce/data/raw/`. Check: `git diff --stat e2e-baseline -- src tests evals .claude scenario/ecommerce/data/raw` is empty.

## Open questions

Requirements questions Q1..Q12 are open and unchanged (the human must answer them): Q1 freshness SLA and latest ready time; Q2 payment metrics on order_date or payment_date; Q3 retention; Q4 named consumer and who signs off status semantics; Q5 PII masking and legal basis; Q6 team skills; Q7 negative quantity meaning; Q8 single currency and whether refunded orders belong in gross_revenue; Q9 status triggers and whether WARN/FAIL block publication; Q10 separate quarantine/summary acceptable for invalid dates and orphans; Q11 missing customer_id as UNKNOWN; Q12 snapshot vs delta arrival.

Architect additions:
- Q13: sparse grain (no row for a date and country without orders): acceptable, or zero-filled?
- Q14: lookback window `LOOKBACK_DAYS` (proposed 7, no evidence) for late payments.
- Q15: country as current customer value (SCD1) acceptable, or should it be as of the order date?
- Q16: who owns and reviews the quarantine, and for how long is it kept?
- Q17 (consequence of ADR-003): status/payment contradictions (success payment on cancelled/pending/refunded orders: 970; failed payment on completed orders: 220) are informational only; is that agreed, or should they be WARN even if nearly every row becomes WARN?
- Payments of the 25 negative-quantity orders: expected quarantine count not profiled (UNKNOWN); the builder's tests will record the real number.
