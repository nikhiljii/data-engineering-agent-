"""Release tooling for the ecommerce scenario: stage -> smoke -> promote -> rollback.

Same semantics as the baseline src/deploy.py (read-only reference, not modified):
  * a release is a directory `<root>/<release>/` holding `db.duckdb` and `manifest.json`;
  * `current.json` names the live release; only promote/rollback move it, atomically
    (temp file + os.replace), and a release directory is never deleted;
  * promote and rollback need a non-empty approver, otherwise ApprovalRequired and nothing changes;
  * stage never touches `current.json`; a failed smoke is recorded, never retried, and the
    release can never be promoted (no manifest).

The build is done by the pipeline's documented CLI:
  python -m src_scenario.run --backfill --raw-dir DIR --db <root>/<release>/db.duckdb

CLI:
  python -m src_scenario.release stage    --root R [--raw-dir DIR]
  python -m src_scenario.release promote  --root R --release REL --approver NAME
  python -m src_scenario.release rollback --root R --approver NAME
  python -m src_scenario.release status   --root R
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import duckdb

try:  # reuse the baseline exception class (import only)
    from src.agent import ApprovalRequired
except ImportError:  # run from outside the repo root: add it, then import
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from src.agent import ApprovalRequired

SCENARIO_DIR = Path(__file__).resolve().parents[1]          # .../scenario/ecommerce
DEFAULT_ROOT = SCENARIO_DIR / "releases"
DEFAULT_RAW = SCENARIO_DIR / "data" / "raw"
RAW_FILES = ("customers.csv", "orders.csv", "payments.csv")
RUN_TIMEOUT_S = 300

# R10: exact output contract of marts.daily_country_report, in order.
R10_COLUMNS = [("report_date", "DATE"), ("country", "VARCHAR"), ("total_orders", "INTEGER"),
               ("completed_orders", "INTEGER"), ("gross_revenue", "DECIMAL(18,2)"),
               ("successful_payments", "INTEGER"), ("failed_payments", "INTEGER"),
               ("data_quality_status", "VARCHAR")]
PII_COLUMNS = ("customer_name", "email")                    # R2


class SmokeFailed(Exception):
    pass


# ---- pipeline invocation -------------------------------------------------------

def run_pipeline_cli(raw_dir: Path, db: Path) -> tuple[int, str]:
    """Build `db` from `raw_dir` through the documented run CLI. Returns (exit code, output tail)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SCENARIO_DIR)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    p = subprocess.run([sys.executable, "-m", "src_scenario.run", "--backfill",
                        "--raw-dir", str(raw_dir), "--db", str(db)],
                       capture_output=True, text=True, env=env, timeout=RUN_TIMEOUT_S)
    return p.returncode, (p.stdout + p.stderr)[-2000:]


# ---- pointer ---------------------------------------------------------------------

def _pointer(root: Path) -> Path:
    return root / "current.json"


def _read_pointer(root: Path) -> dict:
    p = _pointer(root)
    return json.loads(p.read_text()) if p.exists() else {"current": None, "history": []}


def _write_pointer(root: Path, state: dict) -> None:
    tmp = root / f"current.json.tmp-{os.getpid()}"
    with open(tmp, "w") as f:
        f.write(json.dumps(state, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, _pointer(root))  # atomic on POSIX; the old pointer survives any earlier failure


def _release_dir(root: Path, release: str | None) -> Path:
    if not release or release != Path(release).name or release in (".", ".."):
        raise SmokeFailed(f"invalid release id {release!r}")
    return root / release


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- smoke ---------------------------------------------------------------------------

def smoke(db: Path, run_rc: int) -> tuple[dict, int]:
    """Smoke checks on a built db: run not STOPPED, mart non-empty, R10 schema, R2 no PII."""
    checks: dict = {"run_exit_code": {"ok": run_rc == 0, "detail": run_rc}}
    mart_rows = 0
    if not Path(db).exists():
        checks["db_exists"] = {"ok": False, "detail": "no db file produced"}
        return checks, 0
    con = duckdb.connect(str(db), read_only=True)
    try:
        try:
            st = [r[0] for r in con.execute("select run_status from marts.run_summary").fetchall()]
            checks["run_not_stopped"] = {"ok": bool(st) and all(s != "STOPPED" for s in st), "detail": st}
        except duckdb.Error as e:
            checks["run_not_stopped"] = {"ok": False, "detail": f"marts.run_summary unreadable: {e}"}
        try:
            mart_rows = con.execute("select count(*) from marts.daily_country_report").fetchone()[0]
            checks["mart_non_empty"] = {"ok": mart_rows > 0, "detail": mart_rows}
            cols = [(r[0], r[1]) for r in con.execute("describe marts.daily_country_report").fetchall()]
            checks["r10_schema"] = {"ok": cols == R10_COLUMNS, "detail": cols}
        except duckdb.Error as e:
            checks["mart_non_empty"] = {"ok": False, "detail": f"marts.daily_country_report unreadable: {e}"}
            checks["r10_schema"] = {"ok": False, "detail": "mart missing"}
        pii = con.execute("select table_schema || '.' || table_name || '.' || column_name from information_schema.columns "
                          "where lower(column_name) in ('customer_name', 'email')").fetchall()
        checks["r2_no_pii"] = {"ok": not pii, "detail": [r[0] for r in pii]}
    finally:
        con.close()
    return checks, mart_rows


# ---- operations -----------------------------------------------------------------------

def _new_release_dir(root: Path) -> tuple[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    while True:
        ms = int(time.time() * 1000)
        release = time.strftime("%Y%m%d-%H%M%S", time.gmtime(ms // 1000)) + f"-{ms % 1000:03d}"
        try:
            (root / release).mkdir()
            return release, root / release
        except FileExistsError:
            time.sleep(0.002)


def stage(root: Path, raw_dir: Path = DEFAULT_RAW, runner=None) -> str:
    """Build a release in isolation and smoke-test it exactly once. Never touches `current`."""
    root, raw_dir = Path(root), Path(raw_dir)
    runner = runner or run_pipeline_cli
    missing = [n for n in RAW_FILES if not (raw_dir / n).is_file()]
    if missing:
        raise SmokeFailed(f"raw files missing in {raw_dir}: {missing}")
    raw_sha = {n: _sha256(raw_dir / n) for n in RAW_FILES}
    release, rdir = _new_release_dir(root)
    db = rdir / "db.duckdb"
    try:
        rc, out = runner(raw_dir, db)
    except Exception as e:  # a crashed build is a failed smoke; never retried
        rc, out = -1, f"runner raised {type(e).__name__}: {e}"
    checks, mart_rows = smoke(db, rc)
    passed = all(c["ok"] for c in checks.values())
    if not passed:
        (rdir / "smoke_failed.json").write_text(json.dumps(
            {"release": release, "passed": False, "checks": checks, "run_output_tail": out}, indent=2, default=str))
        failed = [k for k, c in checks.items() if not c["ok"]]
        raise SmokeFailed(f"smoke failed for {release}: {failed}")
    (rdir / "manifest.json").write_text(json.dumps(
        {"release": release, "raw_dir": str(raw_dir), "raw_sha256": raw_sha, "mart_rows": mart_rows,
         "smoke": {"passed": True, "checks": checks}, "run_output_tail": out}, indent=2, default=str))
    return release


def promote(root: Path, release: str, approver: str | None) -> dict:
    root = Path(root)
    if not approver or not str(approver).strip():
        raise ApprovalRequired("promotion needs a named human approver")
    rdir = _release_dir(root, release)
    mp = rdir / "manifest.json"
    if not mp.exists() or not json.loads(mp.read_text()).get("smoke", {}).get("passed"):
        raise SmokeFailed(f"release {release} was never staged and smoke-tested")
    state = _read_pointer(root)
    if state["current"] == release:
        raise SmokeFailed(f"release {release} is already current")
    if state["current"]:
        state["history"].append(state["current"])
    state["current"] = release
    state["approved_by"] = str(approver).strip()
    _write_pointer(root, state)
    return state


def rollback(root: Path, approver: str | None) -> dict:
    root = Path(root)
    if not approver or not str(approver).strip():
        raise ApprovalRequired("rollback needs a named human approver")
    state = _read_pointer(root)
    if not state["history"]:
        raise SmokeFailed("no previous release to roll back to")
    target = state["history"][-1]
    if not (_release_dir(root, target) / "db.duckdb").exists():
        raise SmokeFailed(f"rollback target {target} is missing on disk")
    state["history"].pop()
    state["current"] = target
    state["approved_by"] = str(approver).strip()
    _write_pointer(root, state)
    return state


def status(root: Path) -> dict:
    """Read-only: pointer plus the release directories present. Creates nothing."""
    root = Path(root)
    state = _read_pointer(root)
    state["releases"] = sorted(d.name for d in root.iterdir() if d.is_dir()) if root.is_dir() else []
    return state


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src_scenario.release")
    p.add_argument("cmd", choices=["stage", "promote", "rollback", "status"])
    p.add_argument("--root", default=str(DEFAULT_ROOT))
    p.add_argument("--raw-dir", default=str(DEFAULT_RAW))
    p.add_argument("--release")
    p.add_argument("--approver")
    a = p.parse_args(argv)
    root = Path(a.root)
    try:
        if a.cmd == "stage":
            print(json.dumps({"staged": stage(root, Path(a.raw_dir))}))
        elif a.cmd == "promote":
            print(json.dumps(promote(root, a.release, a.approver)))
        elif a.cmd == "rollback":
            print(json.dumps(rollback(root, a.approver)))
        else:
            print(json.dumps(status(root)))
    except (SmokeFailed, ApprovalRequired) as e:
        print(json.dumps({"error": type(e).__name__, "detail": str(e)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
