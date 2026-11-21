"""Unit tests for src_scenario.incident (WP-B).

src_scenario.run (WP-A) may not exist yet: tests that need a pipeline use a STUB runner
(clearly marked) injected via incident.run_pipeline_cli. Real-file tests only read the
raw data (hashed before/after). Scratch output goes to tmp_path only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import duckdb
import pytest

from src_scenario import incident

REAL_RAW = Path(__file__).resolve().parents[2] / "data" / "raw"
BASELINE = {"customers": 20, "orders": 100, "payments": 100}


def tree_hash(d: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(d).iterdir()) if p.is_file()}


def write_raw(d: Path, *, bad_date_share=0.0, orders=100, drop=(), empty=(), header_override=None, payments=100):
    d.mkdir(parents=True, exist_ok=True)
    cust = ["customer_id,customer_name,email,country,created_at"] + [f"C{i:03d},n,e,ES,2026-01-01" for i in range(20)]
    bad = int(orders * bad_date_share)
    ords = ["order_id,customer_id,order_date,product_id,quantity,unit_price,order_status"]
    for i in range(orders):
        date = "31/13/2026" if i < bad else "2026-09-01"
        ords.append(f"O{i:04d},C{i % 20:03d},{date},P1,2,10.0,completed")
    pays = ["payment_id,order_id,payment_date,payment_amount,payment_status"] + \
           [f"PAY{i:04d},O{i:04d},2026-09-01,20.0,success" for i in range(payments)]
    for name, lines in (("customers", cust), ("orders", ords), ("payments", pays)):
        if name in drop:
            continue
        body = [] if name in empty else lines
        if header_override and name in header_override:
            body = [header_override[name]] + body[1:]
        (d / f"{name}.csv").write_text("\n".join(body) + ("\n" if body else ""))
    return d


class StubRunner:
    """STUB standing in for `python -m src_scenario.run --backfill --raw-dir D --db P`.
    Emulates the stop rule R17 (exit 2, run_status STOPPED) when told to."""
    def __init__(self, rc=0, status="PUBLISHED"):
        self.rc, self.status, self.calls = rc, status, []

    def __call__(self, raw_dir, db):
        self.calls.append((Path(raw_dir), Path(db)))
        con = duckdb.connect(str(db))
        con.execute("create schema marts")
        con.execute("create table marts.run_summary (run_id VARCHAR, run_status VARCHAR, excluded_share DOUBLE)")
        con.execute("insert into marts.run_summary values ('r1', ?, 0.2)", [self.status])
        con.close()
        return self.rc, "STUB output"


def investigate(tmp_path, raw, runner, **kw):
    return incident.investigate(raw, tmp_path / "work", runner=runner, baseline=kw.pop("baseline", BASELINE), **kw)


# ---- OK path --------------------------------------------------------------------------

def test_clean_input_is_ok_and_audited(tmp_path):
    raw = write_raw(tmp_path / "raw")
    r = StubRunner()
    res = investigate(tmp_path, raw, r)
    assert res["status"] == "OK", res
    assert set(res) >= {"status", "reason", "evidence", "audit_log"}
    assert res["evidence"]["row_counts"] == {"customers": 20, "orders": 100, "payments": 100}
    assert res["evidence"]["excluded_share"] == 0
    lines = (tmp_path / "work" / "audit.jsonl").read_text().splitlines()
    assert lines and all(json.loads(l)["kind"] for l in lines)
    assert len(r.calls) == 1


def test_audit_log_appends_across_runs(tmp_path):
    raw = write_raw(tmp_path / "raw")
    investigate(tmp_path, raw, StubRunner())
    n1 = len((tmp_path / "work" / "audit.jsonl").read_text().splitlines())
    investigate(tmp_path, raw, StubRunner())
    assert len((tmp_path / "work" / "audit.jsonl").read_text().splitlines()) == 2 * n1


# ---- escalation cases -------------------------------------------------------------------

def test_escalates_on_over_15_percent_invalid_dates(tmp_path):
    raw = write_raw(tmp_path / "raw", bad_date_share=0.20)
    res = investigate(tmp_path, raw, StubRunner(rc=2, status="STOPPED"))
    assert res["status"] == "ESCALATED"
    assert res["evidence"]["excluded_share"] == pytest.approx(0.20)
    assert "invalid_order_date" in json.dumps(res["evidence"])


def test_independent_share_check_escalates_even_if_pipeline_reports_ok(tmp_path):
    raw = write_raw(tmp_path / "raw", bad_date_share=0.20)
    res = investigate(tmp_path, raw, StubRunner())            # pipeline says fine: still escalate
    assert res["status"] == "ESCALATED"


def test_exactly_15_percent_is_not_escalated(tmp_path):
    raw = write_raw(tmp_path / "raw", bad_date_share=0.15)
    res = investigate(tmp_path, raw, StubRunner())
    assert res["status"] == "OK", res


def test_escalates_when_run_stopped_even_with_exit_zero(tmp_path):
    raw = write_raw(tmp_path / "raw")
    res = investigate(tmp_path, raw, StubRunner(rc=0, status="STOPPED"))
    assert res["status"] == "ESCALATED" and "STOPPED" in res["reason"]


def test_escalates_when_run_exit_nonzero(tmp_path):
    raw = write_raw(tmp_path / "raw")
    res = investigate(tmp_path, raw, StubRunner(rc=1))
    assert res["status"] == "ESCALATED"


def test_escalates_on_missing_file_without_running_pipeline(tmp_path):
    raw = write_raw(tmp_path / "raw", drop=("payments",))
    r = StubRunner()
    res = investigate(tmp_path, raw, r)
    assert res["status"] == "ESCALATED" and "payments" in res["reason"]
    assert r.calls == []


def test_escalates_on_empty_file(tmp_path):
    raw = write_raw(tmp_path / "raw", empty=("orders",))
    res = investigate(tmp_path, raw, StubRunner())
    assert res["status"] == "ESCALATED" and "orders" in res["reason"]


def test_escalates_on_header_only_file(tmp_path):
    raw = write_raw(tmp_path / "raw", orders=0, payments=0)
    res = investigate(tmp_path, raw, StubRunner())
    assert res["status"] == "ESCALATED"


def test_escalates_on_header_drift(tmp_path):
    raw = write_raw(tmp_path / "raw", header_override={"orders": "order_id,cust_id,order_date,product_id,quantity,unit_price,order_status"})
    r = StubRunner()
    res = investigate(tmp_path, raw, r)
    assert res["status"] == "ESCALATED" and "header" in res["reason"]
    assert r.calls == []


def test_escalates_on_volume_band_breach(tmp_path):
    raw = write_raw(tmp_path / "raw", orders=40, payments=40)        # 40 vs baseline 100: -60%
    res = investigate(tmp_path, raw, StubRunner())
    assert res["status"] == "ESCALATED" and "volume" in res["reason"]
    assert res["evidence"]["volume"]["orders"]["breach"] is True


def test_volume_within_band_ok(tmp_path):
    raw = write_raw(tmp_path / "raw", orders=60, payments=60)        # -40%
    assert investigate(tmp_path, raw, StubRunner())["status"] == "OK"


def test_missing_raw_dir_escalates(tmp_path):
    res = investigate(tmp_path, tmp_path / "nope", StubRunner())
    assert res["status"] == "ESCALATED"


def test_runner_crash_escalates(tmp_path):
    raw = write_raw(tmp_path / "raw")

    def boom(raw_dir, db):
        raise OSError("x")
    assert investigate(tmp_path, raw, boom)["status"] == "ESCALATED"


def test_only_ok_or_escalated_and_no_actions_produced(tmp_path):
    for share in (0.0, 0.3):
        raw = write_raw(tmp_path / f"raw{share}", bad_date_share=share)
        res = investigate(tmp_path, raw, StubRunner())
        assert res["status"] in ("OK", "ESCALATED")
        assert "actions" not in res and "proposed_config" not in res
    assert incident.STATUSES == ("OK", "ESCALATED")


# ---- read-only / no production writes -----------------------------------------------------

def test_inputs_never_modified_hash_before_after(tmp_path):
    raw = write_raw(tmp_path / "raw", bad_date_share=0.3)
    before = tree_hash(raw)
    investigate(tmp_path, raw, StubRunner(rc=2, status="STOPPED"))
    assert tree_hash(raw) == before
    assert sorted(p.name for p in raw.iterdir()) == ["customers.csv", "orders.csv", "payments.csv"]


def test_real_raw_files_unchanged_and_scratch_db_only_under_workdir(tmp_path):
    before = tree_hash(REAL_RAW)
    r = StubRunner()
    res = incident.investigate(REAL_RAW, tmp_path / "work", runner=r)     # default baseline = documented counts
    assert tree_hash(REAL_RAW) == before
    assert len(r.calls) == 1
    db = r.calls[0][1].resolve()
    assert (tmp_path / "work").resolve() in db.parents
    assert res["evidence"]["row_counts"] == {"customers": 1200, "orders": 6040, "payments": 5950}
    assert res["status"] == "OK", res
    assert res["evidence"]["excluded_share"] == pytest.approx(55 / 6000, abs=1e-4)


def test_pipeline_modification_of_inputs_is_detected(tmp_path):
    raw = write_raw(tmp_path / "raw")

    def evil(raw_dir, db):
        (Path(raw_dir) / "orders.csv").write_text("tampered\n")
        return StubRunner()(raw_dir, db)
    res = investigate(tmp_path, raw, evil)
    assert res["status"] == "ESCALATED" and "modified" in res["reason"]


@pytest.mark.parametrize("bad_work", ["inside_raw", "same_as_raw", "warehouse", "releases"])
def test_refuses_unsafe_workdir(tmp_path, bad_work):
    raw = write_raw(tmp_path / "raw")
    work = {"inside_raw": raw / "w", "same_as_raw": raw,
            "warehouse": tmp_path / "ecommerce" / "warehouse" / "w",
            "releases": tmp_path / "ecommerce" / "releases" / "w"}[bad_work]
    before = tree_hash(raw)
    with pytest.raises(incident.UnsafeWorkdir):
        incident.investigate(raw, work, runner=StubRunner(), baseline=BASELINE)
    assert tree_hash(raw) == before


def test_each_investigation_uses_fresh_scratch_db(tmp_path):
    raw = write_raw(tmp_path / "raw")
    r = StubRunner()
    investigate(tmp_path, raw, r)
    investigate(tmp_path, raw, r)
    assert r.calls[0][1] != r.calls[1][1]


def test_release_root_is_never_touched(tmp_path):
    raw = write_raw(tmp_path / "raw")
    rel = tmp_path / "releases"
    rel.mkdir()
    (rel / "current.json").write_text('{"current": "x", "history": []}')
    before = tree_hash(rel)
    investigate(tmp_path, raw, StubRunner())
    assert tree_hash(rel) == before


# ---- CLI ------------------------------------------------------------------------------------

def test_cli_exit_codes_and_json(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(incident, "run_pipeline_cli", StubRunner())
    base = tmp_path / "baseline.json"
    base.write_text(json.dumps(BASELINE))
    raw = write_raw(tmp_path / "raw")
    assert incident.main(["--raw-dir", str(raw), "--workdir", str(tmp_path / "w1"), "--baseline", str(base)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "OK"
    bad = write_raw(tmp_path / "raw2", bad_date_share=0.3)
    assert incident.main(["--raw-dir", str(bad), "--workdir", str(tmp_path / "w2"), "--baseline", str(base)]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ESCALATED" and out["audit_log"]


def test_cli_unsafe_workdir_exits_2(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(incident, "run_pipeline_cli", StubRunner())
    raw = write_raw(tmp_path / "raw")
    assert incident.main(["--raw-dir", str(raw), "--workdir", str(raw / "w")]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "ESCALATED"


def test_module_never_imports_deploy_or_applies_fixes():
    src = Path(incident.__file__).read_text()
    for banned in ("promote(", "apply_to_prod", "unlink(", "rmtree", "os.remove", "ACTIONS ="):
        assert banned not in src


# ---- integration with the real run CLI (skipped until WP-A lands) ----------------------------

@pytest.mark.skipif(importlib.util.find_spec("src_scenario.run") is None,
                    reason="src_scenario.run (WP-A) not present yet")
def test_real_pipeline_on_real_files_is_ok(tmp_path):
    before = tree_hash(REAL_RAW)
    res = incident.investigate(REAL_RAW, tmp_path / "work")
    assert res["status"] == "OK", res
    assert tree_hash(REAL_RAW) == before
