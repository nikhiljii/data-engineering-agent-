-- stg_payments: one row per payment, latest batch of raw.payments. Typed, trimmed, raw strings kept for quarantine.
CREATE OR REPLACE VIEW staging.stg_payments AS
SELECT
    nullif(trim(payment_id), '')     AS payment_id,
    nullif(trim(order_id), '')       AS order_id,
    order_id                         AS order_id_raw,
    payment_date                     AS payment_date_raw,
    staging.parse_iso_date(payment_date) AS payment_date,
    payment_amount                   AS payment_amount_raw,
    try_cast(trim(payment_amount) AS DECIMAL(18, 6)) AS payment_amount,
    nullif(trim(payment_status), '') AS payment_status,
    _batch_id,
    _source_row_number
FROM raw.payments
WHERE _batch_id = (SELECT max(_batch_id) FROM raw.payments);
