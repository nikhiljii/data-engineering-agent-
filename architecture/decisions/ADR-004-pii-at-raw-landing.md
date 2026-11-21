# ADR-004: PII at raw landing (column pruning)

Status: PROPOSED (needs human approval before any build)

## Context
`customers.csv` has customer_name and email (PII, synthetic). Profile: `pii: true`, `mask_before_landing: false`.
Requirements A-15 asks for a faithful raw copy; requirements PII section offers column pruning as mitigation.
Neither column is needed by any output column (the report uses only customer_id and country).

## Options considered
1. Land all columns, keep PII in raw, drop it in staging. Faithful, but stores PII in a persistent DuckDB file.
2. Land customers with only customer_id, country, created_at (prune name and email at load). Raw is then no longer a byte-faithful copy of that file, but the original CSV remains the untouched source of truth.
3. Hash email, keep a pseudonymous name. Needs a salt/secret and has no consumer.

## Decision
Option 2 for customers. The loader selects an explicit column list; `customer_name` and `email` are never
written to any DuckDB table. orders and payments are landed with all columns. The pruning is the only
deviation from "raw = unmodified source values" and is stated in rule R2.

## Consequences
No PII in any table, so governance classification is trivial for the report; if a future need for name or
email appears, a new ADR is needed. Reproducing a defect in customers beyond the retained columns needs the CSV.

## Revisit when
Open question Q5 is answered with a masking or lawful-basis requirement (then ETLT, superseding ADR-001), or a column is needed.
