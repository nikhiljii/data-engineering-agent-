-- daily_country_report_dq: issue counts per (report_date, country); status derives from these only (R11).
-- Row set (B9): every (report_date, country) with at least one order that has a valid date (valid or excluded for quantity).
-- Parameter ?: DATE[] of report dates to (re)compute.
SELECT
    report_date,
    country,
    sum(duplicates_collapsed)::INTEGER                                                  AS duplicates_collapsed,
    (count(*) FILTER (WHERE unknown_country))::INTEGER                                  AS unknown_country_orders,
    (count(*) FILTER (WHERE NOT is_valid))::INTEGER                                     AS excluded_orders,
    (count(*) FILTER (WHERE amount_mismatch))::INTEGER                                  AS amount_mismatch_orders,
    (count(*) FILTER (WHERE is_valid AND order_status = 'completed' AND NOT has_payment))::INTEGER AS completed_without_payment,
    (count(*) FILTER (WHERE is_valid AND order_status <> 'completed' AND NOT has_payment))::INTEGER AS no_payment_orders,
    (count(*) FILTER (WHERE NOT is_valid AND has_payment))::INTEGER                     AS payments_excluded,
    (count(*) FILTER (WHERE status_payment_contradiction))::INTEGER                     AS status_payment_contradictions
FROM intermediate.int_order_payments
WHERE report_date IS NOT NULL
  AND report_date IN (SELECT unnest(?::DATE[]))
GROUP BY report_date, country
ORDER BY report_date, country;
