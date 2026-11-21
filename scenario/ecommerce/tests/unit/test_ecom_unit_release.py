"""Unit tests for src_scenario.release (WP-B).

The pipeline core (src_scenario.run) is written by another builder. Tests here inject a
STUB runner (marked STUB below) that fabricates a build; one integration test uses the real
run CLI and is skipped when it does not exist yet. Everything runs in tmp_path, never in a
real release root, and never promotes with an approver value outside a temp dir.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import duckdb
import pytest

from src_scenario import release
from src_scenario.release import ApprovalRequired, SmokeFailed

R10 = [("report_date", "DATE"), ("country", "VARCHAR"), ("total_orders", "INTEGER"),
       ("completed_orders", "INTEGER"), ("gross_revenue", "DECIMAL(18,2)"),
       ("successful_payments", "INTEGER"), ("failed_payments", "INTEGER"),
       ("data_quality_status", "VARCHAR")]


def make_db(db: Path, *, mart_rows=2, status="PUBLISHED", cols=R10, pii=False):
    con = duckdb.connect(str(db))
    con.execute("create schema marts")
    con.execute("create table marts.daily_country_report (%s)" % ", ".join(f"{n} {t}" for n, t in cols))
    for i in range(mart_rows):
        vals = {"DATE": "DATE '2026-09-0%d'" % (i + 1), "VARCHAR": "'ES'", "INTEGER": "1", "DECIMAL(18,2)": "1.50"}
        con.execute("insert into marts.daily_country_report values (%s)" % ", ".join(vals[t] for _, t in cols))
    con.execute("create table marts.run_summary (run_id VARCHAR, run_status VARCHAR)")
    con.execute("insert into marts.run_summary values ('r1', ?)", [status])
    if pii:
        con.execute("create schema raw")
        con.execute("create table raw.customers (customer_id VARCHAR, email VARCHAR)")
    con.close()


class StubRunner:
    """STUB standing in for `python -m src_scenario.run --backfill --raw-dir D --db P`."""
    def __init__(self, rc=0, **db_kwargs):
        self.rc, self.db_kwargs, self.calls = rc, db_kwargs, []

    def __call__(self, raw_dir, db):
        self.calls.append((Path(raw_dir), Path(db)))
        make_db(Path(db), **self.db_kwargs)
        return self.rc, "STUB run output"


@pytest.fixture
def raw(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    for n in ("customers", "orders", "payments"):
        (d / f"{n}.csv").write_text("a,b\n1,2\n")
    return d


@pytest.fixture
def root(tmp_path):
    return tmp_path / "releases"


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tree_hash(d: Path) -> dict:
    return {str(p.relative_to(d)): sha(p) for p in sorted(d.rglob("*")) if p.is_file()}


# ---- stage ----------------------------------------------------------------

def test_stage_builds_release_with_manifest_and_leaves_current_untouched(root, raw):
    r = StubRunner()
    rel = release.stage(root, raw, runner=r)
    assert (root / rel / "db.duckdb").exists()
    m = json.loads((root / rel / "manifest.json").read_text())
    assert m["release"] == rel and m["smoke"]["passed"] is True and m["mart_rows"] == 2
    assert set(m["raw_sha256"]) == {"customers.csv", "orders.csv", "payments.csv"}
    assert not (root / "current.json").exists()
    assert r.calls == [(raw, root / rel / "db.duckdb")]


def test_stage_does_not_modify_existing_pointer_or_other_releases(root, raw):
    a = release.stage(root, raw, runner=StubRunner())
    release.promote(root, a, "Test Approver")
    before_ptr = (root / "current.json").read_bytes()
    before_tree = tree_hash(root / a)
    release.stage(root, raw, runner=StubRunner())
    assert (root / "current.json").read_bytes() == before_ptr
    assert tree_hash(root / a) == before_tree


def test_stage_does_not_modify_raw_inputs(root, raw):
    before = tree_hash(raw)
    release.stage(root, raw, runner=StubRunner())
    assert tree_hash(raw) == before


@pytest.mark.parametrize("name,runner", [
    ("nonzero_exit", StubRunner(rc=2)),
    ("stopped", StubRunner(status="STOPPED")),
    ("empty_mart", StubRunner(mart_rows=0)),
    ("bad_schema", StubRunner(cols=R10[:-1])),
    ("wrong_order", StubRunner(cols=[R10[1], R10[0]] + R10[2:])),
    ("pii_column", StubRunner(pii=True)),
])
def test_failed_smoke_raises_leaves_pointer_and_is_not_retried(root, raw, name, runner):
    with pytest.raises(SmokeFailed):
        release.stage(root, raw, runner=runner)
    assert len(runner.calls) == 1                      # no retry
    assert not (root / "current.json").exists()
    dirs = [d for d in root.iterdir() if d.is_dir()]
    assert len(dirs) == 1                              # release dir kept for forensics, never deleted
    assert not (dirs[0] / "manifest.json").exists()    # failed build can never be promoted
    assert (dirs[0] / "smoke_failed.json").exists()
    assert json.loads((dirs[0] / "smoke_failed.json").read_text())["passed"] is False
    with pytest.raises(SmokeFailed):
        release.promote(root, dirs[0].name, "Test Approver")


def test_runner_crash_is_smoke_failure_not_retried(root, raw):
    calls = []

    def boom(raw_dir, db):
        calls.append(1)
        raise OSError("disk")
    with pytest.raises(SmokeFailed):
        release.stage(root, raw, runner=boom)
    assert calls == [1]


def test_missing_raw_dir_fails_before_building(root, tmp_path):
    r = StubRunner()
    with pytest.raises(SmokeFailed):
        release.stage(root, tmp_path / "nope", runner=r)
    assert r.calls == []


# ---- promote / approver gate -----------------------------------------------

@pytest.mark.parametrize("approver", [None, "", "   "])
def test_promote_without_approver_refused_and_pointer_unchanged(root, raw, approver):
    rel = release.stage(root, raw, runner=StubRunner())
    with pytest.raises(ApprovalRequired):
        release.promote(root, rel, approver)
    assert not (root / "current.json").exists()
    # and with an existing pointer: bytes unchanged
    other = release.stage(root, raw, runner=StubRunner())
    release.promote(root, other, "Test Approver")
    before = (root / "current.json").read_bytes()
    with pytest.raises(ApprovalRequired):
        release.promote(root, rel, approver)
    assert (root / "current.json").read_bytes() == before


def test_approval_required_is_the_baseline_class():
    from src.agent import ApprovalRequired as Base
    assert release.ApprovalRequired is Base


def test_promote_unstaged_or_unsafe_release_refused(root, raw):
    release.stage(root, raw, runner=StubRunner())
    for bad in ("does-not-exist", "../x", "", "a/b"):
        with pytest.raises(SmokeFailed):
            release.promote(root, bad, "Test Approver")
    assert not (root / "current.json").exists()


def test_promote_moves_pointer_keeps_history_and_previous_release_intact(root, raw):
    a = release.stage(root, raw, runner=StubRunner())
    b = release.stage(root, raw, runner=StubRunner())
    s1 = release.promote(root, a, "Test Approver")
    assert s1["current"] == a and s1["approved_by"] == "Test Approver" and s1["history"] == []
    a_tree = tree_hash(root / a)
    s2 = release.promote(root, b, "Test Approver")
    assert s2["current"] == b and s2["history"] == [a]
    assert tree_hash(root / a) == a_tree
    assert json.loads((root / "current.json").read_text()) == s2
    assert not list(root.glob("current.json.*"))        # no temp file left behind


def test_promote_same_release_twice_refused(root, raw):
    a = release.stage(root, raw, runner=StubRunner())
    release.promote(root, a, "Test Approver")
    with pytest.raises(SmokeFailed):
        release.promote(root, a, "Test Approver")


def test_pointer_write_is_atomic(root, raw, monkeypatch):
    a = release.stage(root, raw, runner=StubRunner())
    b = release.stage(root, raw, runner=StubRunner())
    release.promote(root, a, "Test Approver")
    before = (root / "current.json").read_bytes()

    def fail(src, dst):
        raise OSError("crash during swap")
    monkeypatch.setattr(release.os, "replace", fail)
    with pytest.raises(OSError):
        release.promote(root, b, "Test Approver")
    assert (root / "current.json").read_bytes() == before   # old pointer fully intact


def test_promote_uses_os_replace_on_a_temp_file(root, raw, monkeypatch):
    a = release.stage(root, raw, runner=StubRunner())
    seen = []
    real = release.os.replace
    monkeypatch.setattr(release.os, "replace", lambda s, d: (seen.append((Path(s).name, Path(d).name)), real(s, d))[1])
    release.promote(root, a, "Test Approver")
    assert len(seen) == 1 and seen[0][1] == "current.json" and seen[0][0] != "current.json"


# ---- rollback ---------------------------------------------------------------

def test_rollback_requires_approver_and_history(root, raw):
    a = release.stage(root, raw, runner=StubRunner())
    b = release.stage(root, raw, runner=StubRunner())
    release.promote(root, a, "Test Approver")
    release.promote(root, b, "Test Approver")
    before = (root / "current.json").read_bytes()
    for approver in (None, "", "  "):
        with pytest.raises(ApprovalRequired):
            release.rollback(root, approver)
    assert (root / "current.json").read_bytes() == before


def test_rollback_restores_previous_and_keeps_bad_release_on_disk(root, raw):
    a = release.stage(root, raw, runner=StubRunner())
    b = release.stage(root, raw, runner=StubRunner())
    release.promote(root, a, "Test Approver")
    release.promote(root, b, "Test Approver")
    trees = {x: tree_hash(root / x) for x in (a, b)}
    s = release.rollback(root, "Test Approver")
    assert s["current"] == a and s["history"] == []
    assert {x: tree_hash(root / x) for x in (a, b)} == trees
    with pytest.raises(SmokeFailed):                     # nothing left to roll back to
        release.rollback(root, "Test Approver")
    assert json.loads((root / "current.json").read_text())["current"] == a


def test_rollback_refuses_when_target_release_missing(root, raw):
    import shutil
    a = release.stage(root, raw, runner=StubRunner())
    b = release.stage(root, raw, runner=StubRunner())
    release.promote(root, a, "Test Approver")
    release.promote(root, b, "Test Approver")
    shutil.rmtree(root / a)                              # test-only damage inside tmp
    before = (root / "current.json").read_bytes()
    with pytest.raises(SmokeFailed):
        release.rollback(root, "Test Approver")
    assert (root / "current.json").read_bytes() == before


# ---- status and CLI ----------------------------------------------------------

def test_status_is_read_only_and_does_not_create_root(root):
    s = release.status(root)
    assert s["current"] is None and s["releases"] == []
    assert not root.exists()


def test_status_lists_releases(root, raw):
    a = release.stage(root, raw, runner=StubRunner())
    s = release.status(root)
    assert s["current"] is None and s["releases"] == [a]


def test_cli_promote_without_approver_exits_1_and_leaves_pointer(root, raw, capsys, monkeypatch):
    monkeypatch.setattr(release, "run_pipeline_cli", StubRunner())
    assert release.main(["stage", "--root", str(root), "--raw-dir", str(raw)]) == 0
    rel = json.loads(capsys.readouterr().out)["staged"]
    assert release.main(["promote", "--root", str(root), "--release", rel]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "ApprovalRequired"
    assert not (root / "current.json").exists()
    assert release.main(["rollback", "--root", str(root)]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "ApprovalRequired"


def test_cli_stage_failure_exits_1(root, raw, capsys, monkeypatch):
    monkeypatch.setattr(release, "run_pipeline_cli", StubRunner(rc=2))
    assert release.main(["stage", "--root", str(root), "--raw-dir", str(raw)]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "SmokeFailed"


def test_cli_full_cycle_in_temp_root(root, raw, capsys, monkeypatch):
    monkeypatch.setattr(release, "run_pipeline_cli", StubRunner())
    release.main(["stage", "--root", str(root), "--raw-dir", str(raw)])
    a = json.loads(capsys.readouterr().out)["staged"]
    assert release.main(["promote", "--root", str(root), "--release", a, "--approver", "Test Approver"]) == 0
    capsys.readouterr()
    assert release.main(["status", "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["current"] == a


def test_module_has_no_delete_calls():
    src = Path(release.__file__).read_text()
    for banned in ("rmtree", "unlink(", "os.remove", "os.rmdir", ".rmdir("):
        assert banned not in src


# ---- integration with the real run CLI (skipped until WP-A lands) -------------

@pytest.mark.skipif(importlib.util.find_spec("src_scenario.run") is None,
                    reason="src_scenario.run (WP-A) not present yet")
def test_stage_with_real_run_cli(root):
    raw = Path(__file__).resolve().parents[2] / "data" / "raw"
    before = tree_hash(raw)
    rel = release.stage(root, raw)
    m = json.loads((root / rel / "manifest.json").read_text())
    assert m["smoke"]["passed"] and m["mart_rows"] > 0
    assert not (root / "current.json").exists()
    assert tree_hash(raw) == before
