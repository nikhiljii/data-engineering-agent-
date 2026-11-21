"""R8 (rerun identical), R9 (other dates untouched), R20 (raw load idempotent). Each has a positive control."""
import pytest

import ecom_helpers as H

TABLES = ["marts.daily_country_report", "marts.daily_country_report_dq", "quarantine.orders_rejected", "quarantine.payments_rejected"]


def _hashes(db, exclude=()):
    c = H.connect_ro(db)
    try:
        return {t: H.table_hash(c, t, exclude) for t in TABLES}
    finally:
        c.close()


def _counts(db):
    c = H.connect_ro(db)
    try:
        return {t: c.execute(f"SELECT count(*) FROM raw.{t}").fetchone()[0] for t in ("customers", "orders", "payments")}
    finally:
        c.close()


def test_r8_rerun_same_batch_same_db_is_byte_identical(workdir):
    p1 = H.run_pipeline(workdir["raw"], workdir["db"], "--backfill")
    assert p1.returncode == 0, H.describe(p1)
    h1 = _hashes(workdir["db"])
    assert all(h is not None for h in h1.values())
    p2 = H.run_pipeline(workdir["raw"], workdir["db"], "--backfill")
    assert p2.returncode == 0, H.describe(p2)
    assert _hashes(workdir["db"]) == h1


def test_r8_two_independent_databases_agree_on_content_columns(workdir, tmp_path):
    for i, db in enumerate((workdir["db"], tmp_path / "b.duckdb")):
        p = H.run_pipeline(workdir["raw"], db, "--backfill")
        assert p.returncode == 0, H.describe(p)
    a = _hashes(workdir["db"], exclude=("_", "batch_id"))
    b = _hashes(tmp_path / "b.duckdb", exclude=("_", "batch_id"))
    assert a == b


def test_r8_hash_detects_a_real_difference(workdir, tmp_path):
    """Positive control for the R8 comparison: a different input must change the hashes."""
    p = H.run_pipeline(workdir["raw"], workdir["db"], "--backfill")
    assert p.returncode == 0, H.describe(p)
    raw2 = H.copy_raw(tmp_path / "raw2")
    H.mutate(raw2, "orders", lambda h, rows: (h, [r[:4] + ["7"] + r[5:] if i == 3 and r[6] == "completed" else r for i, r in enumerate(rows)]))
    H.mutate(raw2, "orders", lambda h, rows: (h, [r[:4] + ["9"] + r[5:] if r[6] == "completed" and r[4] == "1" and i % 7 == 0 else r for i, r in enumerate(rows)]))
    p2 = H.run_pipeline(raw2, tmp_path / "c.duckdb", "--backfill")
    assert p2.returncode == 0, H.describe(p2)
    assert _hashes(tmp_path / "c.duckdb", exclude=("_", "batch_id"))["marts.daily_country_report"] != \
        _hashes(workdir["db"], exclude=("_", "batch_id"))["marts.daily_country_report"]


def test_r20_loading_same_file_twice_adds_zero_rows(workdir, tmp_path):
    assert H.run_pipeline(workdir["raw"], workdir["db"], "--backfill").returncode == 0
    before = _counts(workdir["db"])
    assert before == {"customers": 1200, "orders": 6040, "payments": 5950}
    assert H.run_pipeline(workdir["raw"], workdir["db"], "--backfill").returncode == 0
    assert _counts(workdir["db"]) == before
    # duplicated file: identical content under another directory (same sha256) must also add 0 rows
    dup = H.copy_raw(tmp_path / "dupdir")
    p = H.run_pipeline(dup, workdir["db"], "--backfill")
    assert p.returncode == 0, H.describe(p)
    assert _counts(workdir["db"]) == before


def test_r20_changed_file_is_appended_as_new_batch(workdir, tmp_path):
    """Positive control: raw is append-only by batch, a file with a new sha256 adds rows."""
    assert H.run_pipeline(workdir["raw"], workdir["db"], "--backfill").returncode == 0
    raw2 = H.copy_raw(tmp_path / "r2")
    H.mutate(raw2, "payments", lambda h, rows: (h, rows[:-5] + [["PAYZZ%03d" % i, "O9100%02d" % i, "2026-09-10", "10.00", "success"] for i in range(5)] + rows[-5:]))
    p = H.run_pipeline(raw2, workdir["db"], "--backfill")
    assert p.returncode == 0, H.describe(p)
    assert _counts(workdir["db"])["payments"] == 5950 + 5955
    c = H.connect_ro(workdir["db"])
    assert c.execute("SELECT count(DISTINCT _batch_id) FROM raw.payments").fetchone()[0] == 2
    assert c.execute("SELECT count(DISTINCT _batch_id) FROM raw.orders").fetchone()[0] == 1


def test_r9_rerun_of_one_date_leaves_outside_dates_untouched(workdir, tmp_path):
    assert H.run_pipeline(workdir["raw"], workdir["db"], "--backfill").returncode == 0
    q = ("SELECT * FROM marts.daily_country_report WHERE report_date <= DATE '2026-09-10' ORDER BY ALL")
    q2 = ("SELECT * FROM marts.daily_country_report WHERE report_date = DATE '2026-09-30' ORDER BY ALL")
    c = H.connect_ro(workdir["db"]); old_rows, old_30 = c.execute(q).fetchall(), c.execute(q2).fetchall(); c.close()
    # new batch: change a completed order on 2026-09-01 (outside window) AND one on 2026-09-30 (inside window)
    raw2 = H.copy_raw(tmp_path / "r9")

    def bump(h, rows):
        done = set()
        out = []
        for r in rows:
            if r[2] in ("2026-09-01", "2026-09-30") and r[6] == "completed" and r[4].lstrip("-").isdigit() and int(r[4]) > 0 and r[2] not in done:
                r = r[:4] + ["50"] + r[5:]
                done.add(r[2])
            out.append(r)
        assert done == {"2026-09-01", "2026-09-30"}
        return h, out
    H.mutate(raw2, "orders", bump)
    p = H.run_pipeline(raw2, workdir["db"], "--report-date", "2026-09-30")
    assert p.returncode == 0, H.describe(p)
    c = H.connect_ro(workdir["db"])
    assert c.execute(q).fetchall() == old_rows, "dates outside the recompute window changed (R9)"
    assert c.execute(q2).fetchall() != old_30, "positive control: the recomputed date should reflect the new batch"
    c.close()
