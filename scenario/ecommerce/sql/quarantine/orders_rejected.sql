-- Rejected order rows: kept orders that are invalid (B4) plus every surplus duplicate row (B3).
-- A surplus row carries only duplicate_order_surplus: validity belongs to the kept version of the order.
-- reasons: all of them, sorted, pipe separated; primary_reason by precedence invalid_order_date, negative_quantity, invalid_quantity, duplicate_order_surplus.
WITH rejected AS (
    SELECT order_id, customer_id_raw, order_date_raw, product_id, quantity_raw, unit_price_raw, order_status,
           invalid_reasons AS reasons, _batch_id, _source_row_number
    FROM staging.stg_orders
    WHERE NOT is_valid
    UNION ALL
    SELECT order_id, customer_id_raw, order_date_raw, product_id, quantity_raw, unit_price_raw, order_status,
           ['duplicate_order_surplus'] AS reasons, _batch_id, _source_row_number
    FROM staging.stg_orders_duplicate_rows
)
SELECT
    order_id, customer_id_raw AS customer_id, order_date_raw AS order_date, product_id,
    quantity_raw AS quantity, unit_price_raw AS unit_price, order_status,
    array_to_string(list_sort(reasons), '|') AS reason,
    CASE
        WHEN list_contains(reasons, 'invalid_order_date')     THEN 'invalid_order_date'
        WHEN list_contains(reasons, 'negative_quantity')      THEN 'negative_quantity'
        WHEN list_contains(reasons, 'invalid_quantity')       THEN 'invalid_quantity'
        ELSE 'duplicate_order_surplus'
    END AS primary_reason,
    _batch_id, _source_row_number
FROM rejected
ORDER BY order_id, _source_row_number;
