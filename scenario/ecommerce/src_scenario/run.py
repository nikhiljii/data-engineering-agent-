"""Orchestration: load raw -> raw contract -> staging -> staging contract -> intermediate -> marts + quarantine (one transaction) -> audit.

    python -m src_scenario.run --report-date D | --backfill [--raw-dir PATH] [--db PATH]

Exit code 0 = PUBLISHED, 1 = STOPPED (nothing published, reason on stderr and in audit), 2 = bad arguments.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

from . import checks, raw_load
from .config import (DEFAULT_DB, DEFAULT_RAW_DIR, INTERMEDIATE_MODELS, LOOKBACK_DAYS, MAX_EXCLUDED_SHARE,
                     SQL_DIR, STAGING_MODELS)
from .events import Event, failed


@dataclass
class RunResult:
    run_id: str
    status: str                      # PUBLISHED | STOPPED
    stop_reason: str | None
    report_dates: list[date]
    events: list[Event] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return 0 if self.status == "PUBLISHED" else 1


def _sql(layer: str, name: str) -> str:
    return (SQL_DIR / layer / f"{name}.sql").read_text()


def _exec_file(con, layer: str, name: str) -> None:
    con.execute(_sql(layer, name))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _next_run_id(con) -> str:
    n = con.execute("SELECT count(*) FROM audit.run_log").fetchone()[0]
    return f"run-{n + 1:06d}"


def _summary_counts(con, report_dates: list[date]) -> dict:
    """Run-level numbers computed from views/selects, so they exist even when nothing was published."""
    s: dict = {}
    try:
        n_ids, n_excl = con.execute(
            "SELECT count(DISTINCT order_id), count(DISTINCT order_id) FILTER (WHERE NOT is_valid) FROM staging.stg_orders").fetchone()
        s["distinct_order_ids"], s["distinct_excluded_orders"] = n_ids, n_excl
        s["excluded_share"] = (n_excl / n_ids) if n_ids else 0.0
        for table, reasons in (("orders_rejected", ["duplicate_order_surplus", "invalid_order_date", "negative_quantity", "invalid_quantity"]),
                               ("payments_rejected", ["orphan_payment", "order_excluded"])):
            prefix = "q_orders_" if table == "orders_rejected" else "q_payments_"
            counts = dict(con.execute(f"SELECT primary_reason, count(*) FROM ({_sql('quarantine', table).rstrip().rstrip(';')}) GROUP BY 1").fetchall())
            for r in reasons:
                s[prefix + r] = counts.get(r, 0)
        s["orphan_payment_count"] = s["q_payments_orphan_payment"]
        s["fk_violations_stg_orders_customer_id"] = con.execute(
            "SELECT count(*) FROM staging.stg_orders WHERE customer_id IS NOT NULL AND customer_id NOT IN (SELECT customer_id FROM staging.stg_customers)").fetchone()[0]
        s["fk_violations_stg_payments_order_id"] = con.execute(
            "SELECT count(*) FROM staging.stg_payments WHERE order_id NOT IN (SELECT order_id FROM staging.stg_orders WHERE order_id IS NOT NULL)").fetchone()[0]
    except duckdb.Error:
        pass
    return s


def _raw_rows(con, batches: dict) -> dict:
    out = {}
    for source in ("customers", "orders", "payments"):
        b = batches.get(source, {}).get("batch_id")
        out[f"raw_rows_{source}"] = con.execute(f"SELECT count(*) FROM raw.{source} WHERE _batch_id = ?", [b]).fetchone()[0] if b else None
    return out


def _write_audit(con, *, run_id, started, t0, mode, dates, batches, status, stop_reason, summary, events) -> None:
    duration = time.perf_counter() - t0
    fresh = [e.as_dict() for e in events if e.check.endswith("_freshness")]
    vol = [e.as_dict() for e in events if e.check.endswith("_volume_band")]
    q_o = sum(v for k, v in summary.items() if k.startswith("q_orders_") and v is not None)
    q_p = sum(v for k, v in summary.items() if k.startswith("q_payments_") and v is not None)
    con.execute(
        "INSERT INTO audit.run_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [run_id, started, started + timedelta(seconds=duration), duration, mode, [d.isoformat() for d in dates],
         batches.get("customers", {}).get("batch_id"), batches.get("orders", {}).get("batch_id"), batches.get("payments", {}).get("batch_id"),
         status, stop_reason, summary.get("raw_rows_customers"), summary.get("raw_rows_orders"), summary.get("raw_rows_payments"),
         q_o, q_p, summary.get("excluded_share"),
         json.dumps(fresh, default=str), json.dumps(vol, default=str), json.dumps([e.as_dict() for e in events], default=str)])
    con.executemany("INSERT INTO audit.contract_events VALUES (?,?,?,?,?)",
                    [[run_id, e.flow, e.check, e.ok, e.detail_json()] for e in events])


SUMMARY_COLS = ["raw_rows_customers", "raw_rows_orders", "raw_rows_payments",
                "q_orders_duplicate_order_surplus", "q_orders_invalid_order_date", "q_orders_negative_quantity", "q_orders_invalid_quantity",
                "q_payments_orphan_payment", "q_payments_order_excluded",
                "distinct_order_ids", "distinct_excluded_orders", "excluded_share", "orphan_payment_count",
                "fk_violations_stg_orders_customer_id", "fk_violations_stg_payments_order_id",
                "rows_ok", "rows_warn", "rows_fail"]


def _write_summary(con, run_id, dates, status, stop_reason, s) -> None:
    con.execute(
        f"INSERT INTO marts.run_summary VALUES (?, ?, {', '.join('?' for _ in SUMMARY_COLS)}, ?, ?)",
        [run_id, [d.isoformat() for d in dates]] + [s.get(c) for c in SUMMARY_COLS] + [status, stop_reason])


def _create_views(con, layer: str, models: list[str]) -> None:
    for m in models:
        _exec_file(con, layer, m)


def _target_dates(con, report_date: date | None, backfill: bool) -> list[date]:
    if backfill:   # every date present in the data, plus dates still in the mart (an order that vanished must vanish from the report)
        rows = con.execute("SELECT report_date FROM intermediate.int_orders_enriched WHERE report_date IS NOT NULL "
                           "UNION SELECT report_date FROM marts.daily_country_report ORDER BY 1").fetchall()
        return [r[0] for r in rows]
    return [report_date - timedelta(days=i) for i in range(LOOKBACK_DAYS, -1, -1)]


def run(raw_dir: Path | str = DEFAULT_RAW_DIR, db: Path | str = DEFAULT_DB, *, report_date: date | None = None,
        backfill: bool = False, now: datetime | None = None) -> RunResult:
    if backfill == (report_date is not None):
        raise ValueError("give exactly one of report_date or backfill")
    started = now or _utcnow()
    t0 = time.perf_counter()
    raw_dir = Path(raw_dir)
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db))
    mode = "backfill" if backfill else f"report_date={report_date.isoformat()}"
    ctx = {"events": [], "batches": {}, "dates": [], "summary": {}}
    run_id = "run-unassigned"
    try:
        _exec_file(con, "raw", "00_schemas")
        _exec_file(con, "raw", "01_tables")
        _exec_file(con, "marts", "00_tables")
        _exec_file(con, "quarantine", "00_tables")
        _exec_file(con, "audit", "00_tables")
        run_id = _next_run_id(con)

        def stop(reason: str) -> RunResult:
            s = {**(_summary_counts(con, ctx["dates"]) if ctx.get("views") else {}), **_raw_rows(con, ctx["batches"])}
            s.setdefault("rows_ok", None)
            _write_summary(con, run_id, ctx["dates"], "STOPPED", reason, s)
            _write_audit(con, run_id=run_id, started=started, t0=t0, mode=mode, dates=ctx["dates"], batches=ctx["batches"],
                         status="STOPPED", stop_reason=reason, summary=s, events=ctx["events"])
            return RunResult(run_id, "STOPPED", reason, ctx["dates"], ctx["events"], s)

        # 1. raw: validate every source, land only if all pass, then post-load checks
        ev, plan = raw_load.validate_sources(con, raw_dir)
        ctx["events"] += ev
        if failed(ev):
            return stop("raw contract failed (before landing): " + ", ".join(e.check for e in failed(ev)))
        ctx["batches"] = raw_load.land_sources(con, plan, started)
        ev = raw_load.post_load_checks(con, plan, started)
        ctx["events"] += ev
        if failed(ev):
            return stop("raw contract failed: " + ", ".join(e.check for e in failed(ev)))

        # 2. staging + staging contract
        _exec_file(con, "staging", "00_macros")
        _create_views(con, "staging", STAGING_MODELS)
        _create_views(con, "intermediate", INTERMEDIATE_MODELS)
        ctx["views"] = True
        ev = checks.staging_checks(con)
        ctx["events"] += ev
        if failed(ev):
            return stop("staging contract failed: " + ", ".join(e.check for e in failed(ev)))

        # 3. stop rule (R17)
        s = {**_raw_rows(con, ctx["batches"]), **_summary_counts(con, [])}
        ctx["dates"] = _target_dates(con, report_date, backfill)
        share = s["excluded_share"]
        ev = [Event("run", "excluded_share_within_limit", share <= MAX_EXCLUDED_SHARE,
                    {"excluded_orders": s["distinct_excluded_orders"], "distinct_order_ids": s["distinct_order_ids"],
                     "share": round(share, 6), "max": MAX_EXCLUDED_SHARE})]
        ctx["events"] += ev
        if failed(ev):
            return stop(f"excluded share {share:.4f} > {MAX_EXCLUDED_SHARE}: escalate to a human, nothing published")

        # 4. transform: delete+insert per report_date and full quarantine replace, one transaction (ADR-005)
        dates = ctx["dates"]
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM marts.daily_country_report WHERE report_date IN (SELECT unnest(?::DATE[]))", [dates])
            con.execute("DELETE FROM marts.daily_country_report_dq WHERE report_date IN (SELECT unnest(?::DATE[]))", [dates])
            con.execute(f"INSERT INTO marts.daily_country_report_dq {_sql('marts', 'daily_country_report_dq')}", [dates])
            con.execute(f"INSERT INTO marts.daily_country_report {_sql('marts', 'daily_country_report')}", [dates])
            con.execute("DELETE FROM quarantine.orders_rejected")
            con.execute("DELETE FROM quarantine.payments_rejected")
            con.execute(f"INSERT INTO quarantine.orders_rejected {_sql('quarantine', 'orders_rejected')}")
            con.execute(f"INSERT INTO quarantine.payments_rejected {_sql('quarantine', 'payments_rejected')}")
            ev = checks.mart_checks(con, dates, full_coverage=backfill)
            ctx["events"] += ev
            if failed(ev):
                con.execute("ROLLBACK")
                return stop("mart contract failed: " + ", ".join(e.check for e in failed(ev)))
            by = dict(con.execute("SELECT data_quality_status, count(*) FROM marts.daily_country_report "
                                  "WHERE report_date IN (SELECT unnest(?::DATE[])) GROUP BY 1", [dates]).fetchall())
            s.update(rows_ok=by.get("OK", 0), rows_warn=by.get("WARN", 0), rows_fail=by.get("FAIL", 0))
            _write_summary(con, run_id, dates, "PUBLISHED", None, s)
            _write_audit(con, run_id=run_id, started=started, t0=t0, mode=mode, dates=dates, batches=ctx["batches"],
                         status="PUBLISHED", stop_reason=None, summary=s, events=ctx["events"])
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except duckdb.Error:
                pass
            raise
        return RunResult(run_id, "PUBLISHED", None, dates, ctx["events"], s)
    except Exception as exc:   # unexpected failure: publish nothing, say why, exit non-zero
        reason = f"unhandled {type(exc).__name__}: {exc}"
        try:
            _write_audit(con, run_id=run_id, started=started, t0=t0, mode=mode, dates=ctx["dates"], batches=ctx["batches"],
                         status="STOPPED", stop_reason=reason, summary={}, events=ctx["events"])
        except Exception:
            pass
        return RunResult(run_id, "STOPPED", reason, ctx["dates"], ctx["events"], {})
    finally:
        con.close()


def _iso_date(text: str) -> date:
    try:
        if len(text) != 10:
            raise ValueError(text)
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO date YYYY-MM-DD: {text!r}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src_scenario.run", description=__doc__.split("\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--report-date", type=_iso_date, help=f"recompute D and the {LOOKBACK_DAYS} days before it")
    g.add_argument("--backfill", action="store_true", help="recompute every report_date present in the data")
    p.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="directory with customers.csv, orders.csv, payments.csv")
    p.add_argument("--db", default=str(DEFAULT_DB), help="DuckDB file")
    a = p.parse_args(argv)
    res = run(a.raw_dir, a.db, report_date=a.report_date, backfill=a.backfill)
    out = {"run_id": res.run_id, "status": res.status, "stop_reason": res.stop_reason,
           "report_dates": [d.isoformat() for d in res.report_dates][:1] + [d.isoformat() for d in res.report_dates][-1:],
           "n_report_dates": len(res.report_dates), "summary": res.summary}
    print(json.dumps(out, default=str, sort_keys=True))
    if res.status == "STOPPED":
        print(f"STOPPED: {res.stop_reason}", file=sys.stderr)
        for e in failed(res.events):
            print(f"  FAILED {e.flow}.{e.check}: {e.detail_json()}", file=sys.stderr)
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())
