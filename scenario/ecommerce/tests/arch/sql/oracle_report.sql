-- Independent oracle, written from architecture rules B1..B9 only (not from builder code).
-- Reads the raw CSVs directly. {raw_dir} is substituted by the test helper.
CREATE OR REPLACE TEMP VIEW o_cust AS
SELECT customer_id, country FROM read_csv('{raw_dir}/customers.csv', header=true, all_varchar=true);

CREATE OR REPLACE TEMP VIEW o_src AS
SELECT row_number() OVER () AS rn, order_id, customer_id, order_date, product_id, quantity, unit_price, order_status
FROM read_csv('{raw_dir}/orders.csv', header=true, all_varchar=true);

-- B3: one row per order_id, non-null customer_id first, then deterministic tie-break
CREATE OR REPLACE TEMP VIEW o_ranked AS
SELECT *, row_number() OVER (PARTITION BY order_id
            ORDER BY (customer_id IS NULL), order_date, product_id, quantity, unit_price, order_status, rn) AS pick,
          count(*) OVER (PARTITION BY order_id) AS copies
FROM o_src;

-- B1 strict ISO date, B4 validity
CREATE OR REPLACE TEMP VIEW o_orders AS
SELECT r.order_id, r.customer_id, r.order_status, r.copies,
       CAST(try_strptime(r.order_date, '%Y-%m-%d') AS DATE) AS d,
       try_cast(r.quantity AS BIGINT) AS q,
       try_cast(r.unit_price AS DECIMAL(18,4)) AS price,
       coalesce(c.country, 'UNKNOWN') AS country,
       (try_strptime(r.order_date, '%Y-%m-%d') IS NOT NULL AND try_cast(r.quantity AS BIGINT) > 0) AS is_valid
FROM o_ranked r LEFT JOIN o_cust c ON c.customer_id = r.customer_id
WHERE r.pick = 1;

CREATE OR REPLACE TEMP VIEW o_pay AS
SELECT payment_id, order_id, payment_date, try_cast(payment_amount AS DECIMAL(18,4)) AS amount, payment_status
FROM read_csv('{raw_dir}/payments.csv', header=true, all_varchar=true);

-- B5..B9 (B9: a row exists when an order with a valid date maps to it, even if excluded for quantity)
CREATE OR REPLACE TEMP VIEW o_report AS
SELECT o.d AS report_date, o.country,
  CAST(count(*) FILTER (WHERE o.is_valid) AS INTEGER) AS total_orders,
  CAST(count(*) FILTER (WHERE o.is_valid AND o.order_status = 'completed') AS INTEGER) AS completed_orders,
  CAST(round(coalesce(sum(o.q * o.price) FILTER (WHERE o.is_valid AND o.order_status = 'completed'), 0), 2) AS DECIMAL(18,2)) AS gross_revenue,
  CAST(count(p.payment_id) FILTER (WHERE o.is_valid AND p.payment_status = 'success') AS INTEGER) AS successful_payments,
  CAST(count(p.payment_id) FILTER (WHERE o.is_valid AND p.payment_status = 'failed') AS INTEGER) AS failed_payments,
  CAST(count(*) FILTER (WHERE NOT o.is_valid) AS INTEGER) AS o_excluded,
  CAST(count(*) FILTER (WHERE o.is_valid AND o.copies > 1) AS INTEGER) AS o_dup,
  CAST(count(*) FILTER (WHERE o.is_valid AND o.country = 'UNKNOWN') AS INTEGER) AS o_unknown,
  CAST(count(*) FILTER (WHERE o.is_valid AND p.payment_id IS NOT NULL AND abs(p.amount - o.q * o.price) > 0.01) AS INTEGER) AS o_mismatch,
  CAST(count(*) FILTER (WHERE o.is_valid AND o.order_status = 'completed' AND p.payment_id IS NULL) AS INTEGER) AS o_cwp,
  CASE WHEN count(*) FILTER (WHERE NOT o.is_valid) > 0 THEN 'FAIL'
       WHEN count(*) FILTER (WHERE o.is_valid AND o.copies > 1) > 0
         OR count(*) FILTER (WHERE o.is_valid AND o.country = 'UNKNOWN') > 0
         OR count(*) FILTER (WHERE o.is_valid AND p.payment_id IS NOT NULL AND abs(p.amount - o.q * o.price) > 0.01) > 0
         OR count(*) FILTER (WHERE o.is_valid AND o.order_status = 'completed' AND p.payment_id IS NULL) > 0 THEN 'WARN'
       ELSE 'OK' END AS data_quality_status
FROM o_orders o LEFT JOIN o_pay p ON p.order_id = o.order_id
WHERE o.d IS NOT NULL
GROUP BY o.d, o.country;
