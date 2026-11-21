#!/usr/bin/env python3
"""Fault-injection generator for the incident step (quality-engineer, written from the architecture only).

Writes fault-injected copies of customers.csv / orders.csv / payments.csv into <out>/<case>/.
NEVER writes into scenario/ecommerce/data/raw/ (refuses). Usage:
    python scenario/ecommerce/tests/faults/make_faults.py --out .incident/inputs --list
    python scenario/ecommerce/tests/faults/make_faults.py --out .incident/inputs [--case f1_invalid_dates]
Each case dir holds the three CSVs; `--list` prints the oracle (expected pipeline outcome) per case.
"""
import argparse
import csv
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RAW = ROOT / "scenario" / "ecommerce" / "data" / "raw"
FILES = ("customers.csv", "orders.csv", "payments.csv")


def read(name, src):
    with open(src / name, newline="") as f:
        r = list(csv.reader(f))
    return r[0], r[1:]


def write(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def f0(dst, src):
    pass


def f1(dst, src):
    h, rows = read("orders.csv", src)
    write(dst / "orders.csv", h, [r[:2] + ["not-a-date"] + r[3:] if i % 5 == 0 else r for i, r in enumerate(rows)])


def f2(dst, src):
    h, rows = read("orders.csv", src)
    write(dst / "orders.csv", ["order_ref"] + h[1:], rows)


def f3(dst, src):
    h, _ = read("payments.csv", src)
    write(dst / "payments.csv", h, [])


def f3b(dst, src):
    (dst / "payments.csv").unlink()


def f4(dst, src):
    h, rows = read("orders.csv", src)
    write(dst / "orders.csv", h, rows[: int(len(rows) * 0.4)])


def f5(dst, src):
    h, rows = read("customers.csv", src)          # PII columns customer_name,email are kept (as in the source) + duplicated key
    write(dst / "customers.csv", h, rows + [rows[0]])


CASES = {
    "f0_control": (f0, "control: unmodified copy of the raw files",
                   "run_status PUBLISHED, exit 0, excluded_share about 0.009, 30 orphan payments, 95 quarantined order rows. No escalation."),
    "f1_invalid_dates": (f1, "F1: every 5th orders.order_date replaced by 'not-a-date' (about 20% of orders excluded)",
                         "R17: run STOPPED, exit non-zero, nothing published, audit.run_log status STOPPED, excluded_share > 0.15. ESCALATE to a human; no auto-fix."),
    "f2_header_renamed": (f2, "F2: orders.csv header column order_id renamed to order_ref",
                          "raw contract failure (exact header): STOPPED before staging, exit non-zero, nothing published. ESCALATE (schema change at the source)."),
    "f3_payments_empty": (f3, "F3: payments.csv has the header and zero data rows",
                          "raw contract failure (row count > 0): STOPPED, exit non-zero, nothing published. ESCALATE."),
    "f3b_payments_missing": (f3b, "F3b: payments.csv file absent",
                             "STOPPED / non-zero exit, nothing published (file cannot be loaded). ESCALATE."),
    "f4_volume_drop": (f4, "F4: orders.csv truncated to the first 40% of rows (-60%)",
                       "R19 volume band (+/-50% vs previous batch): STOPPED, exit non-zero, previous publication untouched. Only detectable when a previous batch exists in the same --db: run f0_control into that db first. ESCALATE."),
    "f5_customers_pii_dup": (f5, "F5: customers.csv keeps customer_name/email (as in source) and customer_id C00001 duplicated",
                             "customers contract (customer_id unique): STOPPED, exit non-zero, nothing published. R2 still holds: no customer_name/email column or value in any DuckDB table. ESCALATE."),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="output directory (one sub-directory per case)")
    ap.add_argument("--case", choices=sorted(CASES), help="generate one case only")
    ap.add_argument("--list", action="store_true", help="print each case and the expected outcome (oracle)")
    a = ap.parse_args()
    if a.list:
        for k, (_, what, expect) in CASES.items():
            print(f"{k}\n  fault   : {what}\n  expect  : {expect}")
    if not a.out:
        if not a.list:
            ap.error("--out is required unless only --list is given")
        return 0
    out = Path(a.out).resolve()
    if out == RAW.resolve() or RAW.resolve() in out.parents:
        sys.exit("refusing to write into the raw data directory")
    for k, (fn, _, _) in CASES.items():
        if a.case and a.case != k:
            continue
        d = out / k
        d.mkdir(parents=True, exist_ok=True)
        for n in FILES:
            shutil.copyfile(RAW / n, d / n)
        fn(d, d)
        print(f"wrote {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
