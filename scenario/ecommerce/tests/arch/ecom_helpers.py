"""Helpers for the architecture-derived test suite (quality-engineer).

Written from architecture/data-architecture.md, ADR-001..005, requirements.md and foreign-keys.yaml only.
Pure check functions (take a duckdb connection or text, return a list of violation strings) are exercised
against seeded defects in test_ecom_selfcheck.py, so every check is shown to be able to fail.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]                       # repo root
SCEN = ROOT / "scenario" / "ecommerce"
RAW_DIR = SCEN / "data" / "raw"
SQL_DIR = SCEN / "sql"
CONTRACTS_DIR = SCEN / "contracts"
ORACLE_SQL = HERE / "sql" / "oracle_report.sql"

# sha256 of the read-only raw inputs, recorded when the tests were written (R23: raw data untouched).
RAW_SHA256 = {
    "customers.csv": "19362d901eeb159724df430d42c5b9aca5d32fe53f887ec0c632a42688c9e5f1",
    "orders.csv": "f8299ec10054124271069604b09f0e1cce944f7eae7feaa66724e2c56942e006",
    "payments.csv": "b03f519ea10e781266ca6cf96c1ad873ea639ed7cd26de7448abf8e7a28f34fb",
}

SCHEMAS = ["raw", "staging", "intermediate", "marts", "quarantine", "audit"]
REPORT_COLUMNS = [
    ("report_date", "DATE"), ("country", "VARCHAR"), ("total_orders", "INTEGER"),
    ("completed_orders", "INTEGER"), ("gross_revenue", "DECIMAL(18,2)"),
    ("successful_payments", "INTEGER"), ("failed_payments", "INTEGER"), ("data_quality_status", "VARCHAR"),
]
DQ_COLUMNS = ["duplicates_collapsed", "unknown_country_orders", "excluded_orders", "amount_mismatch_orders",
              "completed_without_payment", "no_payment_orders", "payments_excluded", "status_payment_contradictions"]
ORDER_REASONS = {"duplicate_order_surplus", "invalid_order_date", "negative_quantity", "invalid_quantity"}
PAYMENT_REASONS = {"orphan_payment", "order_excluded"}
ORDER_PRECEDENCE = ["invalid_order_date", "negative_quantity", "invalid_quantity", "duplicate_order_surplus"]
COUNTRIES = {"DE", "ES", "FR", "GB", "IN", "IT", "NL", "US", "UNKNOWN"}
CLASSIFICATIONS = {"public", "internal", "confidential", "pii"}
RAW_META = ["_batch_id", "_loaded_at", "_source_file", "_source_sha256", "_source_row_number"]
RAW_DATA_COLS = {
    "customers": ["customer_id", "country", "created_at"],
    "orders": ["order_id", "customer_id", "order_date", "product_id", "quantity", "unit_price", "order_status"],
    "payments": ["payment_id", "order_id", "payment_date", "payment_amount", "payment_status"],
}
CSV_HEADERS = {
    "customers": ["customer_id", "customer_name", "email", "country", "created_at"],
    "orders": RAW_DATA_COLS["orders"],
    "payments": RAW_DATA_COLS["payments"],
}


# ---------------------------------------------------------------- running the pipeline
def run_pipeline(raw_dir, db, *args, timeout=900):
    env = dict(os.environ, PYTHONPATH=os.environ.get("ECOM_PYTHONPATH", str(SCEN)))   # override only to demo RED on an empty package
    cmd = [sys.executable, "-m", "src_scenario.run", *args, "--raw-dir", str(raw_dir), "--db", str(db)]
    return subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)


def describe(proc):
    return f"rc={proc.returncode}\nSTDOUT:\n{proc.stdout[-2000:]}\nSTDERR:\n{proc.stderr[-2000:]}"


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def connect_ro(db):
    return duckdb.connect(str(db), read_only=True)


def table_exists(con, schema, name):
    return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=? AND table_name=?",
                       [schema, name]).fetchone()[0] > 0


def row_count_or_zero(con, schema, name):
    return con.execute(f"SELECT count(*) FROM {schema}.{name}").fetchone()[0] if table_exists(con, schema, name) else 0


def load_oracle(con, raw_dir):
    """Create TEMP views o_cust/o_src/o_ranked/o_orders/o_pay/o_report over the raw CSVs in `raw_dir`."""
    sql = ORACLE_SQL.read_text().replace("{raw_dir}", str(raw_dir))
    for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
        con.execute(stmt)


# ---------------------------------------------------------------- CSV mutation helpers (seeded defects)
def read_csv_rows(path):
    with open(path, newline="") as f:
        r = list(csv.reader(f))
    return r[0], r[1:]


def write_csv_rows(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def copy_raw(dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for n in ("customers.csv", "orders.csv", "payments.csv"):
        (dest / n).write_bytes((RAW_DIR / n).read_bytes())
    return dest


def mutate(dest, source, fn):
    """Apply fn(header, rows) -> (header, rows) to one CSV in `dest` (a tmp copy, never the real raw data)."""
    dest = Path(dest)
    assert RAW_DIR.resolve() not in dest.resolve().parents and dest.resolve() != RAW_DIR.resolve()
    p = dest / f"{source}.csv"
    h, rows = read_csv_rows(p)
    h2, rows2 = fn(h, rows)
    write_csv_rows(p, h2, rows2)


# ---------------------------------------------------------------- pure checks: return list of violations
def check_no_pii_columns(con):
    rows = con.execute("SELECT table_schema, table_name, column_name FROM information_schema.columns "
                       "WHERE lower(column_name) IN ('customer_name','email')").fetchall()
    return [f"PII column {s}.{t}.{c}" for s, t, c in rows]


def check_no_pii_values(con):
    bad = []
    cols = con.execute("SELECT table_schema, table_name, column_name FROM information_schema.columns "
                       "WHERE data_type='VARCHAR' AND table_schema IN ('raw','staging','intermediate','marts','quarantine','audit')").fetchall()
    for s, t, c in cols:
        n = con.execute(f'SELECT count(*) FROM {s}."{t}" WHERE "{c}" LIKE \'%@%\' OR "{c}" LIKE \'Customer %\'').fetchone()[0]
        if n:
            bad.append(f"{s}.{t}.{c}: {n} values look like email/customer name")
    return bad


def check_schemas(con):
    have = {r[0] for r in con.execute("SELECT schema_name FROM information_schema.schemata").fetchall()}
    return [f"missing schema {s}" for s in SCHEMAS if s not in have]


def check_raw_all_varchar(con):
    bad = []
    for t, cols in RAW_DATA_COLS.items():
        got = dict(con.execute("SELECT column_name, data_type FROM information_schema.columns "
                               "WHERE table_schema='raw' AND table_name=?", [t]).fetchall())
        for c in cols:
            if got.get(c) != "VARCHAR":
                bad.append(f"raw.{t}.{c} type {got.get(c)!r}, expected VARCHAR")
        for c in RAW_META:
            if c not in got:
                bad.append(f"raw.{t} missing metadata column {c}")
    return bad


def check_report_columns(con):
    got = con.execute("SELECT column_name, data_type FROM information_schema.columns "
                      "WHERE table_schema='marts' AND table_name='daily_country_report' ORDER BY ordinal_position").fetchall()
    return [] if [tuple(x) for x in got] == REPORT_COLUMNS else [f"columns {got} != {REPORT_COLUMNS}"]


def check_status_derivation(con):
    """R11 on marts.daily_country_report vs _dq."""
    q = """
    SELECT m.report_date, m.country, m.data_quality_status,
      CASE WHEN d.excluded_orders > 0 THEN 'FAIL'
           WHEN d.duplicates_collapsed + d.unknown_country_orders + d.amount_mismatch_orders + d.completed_without_payment > 0 THEN 'WARN'
           ELSE 'OK' END AS derived
    FROM marts.daily_country_report m FULL JOIN marts.daily_country_report_dq d USING (report_date, country)"""
    out = []
    for rd, c, s, der in con.execute(q).fetchall():
        if s not in ("OK", "WARN", "FAIL"):
            out.append(f"{rd} {c}: status {s!r} not allowed")
        elif s != der:
            out.append(f"{rd} {c}: status {s} but dq counts derive {der}")
    return out


def check_quarantine_reasons(con):
    """R16: primary_reason non-null and in accepted set; every reason token accepted; primary follows precedence (orders)."""
    out = []
    for tbl, accepted in (("orders_rejected", ORDER_REASONS), ("payments_rejected", PAYMENT_REASONS)):
        for pr, reason in con.execute(f"SELECT primary_reason, reason FROM quarantine.{tbl}").fetchall():
            if pr is None or pr not in accepted:
                out.append(f"{tbl}: primary_reason {pr!r} not accepted")
                continue
            toks = (reason or "").split("|")
            if any(t not in accepted for t in toks) or pr not in toks:
                out.append(f"{tbl}: reason {reason!r} inconsistent with primary {pr!r}")
            if toks != sorted(toks):
                out.append(f"{tbl}: reason tokens not sorted: {reason!r}")
            if tbl == "orders_rejected":
                best = next(r for r in ORDER_PRECEDENCE if r in toks)
                if pr != best:
                    out.append(f"{tbl}: primary {pr} violates precedence, expected {best} for {reason!r}")
    return out


def table_hash(con, fq_table, exclude_prefixes=()):
    cols = [r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_schema||'.'||table_name=? "
                                      "ORDER BY ordinal_position", [fq_table]).fetchall()]
    cols = [c for c in cols if not any(c.startswith(p) for p in exclude_prefixes)]
    if not cols:
        return None
    sel = ", ".join(f'CAST("{c}" AS VARCHAR)' for c in cols)
    rows = con.execute(f"SELECT {sel} FROM {fq_table} ORDER BY ALL").fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def report_diff(con):
    """Compare marts.daily_country_report with the CSV oracle views. Returns violations."""
    q = """
    SELECT coalesce(m.report_date, o.report_date) rd, coalesce(m.country, o.country) c,
           m.total_orders, o.total_orders, m.completed_orders, o.completed_orders, m.gross_revenue, o.gross_revenue,
           m.successful_payments, o.successful_payments, m.failed_payments, o.failed_payments,
           m.data_quality_status, o.data_quality_status, m.report_date IS NULL, o.report_date IS NULL
    FROM marts.daily_country_report m FULL JOIN o_report o ON m.report_date = o.report_date AND m.country = o.country"""
    out = []
    names = ["total_orders", "completed_orders", "gross_revenue", "successful_payments", "failed_payments", "data_quality_status"]
    for r in con.execute(q).fetchall():
        if r[14]:
            out.append(f"{r[0]} {r[1]}: row missing from mart (B9)")
        elif r[15]:
            out.append(f"{r[0]} {r[1]}: unexpected extra mart row (B9)")
        else:
            for i, n in enumerate(names):
                if r[2 + 2 * i] != r[3 + 2 * i]:
                    out.append(f"{r[0]} {r[1]}: {n} mart={r[2 + 2 * i]} oracle={r[3 + 2 * i]}")
    return out


def check_unique_not_null(con, fq_table, cols):
    out = []
    n_null = con.execute(f"SELECT count(*) FROM {fq_table} WHERE " + " OR ".join(f'"{c}" IS NULL' for c in cols)).fetchone()[0]
    if n_null:
        out.append(f"{fq_table}: {n_null} rows with NULL in key {cols}")
    k = ", ".join(f'"{c}"' for c in cols)
    n_dup = con.execute(f"SELECT count(*) FROM (SELECT {k} FROM {fq_table} GROUP BY {k} HAVING count(*) > 1)").fetchone()[0]
    if n_dup:
        out.append(f"{fq_table}: {n_dup} duplicated keys {cols}")
    return out


# ---------------------------------------------------------------- static code checks (R3, R8, R21, R22)
_LAYER_RE = re.compile(r"\b(raw|staging|intermediate|marts|quarantine|audit)\s*\.\s*[A-Za-z_]\w*", re.I)
ALLOWED_REFS = {  # layer directory -> schemas its SQL may reference (R3)
    "staging": {"raw", "staging"},
    "intermediate": {"staging", "intermediate"},
    "marts": {"staging", "intermediate", "marts"},
    "quarantine": {"staging", "intermediate", "quarantine"},
}


def strip_sql_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"--[^\n]*", " ", text)


def check_sql_layering(files):
    """files: {relative_posix_path: text} such as {'marts/x.sql': '...'}"""
    out = []
    for rel, text in files.items():
        layer = rel.split("/")[0]
        if layer not in ALLOWED_REFS:
            continue
        for m in _LAYER_RE.finditer(strip_sql_comments(text)):
            if m.group(1).lower() not in ALLOWED_REFS[layer]:
                out.append(f"{rel}: layer '{layer}' references schema '{m.group(1).lower()}' ({m.group(0)})")
    return out


_NONDET_RE = re.compile(r"\b(now\s*\(|current_timestamp|current_date|today\s*\(|random\s*\(|uuid\s*\(|gen_random_uuid)", re.I)


def check_sql_deterministic(files):
    out = []
    for rel, text in files.items():
        if rel.split("/")[0] in ("raw", "audit"):
            continue
        for m in _NONDET_RE.finditer(strip_sql_comments(text)):
            out.append(f"{rel}: non-deterministic construct {m.group(0)!r}")
    return out


def read_sql_files():
    return {p.relative_to(SQL_DIR).as_posix(): p.read_text() for p in sorted(SQL_DIR.rglob("*.sql"))}


def collect_column_classes(doc):
    """Walk a parsed contract YAML; return {column_name: {'classification':..., 'masking':...}}.
    Supports a list of {name|column: X, classification: Y} dicts and a mapping {X: {classification: Y}}."""
    found = {}

    def walk(node):
        if isinstance(node, dict):
            name = node.get("name") or node.get("column")
            if isinstance(name, str) and "classification" in node:
                found.setdefault(name.lower(), []).append(node)
            for k, v in node.items():
                if isinstance(v, dict) and "classification" in v and isinstance(k, str):
                    found.setdefault(k.lower(), []).append(v)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(doc)
    return found


def check_contract_columns(docs, mart_columns):
    """docs: list of parsed yaml; mart_columns: list of (table, column). Every column needs a classification; PII needs masking."""
    found = {}
    for d in docs:
        for k, v in collect_column_classes(d).items():
            found.setdefault(k, []).extend(v)
    out = []
    for tbl, col in mart_columns:
        entries = found.get(col.lower())
        if not entries:
            out.append(f"{tbl}.{col}: not classified in contracts")
            continue
        for e in entries:
            cls = str(e.get("classification", "")).lower()
            if cls not in CLASSIFICATIONS:
                out.append(f"{tbl}.{col}: classification {e.get('classification')!r} invalid")
            if cls == "pii" and not (e.get("masking") or e.get("mask") or e.get("masking_rule")):
                out.append(f"{tbl}.{col}: PII without masking rule")
    return out


_FORBIDDEN = [
    (re.compile(r"\b(boto3|botocore|google\.cloud|azure\.|snowflake|requests|urllib\.request|urllib3|httpx|aiohttp|http\.client|socket)\b"), "cloud/network library"),
    (re.compile(r"https?://"), "URL / network call"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token)\b\s*[:=]\s*['\"][^'\"\s]{4,}"), "credential-looking literal"),
]


def check_forbidden(files):
    out = []
    for rel, text in files.items():
        for rx, what in _FORBIDDEN:
            for m in rx.finditer(text):
                out.append(f"{rel}: {what}: {m.group(0)!r}")
    return out


def scenario_code_files():
    """Code/config files of the pipeline (tests/, data/, briefs/, docs, warehouse excluded)."""
    exts = {".py", ".sql", ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".sh"}
    out = {}
    for p in SCEN.rglob("*"):
        rel = p.relative_to(SCEN)
        if not p.is_file() or rel.parts[0] in ("tests", "data", "briefs", "warehouse", "__pycache__") or "__pycache__" in rel.parts:
            continue
        if p.suffix in exts or p.name.startswith(".env"):
            out[rel.as_posix()] = p.read_text(errors="replace")
    return out
