-- Shared parsing rules for staging (B1, B4). Strict ISO date; strict integer.
CREATE OR REPLACE MACRO staging.parse_iso_date(x) AS
    CASE WHEN regexp_full_match(trim(x), '[0-9]{4}-[0-9]{2}-[0-9]{2}') THEN try_cast(trim(x) AS DATE) END;
CREATE OR REPLACE MACRO staging.parse_int(x) AS
    CASE WHEN regexp_full_match(trim(x), '-?[0-9]+') THEN try_cast(trim(x) AS BIGINT) END;
-- B4: invalid reasons of one source row (list, may be empty).
CREATE OR REPLACE MACRO staging.order_invalid_reasons(order_date, quantity) AS
    list_filter([
        CASE WHEN order_date IS NULL THEN 'invalid_order_date' END,
        CASE WHEN quantity < 0 THEN 'negative_quantity' END,
        CASE WHEN quantity IS NULL OR quantity = 0 THEN 'invalid_quantity' END
    ], lambda x: x IS NOT NULL);
