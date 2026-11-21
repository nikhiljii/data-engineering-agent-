"""Fixtures for the builder's unit tests (prefix test_ecom_unit_). Not the quality-engineer's suite."""
import csv
import hashlib
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

SCENARIO = Path(__file__).resolve().parents[2]
REAL_RAW = SCENARIO / "data" / "raw"

CUST_HDR = ["customer_id", "customer_name", "email", "country", "created_at"]
ORD_HDR = ["order_id", "customer_id", "order_date", "product_id", "quantity", "unit_price", "order_status"]
PAY_HDR = ["payment_id", "order_id", "payment_date", "payment_amount", "payment_status"]


def write_csv(path: Path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def small_fixture(raw: Path, extra_bad_date_orders: int = 0):
    """Hand-computed fixture, see test_ecom_unit_business_rules.py for the expected values."""
    raw.mkdir(parents=True, exist_ok=True)
    write_csv(raw / "customers.csv", CUST_HDR, [
        ["C1", "Name One", "one@example.com", "DE", "2026-01-01"],
        ["C2", "Name Two", "two@example.com", "ES", "2026-01-02"]])
    orders = [
        ["O1", "C1", "2026-09-01", "P1", "2", "10.00", "completed"],
        ["O1", "C1", "2026-09-01", "P1", "2", "10.00", "completed"],      # identical duplicate
        ["O2", "C1", "2026-09-01", "P1", "1", "5.50", "refunded"],
        ["O3", "C2", "2026-09-01", "P1", "3", "4.00", "completed"],
        ["O4", "C2", "2026-09-01", "P1", "-1", "9.00", "completed"],      # negative quantity
        ["O5", "", "2026-09-02", "P1", "1", "7.00", "completed"],         # missing customer -> UNKNOWN
        ["O6", "C1", "not-a-date", "P1", "1", "1.00", "completed"],       # invalid date
        ["O7", "C1", "2026-09-02", "P1", "1", "3.00", "completed"],       # completed, no payment
        ["O8", "C1", "2026-09-04", "P1", "2", "5.00", "completed"],       # payment 7.00 failed: mismatch + contradiction
        ["O9", "C2", "2026-09-05", "P1", "1", "2.00", "cancelled"],       # success on cancelled: informational
    ] + [[f"O1{i:02d}", "C2", "2026-09-03", "P1", "1", "1.00", "pending"] for i in range(40)]
    orders += [[f"OB{i:02d}", "C1", "31/02/2026", "P1", "1", "1.00", "pending"] for i in range(extra_bad_date_orders)]
    write_csv(raw / "orders.csv", ORD_HDR, orders)
    write_csv(raw / "payments.csv", PAY_HDR, [
        ["P1", "O1", "2026-09-01", "20.00", "success"],
        ["P2", "O2", "2026-09-01", "5.50", "failed"],
        ["P3", "O3", "2026-09-01", "12.00", "success"],
        ["P4", "O4", "2026-09-01", "9.00", "success"],
        ["P5", "O5", "2026-09-02", "7.00", "success"],
        ["P6", "O6", "2026-09-02", "1.00", "success"],
        ["P7", "OX", "2026-09-02", "1.00", "success"],                    # orphan
        ["P8", "O8", "2026-09-04", "7.00", "failed"],
        ["P9", "O9", "2026-09-05", "2.00", "success"]])
    return raw


@pytest.fixture
def small_raw(tmp_path):
    return small_fixture(tmp_path / "raw")


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "wh" / "e.duckdb"


def table_hash(db, query: str) -> str:
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(query).fetchall()
    finally:
        con.close()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def q(db, sql, params=None):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()


HASH_QUERIES = {
    "report": "SELECT * FROM marts.daily_country_report ORDER BY ALL",
    "dq": "SELECT * FROM marts.daily_country_report_dq ORDER BY ALL",
    "q_orders": "SELECT * FROM quarantine.orders_rejected ORDER BY ALL",
    "q_payments": "SELECT * FROM quarantine.payments_rejected ORDER BY ALL",
}


def cli(args, cwd=SCENARIO):
    import os
    env = dict(os.environ, PYTHONPATH=str(SCENARIO))
    return subprocess.run([sys.executable, "-m", "src_scenario.run", *args], capture_output=True, text=True, cwd=cwd, env=env)


@pytest.fixture(scope="module")
def real_db(tmp_path_factory):
    """Backfill on the real files once per module."""
    from src_scenario.run import run
    db = tmp_path_factory.mktemp("real") / "ecommerce.duckdb"
    res = run(REAL_RAW, db, backfill=True)
    assert res.status == "PUBLISHED", res.stop_reason
    return db
