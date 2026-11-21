-- Audit: run metadata only (never part of the idempotency comparison).
CREATE TABLE IF NOT EXISTS audit.run_log (
    run_id VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP, duration_seconds DOUBLE,
    mode VARCHAR, report_dates VARCHAR[],
    batch_id_customers VARCHAR, batch_id_orders VARCHAR, batch_id_payments VARCHAR,
    status VARCHAR, stop_reason VARCHAR,
    raw_rows_customers INTEGER, raw_rows_orders INTEGER, raw_rows_payments INTEGER,
    quarantined_orders INTEGER, quarantined_payments INTEGER, excluded_share DOUBLE,
    freshness_checks VARCHAR, volume_checks VARCHAR, events VARCHAR);
CREATE TABLE IF NOT EXISTS audit.contract_events (
    run_id VARCHAR, flow VARCHAR, "check" VARCHAR, ok BOOLEAN, detail VARCHAR);
