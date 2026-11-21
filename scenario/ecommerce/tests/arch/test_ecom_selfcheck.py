"""Proves the checks can fail: each pure check is run on a clean synthetic fixture (passes) and on a seeded defect (flags it).
These tests do not need the builder's code and are expected to be GREEN now."""
import duckdb
import pytest

import ecom_helpers as H


def _mem():
    return duckdb.connect(":memory:")


def _good_mart(con):
    for s in H.SCHEMAS:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
    con.execute("""CREATE TABLE marts.daily_country_report(report_date DATE, country VARCHAR, total_orders INTEGER, completed_orders INTEGER,
                   gross_revenue DECIMAL(18,2), successful_payments INTEGER, failed_payments INTEGER, data_quality_status VARCHAR)""")
    con.execute("""CREATE TABLE marts.daily_country_report_dq(report_date DATE, country VARCHAR, duplicates_collapsed INTEGER,
                   unknown_country_orders INTEGER, excluded_orders INTEGER, amount_mismatch_orders INTEGER, completed_without_payment INTEGER)""")
    con.execute("INSERT INTO marts.daily_country_report VALUES ('2026-09-01','DE',3,2,10.50,2,1,'OK'),('2026-09-01','FR',0,0,0,0,0,'FAIL'),('2026-09-02','DE',2,1,5,1,0,'WARN')")
    con.execute("INSERT INTO marts.daily_country_report_dq VALUES ('2026-09-01','DE',0,0,0,0,0),('2026-09-01','FR',0,0,1,0,0),('2026-09-02','DE',1,0,0,0,0)")


def test_selfcheck_status_derivation_flags_wrong_status():
    c = _mem(); _good_mart(c)
    assert H.check_status_derivation(c) == []
    c.execute("UPDATE marts.daily_country_report SET data_quality_status='OK' WHERE country='FR'")
    assert H.check_status_derivation(c) != []
    c.execute("UPDATE marts.daily_country_report SET data_quality_status='MAYBE' WHERE country='FR'")
    assert any("not allowed" in v for v in H.check_status_derivation(c))


def test_selfcheck_report_columns_flags_extra_column_wrong_type_and_order():
    c = _mem(); _good_mart(c)
    assert H.check_report_columns(c) == []
    c.execute("ALTER TABLE marts.daily_country_report ADD COLUMN extra INTEGER")
    assert H.check_report_columns(c) != []
    c2 = _mem(); _good_mart(c2)
    c2.execute("ALTER TABLE marts.daily_country_report ALTER gross_revenue TYPE DOUBLE")
    assert H.check_report_columns(c2) != []


def test_selfcheck_unique_not_null():
    c = _mem(); _good_mart(c)
    assert H.check_unique_not_null(c, "marts.daily_country_report", ["report_date", "country"]) == []
    c.execute("INSERT INTO marts.daily_country_report VALUES ('2026-09-01','DE',1,1,1,1,1,'OK')")
    assert any("duplicated" in v for v in H.check_unique_not_null(c, "marts.daily_country_report", ["report_date", "country"]))
    c.execute("INSERT INTO marts.daily_country_report VALUES (NULL,'DE',1,1,1,1,1,'OK')")
    assert any("NULL" in v for v in H.check_unique_not_null(c, "marts.daily_country_report", ["report_date", "country"]))


def test_selfcheck_pii_checks():
    c = _mem(); _good_mart(c)
    c.execute("CREATE TABLE raw.customers(customer_id VARCHAR, country VARCHAR)")
    c.execute("INSERT INTO raw.customers VALUES ('C1','DE')")
    assert H.check_no_pii_columns(c) == [] and H.check_no_pii_values(c) == []
    c.execute("CREATE VIEW staging.v AS SELECT 'a' AS Email")
    assert H.check_no_pii_columns(c) != []                      # views and case-insensitive
    c.execute("INSERT INTO raw.customers VALUES ('C2','c2@example.com')")
    assert H.check_no_pii_values(c) != []


def test_selfcheck_schemas_and_raw_varchar():
    c = _mem()
    assert len(H.check_schemas(c)) == 6                                         # empty database: all six layer schemas missing
    _good_mart(c)
    assert H.check_schemas(c) == []
    c.execute("CREATE TABLE raw.orders(order_id VARCHAR, customer_id VARCHAR, order_date DATE, product_id VARCHAR, quantity INTEGER, unit_price VARCHAR, order_status VARCHAR)")
    v = H.check_raw_all_varchar(c)
    assert any("order_date" in x for x in v) and any("quantity" in x for x in v)


def test_selfcheck_quarantine_reasons_and_precedence():
    c = _mem(); _good_mart(c)
    c.execute("CREATE TABLE quarantine.orders_rejected(primary_reason VARCHAR, reason VARCHAR)")
    c.execute("CREATE TABLE quarantine.payments_rejected(primary_reason VARCHAR, reason VARCHAR)")
    c.execute("INSERT INTO quarantine.orders_rejected VALUES ('invalid_order_date','invalid_order_date|negative_quantity'),('duplicate_order_surplus','duplicate_order_surplus')")
    c.execute("INSERT INTO quarantine.payments_rejected VALUES ('orphan_payment','orphan_payment')")
    assert H.check_quarantine_reasons(c) == []
    c.execute("INSERT INTO quarantine.orders_rejected VALUES ('negative_quantity','invalid_order_date|negative_quantity')")
    assert any("precedence" in v for v in H.check_quarantine_reasons(c))
    c2 = _mem(); _good_mart(c2)
    c2.execute("CREATE TABLE quarantine.orders_rejected(primary_reason VARCHAR, reason VARCHAR)")
    c2.execute("CREATE TABLE quarantine.payments_rejected(primary_reason VARCHAR, reason VARCHAR)")
    c2.execute("INSERT INTO quarantine.orders_rejected VALUES (NULL,'whatever')")
    assert H.check_quarantine_reasons(c2) != []


def test_selfcheck_table_hash_detects_row_change_and_ignores_excluded_columns():
    c = _mem(); _good_mart(c)
    h = H.table_hash(c, "marts.daily_country_report")
    assert h == H.table_hash(c, "marts.daily_country_report")
    c.execute("UPDATE marts.daily_country_report SET gross_revenue = 10.51 WHERE country='DE' AND report_date='2026-09-01'")
    assert H.table_hash(c, "marts.daily_country_report") != h
    c.execute("CREATE TABLE marts.t(a INTEGER, _batch_id VARCHAR)"); c.execute("INSERT INTO marts.t VALUES (1,'x')")
    h1 = H.table_hash(c, "marts.t", ("_",)); c.execute("UPDATE marts.t SET _batch_id='y'")
    assert H.table_hash(c, "marts.t", ("_",)) == h1 and H.table_hash(c, "marts.t") != H.table_hash(c, "marts.t", ("_",))


def test_selfcheck_oracle_and_report_diff_detect_seeded_mart_errors(tmp_path):
    """Build a tiny raw dir, a mart that matches the oracle (passes), then corrupt it (fails)."""
    raw = tmp_path / "raw"; raw.mkdir()
    H.write_csv_rows(raw / "customers.csv", H.CSV_HEADERS["customers"], [["C1", "n", "e", "DE", "2026-01-01"], ["C2", "n", "e", "FR", "2026-01-01"]])
    H.write_csv_rows(raw / "orders.csv", H.CSV_HEADERS["orders"], [
        ["O1", "C1", "2026-09-01", "P1", "2", "10.00", "completed"],
        ["O1", "C1", "2026-09-01", "P1", "2", "10.00", "completed"],      # duplicate: never summed (B3)
        ["O2", "", "2026-09-01", "P1", "1", "5.00", "pending"],           # UNKNOWN country (B2)
        ["O3", "C2", "2026-13-45", "P1", "1", "5.00", "completed"],       # invalid date: no row (B1/B4)
        ["O4", "C2", "2026-09-02", "P1", "-1", "5.00", "completed"],      # negative: FAIL row with total 0 (B9)
        ["O5", "C1", "2026-09-02", "P1", "3", "2.00", "completed"]])
    H.write_csv_rows(raw / "payments.csv", H.CSV_HEADERS["payments"], [
        ["Y1", "O1", "2026-09-05", "20.00", "success"], ["Y2", "O5", "2026-09-02", "1.00", "failed"], ["Y3", "O9", "2026-09-02", "1.00", "success"]])
    c = _mem(); _good_mart(c); c.execute("DELETE FROM marts.daily_country_report"); H.load_oracle(c, raw)
    got = c.execute("SELECT report_date, country, total_orders, completed_orders, gross_revenue, successful_payments, failed_payments, data_quality_status "
                    "FROM o_report ORDER BY ALL").fetchall()
    assert [(str(r[0]), r[1], r[2], r[3], float(r[4]), r[5], r[6], r[7]) for r in got] == [
        ("2026-09-01", "DE", 1, 1, 20.0, 1, 0, "WARN"),      # O1 (dup collapsed) + payment attributed to order date, not payment date
        ("2026-09-01", "UNKNOWN", 1, 0, 0.0, 0, 0, "WARN"),
        ("2026-09-02", "DE", 1, 1, 6.0, 0, 1, "WARN"),       # O5: payment 1.00 vs 6.00 -> mismatch
        ("2026-09-02", "FR", 0, 0, 0.0, 0, 0, "FAIL")]
    c.execute("INSERT INTO marts.daily_country_report SELECT report_date, country, total_orders, completed_orders, gross_revenue, successful_payments, failed_payments, data_quality_status FROM o_report")
    assert H.report_diff(c) == []
    c.execute("UPDATE marts.daily_country_report SET gross_revenue = 40.00 WHERE country='DE' AND report_date='2026-09-01'")     # copies summed
    assert any("gross_revenue" in v for v in H.report_diff(c))
    c.execute("DELETE FROM marts.daily_country_report WHERE country='FR'")                                                       # B9 row dropped
    assert any("missing" in v for v in H.report_diff(c))
    c.execute("INSERT INTO marts.daily_country_report VALUES ('2026-09-03','DE',0,0,0,0,0,'OK')")                                # zero-filled row
    assert any("extra" in v for v in H.report_diff(c))


def test_selfcheck_layering_detects_raw_in_mart_and_downstream_reference():
    good = {"staging/a.sql": "select * from raw.orders", "intermediate/b.sql": "select * from staging.stg_orders -- raw.x",
            "marts/c.sql": "select * from intermediate.int_orders_enriched", "quarantine/d.sql": "select * from staging.stg_orders"}
    assert H.check_sql_layering(good) == []
    assert H.check_sql_layering({**good, "marts/e.sql": "select * from raw.orders"}) != []
    assert H.check_sql_layering({**good, "intermediate/f.sql": "select * from marts.daily_country_report"}) != []
    assert H.check_sql_layering({**good, "staging/g.sql": "select * from intermediate.x"}) != []


def test_selfcheck_determinism_detects_now_random_uuid_but_not_comments():
    assert H.check_sql_deterministic({"marts/a.sql": "select 1 -- now() is not used\n/* random() */"}) == []
    for bad in ("select now()", "select current_timestamp", "select random()", "select uuid()", "select CURRENT_DATE"):
        assert H.check_sql_deterministic({"marts/a.sql": bad}) != [], bad
    assert H.check_sql_deterministic({"raw/load.sql": "select now()"}) == []           # loader may


def test_selfcheck_contract_detects_unclassified_column_and_pii_without_masking():
    cols = [("t", "a"), ("t", "b")]
    ok = [{"columns": [{"name": "a", "classification": "internal"}, {"name": "b", "classification": "confidential"}]}]
    assert H.check_contract_columns(ok, cols) == []
    assert H.check_contract_columns([{"columns": [{"name": "a", "classification": "internal"}]}], cols) != []
    assert H.check_contract_columns([{"columns": [{"name": "a", "classification": "internal"}, {"name": "b", "classification": "secret"}]}], cols) != []
    assert H.check_contract_columns([{"columns": {"a": {"classification": "internal"}, "b": {"classification": "PII"}}}], cols) != []
    assert H.check_contract_columns([{"columns": {"a": {"classification": "internal"}, "b": {"classification": "PII", "masking": "drop"}}}], cols) == []


def test_selfcheck_forbidden_patterns():
    assert H.check_forbidden({"a.py": "import duckdb\nprint('hi')"}) == []
    for bad in ("import boto3", "from google.cloud import bigquery", "import requests", "import urllib.request",
                "u = 'https://x.example'", "password = 'hunter22'", "k = 'AKIAABCDEFGHIJKLMNOP'", "import snowflake.connector"):
        assert H.check_forbidden({"a.py": bad}) != [], bad


def test_selfcheck_mutation_helper_refuses_to_touch_real_raw_data():
    with pytest.raises(AssertionError):
        H.mutate(H.RAW_DIR, "orders", lambda h, r: (h, r))
    assert H.sha256_file(H.RAW_DIR / "orders.csv") == H.RAW_SHA256["orders.csv"]


def test_selfcheck_csv_mutations_have_the_intended_effect(tmp_path):
    raw = H.copy_raw(tmp_path / "r")
    H.mutate(raw, "orders", lambda h, rows: (h, [r[:2] + ["not-a-date"] + r[3:] if i % 5 == 0 else r for i, r in enumerate(rows)]))
    c = _mem(); H.load_oracle(c, raw)
    invalid_dates = c.execute("SELECT count(*) FROM o_orders WHERE d IS NULL").fetchone()[0]
    assert invalid_dates / c.execute("SELECT count(*) FROM o_orders").fetchone()[0] > 0.15      # the F1/R17 seed really exceeds 15%
    raw2 = H.copy_raw(tmp_path / "r2")
    H.mutate(raw2, "orders", lambda h, rows: (h, [r[:2] + ["not-a-date"] + r[3:] if i % 10 == 0 else r for i, r in enumerate(rows)]))
    c2 = _mem(); H.load_oracle(c2, raw2)
    share = c2.execute("SELECT count(*) FILTER (WHERE NOT is_valid) * 1.0 / count(*) FROM o_orders").fetchone()[0]
    assert 0.08 < share < 0.15                                                                  # the control seed stays under 15%


def test_selfcheck_r15_sql_flags_seeded_revenue_error(tmp_path):
    raw = tmp_path / "raw"; raw.mkdir()
    H.write_csv_rows(raw / "customers.csv", H.CSV_HEADERS["customers"], [["C1", "n", "e", "DE", "2026-01-01"]])
    H.write_csv_rows(raw / "orders.csv", H.CSV_HEADERS["orders"], [
        ["O1", "C1", "2026-09-01", "P1", "2", "10.00", "completed"], ["O2", "", "2026-09-01", "P1", "1", "5.50", "completed"],
        ["O3", "C1", "2026-09-01", "P1", "4", "1.00", "refunded"], ["O4", "C1", "2026-09-01", "P1", "-1", "9.00", "completed"]])
    H.write_csv_rows(raw / "payments.csv", H.CSV_HEADERS["payments"], [["Y1", "O1", "2026-09-01", "1.00", "success"]])
    c = _mem(); _good_mart(c); c.execute("DELETE FROM marts.daily_country_report"); H.load_oracle(c, raw)
    c.execute("INSERT INTO marts.daily_country_report VALUES ('2026-09-01','DE',1,1,20.00,1,0,'OK'),('2026-09-01','UNKNOWN',1,1,5.50,0,0,'WARN')")
    sql = (H.HERE / "sql" / "r15_gross_revenue_oracle.sql").read_text()
    assert c.execute(sql).fetchall() == []
    c.execute("UPDATE marts.daily_country_report SET gross_revenue = 24.00 WHERE country='DE'")        # refunded/negative order leaked in
    assert len(c.execute(sql).fetchall()) == 1
    c.execute("UPDATE marts.daily_country_report SET gross_revenue = 20.00 WHERE country='DE'")
    c.execute("DELETE FROM marts.daily_country_report WHERE country='UNKNOWN'")                       # missing row also flagged
    assert len(c.execute(sql).fetchall()) == 1
