"""Read-only incident investigation for the ecommerce scenario.

Same safety semantics as the baseline src/incident_cli.py + src/agent.py (read-only reference):
  * the pipeline only ever runs into a throw-away scratch DB under --workdir (never the dev DB,
    never a release DB); raw inputs are hashed before and after and are never written;
  * the outcome set is closed: OK or ESCALATED. The scenario defines no remediation actions
    (CLAUDE.md: a new action needs a guardrail test first), so PROPOSED is never produced;
  * every step is appended to <workdir>/audit.jsonl.

ESCALATED when: raw dir/file missing or empty, header drift, volume band breach, run STOPPED
(R17) or non-zero exit, share of excluded orders above MAX_EXCLUDED_SHARE (independent re-check
from the CSV), or the inputs changed during the investigation.

  python -m src_scenario.incident --raw-dir DIR --workdir .incident [--baseline FILE.json]
Output JSON {status, reason, evidence, audit_log, audit_log_path}; exit 0 OK, 2 ESCALATED.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import duckdb

SCENARIO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RAW = SCENARIO_DIR / "data" / "raw"
STATUSES = ("OK", "ESCALATED")                     # closed outcome set
MAX_EXCLUDED_SHARE = 0.15                          # mirrors R17 / CLAUDE.md 15% rule (own copy; not the pipeline's)
VOLUME_BAND = 0.5                                  # +/-50% vs baseline (data-architecture, raw contract)
REFERENCE_ROWS = {"customers": 1200, "orders": 6040, "payments": 5950}   # documented counts of the current files
RUN_TIMEOUT_S = 300
FORBIDDEN_WORKDIR_PARTS = ("warehouse", "releases")

HEADERS = {
    "customers": ["customer_id", "customer_name", "email", "country", "created_at"],
    "orders": ["order_id", "customer_id", "order_date", "product_id", "quantity", "unit_price", "order_status"],
    "payments": ["payment_id", "order_id", "payment_date", "payment_amount", "payment_status"],
}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class UnsafeWorkdir(Exception):
    pass


class Audit:
    def __init__(self, path: Path):
        self.path, self.entries = path, []

    def log(self, kind: str, **detail) -> None:
        entry = {"ts": round(time.time(), 3), "kind": kind, **detail}
        self.entries.append(entry)
        with self.path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")


def run_pipeline_cli(raw_dir: Path, db: Path) -> tuple[int, str]:
    """Run the documented pipeline CLI into `db` (a scratch path chosen by investigate())."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SCENARIO_DIR)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    p = subprocess.run([sys.executable, "-m", "src_scenario.run", "--backfill",
                        "--raw-dir", str(raw_dir), "--db", str(db)],
                       capture_output=True, text=True, env=env, timeout=RUN_TIMEOUT_S)
    return p.returncode, (p.stdout + p.stderr)[-500:]


def _hash_dir(d: Path) -> dict:
    out = {}
    for p in sorted(d.iterdir()):
        if p.is_file():
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            out[p.name] = h.hexdigest()
    return out


def _check_workdir(raw_dir: Path, workdir: Path) -> Path:
    w, r = workdir.resolve(), raw_dir.resolve()
    if w == r or r in w.parents:
        raise UnsafeWorkdir(f"workdir {w} is inside the raw input dir {r}")
    bad = [x for x in w.parts if x in FORBIDDEN_WORKDIR_PARTS]
    if bad:
        raise UnsafeWorkdir(f"workdir {w} is under a dev-warehouse or release path component {bad}")
    return w


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    return (rows[0], rows[1:]) if rows else ([], [])


def _order_analysis(rows: list[list[str]]) -> dict:
    """Independent re-derivation of the excluded-order share (B1, B4). Counts only, no row values."""
    i_id, i_date, i_qty = 0, 2, 4
    best: dict[str, set] = {}                      # order_id -> reasons of its best (least invalid) version
    for r in rows:
        if len(r) < 7:
            continue
        reasons = set()
        d = r[i_date].strip()
        ok = bool(_ISO.match(d))
        if ok:
            try:
                datetime.strptime(d, "%Y-%m-%d")
            except ValueError:
                ok = False
        if not ok:
            reasons.add("invalid_order_date")
        q = r[i_qty].strip()
        try:
            qv = int(q)
            if qv < 0:
                reasons.add("negative_quantity")
            elif qv == 0:
                reasons.add("invalid_quantity")
        except ValueError:
            reasons.add("invalid_quantity")
        cur = best.get(r[i_id])
        if cur is None or len(reasons) < len(cur):
            best[r[i_id]] = reasons
    n = len(best)
    by_reason: dict[str, int] = {}
    excluded = 0
    for reasons in best.values():
        if reasons:
            excluded += 1
            for x in reasons:
                by_reason[x] = by_reason.get(x, 0) + 1
    return {"distinct_orders": n, "excluded_orders": excluded, "by_reason": dict(sorted(by_reason.items())),
            "share": (excluded / n) if n else 0}


def investigate(raw_dir: Path, workdir: Path, runner=None, baseline: dict | None = None) -> dict:
    raw_dir, workdir = Path(raw_dir), Path(workdir)
    runner = runner or run_pipeline_cli
    baseline = baseline or REFERENCE_ROWS
    w = _check_workdir(raw_dir, workdir)
    w.mkdir(parents=True, exist_ok=True)
    audit = Audit(w / "audit.jsonl")
    audit.log("start", raw_dir=str(raw_dir), mode="read-only")
    reasons: list[str] = []
    ev: dict = {"files": {}, "row_counts": {}, "volume": {}}

    def finish(status: str, reason: str) -> dict:
        assert status in STATUSES
        audit.log("outcome", status=status, reason=reason)
        return {"status": status, "reason": reason, "evidence": ev,
                "audit_log": audit.entries, "audit_log_path": str(w / "audit.jsonl")}

    if not raw_dir.is_dir():
        return finish("ESCALATED", f"raw dir missing: {raw_dir}")

    before = _hash_dir(raw_dir)
    ev["raw_sha256"] = before
    audit.log("inputs_hashed", files=sorted(before))

    # 1. structural checks (file present, non-empty, exact header, rows > 0)
    structural = False
    orders_rows: list[list[str]] | None = None
    for name, hdr in HEADERS.items():
        path = raw_dir / f"{name}.csv"
        if not path.is_file() or path.stat().st_size == 0:
            ev["files"][name] = {"ok": False, "problem": "missing or empty file"}
            reasons.append(f"{name}: missing or empty file")
            structural = True
            continue
        try:
            header, rows = _read_csv(path)
        except (OSError, UnicodeDecodeError, csv.Error) as e:
            ev["files"][name] = {"ok": False, "problem": f"unreadable: {type(e).__name__}"}
            reasons.append(f"{name}: unreadable")
            structural = True
            continue
        ev["row_counts"][name] = len(rows)
        if header != hdr:
            ev["files"][name] = {"ok": False, "problem": "header mismatch",
                                 "missing": [c for c in hdr if c not in header],
                                 "extra": [c for c in header if c not in hdr]}
            reasons.append(f"{name}: header mismatch")
            structural = True
        elif not rows:
            ev["files"][name] = {"ok": False, "problem": "no data rows"}
            reasons.append(f"{name}: no data rows")
            structural = True
        else:
            ev["files"][name] = {"ok": True}
            if name == "orders":
                orders_rows = rows
        audit.log("file_check", file=name, **{k: v for k, v in ev["files"][name].items()})

    # 2. volume band vs baseline
    for name, n in ev["row_counts"].items():
        b = baseline.get(name)
        if not b:
            continue
        change = (n - b) / b
        breach = abs(change) > VOLUME_BAND
        ev["volume"][name] = {"rows": n, "baseline": b, "change": round(change, 4), "breach": breach}
        if breach:
            reasons.append(f"volume band breach on {name}: {n} rows vs baseline {b} ({change:+.0%})")
    audit.log("volume_check", volume=ev["volume"])

    # 3. independent excluded-share check
    if orders_rows is not None:
        a = _order_analysis(orders_rows)
        ev["excluded_share"] = a["share"]
        ev["orders_analysis"] = a
        audit.log("excluded_share_check", **a)
        if a["share"] > MAX_EXCLUDED_SHARE:
            reasons.append(f"excluded orders {a['excluded_orders']}/{a['distinct_orders']} = {a['share']:.1%} "
                           f"exceeds {MAX_EXCLUDED_SHARE:.0%} ({a['by_reason']})")

    # 4. run the pipeline into a scratch DB (skipped when inputs are structurally unusable)
    if structural:
        ev["run"] = {"skipped": "inputs structurally invalid"}
        audit.log("pipeline_skipped", why="structural failure")
    else:
        scratch = w / "scratch" / f"{int(time.time() * 1000)}-{os.getpid()}" / "db.duckdb"
        scratch.parent.mkdir(parents=True, exist_ok=False)
        audit.log("pipeline_run_start", scratch_db=str(scratch))
        try:
            rc, out = runner(raw_dir, scratch)
        except Exception as e:
            rc, out = -1, f"runner raised {type(e).__name__}: {e}"
        run: dict = {"exit_code": rc, "output_tail": out[-500:], "scratch_db": str(scratch)}
        if scratch.exists():
            try:
                con = duckdb.connect(str(scratch), read_only=True)
                try:
                    run["run_status"] = [r[0] for r in con.execute("select run_status from marts.run_summary").fetchall()]
                finally:
                    con.close()
            except duckdb.Error as e:
                run["run_summary_error"] = str(e)[:200]
        ev["run"] = run
        audit.log("pipeline_run_end", exit_code=rc, run_status=run.get("run_status"))
        if "STOPPED" in run.get("run_status", []):
            reasons.append("pipeline run STOPPED (stop rule R17 or failed contract)")
        elif rc != 0:
            reasons.append(f"pipeline run exited with code {rc}")

    # 5. inputs untouched?
    after = _hash_dir(raw_dir)
    ev["inputs_unchanged"] = after == before
    audit.log("inputs_verified", unchanged=after == before)
    if after != before:
        reasons.append("raw inputs were modified during the investigation")

    if reasons:
        return finish("ESCALATED", "; ".join(reasons))
    return finish("OK", "all checks passed")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src_scenario.incident")
    p.add_argument("--raw-dir", default=str(DEFAULT_RAW))
    p.add_argument("--workdir", default=".incident")
    p.add_argument("--baseline", help="JSON file {customers, orders, payments: expected row count}")
    a = p.parse_args(argv)
    baseline = json.loads(Path(a.baseline).read_text()) if a.baseline else None
    try:
        res = investigate(Path(a.raw_dir), Path(a.workdir), runner=run_pipeline_cli, baseline=baseline)
    except UnsafeWorkdir as e:
        res = {"status": "ESCALATED", "reason": f"unsafe workdir: {e}", "evidence": {}, "audit_log": []}
    print(json.dumps(res, indent=2, default=str))
    return 0 if res["status"] == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
