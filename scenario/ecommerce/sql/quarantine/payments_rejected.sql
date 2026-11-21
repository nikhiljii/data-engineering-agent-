-- Rejected payment rows: orphan payments (no order) and payments of excluded orders (D3/D4 orders).
SELECT
    p.payment_id, p.order_id_raw AS order_id, p.payment_date_raw AS payment_date, p.payment_amount_raw AS payment_amount,
    p.payment_status,
    CASE WHEN o.order_id IS NULL THEN 'orphan_payment' ELSE 'order_excluded' END AS reason,
    CASE WHEN o.order_id IS NULL THEN 'orphan_payment' ELSE 'order_excluded' END AS primary_reason,
    p._batch_id, p._source_row_number
FROM staging.stg_payments p
LEFT JOIN intermediate.int_orders_enriched o ON o.order_id = p.order_id
WHERE o.order_id IS NULL OR NOT o.is_valid
ORDER BY p.payment_id, p._source_row_number;
