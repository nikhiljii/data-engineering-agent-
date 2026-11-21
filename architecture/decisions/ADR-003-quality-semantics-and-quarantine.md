# ADR-003: Defect handling, quarantine and data_quality_status semantics

Status: PROPOSED (needs human approval before any build)

## Context
requirements.md D1..D7 and assumptions A-4..A-12, A-14. Ad hoc profiling by the architect (DuckDB over
the raw CSVs, approximate de-duplication, read-only) shows that the analyst's WARN trigger list, taken
literally, makes WARN meaningless:
- 264 (report_date, country) cells exist after de-duplication and removing invalid dates.
- 24 cells contain a negative-quantity order (FAIL).
- 263 of 264 cells would be WARN if status/payment contradictions count (completed with failed payment
  220, success on cancelled/pending/refunded 970).
- 126 of 264 would be WARN if contradictions are excluded and only amount mismatch, completed order
  without payment, UNKNOWN country and collapsed duplicates count.
These are architect's exploratory numbers, not a validated result; the builder's tests will produce the real ones.

## Options considered
1. Analyst A-12 as written: every contradiction triggers WARN. Result: a status column that says WARN almost always, so consumers learn to ignore it.
2. Contradictions are recorded and counted in a detail table but do not drive WARN; WARN needs a
   money-relevant or attribution-relevant issue.
3. Drop WARN and have only OK/FAIL. Loses the "included but flagged" signal the requirements ask for.

## Decision
Option 2, pending human confirmation (open question Q9). Row-level handling:

| Defect | Handling | Row status effect | Where it is recorded |
|---|---|---|---|
| D1 duplicate order_id | Keep one version (most complete: non-null customer_id first, then deterministic tie-break on remaining columns); surplus copies go to quarantine with reason `duplicate_order_surplus` | WARN for the kept order's row | quarantine.orders_rejected, dq detail `duplicates_collapsed` |
| D2 missing customer_id | Order kept, country `UNKNOWN` | WARN | dq detail `unknown_country_orders` |
| D3 invalid order_date | Order quarantined (`invalid_order_date`); no report_date, so no mart row | none (cannot attribute); run-level counter | quarantine.orders_rejected, marts.run_summary |
| D4 negative quantity | Order quarantined (`negative_quantity`), excluded from all measures; report row still exists for its date and country | FAIL | quarantine.orders_rejected, dq detail `excluded_orders` |
| D5 order without payment | Kept; flagged only when status is `completed` | WARN if completed | dq detail `completed_without_payment`; others counted as `no_payment_orders` informational |
| D6 orphan payment | Quarantined (`orphan_payment`), not counted | none (no row); run-level counter | quarantine.payments_rejected |
| D7 payment amount differs | Kept and counted, flagged, never corrected; tolerance 0.01 | WARN | dq detail `amount_mismatch_orders` |
| Payment of an excluded order (D3/D4 orders) | Quarantined (`order_excluded`), not counted in successful/failed | follows the order (FAIL for D4) | quarantine.payments_rejected |
| Status/payment contradiction | Counted only | none (informational) | dq detail `status_payment_contradictions` |

A row's `data_quality_status` is the worst of OK < WARN < FAIL. A row exists in the report for a
(report_date, country) when at least one order with a valid date is attributed to it, including orders
that were excluded for negative quantity; consequently a FAIL row can have total_orders = 0.

Escalation: if excluded orders (D3 + D4, distinct order_ids) exceed 15% of distinct order_ids in the
snapshot, or any raw-layer contract check fails, the run stops and publishes nothing (CLAUDE.md hard
rule). Observed today: 55 of 6,000 (about 0.9%).

## Consequences
- Output schema stays the 8 required columns; explanation lives in `marts.daily_country_report_dq` (same key) and `marts.run_summary`.
- Rules R11 and R12 depend on this table. If the human rejects option 2 the contradiction rule moves into WARN and a new ADR supersedes this one.

## Revisit when
The business owner answers Q7, Q9, Q10 or Q11, or the WARN share of rows is shown to be above an agreed ceiling.
