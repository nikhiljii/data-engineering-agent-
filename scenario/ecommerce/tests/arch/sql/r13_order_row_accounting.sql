-- R13: raw order rows = stg_orders rows + stg_orders_duplicate_rows rows; stg_orders = valid + invalid;
-- invalid orders = distinct quarantined orders carrying an invalid reason.
SELECT
  (SELECT count(*) FROM raw.orders) AS raw_rows,
  (SELECT count(*) FROM staging.stg_orders) AS stg_rows,
  (SELECT count(*) FROM staging.stg_orders_duplicate_rows) AS dup_rows,
  (SELECT count(*) FROM staging.stg_orders WHERE is_valid) AS valid_rows,
  (SELECT count(*) FROM staging.stg_orders WHERE NOT is_valid) AS invalid_rows,
  (SELECT count(DISTINCT order_id) FROM quarantine.orders_rejected
     WHERE reason LIKE '%invalid_order_date%' OR reason LIKE '%negative_quantity%' OR reason LIKE '%invalid_quantity%') AS quarantined_invalid;
