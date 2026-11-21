"""Stop rules: excluded share, raw and staging contract failures, freshness, volume, stale-file. Nothing is published on STOPPED."""
import csv
import shutil
from datetime import datetime, timedelta

from conftest import ORD_HDR, PAY_HDR, cli, q, small_fixture, write_csv
from src_scenario.run import run


def _mart_rows(db):
    return q(db, "SELECT count(*) FROM marts.daily_country_report")[0][0]


def test_excluded_share_over_15_percent_stops_and_publishes_nothing(tmp_path, db_path):
    raw = small_fixture(tmp_path / "raw", extra_bad_date_orders=10)    # 12 excluded of 59 ids = 20.3%
    res = run(raw, db_path, backfill=True)
    assert res.status == "STOPPED" and res.exit_code == 1
    assert "excluded share" in res.stop_reason
    assert _mart_rows(db_path) == 0
    assert q(db_path, "SELECT count(*) FROM quarantine.orders_rejected") == [(0,)]
    assert q(db_path, "SELECT run_status, distinct_excluded_orders, distinct_order_ids FROM marts.run_summary") == [("STOPPED", 12, 59)]
    assert q(db_path, "SELECT status FROM audit.run_log") == [("STOPPED",)]
    r = cli(["--backfill", "--raw-dir", str(raw), "--db", str(tmp_path / "cli.duckdb")])
    assert r.returncode == 1 and "STOPPED" in r.stderr


def test_stopped_rerun_keeps_previously_published_data(tmp_path, db_path):
    good = small_fixture(tmp_path / "good")
    assert run(good, db_path, backfill=True).status == "PUBLISHED"
    before = q(db_path, "SELECT * FROM marts.daily_country_report ORDER BY ALL")
    bad = small_fixture(tmp_path / "bad", extra_bad_date_orders=10)
    assert run(bad, db_path, backfill=True).status == "STOPPED"
    assert q(db_path, "SELECT * FROM marts.daily_country_report ORDER BY ALL") == before


def test_share_exactly_at_limit_is_allowed(tmp_path, db_path):
    # 15 excluded of 100 distinct ids: share == 0.15, not above, so it runs
    raw = tmp_path / "raw"
    raw.mkdir()
    write_csv(raw / "customers.csv", ["customer_id", "customer_name", "email", "country", "created_at"], [["C1", "n", "e", "DE", "2026-01-01"]])
    write_csv(raw / "orders.csv", ORD_HDR, [[f"O{i}", "C1", "2026-09-01" if i >= 15 else "bad", "P", "1", "1.00", "pending"] for i in range(100)])
    write_csv(raw / "payments.csv", PAY_HDR, [["P1", "O99", "2026-09-01", "1.00", "success"]])
    assert run(raw, db_path, backfill=True).status == "PUBLISHED"


def test_bad_header_stops_before_landing(small_raw, db_path):
    write_csv(small_raw / "payments.csv", ["payment_id", "order_id", "payment_date", "amount", "payment_status"], [["P1", "O1", "2026-09-01", "1", "success"]])
    res = run(small_raw, db_path, backfill=True)
    assert res.status == "STOPPED" and "payments_header_exact" in res.stop_reason
    assert q(db_path, "SELECT (SELECT count(*) FROM raw.customers), (SELECT count(*) FROM raw.orders), (SELECT count(*) FROM raw.payments)") == [(0, 0, 0)]


def test_missing_and_empty_file_stop(small_raw, db_path):
    (small_raw / "orders.csv").unlink()
    assert "orders_file_exists" in run(small_raw, db_path, backfill=True).stop_reason
    write_csv(small_raw / "orders.csv", ORD_HDR, [])
    assert "orders_rows_gt_zero" in run(small_raw, db_path, backfill=True).stop_reason


def test_unknown_order_status_fails_staging_contract(small_raw, db_path):
    with open(small_raw / "orders.csv", "a", newline="") as f:
        csv.writer(f).writerow(["OQ", "C1", "2026-09-07", "P", "1", "1.00", "shipped"])
    res = run(small_raw, db_path, backfill=True)
    assert res.status == "STOPPED" and "orders_order_status_accepted" in res.stop_reason
    assert _mart_rows(db_path) == 0


def test_two_payments_for_one_order_fail_contract(small_raw, db_path):
    with open(small_raw / "payments.csv", "a", newline="") as f:
        csv.writer(f).writerow(["P99", "O1", "2026-09-01", "1.00", "success"])
    res = run(small_raw, db_path, backfill=True)
    assert res.status == "STOPPED" and "payments_at_most_one_payment_per_order" in res.stop_reason


def test_bad_unit_price_and_all_failures_reported(small_raw, db_path):
    with open(small_raw / "orders.csv", "a", newline="") as f:
        w = csv.writer(f)
        w.writerow(["OQ", "C1", "2026-09-07", "P", "1", "abc", "completed"])
        w.writerow(["OR", "C1", "2026-09-07", "P", "1", "1.00", "weird"])
    res = run(small_raw, db_path, backfill=True)
    failed = {e.check for e in res.events if not e.ok}
    assert {"orders_unit_price_numeric_positive", "orders_order_status_accepted"} <= failed   # compound faults all reported


def test_volume_band_breach_stops_and_does_not_land(small_raw, db_path):
    assert run(small_raw, db_path, backfill=True).status == "PUBLISHED"
    rows = list(csv.reader(open(small_raw / "orders.csv")))
    with open(small_raw / "orders.csv", "a", newline="") as f:
        csv.writer(f).writerows([[f"OV{i}", "C1", "2026-09-08", "P", "1", "1.00", "pending"] for i in range(100)])   # 49+... > +50%
    res = run(small_raw, db_path, backfill=True)
    assert res.status == "STOPPED" and "orders_volume_band" in res.stop_reason
    assert q(db_path, "SELECT count(DISTINCT _batch_id) FROM raw.orders") == [(1,)]


def test_changed_file_appends_new_batch_and_staging_reads_latest(small_raw, db_path):
    run(small_raw, db_path, backfill=True)
    with open(small_raw / "orders.csv", "a", newline="") as f:
        csv.writer(f).writerow(["ONEW", "C2", "2026-09-10", "P", "1", "2.00", "completed"])
    res = run(small_raw, db_path, backfill=True)
    assert res.status == "PUBLISHED", res.stop_reason
    assert q(db_path, "SELECT count(DISTINCT _batch_id) FROM raw.orders") == [(2,)]
    assert q(db_path, "SELECT total_orders FROM marts.daily_country_report WHERE report_date='2026-09-10'") == [(1,)]


def test_reloading_an_older_file_is_refused_not_silently_stale(small_raw, db_path, tmp_path):
    shutil.copy(small_raw / "orders.csv", tmp_path / "orders_v1.csv")
    run(small_raw, db_path, backfill=True)
    with open(small_raw / "orders.csv", "a", newline="") as f:
        csv.writer(f).writerow(["ONEW", "C2", "2026-09-10", "P", "1", "2.00", "completed"])
    run(small_raw, db_path, backfill=True)
    shutil.copy(tmp_path / "orders_v1.csv", small_raw / "orders.csv")
    res = run(small_raw, db_path, backfill=True)
    assert res.status == "STOPPED" and "orders_latest_batch_is_current_file" in res.stop_reason


def test_freshness_over_24h_stops(small_raw, db_path):
    t0 = datetime(2026, 11, 1, 8, 0)
    assert run(small_raw, db_path, backfill=True, now=t0).status == "PUBLISHED"
    assert run(small_raw, db_path, backfill=True, now=t0 + timedelta(hours=23)).status == "PUBLISHED"
    res = run(small_raw, db_path, backfill=True, now=t0 + timedelta(hours=25))
    assert res.status == "STOPPED" and "freshness" in res.stop_reason


def test_landing_is_all_or_nothing(small_raw, db_path):
    import duckdb
    import pytest
    from src_scenario import raw_load
    from src_scenario.run import _exec_file
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    _exec_file(con, "raw", "00_schemas")
    _exec_file(con, "raw", "01_tables")
    _, plan = raw_load.validate_sources(con, small_raw)
    assert set(plan) == {"customers", "orders", "payments"}
    (small_raw / "orders.csv").unlink()          # customers (first) lands, orders (second) then fails
    with pytest.raises(duckdb.Error):
        raw_load.land_sources(con, plan, datetime(2026, 11, 1))
    assert con.execute("SELECT (SELECT count(*) FROM raw.customers), (SELECT count(*) FROM raw.orders), (SELECT count(*) FROM raw.payments)").fetchall() == [(0, 0, 0)]
    con.close()
