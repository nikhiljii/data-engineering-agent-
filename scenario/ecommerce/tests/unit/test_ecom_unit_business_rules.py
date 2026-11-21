"""B1..B9 on a small hand-computed fixture (expected values written by hand, not read back from the pipeline)."""
from datetime import date
from decimal import Decimal

from conftest import q
from src_scenario.run import run


def test_hand_computed_report_and_dq(small_raw, db_path):
    res = run(small_raw, db_path, backfill=True)
    assert res.status == "PUBLISHED", res.stop_reason
    rows = q(db_path, "SELECT * FROM marts.daily_country_report ORDER BY report_date, country")
    d = date
    assert rows == [
        (d(2026, 9, 1), "DE", 2, 1, Decimal("20.00"), 1, 1, "WARN"),    # O1 (duplicate collapsed), O2 refunded + failed
        (d(2026, 9, 1), "ES", 1, 1, Decimal("12.00"), 1, 0, "FAIL"),    # O3 valid; O4 negative qty excluded, its payment quarantined
        (d(2026, 9, 2), "DE", 1, 1, Decimal("3.00"), 0, 0, "WARN"),     # O7 completed without payment
        (d(2026, 9, 2), "UNKNOWN", 1, 1, Decimal("7.00"), 1, 0, "WARN"),  # O5 missing customer
        (d(2026, 9, 3), "ES", 40, 0, Decimal("0.00"), 0, 0, "OK"),      # pending orders without payment: informational only
        (d(2026, 9, 4), "DE", 1, 1, Decimal("10.00"), 0, 1, "WARN"),    # amount mismatch (10.00 vs 7.00); revenue from order line
        (d(2026, 9, 5), "ES", 1, 0, Decimal("0.00"), 1, 0, "OK"),       # success on cancelled: contradiction is informational
    ]
    dq = q(db_path, "SELECT report_date, country, duplicates_collapsed, unknown_country_orders, excluded_orders, amount_mismatch_orders, "
                    "completed_without_payment, no_payment_orders, payments_excluded, status_payment_contradictions "
                    "FROM marts.daily_country_report_dq ORDER BY report_date, country")
    assert dq == [
        (d(2026, 9, 1), "DE", 1, 0, 0, 0, 0, 0, 0, 0),
        (d(2026, 9, 1), "ES", 0, 0, 1, 0, 0, 0, 1, 0),
        (d(2026, 9, 2), "DE", 0, 0, 0, 0, 1, 0, 0, 0),
        (d(2026, 9, 2), "UNKNOWN", 0, 1, 0, 0, 0, 0, 0, 0),
        (d(2026, 9, 3), "ES", 0, 0, 0, 0, 0, 40, 0, 0),
        (d(2026, 9, 4), "DE", 0, 0, 0, 1, 0, 0, 0, 1),
        (d(2026, 9, 5), "ES", 0, 0, 0, 0, 0, 0, 0, 1),
    ]


def test_quarantine_contents_and_reasons(small_raw, db_path):
    run(small_raw, db_path, backfill=True)
    assert q(db_path, "SELECT order_id, reason, primary_reason FROM quarantine.orders_rejected ORDER BY order_id") == [
        ("O1", "duplicate_order_surplus", "duplicate_order_surplus"),
        ("O4", "negative_quantity", "negative_quantity"),
        ("O6", "invalid_order_date", "invalid_order_date")]
    assert q(db_path, "SELECT payment_id, primary_reason FROM quarantine.payments_rejected ORDER BY payment_id") == [
        ("P4", "order_excluded"), ("P6", "order_excluded"), ("P7", "orphan_payment")]
    # original raw value kept (not the parsed one)
    assert q(db_path, "SELECT order_date, quantity FROM quarantine.orders_rejected WHERE order_id IN ('O6','O4') ORDER BY order_id") == [
        ("2026-09-01", "-1"), ("not-a-date", "1")]


def test_multi_reason_row_carries_all_reasons_sorted(tmp_path, db_path):
    from conftest import small_fixture, write_csv, ORD_HDR
    raw = small_fixture(tmp_path / "raw")
    rows = [["OZ", "C1", "bad", "P1", "-2", "1.00", "pending"]] + [[f"OF{i:02d}", "C1", "2026-09-03", "P1", "1", "1.00", "pending"] for i in range(300)]
    import csv
    with open(raw / "orders.csv", "a", newline="") as f:
        csv.writer(f).writerows(rows)
    res = run(raw, db_path, backfill=True)
    assert res.status == "PUBLISHED", res.stop_reason
    assert q(db_path, "SELECT reason, primary_reason FROM quarantine.orders_rejected WHERE order_id='OZ'") == [
        ("invalid_order_date|negative_quantity", "invalid_order_date")]


def test_conflicting_duplicate_keeps_version_with_customer(tmp_path, db_path):
    import csv
    from conftest import small_fixture
    raw = small_fixture(tmp_path / "raw")
    with open(raw / "orders.csv", "a", newline="") as f:
        w = csv.writer(f)
        w.writerow(["OD", "", "2026-09-06", "P1", "1", "8.00", "completed"])
        w.writerow(["OD", "C2", "2026-09-06", "P1", "1", "8.00", "completed"])
    run(raw, db_path, backfill=True)
    assert q(db_path, "SELECT country, total_orders, gross_revenue, data_quality_status FROM marts.daily_country_report WHERE report_date='2026-09-06'") == [
        ("ES", 1, Decimal("8.00"), "WARN")]   # kept the C2 copy, order counted once, duplicate collapsed -> WARN
    assert q(db_path, "SELECT customer_id FROM quarantine.orders_rejected WHERE order_id='OD'") == [("",)]


def test_gross_revenue_ignores_payment_amount(small_raw, db_path):
    import csv
    run(small_raw, db_path, backfill=True)
    base = q(db_path, "SELECT report_date, country, gross_revenue FROM marts.daily_country_report ORDER BY ALL")
    # mutate every payment amount (still numeric, > 0), rerun: gross_revenue must not move (R15)
    rows = list(csv.reader(open(small_raw / "payments.csv")))
    for r in rows[1:]:
        r[3] = "999.99"
    with open(small_raw / "payments.csv", "w", newline="") as f:
        csv.writer(f).writerows(rows)
    from datetime import datetime
    res = run(small_raw, db_path, backfill=True)
    assert res.status == "PUBLISHED", res.stop_reason
    assert q(db_path, "SELECT report_date, country, gross_revenue FROM marts.daily_country_report ORDER BY ALL") == base


def test_real_data_revenue_matches_independent_python(real_db):
    import csv
    from collections import defaultdict
    from conftest import REAL_RAW
    cust = {r["customer_id"]: r["country"] for r in csv.DictReader(open(REAL_RAW / "customers.csv"))}
    seen, rev = {}, defaultdict(lambda: Decimal(0))
    for r in csv.DictReader(open(REAL_RAW / "orders.csv")):
        oid = r["order_id"]
        if oid in seen and (seen[oid]["customer_id"] or not r["customer_id"]):
            continue                       # keep the version with a customer
        seen[oid] = r
    import re
    for r in seen.values():
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["order_date"]):
            continue
        try:
            date.fromisoformat(r["order_date"])
        except ValueError:
            continue
        if int(r["quantity"]) <= 0 or r["order_status"] != "completed":
            continue
        rev[(date.fromisoformat(r["order_date"]), cust.get(r["customer_id"], "UNKNOWN"))] += int(r["quantity"]) * Decimal(r["unit_price"])
    got = {(d, c): v for d, c, v in q(real_db, "SELECT report_date, country, gross_revenue FROM marts.daily_country_report WHERE gross_revenue > 0")}
    assert got == {k: v.quantize(Decimal("0.01")) for k, v in rev.items() if v > 0}


def test_duplicate_pair_with_one_invalid_copy_keeps_reconciliation(tmp_path, db_path):
    """One copy of a duplicated order has a broken date, the other is fine: the order is valid and counted once (R12/R13)."""
    import csv
    from conftest import small_fixture
    raw = small_fixture(tmp_path / "raw")
    with open(raw / "orders.csv", "a", newline="") as f:
        w = csv.writer(f)
        w.writerow(["OM", "C2", "2026-09-06", "P1", "1", "8.00", "completed"])
        w.writerow(["OM", "C2", "garbage", "P1", "1", "8.00", "completed"])
    res = run(raw, db_path, backfill=True)
    assert res.status == "PUBLISHED", res.stop_reason
    assert q(db_path, "SELECT total_orders FROM marts.daily_country_report WHERE report_date='2026-09-06'") == [(1,)]
    assert q(db_path, "SELECT reason FROM quarantine.orders_rejected WHERE order_id='OM'") == [("duplicate_order_surplus",)]
