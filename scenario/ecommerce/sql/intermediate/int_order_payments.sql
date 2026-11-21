-- int_order_payments: grain = one row per order (at most one payment per order is a staging contract), with its payment and flags.
-- Orphan payments have no order and are not in this model (they go to quarantine).
CREATE OR REPLACE VIEW intermediate.int_order_payments AS
SELECT
    o.*,
    p.payment_id,
    p.payment_status,
    p.payment_amount,
    p.payment_id IS NOT NULL AS has_payment,
    -- D7: tolerance 0.01, valid orders only (payments of excluded orders are quarantined)
    (o.is_valid AND p.payment_id IS NOT NULL AND o.order_total IS NOT NULL AND p.payment_amount IS NOT NULL
        AND abs(o.order_total - p.payment_amount) > 0.01) AS amount_mismatch,
    -- informational only (ADR-003)
    (o.is_valid AND p.payment_id IS NOT NULL AND (
        (o.order_status = 'completed' AND p.payment_status = 'failed')
        OR (p.payment_status = 'success' AND o.order_status IN ('cancelled', 'pending', 'refunded')))) AS status_payment_contradiction
FROM intermediate.int_orders_enriched o
LEFT JOIN staging.stg_payments p ON p.order_id = o.order_id;
