"""R1, R2, R4, R19 (recording), plus run-level facts. Pipeline-facing: RED until the builder delivers."""
import pytest
import duckdb

import ecom_helpers as H

pytestmark = pytest.mark.usefixtures("baseline")


def test_r4_layer_schemas_exist(con):
    assert H.check_schemas(con) == []


@pytest.mark.parametrize("table,expected", [("customers", 1200), ("orders", 6040), ("payments", 5950)])
def test_r1_raw_row_counts_equal_csv_rows(con, baseline, table, expected):
    csv_rows = con.execute(f"SELECT count(*) FROM read_csv('{baseline['raw']}/{table}.csv', header=true, all_varchar=true)").fetchone()[0]
    assert csv_rows == expected                     # the input really has the stated rows
    assert con.execute(f"SELECT count(*) FROM raw.{table}").fetchone()[0] == expected


def test_r1_raw_columns_are_varchar_with_load_metadata(con):
    assert H.check_raw_all_varchar(con) == []


@pytest.mark.parametrize("table", ["customers", "orders", "payments"])
def test_r1_raw_values_unmodified(con, baseline, table):
    cols = H.RAW_DATA_COLS[table]
    sel = ", ".join(f"NULLIF({c}, '') AS {c}" for c in cols)
    src = f"SELECT {sel} FROM read_csv('{baseline['raw']}/{table}.csv', header=true, all_varchar=true)"
    got = f"SELECT {sel} FROM raw.{table}"
    assert con.execute(f"SELECT count(*) FROM (({src}) EXCEPT ALL ({got}))").fetchone()[0] == 0
    assert con.execute(f"SELECT count(*) FROM (({got}) EXCEPT ALL ({src}))").fetchone()[0] == 0


def test_r1_raw_customers_has_exactly_the_pruned_columns(con):
    cols = [r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='raw' "
                                      "AND table_name='customers'").fetchall()]
    assert set(cols) == set(H.RAW_DATA_COLS["customers"]) | set(H.RAW_META)


def test_r1_load_metadata_populated_and_sha_matches_file(con, baseline):
    for t in ("customers", "orders", "payments"):
        shas = {r[0] for r in con.execute(f"SELECT DISTINCT _source_sha256 FROM raw.{t}").fetchall()}
        assert shas == {H.sha256_file(baseline["raw"] / f"{t}.csv")}
        n_null = con.execute(f"SELECT count(*) FROM raw.{t} WHERE _batch_id IS NULL OR _loaded_at IS NULL "
                             "OR _source_file IS NULL OR _source_row_number IS NULL").fetchone()[0]
        assert n_null == 0


def test_r2_no_pii_column_anywhere(con):
    assert H.check_no_pii_columns(con) == []


def test_r2_no_pii_values_anywhere(con):
    assert H.check_no_pii_values(con) == []


def test_r19_run_log_records_freshness_and_volume_checks(con):
    cols = [r[0].lower() for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='audit' "
                                              "AND table_name='run_log'").fetchall()]
    assert any("fresh" in c for c in cols), f"run_log has no freshness column: {cols}"
    assert any("volume" in c for c in cols), f"run_log has no volume column: {cols}"
    assert con.execute("SELECT count(*) FROM audit.run_log").fetchone()[0] >= 1


def test_run_log_and_summary_for_published_backfill(con):
    st = con.execute("SELECT status FROM audit.run_log ORDER BY finished_at DESC LIMIT 1").fetchone()[0]
    assert st == "PUBLISHED"
    assert con.execute("SELECT count(*) FROM marts.run_summary").fetchone()[0] == 1
    assert con.execute("SELECT run_status FROM marts.run_summary").fetchone()[0] == "PUBLISHED"
