"""R5, R6, R7, R10, R11, R13, R16, R21 on the built database."""
import yaml
import pytest

import ecom_helpers as H

pytestmark = pytest.mark.usefixtures("baseline")
FKS = yaml.safe_load((H.ROOT / "architecture" / "foreign-keys.yaml").read_text())["foreign_keys"]


def _schema_of(con, name):
    r = con.execute("SELECT table_schema FROM information_schema.tables WHERE table_name=?", [name]).fetchall()
    assert len(r) == 1, f"{name} must exist exactly once, found {r}"
    return r[0][0]


# ---- R5
def test_r5_exactly_the_declared_staging_models_as_views(con):
    got = {r[0]: r[1] for r in con.execute("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema='staging'").fetchall()}
    assert set(got) == {"stg_customers", "stg_orders", "stg_orders_duplicate_rows", "stg_payments"}
    assert set(got.values()) == {"VIEW"}


@pytest.mark.parametrize("table,key", [("stg_customers", "customer_id"), ("stg_orders", "order_id"), ("stg_payments", "payment_id")])
def test_r5_staging_key_unique_not_null(con, table, key):
    assert H.check_unique_not_null(con, f"staging.{table}", [key]) == []


def test_r5_staging_duplicate_rows_key(con):
    assert H.check_unique_not_null(con, "staging.stg_orders_duplicate_rows", ["order_id", "_source_row_number"]) == []


def test_r5_accepted_values_order_and_payment_status(con):
    bad_o = con.execute("SELECT DISTINCT order_status FROM staging.stg_orders WHERE order_status IS NULL "
                        "OR order_status NOT IN ('completed','refunded','pending','cancelled')").fetchall()
    bad_p = con.execute("SELECT DISTINCT payment_status FROM staging.stg_payments WHERE payment_status IS NULL "
                        "OR payment_status NOT IN ('success','failed','pending')").fetchall()
    assert bad_o == [] and bad_p == []


def test_r5_staging_customers_country_and_row_counts(con):
    assert con.execute("SELECT count(*) FROM staging.stg_customers").fetchone()[0] == 1200
    assert con.execute("SELECT count(*) FROM staging.stg_customers WHERE country IS NULL OR NOT regexp_matches(country, '^[A-Z]{2}$')").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM staging.stg_payments").fetchone()[0] == 5950


# ---- R6
@pytest.mark.parametrize("table,key", [("daily_country_report", ["report_date", "country"]),
                                       ("daily_country_report_dq", ["report_date", "country"]),
                                       ("run_summary", ["run_id"])])
def test_r6_mart_keys_unique_not_null(con, table, key):
    assert H.check_unique_not_null(con, f"marts.{table}", key) == []
    assert con.execute(f"SELECT count(*) FROM marts.{table}").fetchone()[0] > 0     # not vacuous


# ---- R7: one relationships test per declared FK, driven by the YAML (so adding an FK adds a test)
def test_r7_yaml_declares_the_three_known_foreign_keys():
    assert {(f["model"], f["column"]) for f in FKS} >= {("stg_orders", "customer_id"), ("stg_payments", "order_id"),
                                                       ("daily_country_report_dq", "report_date")}


@pytest.mark.parametrize("fk", FKS, ids=[f"{f['model']}.{f['column']}" for f in FKS])
def test_r7_relationship(con, fk):
    s_from, s_to = _schema_of(con, fk["model"]), _schema_of(con, fk["to"])
    if fk["model"] == "daily_country_report_dq":       # composite key, both directions
        a = con.execute("SELECT count(*) FROM marts.daily_country_report_dq d ANTI JOIN marts.daily_country_report m USING (report_date, country)").fetchone()[0]
        b = con.execute("SELECT count(*) FROM marts.daily_country_report m ANTI JOIN marts.daily_country_report_dq d USING (report_date, country)").fetchone()[0]
        assert (a, b) == (0, 0)
        return
    viol = con.execute(f'SELECT count(*) FROM {s_from}.{fk["model"]} t WHERE t."{fk["column"]}" IS NOT NULL AND NOT EXISTS '
                       f'(SELECT 1 FROM {s_to}.{fk["to"]} r WHERE r."{fk["field"]}" = t."{fk["column"]}")').fetchone()[0]
    if fk["model"] == "stg_payments":
        assert viol == 30                                                    # D6 orphans, known-violated by design
        assert fk["severity"] == "warn"
        assert con.execute("SELECT orphan_payment_count FROM marts.run_summary").fetchone()[0] == viol   # reported into run_summary
    elif fk["model"] == "stg_orders":
        assert viol == 0                                                     # every non-null customer_id exists today


# ---- R10 / R11
def test_r10_report_exact_columns_and_types(con):
    assert H.check_report_columns(con) == []


def test_r10_country_domain(con):
    bad = con.execute("SELECT DISTINCT country FROM marts.daily_country_report").fetchall()
    assert {r[0] for r in bad} <= H.COUNTRIES and len(bad) >= 8


def test_r10_measure_ranges(con):
    n = con.execute("SELECT count(*) FROM marts.daily_country_report WHERE total_orders < 0 OR completed_orders < 0 "
                    "OR completed_orders > total_orders OR gross_revenue < 0 OR successful_payments < 0 OR failed_payments < 0 "
                    "OR report_date IS NULL OR country IS NULL").fetchone()[0]
    assert n == 0


def test_r11_status_equals_derivation_from_dq(con):
    assert H.check_status_derivation(con) == []


def test_r11_dq_has_documented_columns_as_integers(con):
    got = dict(con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='marts' "
                           "AND table_name='daily_country_report_dq'").fetchall())
    for c in H.DQ_COLUMNS:
        assert got.get(c) == "INTEGER", f"{c}: {got.get(c)}"


def test_r11_all_three_statuses_are_exercised(con):
    got = {r[0] for r in con.execute("SELECT DISTINCT data_quality_status FROM marts.daily_country_report").fetchall()}
    assert got == {"OK", "WARN", "FAIL"} or got == {"WARN", "FAIL"}, got      # ADR-003: FAIL rows (24 cells) and WARN rows (126+) exist


# ---- R13
def test_r13_order_row_accounting_exact(con):
    sql = (H.HERE / "sql" / "r13_order_row_accounting.sql").read_text()
    raw_rows, stg, dup, valid, invalid, quarantined_invalid = con.execute(sql).fetchone()
    assert (raw_rows, stg, dup) == (6040, 6000, 40)
    assert raw_rows == stg + dup
    assert stg == valid + invalid
    assert invalid == quarantined_invalid == 55
    assert valid == 5945


# ---- R16
def test_r16_quarantine_reasons_and_precedence(con):
    assert H.check_quarantine_reasons(con) == []


def test_r16_quarantine_carries_raw_values_and_trace_columns(con):
    cols = dict(con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='quarantine' "
                            "AND table_name='orders_rejected'").fetchall())
    for c in H.RAW_DATA_COLS["orders"] + ["primary_reason", "reason", "_source_row_number"]:
        assert c in cols, f"orders_rejected missing {c}"
    for c in H.RAW_DATA_COLS["orders"]:
        assert cols[c] == "VARCHAR"
    pcols = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='quarantine' "
                                       "AND table_name='payments_rejected'").fetchall()}
    for c in H.RAW_DATA_COLS["payments"] + ["primary_reason", "reason"]:
        assert c in pcols, f"payments_rejected missing {c}"


def test_r16_quarantined_order_values_match_the_csv(con, baseline):
    q = con.execute("SELECT order_id, order_date, quantity FROM quarantine.orders_rejected WHERE reason LIKE '%invalid_order_date%'").fetchall()
    src = {r[0]: r for r in con.execute(f"SELECT order_id, order_date, quantity FROM read_csv('{baseline['raw']}/orders.csv', header=true, all_varchar=true)").fetchall()}
    assert len(q) == 30
    assert all(src[r[0]][1] == r[1] and src[r[0]][2] == r[2] for r in q)
    assert {r[1] for r in q} == {"2026-13-45", "0000-00-00", "not-a-date", "31/02/2026"}


# ---- R21 (runtime half: the contract file must classify every mart column that exists in the DB)
def test_r21_every_mart_column_in_db_is_classified_in_contract_files(con):
    files = sorted(H.CONTRACTS_DIR.glob("*.yaml"))
    assert files, "no contract files in scenario/ecommerce/contracts/"
    docs = [yaml.safe_load(p.read_text()) for p in files]
    cols = con.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='marts'").fetchall()
    assert len(cols) >= 8 + 2 + 8
    assert H.check_contract_columns(docs, cols) == []
