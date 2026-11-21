-- Quarantine tables: rejected source rows with all reasons and the original values (all VARCHAR). Rebuilt every run (ADR-005).
CREATE TABLE IF NOT EXISTS quarantine.orders_rejected (
    order_id VARCHAR, customer_id VARCHAR, order_date VARCHAR, product_id VARCHAR, quantity VARCHAR, unit_price VARCHAR, order_status VARCHAR,
    reason VARCHAR, primary_reason VARCHAR, _batch_id VARCHAR, _source_row_number INTEGER);
CREATE TABLE IF NOT EXISTS quarantine.payments_rejected (
    payment_id VARCHAR, order_id VARCHAR, payment_date VARCHAR, payment_amount VARCHAR, payment_status VARCHAR,
    reason VARCHAR, primary_reason VARCHAR, _batch_id VARCHAR, _source_row_number INTEGER);
