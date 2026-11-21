-- stg_orders: one row per distinct order_id from the latest batch of raw.orders (B3).
-- Kept version: non-null customer_id first, then deterministic tie-break on the remaining columns.
-- Invalid rows are kept with is_valid = false and invalid_reasons (B4); nothing is deleted here.
CREATE OR REPLACE VIEW staging.stg_orders AS
WITH typed AS (
    SELECT
        nullif(trim(order_id), '')       AS order_id,
        nullif(trim(customer_id), '')    AS customer_id,
        customer_id                      AS customer_id_raw,
        order_date                       AS order_date_raw,
        staging.parse_iso_date(order_date) AS order_date,
        product_id,
        quantity                         AS quantity_raw,
        staging.parse_int(quantity)      AS quantity,
        unit_price                       AS unit_price_raw,
        try_cast(trim(unit_price) AS DECIMAL(18, 6)) AS unit_price,
        nullif(trim(order_status), '')   AS order_status,
        _batch_id,
        _source_row_number
    FROM raw.orders
    WHERE _batch_id = (SELECT max(_batch_id) FROM raw.orders)
),
ranked AS (
    SELECT *,
        row_number() OVER (PARTITION BY order_id
            ORDER BY (customer_id IS NULL), order_date_raw, product_id, quantity_raw, unit_price_raw, order_status, _source_row_number) AS rn,
        count(*) OVER (PARTITION BY order_id) AS source_row_count
    FROM typed
)
SELECT
    order_id, customer_id, customer_id_raw, order_date_raw, order_date, product_id,
    quantity_raw, quantity, unit_price_raw, unit_price, order_status,
    source_row_count > 1 AS had_duplicate,
    source_row_count,
    staging.order_invalid_reasons(order_date, quantity) AS invalid_reasons,
    len(staging.order_invalid_reasons(order_date, quantity)) = 0 AS is_valid,
    _batch_id,
    _source_row_number
FROM ranked
WHERE rn = 1;
