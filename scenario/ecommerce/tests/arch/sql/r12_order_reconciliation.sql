-- R12: count(distinct raw order_id) = sum(total_orders) + distinct order_ids quarantined as invalid.
-- Returns one row; the test asserts lhs = rhs and, on the current files, lhs = 6000 and quarantined_invalid = 55.
SELECT
  (SELECT count(DISTINCT order_id) FROM raw.orders) AS raw_distinct_orders,
  (SELECT coalesce(sum(total_orders), 0) FROM marts.daily_country_report) AS sum_total_orders,
  (SELECT count(DISTINCT order_id) FROM quarantine.orders_rejected
     WHERE reason LIKE '%invalid_order_date%' OR reason LIKE '%negative_quantity%' OR reason LIKE '%invalid_quantity%') AS quarantined_invalid;
