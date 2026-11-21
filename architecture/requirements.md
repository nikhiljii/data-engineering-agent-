# Requirements

STATUS: DRAFT for architect. Written by `requirements-analyst`. Nothing here chooses a paradigm or designs
the pipeline; assumptions (A-n) are proposals the human can overrule, numbers the human did not give are
under Open questions.

## Goal (one sentence)

Produce, locally with DuckDB and in daily batch, a reporting dataset of orders and payments per report
date and customer country (report_date, country, total_orders, completed_orders, gross_revenue,
successful_payments, failed_payments, data_quality_status) from three raw CSV files that contain known
data-quality defects.

## Consumers and decisions they make

Consumer is not named by the human. Assumed (A-1): business/finance analysts of a fictional e-commerce
company who read the daily country-level table to track order volume, revenue and payment health, and who
must be told when a day/country figure is not trustworthy. Decisions: spotting payment-failure spikes,
tracking revenue per country, deciding whether a day's numbers can be used or need investigation.

## Sources (inventory)

All three are read-only CSV files generated for the scenario; owner = fictional e-commerce company
(no named data owner), access = local file read, change rate = unknown (files are a static snapshot; daily
arrival is assumed, not observed), no credentials.

| Source | Path | Rows (profiled) | Columns | Sensitivity |
|---|---|---|---|---|
| customers | scenario/ecommerce/data/raw/customers.csv | 1,200 | customer_id, customer_name, email, country, created_at | PII: name, email |
| orders | scenario/ecommerce/data/raw/orders.csv | 6,040 | order_id, customer_id, order_date, product_id, quantity, unit_price, order_status | customer_id is a pseudonymous key |
| payments | scenario/ecommerce/data/raw/payments.csv | 5,950 | payment_id, order_id, payment_date, payment_amount, payment_status | none direct |

Observed shape: order_date range (valid rows) 2026-09-01..2026-09-30, 30 distinct dates, 174-231 order rows
per day; payment_date range 2026-09-01..2026-09-30; 8 countries (GB 169, IT 162, NL 155, US 151, IN 151,
ES 143, DE 143, FR 126 customers); order_status: completed 4318, refunded 598, pending 571, cancelled 553;
payment_status: success 5024, failed 557, pending 369. No currency column exists.

## Profiled data-quality defects (DuckDB, row-level evidence)

Counts are from the actual files; commands are in the analyst's report and reproducible with
`read_csv(..., all_varchar=true)` queries.

| # | Defect | Observed |
|---|---|---|
| D1 | Duplicate order_id | 40 order_ids appear twice (40 surplus rows; 6,040 rows, 6,000 distinct ids). 39 pairs are fully identical; 1 pair (O002646) conflicts: one copy has customer_id NULL, the other C00066 |
| D2 | Missing customer_id on orders | 60 rows (empty value), 60 distinct orders; includes one of the O002646 duplicate copies |
| D3 | Invalid order_date | 30 rows, 30 distinct orders: '2026-13-45' (9), '0000-00-00' (9), 'not-a-date' (7), '31/02/2026' (5). 28 of these orders have a payment |
| D4 | Negative quantity | 25 rows (min -3), 25 distinct orders; 17 of them are status completed. No zero or non-numeric quantities |
| D5 | Orders with no payment record | 80 distinct orders (completed 49, refunded 14, pending 11, cancelled 6) |
| D6 | Orphan payments | 30 payments whose order_id is not in orders (order_ids O9000xx; payment_ids PAYX00xx) |
| D7 | Payment amount differs from order total (quantity x unit_price), one payment per order | 75 of 5,920 comparable orders (comparable = single order version, joined to a payment). Ratios cluster at ~0.5, 1.5, 2.0; 25 of the 75 are the negative-quantity orders (negative total vs positive payment) |
| - | Clean checks | customers: 0 duplicate ids, 0 missing country/email/name, 0 invalid created_at; every non-null orders.customer_id exists in customers; payment_id unique; each order has at most 1 payment; no payment before its order date; no whitespace/case anomalies in status or country; unit_price all > 0; payment_amount all > 0 and numeric; payment_date all valid |

Not observed (so no rule is designed for them beyond a generic guard): invalid payment dates, missing
order_id/payment_id, duplicate customers, multiple payments per order, orders referencing unknown customers.

Cross-tab worth knowing (joined orders to payments): payment_status=success appears on orders whose
status is cancelled (176), pending (212), refunded (582) and completed (4,024); completed orders carry
payment failed in 220 cases. Status combinations are therefore not consistent and are not a defect list
the human named; see A-6.

## Freshness, volume, retention, budget

- Freshness: "daily" batch stated. No numeric SLA given (see Open question 1).
- Volume: about 6k order rows, 6k payments, 1.2k customers for 30 days (about 200 orders/day). Tiny; no scale requirement stated.
- Retention: not given (Open question 3). Raw files are the read-only input and are kept as-is.
- Budget: local execution only, no cloud, no spend. Cost = 0 by constraint.
- Output grain: one row per (report_date, country); max expected = 30 dates x 8 countries (+ an Unknown-country bucket, A-5).

## PII and compliance

customers.csv holds customer_name and email, so `pii: true`. The reporting output has no PII columns
(country-level aggregates only). No masking-before-landing requirement is set (`mask_before_landing: false`).
Justification: data is synthetic, all processing is local on a read-only file, and the output carries no
direct identifiers. Counter-argument for the architect/governance: if raw landing copies customers.csv into a
shared or persistent DuckDB file, name/email would be stored in clear; mitigation options are to land only
customer_id + country (column pruning) or hash email. Whether the real company would require masking is a
legal-basis question for the human (Open question 5); this analyst does not decide it.

## Team skills

Not given. Assumed (A-12): SQL is the working language (DuckDB), moderate Python. Profile uses
`sql_strength: medium` as a placeholder (Open question 6).

## Assumptions for ambiguous business rules

Every item is a proposal; the architect and human may overrule. Each one needs a test later.

| ID | Topic | Assumption |
|---|---|---|
| A-1 | Consumer | Finance/business analysts reading a daily country table (see above). |
| A-2 | report_date | Date of the order (`order_date`, parsed as strict ISO `YYYY-MM-DD`). Order metrics are attributed to order_date. Payment metrics are attributed to the order's order_date, not payment_date, so that successful/failed payments relate to the same orders in the same row. (Alternative: payment_date. All observed payment dates are valid and none precede order date, so the choice is a business one, Open question 4.) |
| A-3 | Grain / country | One output row per (report_date, country). `country` is the customer's country from customers.csv via orders.customer_id. |
| A-4 | Duplicate orders | De-duplicate on order_id before any counting, one order = one row. Identical duplicates collapse to one. For the conflicting pair, keep the most complete version (non-null customer_id) and flag the order as `had_duplicate`. Never sum both copies. Surplus rows are counted in a quality metric, not dropped silently. |
| A-5 | Missing customer_id / missing country | An order with no customer_id cannot be given a country. It is kept (it is a real order and revenue) in country = `UNKNOWN` rather than dropped, and flagged. Rationale: dropping 60 rows (about 1%) would understate totals; the row-drop escalation rule (>15%) is not triggered but the choice is visible. Customers file has no missing country today; any future missing country gets the same `UNKNOWN` bucket. |
| A-6 | completed_orders | Order after de-duplication with `order_status = 'completed'` and passing validity (A-7). Refunded, cancelled and pending are NOT completed. Payment status is not used to define completed (data shows completed orders with failed payments: 220). Status/payment contradictions are reported as a quality signal, not corrected. |
| A-7 | Invalid order rows | Order with unparseable order_date (30) cannot be assigned a report_date: it is excluded from the dataset rows and quarantined (with reason) and counted in a data-quality metric, not silently dropped. Negative quantity (25) is invalid: the order is excluded from total_orders, completed_orders and gross_revenue, quarantined with reason `negative_quantity`, and still counted in the quality metric. Rationale: a negative quantity on a `completed` order is an ambiguous return/entry error; business owner should confirm (Open question 7). |
| A-8 | total_orders | Count of distinct valid orders (after A-4, A-7) for that (report_date, country), all statuses. |
| A-9 | gross_revenue | Sum of `quantity x unit_price` over completed, valid, de-duplicated orders only; before refunds, discounts, tax, shipping; in a single unspecified currency (no currency column; assumed to be one currency, Open question 8). Refunded orders are excluded from gross_revenue (their status says money went back) and no refund netting is done, so this is not net revenue. gross_revenue is computed from order lines, NOT from payment_amount, so payment mismatches do not alter it. |
| A-10 | successful_payments / failed_payments | Count of payment records with payment_status 'success' / 'failed' joined to a valid, de-duplicated order in that row. `pending` payments are counted in neither column. These are counts of payments, not amounts. Orphan payments (no order, 30) cannot be attributed to a date/country: they are excluded from the two columns and reported in the quality metric/quarantine. |
| A-11 | Payment amount mismatch | An order whose single payment amount differs from quantity x unit_price (tolerance 0.01) is flagged. It is not corrected and does not change counts. Missing payment (80 orders) is flagged (and expected for pending/cancelled, suspicious for completed). |
| A-12 | data_quality_status values | Per (report_date, country) row: `OK` (no issue touches any order or payment attributed to the row); `WARN` (at least one flagged-but-included issue: duplicate collapsed, missing customer_id in UNKNOWN bucket, payment amount mismatch, completed order without payment, status/payment contradiction); `FAIL` (at least one record that would belong to the row was excluded as invalid, e.g. negative_quantity). Rows for orders with invalid dates cannot be attributed to any report_date; they are reported in a separate unattributed quality summary, and a run-level status is required (A-14). Values are an ordered set OK < WARN < FAIL, the worst applicable wins. Exact thresholds are open (Open question 9). |
| A-13 | Idempotency / rerun | Pipeline is rerunnable for a given report_date; reruns overwrite that date's rows, never append duplicates. Backfill of all 30 dates is the first run. |
| A-14 | Run-level checks | Besides the row column, a run summary exposes: input row counts, quarantined row counts by reason, share of dropped rows (escalate if >15% of orders per CLAUDE.md hard rule), orphan payment count. |
| A-15 | Raw retention | Raw CSVs are kept untouched; raw landing is a faithful copy (all columns as text), so defects stay reproducible. |
| A-16 | Time | report_date is a calendar date, no time zone; no timestamps in sources. |
| A-17 | Currency | One currency assumed; output has no currency column because the required schema has none. |

## Output contract (as requested, unchanged)

report_date (DATE), country (VARCHAR, ISO-2 plus `UNKNOWN`), total_orders (INT), completed_orders (INT),
gross_revenue (DECIMAL(18,2)), successful_payments (INT), failed_payments (INT), data_quality_status
(VARCHAR: OK | WARN | FAIL). Key = (report_date, country). Types are proposals for the architect/modeler.

## Constraints

- Local execution only, DuckDB, no cloud services, no real credentials.
- Batch, daily; no streaming.
- Inputs are read-only; do not modify scenario files.
- Defects must be handled by explicit rules that are tested, never by loosening tests or guards.

## Draft project-profile.yaml values

See `project-profile.yaml` at repo root (adapted from `profiles/elt-cloud-warehouse.yaml`):
processing batch; target embedded_warehouse / duckdb / elastic_compute false; sources three `file`
entries; pii true, mask_before_landing false; transforms sql; sql_strength medium (placeholder);
keep_raw true; schema_change_frequency low; storage_constrained false; freshness_hours 24; paradigm left
`auto` (architect decides and records in ADR-001). The profile was validated with
`src.profile.load_profile` (command and output in the analyst report).

## Open questions for the human

1. Freshness SLA: is 24 h (a new daily run) enough, and what is the latest time of day the report must be ready? (Profile uses 24 as a placeholder derived from "daily".)
2. Which report_date does the business want for payment metrics: order_date (assumed) or payment_date?
3. Retention of raw and reporting data (none stated).
4. Who is the named consumer and who signs off on data_quality_status semantics?
5. Is masking/pseudonymising customer_name and email required if this were real data, and may the raw landing store them in clear? (None assumed, see PII section.)
6. Team SQL/Python strength (placeholder `medium`).
7. Negative quantity: returns, entry errors, or something to net against revenue? Currently excluded and flagged FAIL.
8. Single currency confirmed? Is gross_revenue allowed to exclude refunded orders, or should it include everything not cancelled?
9. data_quality_status: are OK/WARN/FAIL and the proposed triggers what you want, and should WARN/FAIL block publication of the table?
10. Orders with invalid dates (30) and orphan payments (30) cannot be placed in the report: is a separate quarantine/summary table acceptable, or must they appear in the main dataset?
11. Missing customer_id: keep in `UNKNOWN` country (assumed) or exclude?
12. Expected arrival pattern: does each daily run deliver a full snapshot of all history (as the current files look) or only the new day's rows (affects idempotency and duplicate handling)?

## Handoff

Next: `architect` (ADR-001 paradigm, system and data architecture). Not decided here: ELT/ETL/ETLT,
layering, tooling, orchestrator, contracts.
