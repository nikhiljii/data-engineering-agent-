-- daily_country_report: the output (R10). B5 counts, B6 revenue, B7 payments, B8 status from the dq table (ADR-003).
-- Measures use valid orders only. Parameter ?: DATE[] of report dates to (re)compute.
WITH agg AS (
    SELECT
        report_date,
        country,
        (count(*) FILTER (WHERE is_valid))::INTEGER                                              AS total_orders,
        (count(*) FILTER (WHERE is_valid AND order_status = 'completed'))::INTEGER               AS completed_orders,
        coalesce(round(sum(order_total) FILTER (WHERE is_valid AND order_status = 'completed'), 2), 0)::DECIMAL(18, 2) AS gross_revenue,
        (count(*) FILTER (WHERE is_valid AND payment_status = 'success'))::INTEGER               AS successful_payments,
        (count(*) FILTER (WHERE is_valid AND payment_status = 'failed'))::INTEGER                AS failed_payments
    FROM intermediate.int_order_payments
    WHERE report_date IS NOT NULL
      AND report_date IN (SELECT unnest(?::DATE[]))
    GROUP BY report_date, country
)
SELECT
    a.report_date, a.country, a.total_orders, a.completed_orders, a.gross_revenue, a.successful_payments, a.failed_payments,
    CASE
        WHEN d.excluded_orders > 0 THEN 'FAIL'
        WHEN d.duplicates_collapsed + d.unknown_country_orders + d.amount_mismatch_orders + d.completed_without_payment > 0 THEN 'WARN'
        ELSE 'OK'
    END::VARCHAR AS data_quality_status
FROM agg a
JOIN marts.daily_country_report_dq d ON d.report_date = a.report_date AND d.country = a.country
ORDER BY a.report_date, a.country;
