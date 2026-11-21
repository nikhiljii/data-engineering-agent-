"""Acceptance numbers on the real files, idempotency, window, PII, layering rules."""
import re
import time
from datetime import date
from pathlib import Path

import yaml

from conftest import HASH_QUERIES, REAL_RAW, SCENARIO, cli, q, table_hash
from src_scenario.run import run


def test_raw_row_counts_types_and_metadata(real_db):
    assert q(real_db, "SELECT (SELECT count(*) FROM raw.customers), (SELECT count(*) FROM raw.orders), (SELECT count(*) FROM raw.payments)") == [(1200, 6040, 5950)]
    bad = q(real_db, "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema='raw' "
                     "AND column_name NOT LIKE '\\_%' ESCAPE '\\' AND data_type <> 'VARCHAR'")
    assert bad == []
    assert q(real_db, "SELECT count(*) FROM raw.orders WHERE _batch_id IS NULL OR _loaded_at IS NULL OR _source_sha256 IS NULL OR _source_row_number IS NULL") == [(0,)]
    # first data row of the CSV is row 1, empty customer_id stays an empty string (no coercion in raw)
    first = REAL_RAW.joinpath("orders.csv").read_text().splitlines()[1].split(",")[0]
    assert q(real_db, "SELECT order_id FROM raw.orders WHERE _source_row_number = 1") == [(first,)]
    assert q(real_db, "SELECT count(*) FROM raw.orders WHERE customer_id = ''") == [(60,)]


def test_no_pii_column_in_any_table_or_view(real_db):   # R2
    assert q(real_db, "SELECT table_schema, table_name, column_name FROM information_schema.columns "
                      "WHERE lower(column_name) IN ('customer_name', 'email')") == []
    assert [c for (c,) in q(real_db, "SELECT column_name FROM information_schema.columns WHERE table_schema='raw' AND table_name='customers' "
                                     "AND column_name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY ordinal_position")] == ["customer_id", "country", "created_at"]


def test_staging_and_quarantine_counts(real_db):
    assert q(real_db, "SELECT (SELECT count(*) FROM staging.stg_orders), (SELECT count(*) FROM staging.stg_orders_duplicate_rows)") == [(6000, 40)]
    assert q(real_db, "SELECT primary_reason, count(*) FROM quarantine.orders_rejected GROUP BY 1 ORDER BY 1") == [
        ("duplicate_order_surplus", 40), ("invalid_order_date", 30), ("negative_quantity", 25)]
    assert q(real_db, "SELECT count(*) FROM quarantine.orders_rejected") == [(95,)]
    pay = dict(q(real_db, "SELECT primary_reason, count(*) FROM quarantine.payments_rejected GROUP BY 1"))
    assert pay["orphan_payment"] == 30
    # architecture left the payments of negative-quantity orders UNKNOWN; record the real numbers
    neg = q(real_db, "SELECT count(*) FROM quarantine.payments_rejected p WHERE order_id IN "
                     "(SELECT order_id FROM quarantine.orders_rejected WHERE primary_reason='negative_quantity')")[0][0]
    inv = q(real_db, "SELECT count(*) FROM quarantine.payments_rejected p WHERE order_id IN "
                     "(SELECT order_id FROM quarantine.orders_rejected WHERE primary_reason='invalid_order_date')")[0][0]
    print(f"REAL: payments quarantined for negative-quantity orders={neg}, for invalid-date orders={inv}")
    assert (neg, inv) == (25, 28) and pay["order_excluded"] == neg + inv


def test_mart_schema_exact_r10(real_db):
    assert q(real_db, "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='marts' AND table_name='daily_country_report' ORDER BY ordinal_position") == [
        ("report_date", "DATE"), ("country", "VARCHAR"), ("total_orders", "INTEGER"), ("completed_orders", "INTEGER"),
        ("gross_revenue", "DECIMAL(18,2)"), ("successful_payments", "INTEGER"), ("failed_payments", "INTEGER"), ("data_quality_status", "VARCHAR")]


def test_reconciliation_r12_r13_r14(real_db):
    tot = q(real_db, "SELECT sum(total_orders) FROM marts.daily_country_report")[0][0]
    assert tot + 55 == 6000                                                           # R12
    assert q(real_db, "SELECT (SELECT count(*) FROM staging.stg_orders) + (SELECT count(*) FROM staging.stg_orders_duplicate_rows)") == [(6040,)]
    counted = q(real_db, "SELECT count(*) FROM intermediate.int_order_payments WHERE is_valid AND has_payment")[0][0]
    assert counted + q(real_db, "SELECT count(*) FROM quarantine.payments_rejected")[0][0] == 5950   # R14
    s = q(real_db, "SELECT run_status, excluded_share, distinct_excluded_orders, distinct_order_ids, orphan_payment_count, "
                   "fk_violations_stg_payments_order_id, rows_ok + rows_warn + rows_fail FROM marts.run_summary")
    assert s == [("PUBLISHED", 55 / 6000, 55, 6000, 30, 30, 264)]


def test_status_semantics_adr003(real_db):
    # FAIL rows are exactly the rows with an excluded order; contradictions alone never raise WARN
    assert q(real_db, "SELECT count(*) FROM marts.daily_country_report r JOIN marts.daily_country_report_dq d USING (report_date, country) "
                      "WHERE (r.data_quality_status = 'FAIL') <> (d.excluded_orders > 0)") == [(0,)]
    assert q(real_db, "SELECT count(*) FROM marts.daily_country_report r JOIN marts.daily_country_report_dq d USING (report_date, country) "
                      "WHERE r.data_quality_status = 'OK' AND d.status_payment_contradictions > 0") [0][0] > 0


def test_rerun_identical_hashes_and_raw_load_idempotent(tmp_path):
    db = tmp_path / "e.duckdb"
    assert run(REAL_RAW, db, backfill=True).status == "PUBLISHED"
    h1 = {k: table_hash(db, v) for k, v in HASH_QUERIES.items()}
    raw1 = q(db, "SELECT (SELECT count(*) FROM raw.customers), (SELECT count(*) FROM raw.orders), (SELECT count(*) FROM raw.payments)")
    assert run(REAL_RAW, db, backfill=True).status == "PUBLISHED"
    assert {k: table_hash(db, v) for k, v in HASH_QUERIES.items()} == h1               # R8
    assert q(db, "SELECT (SELECT count(*) FROM raw.customers), (SELECT count(*) FROM raw.orders), (SELECT count(*) FROM raw.payments)") == raw1   # R20
    assert q(db, "SELECT count(*) FROM audit.run_log") == [(2,)]
    assert q(db, "SELECT count(*) FROM marts.run_summary") == [(2,)]
    assert q(db, "SELECT count(DISTINCT _batch_id) FROM raw.orders") == [(1,)]


def test_report_date_window_touches_only_lookback(tmp_path):   # R9
    db = tmp_path / "e.duckdb"
    run(REAL_RAW, db, backfill=True)
    import duckdb
    con = duckdb.connect(str(db))
    con.execute("UPDATE marts.daily_country_report SET total_orders = 999 WHERE report_date IN (DATE '2026-09-01', DATE '2026-09-30')")
    con.close()
    res = run(REAL_RAW, db, report_date=date(2026, 9, 30))
    assert res.status == "PUBLISHED"
    assert res.report_dates == [date(2026, 9, d) for d in range(23, 31)]               # D and 7 days before
    assert q(db, "SELECT count(*) FROM marts.daily_country_report WHERE total_orders = 999 AND report_date = DATE '2026-09-01'")[0][0] > 0   # outside window untouched
    assert q(db, "SELECT count(*) FROM marts.daily_country_report WHERE total_orders = 999 AND report_date = DATE '2026-09-30'") == [(0,)]    # inside window repaired
    assert q(db, "SELECT count(*) FROM marts.daily_country_report GROUP BY report_date, country HAVING count(*) > 1") == []


def test_report_date_on_empty_db_publishes_only_window(tmp_path):
    db = tmp_path / "e.duckdb"
    assert run(REAL_RAW, db, report_date=date(2026, 9, 10)).status == "PUBLISHED"
    assert q(db, "SELECT min(report_date), max(report_date) FROM marts.daily_country_report") == [(date(2026, 9, 3), date(2026, 9, 10))]


def test_backfill_under_60_seconds(tmp_path):
    t = time.perf_counter()
    assert run(REAL_RAW, tmp_path / "e.duckdb", backfill=True).status == "PUBLISHED"
    assert time.perf_counter() - t < 60


def test_cli_exit_codes_and_defaults(tmp_path):
    ok = cli(["--backfill", "--raw-dir", str(REAL_RAW), "--db", str(tmp_path / "x.duckdb")])
    assert ok.returncode == 0, ok.stderr
    assert cli(["--report-date", "2026-13-45", "--db", str(tmp_path / "y.duckdb")]).returncode == 2
    assert cli([]).returncode == 2
    p = cli(["--help"])
    assert "--raw-dir" in p.stdout and "--db" in p.stdout and "--backfill" in p.stdout


def test_sql_has_no_clock_or_randomness_and_respects_layering():   # R8, R3
    sql_root = SCENARIO / "sql"
    for f in sql_root.rglob("*.sql"):
        text = re.sub(r"--[^\n]*", "", f.read_text()).lower()
        assert not re.search(r"\b(now\s*\(|current_timestamp|current_date|random\s*\(|uuid\s*\()", text), f
    for layer in ("intermediate", "marts", "quarantine"):
        for f in (sql_root / layer).glob("*.sql"):
            assert not re.search(r"\braw\.", re.sub(r"--[^\n]*", "", f.read_text()).lower()), f
    for f in (sql_root / "intermediate").glob("*.sql"):
        assert not re.search(r"\b(marts|quarantine)\.", f.read_text().lower()), f


def test_no_cloud_or_network_imports():   # R22
    for f in (SCENARIO / "src_scenario").glob("*.py"):
        if f.name in ("release.py", "incident.py"):
            continue
        assert not re.search(r"\b(boto3|google\.cloud|azure|snowflake|requests|urllib\.request|http\.client|socket)\b", f.read_text()), f


def test_contract_classifies_every_mart_column(real_db):   # R21
    spec = yaml.safe_load((SCENARIO / "contracts" / "marts.yaml").read_text())["tables"]
    for tbl, t in spec.items():
        schema, name = tbl.split(".")
        cols = [c for (c,) in q(real_db, "SELECT column_name FROM information_schema.columns WHERE table_schema=? AND table_name=? ORDER BY ordinal_position", [schema, name])]
        assert cols == list(t["columns"]), tbl
        assert all(v["classification"] in {"public", "internal", "confidential", "PII"} for v in t["columns"].values())
    tables = {t for (t,) in q(real_db, "SELECT table_name FROM information_schema.tables WHERE table_schema='marts' ORDER BY table_name")}
    assert tables == {"daily_country_report", "daily_country_report_dq", "run_summary"}
