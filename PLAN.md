# PLAN.md (orchestrator owns this file; update after every step)

## Current state
- Pipeline: ingest -> transform -> publish on DuckDB, with contracts. DONE, tested.
- Incident logic: rule diagnoser + naive comparison, closed actions, guardrails, sandbox verify. DONE.
- Deployment tooling: stage/smoke/promote/rollback with approver gate. DONE, tested.
- Eval harness: 11 scenarios x 3 seeds, hard gates. DONE (rules baseline 100%, which is NOT generalisation evidence).
- Agent + skill files written (this commit). NOT yet exercised by Claude Code end to end.

## Phase 0 architecture gate: BUILT and exercised on the target project
- Layout follows the agreed tree: .claude/ (agents + skills), architecture/, profiles/, src/, evals/, tests/.
- Agents so far: orchestrator, architect, pipeline-builder, quality-engineer, deployment, incident, evaluator.
- src/profile.py, src/paradigm.py (ELT/ETL/ETLT scorer + ADR), src/conformance.py (R1..R19 checker with ratchet baseline).
- Test 1 done on a dbt demo project (jaffle_shop_duckdb): architect pack produced and the conformance checker independently reproduced its violations. Evidence: evals/failure-log.md and evals/architect-runs/.
- Status: architecture pack is PROPOSED, awaiting human approval. 43 tests pass; dbt build 28/28.

## Next steps (in order)
0. Human approves the architecture pack (ADR-001 ELT) and the baseline. Then:
   - test 2: fault injection in seeds (rename, duplicates, nulls); needs a dbt adapter for the incident agent.
   - test 3: feature request through the orchestrator, e.g. fix R9/R10 or add a refunds mart; builder + quality-engineer + evaluator + deployment.
1. Run the orchestrator in Claude Code on a real request: "add a daily returns feed" and record what it gets wrong.
2. Held-out scenarios no rule was written for; the evaluator owns them, builder never sees them.
3. LLM diagnoser behind the same `Diagnoser` interface; compare against rules on the scorecard.
4. Replace DuckDB with a real warehouse trial; put one flow under Airflow or Dagster.
5. Scheduled monitoring run that calls the incident agent and writes findings to PLAN.md.
6. Case study: problem, architecture, scorecard history, failures found, lessons.

## Open questions
- Which real dataset/domain will the flagship demo use for the case study?

## Decisions log
- Agents coordinate through files (PLAN.md, audit logs, releases/), not shared chat memory.

---

# SCENARIO: ecommerce-daily-orders (E2E validation run, 2026-11-21)

Separate from the earlier demo project above (history kept untouched). Scope: implement the approved pipeline under `scenario/ecommerce/`
(local DuckDB, no cloud), validate it, locally stage a release, and test the production-approval gate and an incident scenario.
Architecture pack approved as a TEST APPROVAL only (`architecture/TEST-APPROVAL.md`, tag `e2e-arch-approved`). Promotion approver: UNSET.
Open questions Q1..Q17 are unanswered by the human; they are documented assumptions, not sign-off.

## DELEGATION STATUS (read first)
The orchestrator definition lists the Agent tool, but in this run the orchestrator could NOT spawn nested agents. Therefore:
- NO specialist has been run. No pipeline code, tests, release or incident investigation exists as a result of this plan. Every step below is NOT STARTED.
- Delegation is handed to the human-side dispatcher: run each agent with the brief at `scenario/ecommerce/briefs/<agent>.md`, return its report, and the orchestrator
  (or the dispatcher) records the outcome in the state column. Nothing in the state column may be set to DONE without that agent's real report and command output.

## Interface freeze (so parallel steps can agree without reading each other's code)
Taken from the architecture: code in `scenario/ecommerce/` (package `src_scenario`, `sql/`, `contracts/`, `tests/`); CLI `python -m src_scenario.run --report-date D | --backfill`;
schemas/tables as in data-architecture.md "Layers"; imports need `PYTHONPATH=scenario/ecommerce`.
ORCHESTRATOR PROPOSALS (not in the architecture, need dispatcher confirmation, see D2): CLI flags `--raw-dir PATH` (default `scenario/ecommerce/data/raw`) and `--db PATH`
(default `scenario/ecommerce/warehouse/ecommerce.duckdb`), non-zero exit when run_status = STOPPED; test dirs `tests/arch` (quality-engineer), `tests/unit` (builder), `tests/faults` (quality-engineer).
Test commands always use explicit paths; bare `python -m pytest -q` at root would also collect `scenario/`.

## Decisions needed from the human/dispatcher BEFORE dispatch (blockers)
- D1 (blocks S1c, S4, S6): `src/deploy.py` and `src/incident_cli.py` are bound to the toy pipeline and `src/` is frozen (R23), so deployment and incident cannot act on this pipeline as defined.
  Proposed: builder WP-B writes `src_scenario/release.py` and `src_scenario/incident.py` with the same semantics (staged release, approver gate, read-only investigation, escalation). Accept, or define another way.
- D2 (blocks S1a/S1b): confirm the interface freeze additions (`--raw-dir`, `--db`, exit code).
- D3: quality-engineer's definition says checker logic goes in `src/conformance.py`; that contradicts R23/ADR-002. Plan overrides: QE writes only under `scenario/ecommerce/tests/`. Confirm.

## Ordered plan (smallest testable steps)
| Step | Agent | Depends on | Parallel with | Proves (command in the brief) | State |
|---|---|---|---|---|---|
| S0 | human/dispatcher | none | none | answers D1-D3; dispatches | NOT STARTED |
| S1a | quality-engineer | S0 | S1b, S1c | `pytest scenario/ecommerce/tests/arch` collects; RED before code; every R1..R23, B1..B9 mapped; fault generator `--list` | NOT STARTED |
| S1b (WP-A) | pipeline-builder | S0 | S1a, S1c | `python -m src_scenario.run --backfill --db <scratch>` exit 0; unit tests; git diff of frozen paths empty | NOT STARTED |
| S1c (WP-B) | pipeline-builder (2nd instance) | S0, D1 | S1a, S1b | unit tests: promote without approver refused, incident read-only + escalates | NOT STARTED |
| S2 (WP-C) | pipeline-builder | S1a, S1b (S1c for its own tests) | none | `pytest scenario/ecommerce/tests` green, nothing loosened | NOT STARTED |
| S3 | evaluator | S2, S1c | none | independent recomputation, R1..R23 table, hard gates, PASS/VETO + untested failure mode | NOT STARTED |
| S4 | deployment | S3 = PASS | S6 | `src_scenario.release stage` then `status`; nothing promoted | NOT STARTED |
| S5 | orchestrator + human | S4 | none | present staged release + evidence; ask for a NAMED approver; with none, STOP and record "awaiting approver" | NOT STARTED |
| S6 | incident | S3 = PASS, S1a fault generator, S1c | S4 | control OK; F1..F5 match oracle; ESCALATED respected; inputs/releases untouched | NOT STARTED |
| S7 | evaluator | S4, S6 | none | raw sha256 unchanged, git status clean, `current.json` unchanged, outputs only in `.incident/` and `releases/` | NOT STARTED |
| S8 | orchestrator | S7 | none | PLAN.md state/decisions/open questions updated | NOT STARTED |

Parallel groups: {S1a, S1b, S1c} then S2, S3 sequential, then {S4, S6} in parallel, then S5, S7, S8 sequential.
Failure routing: evaluator VETO or deployment smoke failure goes back to builder via S2 and re-runs S3 onward. Guardrail block or escalation from any agent: stop and surface to the human, do not route around.

## Expected end state of this run if everything works
Release STAGED, not promoted. Gate S5 ends at "awaiting named approver" because `architecture/TEST-APPROVAL.md` says none will be supplied. Production-gate evidence comes from builder unit tests
(refusal and unchanged pointer), the evaluator's read of them, and `status` showing no live release. The gate is not exercised by running `promote`.

## Briefs
`scenario/ecommerce/briefs/{quality-engineer,pipeline-builder,evaluator,deployment,incident}.md`

## Findings about the agent definitions (so far, from reading them)
1. Orchestrator lists Agent but nested spawning is unavailable here; plan hands off to the dispatcher.
2. `deployment`/`incident` definitions and CLAUDE.md commands point to `src.deploy` / `src.incident_cli`, which only support the toy pipeline (`mart_daily_revenue`, single orders.csv). Not usable for ecommerce (D1).
3. `evaluator` steps 1-3 (`src.evals`, `evals/history.jsonl`) score the toy diagnoser, so a pass there is not evidence about this pipeline; step 6 `src.conformance` needs a dbt project and is N/A per ADR-002. No scenario-level scorecard exists; the evaluator brief substitutes independent recomputation.
4. `quality-engineer` says to write checker logic in `src/conformance.py`, conflicting with R23 (D3).
5. `evaluator` has no Write tool, `deployment` and `incident` have no Write/Edit: reports return as text and the orchestrator persists them. Held-out scenarios proposed by the evaluator cannot be saved by it.
6. `pipeline-builder` and `quality-engineer` both write tests; the brief splits dirs (`tests/unit` vs `tests/arch`) to keep QE independent. The builder needs interface names; the QE gets them only from the architecture (some are gaps, see D2).
7. Approver gate in `src/deploy.py` only checks the approver string is non-empty; any string is accepted, so "named human" is a procedural rule, not enforced. Same for the scenario tool if it copies the semantics.
8. Incident definition expects PROPOSED fixes, but this scenario has no remediation actions in the closed set, so only OK/ESCALATED are expected.
9. Orchestrator step 6 says "after deployment, hand failures to incident", but nothing is promoted in this run, so incident runs against fault-injected inputs, not a live release.
10. Architecture UNKNOWN: count of quarantined payments for negative-quantity orders; tests must assert invariants, builder records the real number.

## Open questions (this scenario)
- D1, D2, D3 above. Q1..Q17 from the architecture remain open (assumptions, not sign-off).
- Is a CLI-level refusal probe of `promote` (no approver, scratch release root) acceptable as gate evidence, or only the unit test? The plan uses only the unit test.

### Dispatcher decisions (e2e validation, 2026-11-21) - answering D1-D3
- D1 ACCEPTED: pipeline-builder WP-B writes `scenario/ecommerce/src_scenario/release.py` and `incident.py` with the same semantics as `src/deploy.py` / `src/incident_cli.py` (approver gate, atomic pointer, smoke, rollback). `src/` stays untouched (R23). The toy tools are NOT run against the ecommerce CSVs.
- D2 ACCEPTED: CLI hooks `--raw-dir`, `--db` and a non-zero exit code when a run is STOPPED are added to the architecture contract as test hooks (proposal by orchestrator, accepted by dispatcher; recorded as a post-approval clarification, not a design change).
- D3 ACCEPTED: the quality-engineer works only under `scenario/ecommerce/tests/`; the agent-definition instruction to extend `src/conformance.py` is overridden by R23/ADR-002 for this run.
- Nested delegation unavailable inside subagents: the dispatcher (this harness session) launches each specialist as an isolated general-purpose run that is given the agent definition file as its instructions. This is NOT the native Claude Code `.claude/agents` mechanism; reported as such.

### S8 update (orchestrator step, written by dispatcher), 2026-11-21
- State: build tag e2e-build-v2 evaluator PASS (204 tests); local release staged, NOT promoted (approver UNSET); incident control OK + 6 faults ESCALATED.
- Open: NEEDS-ADR (freshness on rerun >24h; reverting to an older file); remediation actions + evaluator revalidation BLOCKED; src.conformance BLOCKED (no dbt project); held-out scenarios unexecuted.
- Full evidence: evals/end-to-end-validation-report.md
