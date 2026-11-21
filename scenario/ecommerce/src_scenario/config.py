"""Constants and paths. Thresholds live here, never inline (data-contracts skill)."""
from pathlib import Path

SCENARIO_ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = SCENARIO_ROOT / "sql"
CONTRACTS_DIR = SCENARIO_ROOT / "contracts"
DEFAULT_RAW_DIR = SCENARIO_ROOT / "data" / "raw"
DEFAULT_DB = SCENARIO_ROOT / "warehouse" / "ecommerce.duckdb"

MAX_EXCLUDED_SHARE = 0.15      # R17: excluded distinct orders / distinct order_ids above this stops the run
LOOKBACK_DAYS = 7              # ADR-005, proposed, not validated (Q14)
VOLUME_BAND = 0.5              # +/-50% against the previous batch of the same source (proposed, data-architecture)
FRESHNESS_HOURS = 24           # R19
AMOUNT_TOLERANCE = 0.01        # D7 (kept in intermediate SQL, repeated here for documentation and tests)

# source name -> csv file, exact header, columns landed (ADR-004: customers prunes PII)
SOURCES = {
    "customers": {
        "file": "customers.csv",
        "header": ["customer_id", "customer_name", "email", "country", "created_at"],
        "landed": ["customer_id", "country", "created_at"],
    },
    "orders": {
        "file": "orders.csv",
        "header": ["order_id", "customer_id", "order_date", "product_id", "quantity", "unit_price", "order_status"],
        "landed": ["order_id", "customer_id", "order_date", "product_id", "quantity", "unit_price", "order_status"],
    },
    "payments": {
        "file": "payments.csv",
        "header": ["payment_id", "order_id", "payment_date", "payment_amount", "payment_status"],
        "landed": ["payment_id", "order_id", "payment_date", "payment_amount", "payment_status"],
    },
}

ORDER_STATUSES = ("completed", "refunded", "pending", "cancelled")
PAYMENT_STATUSES = ("success", "failed", "pending")
COUNTRIES = ("DE", "ES", "FR", "GB", "IN", "IT", "NL", "US", "UNKNOWN")
DQ_STATUSES = ("OK", "WARN", "FAIL")

STAGING_MODELS = ["stg_customers", "stg_orders", "stg_orders_duplicate_rows", "stg_payments"]
INTERMEDIATE_MODELS = ["int_orders_enriched", "int_order_payments"]
