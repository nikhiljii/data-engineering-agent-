"""Raw layer: validate the three files, then land them append-only by batch (ADR-004, ADR-005).

The loader is the only code that reads a clock. Landing happens only when every source passed its file-level
contract, so raw never holds a half-accepted set of sources.
"""
from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from .config import FRESHNESS_HOURS, SOURCES, VOLUME_BAND
from .events import Event

NULL_SENTINEL = "__no_null_marker__"   # keep empty strings as empty strings in raw (no coercion to NULL)
META = ["_batch_id", "_loaded_at", "_source_file", "_source_sha256", "_source_row_number"]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect_file(path: Path, header: list[str]) -> dict:
    """Read-only look at a csv: header, data row count, malformed rows. Never raises on bad content."""
    info = {"exists": path.is_file(), "header": None, "rows": 0, "malformed": 0, "error": None}
    if not info["exists"]:
        return info
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            info["header"] = next(reader, None)
            width = len(info["header"] or [])
            for row in reader:
                if not row:
                    continue
                info["rows"] += 1
                if len(row) != width:
                    info["malformed"] += 1
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def _previous_rows(con: duckdb.DuckDBPyConnection, source: str) -> tuple[int | None, str | None]:
    row = con.execute(
        f"SELECT _batch_id, count(*) FROM raw.{source} WHERE _batch_id = (SELECT max(_batch_id) FROM raw.{source}) GROUP BY 1"
    ).fetchone()
    return (row[1], row[0]) if row else (None, None)


def validate_sources(con: duckdb.DuckDBPyConnection, raw_dir: Path) -> tuple[list[Event], dict]:
    """File-level raw contract. Runs every check for every source, writes nothing."""
    events: list[Event] = []
    plan: dict[str, dict] = {}
    for source, spec in SOURCES.items():
        path = Path(raw_dir) / spec["file"]
        info = inspect_file(path, spec["header"])
        events.append(Event("raw", f"{source}_file_exists", info["exists"], {"path": str(path)}))
        if not info["exists"]:
            continue
        readable = info["error"] is None
        events.append(Event("raw", f"{source}_readable", readable, {"error": info["error"]}))
        header_ok = info["header"] == spec["header"]
        events.append(Event("raw", f"{source}_header_exact", header_ok,
                            {"expected": spec["header"], "found": info["header"]}))
        events.append(Event("raw", f"{source}_rows_gt_zero", info["rows"] > 0, {"rows": info["rows"]}))
        events.append(Event("raw", f"{source}_rows_well_formed", info["malformed"] == 0,
                            {"malformed_rows": info["malformed"]}))
        if not (readable and header_ok and info["rows"] > 0 and info["malformed"] == 0):
            continue
        sha = sha256_of(path)
        known = con.execute(f"SELECT _batch_id FROM raw.{source} WHERE _source_sha256 = ? LIMIT 1", [sha]).fetchone()
        prev_rows, prev_batch = _previous_rows(con, source)
        if known:
            events.append(Event("raw", f"{source}_volume_band", True, {"skipped": "file already loaded", "batch_id": known[0]}))
            events.append(Event("raw", f"{source}_latest_batch_is_current_file", known[0] == prev_batch,
                                {"existing_batch": known[0], "latest_batch": prev_batch,
                                 "note": "a file whose sha256 was loaded earlier but is not the latest batch would leave staging on other data"}))
        else:
            if prev_rows:
                change = abs(info["rows"] - prev_rows) / prev_rows
                events.append(Event("raw", f"{source}_volume_band", change <= VOLUME_BAND,
                                    {"rows": info["rows"], "previous_rows": prev_rows, "change": round(change, 4), "band": VOLUME_BAND}))
            else:
                events.append(Event("raw", f"{source}_volume_band", True, {"rows": info["rows"], "previous_rows": None}))
        plan[source] = {"path": path, "sha": sha, "rows": info["rows"], "known_batch": known[0] if known else None}
    return events, plan


def land_sources(con: duckdb.DuckDBPyConnection, plan: dict, loaded_at: datetime) -> dict:
    """Append each file as a new batch unless its sha256 is already loaded (R20). Returns source -> batch_id and rows added."""
    landed = {}
    con.execute("BEGIN TRANSACTION")   # all sources land or none do
    try:
        _land_all(con, plan, loaded_at, landed)
    except Exception:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")
    return landed


def _land_all(con, plan: dict, loaded_at: datetime, landed: dict) -> None:
    for source, p in plan.items():
        spec = SOURCES[source]
        if p["known_batch"]:
            landed[source] = {"batch_id": p["known_batch"], "rows_added": 0}
            continue
        seq = con.execute(f"SELECT count(DISTINCT _batch_id) FROM raw.{source}").fetchone()[0] + 1
        batch_id = f"{source}-{seq:04d}-{p['sha'][:12]}"
        cols = ", ".join(spec["landed"])   # explicit list: PII columns are never selected (ADR-004)
        con.execute(
            f"INSERT INTO raw.{source} SELECT {cols}, ?, ?, ?, ?, row_number() OVER ()::INTEGER "
            f"FROM read_csv(?, all_varchar=true, header=true, nullstr='{NULL_SENTINEL}')",
            [batch_id, loaded_at, p["path"].name, p["sha"], str(p["path"])],
        )
        landed[source] = {"batch_id": batch_id, "rows_added": p["rows"]}


def post_load_checks(con: duckdb.DuckDBPyConnection, plan: dict, now: datetime) -> list[Event]:
    events: list[Event] = []
    for source, p in plan.items():
        spec = SOURCES[source]
        cols = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='raw' AND table_name=? ORDER BY ordinal_position",
            [source]).fetchall()
        non_varchar = [c for c, t in cols if c not in META and t != "VARCHAR"]
        events.append(Event("raw", f"{source}_data_columns_varchar", not non_varchar, {"non_varchar": non_varchar}))
        latest, n, null_meta, sha_ok, newest = con.execute(
            f"SELECT max(_batch_id), count(*), "
            f"count(*) FILTER (WHERE _loaded_at IS NULL OR _source_file IS NULL OR _source_row_number IS NULL OR _batch_id IS NULL), "
            f"count(*) FILTER (WHERE _source_sha256 = ?), max(_loaded_at) FROM raw.{source} "
            f"WHERE _batch_id = (SELECT max(_batch_id) FROM raw.{source})", [p["sha"]]).fetchone()
        events.append(Event("raw", f"{source}_load_metadata_present", null_meta == 0, {"rows_missing_metadata": null_meta}))
        events.append(Event("raw", f"{source}_sha256_recorded", sha_ok == n and n > 0, {"rows": n, "rows_with_current_sha": sha_ok}))
        events.append(Event("raw", f"{source}_rows_loaded_equal_file_rows", n == p["rows"], {"loaded": n, "file_rows": p["rows"]}))
        age = (now - newest) if newest else None
        fresh = age is not None and age <= timedelta(hours=FRESHNESS_HOURS)
        events.append(Event("raw", f"{source}_freshness", fresh,
                            {"newest_loaded_at": str(newest), "age_hours": None if age is None else round(age.total_seconds() / 3600, 3),
                             "max_hours": FRESHNESS_HOURS}))
    return events
