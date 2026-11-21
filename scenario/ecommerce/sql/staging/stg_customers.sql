-- stg_customers: one row per customer, latest batch of raw.customers. Typed, trimmed, no rows dropped.
CREATE OR REPLACE VIEW staging.stg_customers AS
SELECT
    nullif(trim(customer_id), '') AS customer_id,
    nullif(trim(country), '')     AS country,
    created_at                    AS created_at_raw,
    staging.parse_iso_date(created_at) AS created_at,
    _batch_id,
    _source_row_number
FROM raw.customers
WHERE _batch_id = (SELECT max(_batch_id) FROM raw.customers);
