-- stg_orders_duplicate_rows (declared helper): source rows of the latest batch that were not kept by stg_orders (B3).
-- Surplus rows carry no validity verdict of their own: the kept version decides whether the order is valid (keeps R12/R13 exact).
CREATE OR REPLACE VIEW staging.stg_orders_duplicate_rows AS
SELECT
    nullif(trim(order_id), '')       AS order_id,
    customer_id                      AS customer_id_raw,
    order_date                       AS order_date_raw,
    product_id,
    quantity                         AS quantity_raw,
    unit_price                       AS unit_price_raw,
    order_status,
    _batch_id,
    _source_row_number
FROM raw.orders
WHERE _batch_id = (SELECT max(_batch_id) FROM raw.orders)
  AND _source_row_number NOT IN (SELECT _source_row_number FROM staging.stg_orders);
