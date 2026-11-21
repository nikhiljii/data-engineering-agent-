-- Mart tables. Keys are enforced by contract checks and tests (R6), not by constraints (delete+insert inside one transaction).
CREATE TABLE IF NOT EXISTS marts.daily_country_report (
    report_date DATE, country VARCHAR, total_orders INTEGER, completed_orders INTEGER,
    gross_revenue DECIMAL(18, 2), successful_payments INTEGER, failed_payments INTEGER, data_quality_status VARCHAR);
CREATE TABLE IF NOT EXISTS marts.daily_country_report_dq (
    report_date DATE, country VARCHAR, duplicates_collapsed INTEGER, unknown_country_orders INTEGER, excluded_orders INTEGER,
    amount_mismatch_orders INTEGER, completed_without_payment INTEGER, no_payment_orders INTEGER,
    payments_excluded INTEGER, status_payment_contradictions INTEGER);
CREATE TABLE IF NOT EXISTS marts.run_summary (
    run_id VARCHAR, report_dates VARCHAR[],
    raw_rows_customers INTEGER, raw_rows_orders INTEGER, raw_rows_payments INTEGER,
    q_orders_duplicate_order_surplus INTEGER, q_orders_invalid_order_date INTEGER,
    q_orders_negative_quantity INTEGER, q_orders_invalid_quantity INTEGER,
    q_payments_orphan_payment INTEGER, q_payments_order_excluded INTEGER,
    distinct_order_ids INTEGER, distinct_excluded_orders INTEGER, excluded_share DOUBLE,
    orphan_payment_count INTEGER,
    fk_violations_stg_orders_customer_id INTEGER, fk_violations_stg_payments_order_id INTEGER,
    rows_ok INTEGER, rows_warn INTEGER, rows_fail INTEGER,
    run_status VARCHAR, stop_reason VARCHAR);
