# ADR-005: Raw loading and idempotency per report_date

Status: PROPOSED (needs human approval before any build)

## Context
A-13 (rerunnable per report_date), A-15, open question Q12 (does a daily delivery contain the full history or only the new day?). The current files are one full 30-day snapshot. Late payments change the successful/failed counts of an earlier order date because payment metrics are attributed to order_date (A-2).

## Options considered
1. Raw append-only with batch metadata; staging reads the latest batch per source; marts rebuilt by delete+insert for the report_date(s) in a single transaction.
2. Raw truncate-and-reload each run, no history of loads. Simple but loses load audit.
3. Incremental merge by key into marts. More moving parts than 6k rows justify.

## Decision
Option 1.
- Raw loader computes sha256 of each source file. If a batch with the same (source, sha256) already
  exists, the load is a no-op (idempotent). Otherwise it appends the file with `_batch_id`, `_loaded_at`, `_source_file`, `_source_row_number`.
- Staging views read only the latest batch per source. This assumes the full-snapshot arrival pattern (Q12);
  if deltas arrive, staging becomes a union of batches and a new ADR is required.
- A run is `run(report_date)`: it recomputes the report for that date and for the previous
  `LOOKBACK_DAYS` days (constant, proposed 7, not validated) in one transaction: `DELETE FROM marts.daily_country_report WHERE report_date IN (...)` then `INSERT ... SELECT`. Same for the dq detail table. The first run is a backfill of all dates found in the data.
- Mart content is a pure function of the latest raw batches; no timestamps or random values in mart columns. Run metadata goes to `audit.run_log` and `marts.run_summary` (append, keyed by run_id), which are excluded from the idempotency comparison.
- Quarantine tables are rebuilt per run (full replace inside the same transaction) because they are derived from the latest batch.

## Consequences
Rerunning the same date with the same raw data gives identical mart rows (rule R8). A late payment only
corrects history inside the lookback window; older dates need an explicit backfill command.

## Revisit when
Q12 is answered with delta files, retention is defined, or volume makes full recompute slow.
