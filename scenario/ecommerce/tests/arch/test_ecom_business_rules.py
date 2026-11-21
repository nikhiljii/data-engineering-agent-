"""B1..B9, R12, R14, R15, R16 totals against an independent oracle computed from the raw CSVs (sql/oracle_report.sql)."""
import pytest

import ecom_helpers as H

pytestmark = pytest.mark.usefixtures("baseline")


def test_oracle_itself_matches_the_documented_figures(con):
    """Guards the oracle: figures from requirements.md / data-architecture.md."""
    assert con.execute("SELECT count(DISTINCT order_id) FROM o_src").fetchone()[0] == 6000
    assert con.execute("SELECT count(*) FROM o_src").fetchone()[0] == 6040
    assert con.execute("SELECT count(*) FROM o_orders WHERE NOT is_valid").fetchone()[0] == 55
    assert con.execute("SELECT count(*) FROM o_orders WHERE d IS NULL").fetchone()[0] == 30
    assert con.execute("SELECT count(*) FROM o_orders WHERE q < 0").fetchone()[0] == 25


def test_b1_b2_b3_b5_b6_b7_b8_b9_whole_mart_equals_oracle(con):
    """Row set (B9), counts (B3,B5), revenue (B6), payments (B7), status (B8), country (B2), report_date (B1)."""
    assert H.report_diff(con) == []
    assert con.execute("SELECT count(*) FROM marts.daily_country_report").fetchone()[0] > 200     # ADR-003: 264 cells


def test_b1_payment_metrics_follow_order_date_not_payment_date(baseline, workdir):
    """In the supplied files payment_date always equals order_date, so attribution cannot be told apart on real data.
    Seeded mutation: move every payment_date 3 days later (still valid ISO); the mart must not change (B1, A-2)."""
    import datetime as dt

    def shift(h, rows):
        return h, [r[:2] + [(dt.date.fromisoformat(r[2]) + dt.timedelta(days=3)).isoformat()] + r[3:] for r in rows]
    H.mutate(workdir["raw"], "payments", shift)
    assert H.read_csv_rows(workdir["raw"] / "payments.csv")[1] != H.read_csv_rows(H.RAW_DIR / "payments.csv")[1]     # control: mutation applied
    proc = H.run_pipeline(workdir["raw"], workdir["db"], "--backfill")
    assert proc.returncode == 0, H.describe(proc)
    q = "SELECT * FROM marts.daily_country_report ORDER BY ALL"
    a, b = H.connect_ro(baseline["db"]), H.connect_ro(workdir["db"])
    assert a.execute(q).fetchall() == b.execute(q).fetchall()
    assert b.execute("SELECT sum(successful_payments + failed_payments) FROM marts.daily_country_report").fetchone()[0] > 5000


def test_b1_report_dates_are_valid_calendar_dates_in_range(con):
    lo, hi, n = con.execute("SELECT min(report_date), max(report_date), count(DISTINCT report_date) FROM marts.daily_country_report").fetchone()
    assert (str(lo), str(hi), n) == ("2026-09-01", "2026-09-30", 30)


def test_b2_unknown_country_bucket(con):
    got = con.execute("SELECT coalesce(sum(total_orders), 0) FROM marts.daily_country_report WHERE country='UNKNOWN'").fetchone()[0]
    exp = con.execute("SELECT count(*) FROM o_orders WHERE is_valid AND country='UNKNOWN'").fetchone()[0]
    assert exp > 0 and got == exp
    # the conflicting duplicate pair O002646 resolves to the non-null customer (B3), not UNKNOWN
    assert con.execute("SELECT customer_id FROM staging.stg_orders WHERE order_id='O002646'").fetchone()[0] == "C00066"


def test_b3_dedup_never_sums_copies_and_flags_kept_order(con):
    assert con.execute("SELECT count(*) FROM staging.stg_orders_duplicate_rows").fetchone()[0] == 40
    assert con.execute("SELECT count(*) FROM staging.stg_orders WHERE had_duplicate").fetchone()[0] == 40
    assert con.execute("SELECT count(*) FROM quarantine.orders_rejected WHERE reason LIKE '%duplicate_order_surplus%'").fetchone()[0] == 40
    exp = con.execute("SELECT count(*) FROM o_orders WHERE is_valid").fetchone()[0]
    assert con.execute("SELECT sum(total_orders) FROM marts.daily_country_report").fetchone()[0] == exp == 5945


def test_b4_validity_reasons_and_quarantine_counts(con):
    q = dict(con.execute("SELECT primary_reason, count(*) FROM quarantine.orders_rejected GROUP BY 1").fetchall())
    assert q == {"duplicate_order_surplus": 40, "invalid_order_date": 30, "negative_quantity": 25}
    assert con.execute("SELECT count(*) FROM quarantine.orders_rejected").fetchone()[0] == 95
    # excluded orders never appear in measures: dq.excluded_orders sums to the 25 negative-quantity orders
    assert con.execute("SELECT sum(excluded_orders) FROM marts.daily_country_report_dq").fetchone()[0] == 25


def test_b5_completed_is_subset_and_status_only(con):
    assert con.execute("SELECT sum(completed_orders) FROM marts.daily_country_report").fetchone()[0] == \
        con.execute("SELECT count(*) FROM o_orders WHERE is_valid AND order_status='completed'").fetchone()[0]


def test_b6_gross_revenue_decimal_two_places(con):
    assert con.execute("SELECT count(*) FROM marts.daily_country_report WHERE gross_revenue <> round(gross_revenue, 2)").fetchone()[0] == 0
    got = con.execute("SELECT sum(gross_revenue) FROM marts.daily_country_report").fetchone()[0]
    exp = con.execute("SELECT sum(gross_revenue) FROM o_report").fetchone()[0]
    assert got == exp and got > 0


def test_b7_pending_payments_in_neither_column(con):
    pend = con.execute("SELECT count(*) FROM o_pay p JOIN o_orders o USING (order_id) WHERE o.is_valid AND p.payment_status='pending'").fetchone()[0]
    assert pend > 0
    row = con.execute((H.HERE / "sql" / "r14_payment_row_accounting.sql").read_text()).fetchone()
    raw_rows, rejected, counted, reported, expected_sf = row
    assert reported == expected_sf
    assert counted - expected_sf == pend            # pending is counted in the row accounting but in neither report column


def test_b8_status_matches_oracle_and_dq_has_excluded_count(con):
    assert H.check_status_derivation(con) == []
    diffs = [d for d in H.report_diff(con) if "data_quality_status" in d]
    assert diffs == []
    n = con.execute("SELECT count(*) FROM marts.daily_country_report WHERE data_quality_status='FAIL'").fetchone()[0]
    exp = con.execute("SELECT count(*) FROM o_report WHERE data_quality_status='FAIL'").fetchone()[0]
    assert n == exp > 0


def test_b8_contradictions_are_informational_only(con):
    """ADR-003: status/payment contradictions never drive WARN. An OK row may still carry contradictions."""
    n = con.execute("SELECT count(*) FROM marts.daily_country_report m JOIN marts.daily_country_report_dq d USING (report_date, country) "
                    "WHERE m.data_quality_status='OK' AND d.status_payment_contradictions > 0").fetchone()[0]
    o = con.execute("SELECT count(*) FROM o_report WHERE data_quality_status='OK'").fetchone()[0]
    assert o > 0
    assert n > 0, "no OK row carries a status/payment contradiction; ADR-003 says contradictions (970 + 220 orders) do not drive WARN"


def test_b9_sparse_grain_no_zero_filled_rows(con):
    n = con.execute("SELECT count(*) FROM marts.daily_country_report m JOIN marts.daily_country_report_dq d USING (report_date, country) "
                    "WHERE m.total_orders = 0 AND d.excluded_orders = 0").fetchone()[0]
    assert n == 0
    assert con.execute("SELECT count(*) FROM marts.daily_country_report").fetchone()[0] < 30 * 9      # not a full calendar x country grid


# ---- R12, R14, R15 reconciliations
def test_r12_order_reconciliation(con):
    raw_distinct, sum_total, quarantined_invalid = con.execute((H.HERE / "sql" / "r12_order_reconciliation.sql").read_text()).fetchone()
    assert raw_distinct == sum_total + quarantined_invalid
    assert (raw_distinct, quarantined_invalid) == (6000, 55)
    assert sum_total == 5945


def test_r14_payment_row_accounting(con):
    raw_rows, rejected, counted, reported, expected_sf = con.execute((H.HERE / "sql" / "r14_payment_row_accounting.sql").read_text()).fetchone()
    assert raw_rows == 5950
    assert raw_rows == counted + rejected
    orphans = con.execute("SELECT count(*) FROM quarantine.payments_rejected WHERE reason LIKE '%orphan_payment%'").fetchone()[0]
    excluded = con.execute("SELECT count(*) FROM quarantine.payments_rejected WHERE reason LIKE '%order_excluded%'").fetchone()[0]
    assert orphans == 30
    assert excluded >= 28                       # 28 payments of invalid-date orders are documented; negative-quantity ones are UNKNOWN
    assert rejected == orphans + excluded       # no other reasons, no overlap (orphans have no order)
    ex_oracle = con.execute("SELECT count(*) FROM o_pay p JOIN o_orders o USING (order_id) WHERE NOT o.is_valid").fetchone()[0]
    assert excluded == ex_oracle                # B7: payments of invalid orders, whatever their number, are all quarantined


def test_r14_quarantined_payments_have_no_valid_order(con):
    n = con.execute("SELECT count(*) FROM quarantine.payments_rejected q JOIN o_orders o USING (order_id) WHERE o.is_valid").fetchone()[0]
    assert n == 0


def test_r15_gross_revenue_equals_independent_staging_query(con):
    assert con.execute((H.HERE / "sql" / "r15_gross_revenue_oracle.sql").read_text()).fetchall() == []


def test_r15_gross_revenue_ignores_payment_amount_mutation(baseline, workdir):
    """Seeded mutation: scramble every payment_amount; gross_revenue (from order lines) must not change."""
    H.mutate(workdir["raw"], "payments", lambda h, rows: (h, [r[:3] + ["%.2f" % (float(r[3]) * 3 + 7)] + r[4:] for r in rows]))
    proc = H.run_pipeline(workdir["raw"], workdir["db"], "--backfill")
    assert proc.returncode == 0, H.describe(proc)
    a, b = H.connect_ro(baseline["db"]), H.connect_ro(workdir["db"])
    q = "SELECT report_date, country, gross_revenue, total_orders, completed_orders FROM marts.daily_country_report ORDER BY ALL"
    assert a.execute(q).fetchall() == b.execute(q).fetchall()
    # control: the mutation did change something payment-related, otherwise this test would be vacuous
    assert a.execute("SELECT sum(amount_mismatch_orders) FROM marts.daily_country_report_dq").fetchone()[0] != \
        b.execute("SELECT sum(amount_mismatch_orders) FROM marts.daily_country_report_dq").fetchone()[0]


def test_expected_excluded_share_and_summary_fields(con):
    share, orphans, status = con.execute("SELECT excluded_share, orphan_payment_count, run_status FROM marts.run_summary").fetchone()
    assert abs(float(share) - 55 / 6000) < 0.001 and float(share) < 0.15
    assert (orphans, status) == (30, "PUBLISHED")
