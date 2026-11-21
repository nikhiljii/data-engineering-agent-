# ADR-002: Transformation tooling and code location

Status: PROPOSED (needs human approval before any build)

## Context
- Profile: local embedded DuckDB, batch, SQL transforms, team SQL strength medium (placeholder), no cloud.
- The baseline `src/` holds the toy pipeline, scorer, deploy and eval tooling and must stay untouched.
- `requirements.txt` has only duckdb, pyyaml, pytest. dbt is optional and not installed in this environment.
- `src/conformance.py` checks dbt projects only (`models/`, `dbt_project.yml`).

## Options considered
1. dbt-core + dbt-duckdb: models, tests and docs in one convention; the existing conformance checker
   works as is. Cons: new dependency not installed here, heavy for 3 tables, and dbt would be a tool
   chosen before it is needed.
2. Plain SQL files executed by a small Python runner against DuckDB, with pytest and SQL assertion
   files as the tests. Pros: no new dependency, trivially local, every rule is a readable SQL
   statement. Cons: no dbt docs or lineage graph; `src.conformance` cannot check it; we must write our
   own conformance checks.
3. Pure pandas/Python transforms. Rejected: hides rules in code, loses SQL readability, no benefit at this volume.

## Decision
Option 2. All pipeline code lives under `scenario/ecommerce/` (Python package `src_scenario`,
SQL under `scenario/ecommerce/sql/{raw,staging,intermediate,marts,quarantine}/`, tests under
`scenario/ecommerce/tests/`). Nothing under `src/`, `tests/`, `evals/` or `.claude/` is modified.

## Consequences
- Conformance rules R1..R23 in data-architecture.md are checked by `scenario/ecommerce` tests and
  queries over the built DuckDB file, not by `src.conformance` (which would report SKIP/FAIL because
  there is no `dbt_project.yml`). The evaluator must not read that as a violation.
- Rule numbering follows the skill's R1..R6 style; R7 onwards are project-specific.
- Materialization mirrors the dbt convention anyway: staging = views, intermediate = views, marts and quarantine = tables.

## Revisit when
The human wants dbt, a second source or team member joins, or the model count exceeds about 15.
