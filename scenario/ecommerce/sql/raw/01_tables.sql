-- Raw layer: every data column VARCHAR exactly as in the CSV (R1), plus load metadata.
-- customers keeps only customer_id, country, created_at: customer_name and email are never landed (ADR-004, R2).
-- Append-only by batch (ADR-005). _loaded_at is written by the loader (the only place a clock is read).
CREATE TABLE IF NOT EXISTS raw.customers (
    customer_id VARCHAR, country VARCHAR, created_at VARCHAR,
    _batch_id VARCHAR, _loaded_at TIMESTAMP, _source_file VARCHAR, _source_sha256 VARCHAR, _source_row_number INTEGER);
CREATE TABLE IF NOT EXISTS raw.orders (
    order_id VARCHAR, customer_id VARCHAR, order_date VARCHAR, product_id VARCHAR,
    quantity VARCHAR, unit_price VARCHAR, order_status VARCHAR,
    _batch_id VARCHAR, _loaded_at TIMESTAMP, _source_file VARCHAR, _source_sha256 VARCHAR, _source_row_number INTEGER);
CREATE TABLE IF NOT EXISTS raw.payments (
    payment_id VARCHAR, order_id VARCHAR, payment_date VARCHAR, payment_amount VARCHAR, payment_status VARCHAR,
    _batch_id VARCHAR, _loaded_at TIMESTAMP, _source_file VARCHAR, _source_sha256 VARCHAR, _source_row_number INTEGER);
