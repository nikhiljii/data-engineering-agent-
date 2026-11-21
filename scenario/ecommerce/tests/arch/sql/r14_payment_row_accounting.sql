-- R14: raw payment rows = payments counted on valid orders (success+failed+pending) + quarantine.payments_rejected.
-- counted_independent is computed from raw CSV oracle views (o_orders, o_pay), not from the pipeline's staging.
SELECT
  (SELECT count(*) FROM raw.payments) AS raw_rows,
  (SELECT count(*) FROM quarantine.payments_rejected) AS rejected_rows,
  (SELECT count(*) FROM o_pay p JOIN o_orders o ON o.order_id = p.order_id WHERE o.is_valid) AS counted_independent,
  (SELECT sum(successful_payments + failed_payments) FROM marts.daily_country_report) AS reported_success_failed,
  (SELECT count(*) FROM o_pay p JOIN o_orders o ON o.order_id = p.order_id WHERE o.is_valid AND p.payment_status IN ('success','failed')) AS expected_success_failed;
