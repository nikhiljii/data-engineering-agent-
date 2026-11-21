"""Staging and mart contract checks. Every check reports; the caller decides to stop at the boundary."""
from __future__ import annotations

import duckdb
import yaml

from .config import (CONTRACTS_DIR, COUNTRIES, DQ_STATUSES, ORDER_STATUSES, PAYMENT_STATUSES)
from .events import Event


def _violations(con, flow: str, check: str, from_where: str, key: str = "1", params=None) -> Event:
    n = con.execute(f"SELECT count(*) FROM {from_where}", params or []).fetchone()[0]
    sample = [r[0] for r in con.execute(f"SELECT {key} FROM {from_where} LIMIT 5", params or []).fetchall()] if n else []
    return Event(flow, check, n == 0, {"violations": n, "sample": sample})


def _in_list(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def staging_checks(con: duckdb.DuckDBPyConnection) -> list[Event]:
    f = "staging"
    ev: list[Event] = []
    # customers (a failure is a contract failure, customers is a lookup)
    ev.append(_violations(con, f, "customers_customer_id_not_null", "staging.stg_customers WHERE customer_id IS NULL", "_source_row_number"))
    ev.append(_violations(con, f, "customers_customer_id_unique",
                          "(SELECT customer_id FROM staging.stg_customers WHERE customer_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1)", "customer_id"))
    ev.append(_violations(con, f, "customers_country_not_null", "staging.stg_customers WHERE country IS NULL", "customer_id"))
    ev.append(_violations(con, f, "customers_country_iso2",
                          "staging.stg_customers WHERE country IS NOT NULL AND NOT regexp_full_match(country, '[A-Z]{2}')", "customer_id"))
    ev.append(_violations(con, f, "customers_created_at_parses_iso", "staging.stg_customers WHERE created_at IS NULL", "customer_id"))
    # orders
    ev.append(_violations(con, f, "orders_order_id_not_null", "staging.stg_orders WHERE order_id IS NULL", "_source_row_number"))
    ev.append(_violations(con, f, "orders_order_id_unique_after_dedup",
                          "(SELECT order_id FROM staging.stg_orders GROUP BY 1 HAVING count(*) > 1)", "order_id"))
    ev.append(_violations(con, f, "orders_order_status_accepted",
                          f"staging.stg_orders WHERE order_status IS NULL OR order_status NOT IN ({_in_list(ORDER_STATUSES)})", "order_id"))
    ev.append(_violations(con, f, "orders_unit_price_numeric_positive",
                          "staging.stg_orders WHERE unit_price IS NULL OR unit_price <= 0", "order_id"))
    # R18: no silent coercion (non-empty raw value, NULL typed value, no invalid reason)
    ev.append(_violations(con, f, "orders_no_silent_coercion",
                          "staging.stg_orders WHERE (trim(order_date_raw) <> '' AND order_date IS NULL AND NOT list_contains(invalid_reasons, 'invalid_order_date')) "
                          "OR (trim(quantity_raw) <> '' AND quantity IS NULL AND NOT list_contains(invalid_reasons, 'invalid_quantity'))", "order_id"))
    # informational (ok = True): handled by quarantine or flags, see ADR-003
    n_q, n_d, n_c, n_rows, n_dup = con.execute(
        "SELECT count(*) FILTER (WHERE quantity IS NULL OR quantity = 0), count(*) FILTER (WHERE order_date IS NULL), "
        "count(*) FILTER (WHERE customer_id IS NULL), count(*), count(*) FILTER (WHERE had_duplicate) FROM staging.stg_orders").fetchone()
    n_surplus = con.execute("SELECT count(*) FROM staging.stg_orders_duplicate_rows").fetchone()[0]
    ev.append(Event(f, "orders_quantity_non_integer_or_zero_routed_to_quarantine", True,
                    {"rows": n_q, "note": "B4 routes these to quarantine (invalid_quantity); architecture contract text says 'quantity integer' (contradiction reported)"}))
    ev.append(Event(f, "orders_invalid_order_date_routed_to_quarantine", True, {"orders": n_d}))
    ev.append(Event(f, "orders_customer_id_missing_flagged", True, {"orders": n_c}))
    ev.append(Event(f, "orders_raw_duplicate_rate", True, {"duplicate_orders": n_dup, "surplus_rows": n_surplus,
                                                           "rate": round(n_surplus / (n_rows + n_surplus), 6) if n_rows + n_surplus else 0}))
    # payments
    ev.append(_violations(con, f, "payments_payment_id_not_null", "staging.stg_payments WHERE payment_id IS NULL", "_source_row_number"))
    ev.append(_violations(con, f, "payments_payment_id_unique",
                          "(SELECT payment_id FROM staging.stg_payments WHERE payment_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1)", "payment_id"))
    ev.append(_violations(con, f, "payments_order_id_not_null", "staging.stg_payments WHERE order_id IS NULL", "payment_id"))
    ev.append(_violations(con, f, "payments_payment_status_accepted",
                          f"staging.stg_payments WHERE payment_status IS NULL OR payment_status NOT IN ({_in_list(PAYMENT_STATUSES)})", "payment_id"))
    ev.append(_violations(con, f, "payments_payment_amount_numeric_positive",
                          "staging.stg_payments WHERE payment_amount IS NULL OR payment_amount <= 0", "payment_id"))
    ev.append(_violations(con, f, "payments_payment_date_parses_iso", "staging.stg_payments WHERE payment_date IS NULL", "payment_id"))
    ev.append(_violations(con, f, "payments_at_most_one_payment_per_order",
                          "(SELECT order_id FROM staging.stg_payments WHERE order_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1)", "order_id"))
    n_orphans = con.execute("SELECT count(*) FROM staging.stg_payments p WHERE p.order_id IS NOT NULL AND p.order_id NOT IN "
                            "(SELECT order_id FROM staging.stg_orders WHERE order_id IS NOT NULL)").fetchone()[0]
    ev.append(Event(f, "payments_orphan_payment_routed_to_quarantine", True, {"payments": n_orphans}))
    return ev


def _mart_contract_columns() -> dict:
    return yaml.safe_load((CONTRACTS_DIR / "marts.yaml").read_text())["tables"]


def mart_checks(con: duckdb.DuckDBPyConnection, dates: list, full_coverage: bool) -> list[Event]:
    """Run inside the publishing transaction; a failure rolls everything back."""
    f = "marts"
    ev: list[Event] = []
    r = "marts.daily_country_report"
    d = "marts.daily_country_report_dq"
    ev.append(_violations(con, f, "daily_country_report_key_not_null", f"{r} WHERE report_date IS NULL OR country IS NULL", "country"))
    ev.append(_violations(con, f, "daily_country_report_key_unique",
                          f"(SELECT 1 FROM {r} GROUP BY report_date, country HAVING count(*) > 1)"))
    ev.append(_violations(con, f, "daily_country_report_dq_key_unique",
                          f"(SELECT 1 FROM {d} GROUP BY report_date, country HAVING count(*) > 1)"))
    ev.append(_violations(con, f, "daily_country_report_country_accepted",
                          f"{r} WHERE country NOT IN ({_in_list(COUNTRIES)})", "country"))
    ev.append(_violations(con, f, "daily_country_report_measures_in_range",
                          f"{r} WHERE total_orders < 0 OR completed_orders < 0 OR completed_orders > total_orders OR gross_revenue < 0 "
                          "OR successful_payments < 0 OR failed_payments < 0", "report_date"))
    ev.append(_violations(con, f, "daily_country_report_status_accepted",
                          f"{r} WHERE data_quality_status IS NULL OR data_quality_status NOT IN ({_in_list(DQ_STATUSES)})", "report_date"))
    ev.append(_violations(con, f, "daily_country_report_dq_relationship",
                          f"(SELECT report_date, country FROM {r} EXCEPT SELECT report_date, country FROM {d} "
                          f"UNION ALL SELECT report_date, country FROM (SELECT report_date, country FROM {d} EXCEPT SELECT report_date, country FROM {r}))"))
    ev.append(_violations(con, f, "data_quality_status_matches_dq",   # R11
                          f"{r} r JOIN {d} d USING (report_date, country) WHERE r.data_quality_status <> CASE "
                          "WHEN d.excluded_orders > 0 THEN 'FAIL' "
                          "WHEN d.duplicates_collapsed + d.unknown_country_orders + d.amount_mismatch_orders + d.completed_without_payment > 0 THEN 'WARN' "
                          "ELSE 'OK' END", "r.report_date"))
    # reconciliation for the published dates (always) and for the whole snapshot (backfill)
    expected = con.execute("SELECT count(*) FROM intermediate.int_orders_enriched WHERE is_valid AND report_date IN (SELECT unnest(?::DATE[]))", [dates]).fetchone()[0]
    got = con.execute(f"SELECT coalesce(sum(total_orders), 0) FROM {r} WHERE report_date IN (SELECT unnest(?::DATE[]))", [dates]).fetchone()[0]
    ev.append(Event(f, "published_dates_total_orders_equal_valid_orders", expected == got, {"valid_orders": expected, "sum_total_orders": got}))
    if full_coverage:
        # R12 and R14 hold for the whole snapshot only when every date was recomputed
        n_ids, n_inv, s_tot = con.execute(
            f"SELECT (SELECT count(DISTINCT order_id) FROM staging.stg_orders), "
            f"(SELECT count(DISTINCT order_id) FROM quarantine.orders_rejected WHERE primary_reason IN ('invalid_order_date','negative_quantity','invalid_quantity')), "
            f"(SELECT coalesce(sum(total_orders),0) FROM {r})").fetchone()
        ev.append(Event(f, "R12_order_reconciliation", n_ids == s_tot + n_inv, {"distinct_order_ids": n_ids, "sum_total_orders": s_tot, "excluded_orders": n_inv}))
        raw_p, counted, rej = con.execute(
            "SELECT (SELECT count(*) FROM staging.stg_payments), "
            "(SELECT count(*) FROM intermediate.int_order_payments WHERE is_valid AND has_payment), "
            "(SELECT count(*) FROM quarantine.payments_rejected)").fetchone()
        ev.append(Event(f, "R14_payment_row_accounting", raw_p == counted + rej, {"payment_rows": raw_p, "counted": counted, "quarantined": rej}))
    # R21: every column of every mart table is classified in contracts/marts.yaml
    spec = _mart_contract_columns()
    unclassified = []
    for tbl, tspec in spec.items():
        schema, name = tbl.split(".")
        cols = [c for (c,) in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=? AND table_name=? ORDER BY ordinal_position", [schema, name]).fetchall()]
        for c in cols:
            if c not in tspec["columns"] or not tspec["columns"][c].get("classification"):
                unclassified.append(f"{tbl}.{c}")
        if cols != list(tspec["columns"]):
            unclassified.append(f"{tbl}: column order/set differs from contract")
    ev.append(Event(f, "R21_every_mart_column_classified", not unclassified, {"unclassified": unclassified}))
    return ev
