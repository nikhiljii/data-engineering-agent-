-- int_orders_enriched: order + country (B2) + validity + totals. Refs staging only.
CREATE OR REPLACE VIEW intermediate.int_orders_enriched AS
SELECT
    o.order_id,
    o.customer_id,
    o.order_date                         AS report_date,           -- B1
    coalesce(c.country, 'UNKNOWN')       AS country,               -- B2
    c.country IS NULL                    AS unknown_country,
    o.order_status,
    o.quantity,
    o.unit_price,
    o.quantity * o.unit_price            AS order_total,
    o.is_valid,
    o.invalid_reasons,
    o.had_duplicate,
    o.source_row_count - 1               AS duplicates_collapsed,
    o.order_date IS NOT NULL             AS is_attributed,
    o._batch_id,
    o._source_row_number
FROM staging.stg_orders o
LEFT JOIN staging.stg_customers c ON c.customer_id = o.customer_id;
