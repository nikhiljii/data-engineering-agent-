"""R17 (stop and publish nothing), R18 (no silent coercion), R19 (volume band), contract failures at raw/staging boundaries.
Every case runs the real CLI on a seeded mutation of a tmp copy of the raw CSVs."""
import pytest

import ecom_helpers as H


def _last_status(db):
    c = H.connect_ro(db)
    try:
        return c.execute("SELECT status FROM audit.run_log ORDER BY finished_at DESC, started_at DESC LIMIT 1").fetchone()[0]
    finally:
        c.close()


def _assert_stopped_nothing_published(workdir, proc):
    assert proc.returncode != 0, "run should exit non-zero when STOPPED\n" + H.describe(proc)
    assert workdir["db"].exists(), "a stopped run must still record STOPPED in audit.run_log"
    assert _last_status(workdir["db"]) == "STOPPED"
    c = H.connect_ro(workdir["db"])
    try:
        assert H.row_count_or_zero(c, "marts", "daily_country_report") == 0
        assert H.row_count_or_zero(c, "marts", "daily_country_report_dq") == 0
    finally:
        c.close()


def _break_dates(every):
    def f(h, rows):
        return h, [r[:2] + ["not-a-date"] + r[3:] if i % every == 0 else r for i, r in enumerate(rows)]
    return f


# ---- R17
def test_r17_more_than_15_percent_invalid_dates_stops_and_publishes_nothing(workdir):
    H.mutate(workdir["raw"], "orders", _break_dates(5))          # about 20% of orders
    _assert_stopped_nothing_published(workdir, H.run_pipeline(workdir["raw"], workdir["db"], "--backfill"))


def test_r17_just_below_threshold_still_publishes_control(workdir):
    H.mutate(workdir["raw"], "orders", _break_dates(10))         # about 10% (< 15%): control proving the stop is threshold-driven
    p = H.run_pipeline(workdir["raw"], workdir["db"], "--backfill")
    assert p.returncode == 0, H.describe(p)
    assert _last_status(workdir["db"]) == "PUBLISHED"
    c = H.connect_ro(workdir["db"])
    share = float(c.execute("SELECT excluded_share FROM marts.run_summary").fetchone()[0])
    assert 0.08 < share < 0.15


def test_r17_more_than_15_percent_negative_quantity_also_stops(workdir):
    """The threshold counts invalid date AND invalid quantity types; negative quantities are 'invalid quantity' exclusions."""
    H.mutate(workdir["raw"], "orders", lambda h, rows: (h, [r[:4] + ["-1"] + r[5:] if i % 5 == 0 else r for i, r in enumerate(rows)]))
    _assert_stopped_nothing_published(workdir, H.run_pipeline(workdir["raw"], workdir["db"], "--backfill"))


@pytest.mark.parametrize("name,source,fn", [
    ("orders_header_renamed", "orders", lambda h, rows: (["orderid"] + h[1:], rows)),                     # F2
    ("payments_missing_column", "payments", lambda h, rows: (h[:-1], [r[:-1] for r in rows])),
    ("payments_empty", "payments", lambda h, rows: (h, [])),                                              # F3
    ("customers_empty", "customers", lambda h, rows: (h, [])),
    ("customers_duplicate_key", "customers", lambda h, rows: (h, rows + [rows[0]])),                      # F5 (customers contract)
    ("order_status_unknown_value", "orders", lambda h, rows: (h, [r[:6] + ["shipped"] if i == 5 else r for i, r in enumerate(rows)])),
    ("unit_price_not_positive", "orders", lambda h, rows: (h, [r[:5] + ["-5"] + r[6:] if i == 5 else r for i, r in enumerate(rows)])),
    ("unit_price_non_numeric", "orders", lambda h, rows: (h, [r[:5] + ["abc"] + r[6:] if i == 5 else r for i, r in enumerate(rows)])),
    ("payment_status_unknown_value", "payments", lambda h, rows: (h, [r[:4] + ["settled"] if i == 5 else r for i, r in enumerate(rows)])),
    ("payment_amount_non_numeric", "payments", lambda h, rows: (h, [r[:3] + ["n/a"] + r[4:] if i == 5 else r for i, r in enumerate(rows)])),
    ("two_payments_for_one_order", "payments", lambda h, rows: (h, rows + [["PAYDUP1", rows[0][1], rows[0][2], rows[0][3], rows[0][4]]])),
    ("payment_id_duplicated", "payments", lambda h, rows: (h, rows + [rows[0]])),
])
def test_contract_failures_stop_the_run(workdir, name, source, fn):
    H.mutate(workdir["raw"], source, fn)
    _assert_stopped_nothing_published(workdir, H.run_pipeline(workdir["raw"], workdir["db"], "--backfill"))


def test_contract_failure_missing_payments_file(workdir):                                               # F3 (file absent)
    (workdir["raw"] / "payments.csv").unlink()
    p = H.run_pipeline(workdir["raw"], workdir["db"], "--backfill")
    assert p.returncode != 0, H.describe(p)
    assert workdir["db"].exists(), "a stopped run must still record STOPPED in audit.run_log\n" + H.describe(p)
    assert _last_status(workdir["db"]) == "STOPPED"
    c = H.connect_ro(workdir["db"])
    assert H.row_count_or_zero(c, "marts", "daily_country_report") == 0


def test_r17_stopped_run_after_good_run_leaves_published_mart_unchanged(workdir, tmp_path):
    assert H.run_pipeline(workdir["raw"], workdir["db"], "--backfill").returncode == 0
    before = H.connect_ro(workdir["db"])
    h = {t: H.table_hash(before, t) for t in ("marts.daily_country_report", "marts.daily_country_report_dq")}
    before.close()
    bad = H.copy_raw(tmp_path / "bad")
    H.mutate(bad, "orders", _break_dates(5))
    p = H.run_pipeline(bad, workdir["db"], "--backfill")
    assert p.returncode != 0, H.describe(p)
    after = H.connect_ro(workdir["db"])
    assert {t: H.table_hash(after, t) for t in h} == h
    assert _last_status(workdir["db"]) == "STOPPED"


# ---- R19 volume band against the previous batch
def test_r19_volume_drop_over_50_percent_stops(workdir, tmp_path):
    assert H.run_pipeline(workdir["raw"], workdir["db"], "--backfill").returncode == 0
    cut = H.copy_raw(tmp_path / "cut")
    H.mutate(cut, "orders", lambda h, rows: (h, rows[: int(len(rows) * 0.4)]))                          # F4: -60%
    p = H.run_pipeline(cut, workdir["db"], "--backfill")
    assert p.returncode != 0, H.describe(p)
    assert _last_status(workdir["db"]) == "STOPPED"
    c = H.connect_ro(workdir["db"])
    assert c.execute("SELECT count(*) FROM marts.daily_country_report").fetchone()[0] > 0       # earlier publication intact


def test_r19_volume_change_within_band_is_accepted_control(workdir, tmp_path):
    assert H.run_pipeline(workdir["raw"], workdir["db"], "--backfill").returncode == 0
    ok = H.copy_raw(tmp_path / "ok")
    H.mutate(ok, "orders", lambda h, rows: (h, rows[: int(len(rows) * 0.8)]))                           # -20%: inside +/-50%
    p = H.run_pipeline(ok, workdir["db"], "--backfill")
    assert p.returncode == 0, H.describe(p)


# ---- R18 no silent coercion
def test_r18_non_numeric_quantity_is_quarantined_or_stops_never_silently_zeroed(workdir):
    H.mutate(workdir["raw"], "orders", lambda h, rows: (h, [r[:4] + ["abc"] + r[5:] if i == 7 else r for i, r in enumerate(rows)]))
    oid = H.read_csv_rows(workdir["raw"] / "orders.csv")[1][7][0]
    p = H.run_pipeline(workdir["raw"], workdir["db"], "--backfill")
    if p.returncode != 0:
        assert _last_status(workdir["db"]) == "STOPPED"
        return
    c = H.connect_ro(workdir["db"])
    reasons = c.execute("SELECT reason FROM quarantine.orders_rejected WHERE order_id=? AND quantity='abc'", [oid]).fetchall()
    assert reasons and "invalid_quantity" in reasons[0][0], f"order {oid} with quantity 'abc' was neither quarantined nor stopped"
    assert c.execute("SELECT count(*) FROM staging.stg_orders WHERE order_id=? AND is_valid", [oid]).fetchone()[0] == 0


def test_r18_every_nonempty_unparseable_order_date_is_flagged(con):
    n = con.execute("""SELECT count(*) FROM o_src s WHERE s.order_date IS NOT NULL AND s.order_date <> ''
                         AND try_strptime(s.order_date, '%Y-%m-%d') IS NULL
                         AND s.order_id NOT IN (SELECT order_id FROM quarantine.orders_rejected WHERE reason LIKE '%invalid_order_date%')""").fetchone()[0]
    assert n == 0
    assert con.execute("SELECT count(*) FROM o_src WHERE try_strptime(order_date, '%Y-%m-%d') IS NULL").fetchone()[0] == 30     # not vacuous


# ---- R16 multi-reason precedence, seeded
def test_r16_row_with_two_reasons_carries_both_sorted_and_correct_primary(workdir):
    extra = ["O8000001", "C00001", "2026-13-45", "P0001", "-2", "10.00", "completed"]
    H.mutate(workdir["raw"], "orders", lambda h, rows: (h, rows + [extra]))
    p = H.run_pipeline(workdir["raw"], workdir["db"], "--backfill")
    assert p.returncode == 0, H.describe(p)
    c = H.connect_ro(workdir["db"])
    pr, reason = c.execute("SELECT primary_reason, reason FROM quarantine.orders_rejected WHERE order_id='O8000001'").fetchone()
    assert (pr, reason) == ("invalid_order_date", "invalid_order_date|negative_quantity")
    assert H.check_quarantine_reasons(c) == []


# ---- R2 on a hostile input
def test_r2_pii_in_input_never_lands_even_with_a_duplicated_customer_key(workdir):
    H.mutate(workdir["raw"], "customers", lambda h, rows: (h, rows + [rows[0]]))
    p = H.run_pipeline(workdir["raw"], workdir["db"], "--backfill")
    assert p.returncode != 0, H.describe(p)                      # customers contract (unique key) must stop the run
    assert workdir["db"].exists() and _last_status(workdir["db"]) == "STOPPED", H.describe(p)
    c = H.connect_ro(workdir["db"])
    assert H.check_no_pii_columns(c) == []
    assert H.check_no_pii_values(c) == []
