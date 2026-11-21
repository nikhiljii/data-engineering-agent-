-- R15: gross_revenue per (report_date,country) = sum(quantity x unit_price) over valid completed orders.
-- Expected value is computed straight from the raw CSVs (oracle view o_orders, see oracle_report.sql), not from the pipeline.
-- Returns the mismatching rows (either direction); the test asserts the result is empty.
WITH expected AS (
  SELECT d AS report_date, country, round(sum(q * price), 2) AS rev
  FROM o_orders WHERE is_valid AND order_status = 'completed' GROUP BY 1, 2)
SELECT coalesce(m.report_date, e.report_date) AS report_date, coalesce(m.country, e.country) AS country,
       m.gross_revenue, coalesce(e.rev, 0) AS expected_revenue
FROM marts.daily_country_report m FULL JOIN expected e ON m.report_date = e.report_date AND m.country = e.country
WHERE coalesce(m.gross_revenue, -1) <> coalesce(e.rev, 0);
