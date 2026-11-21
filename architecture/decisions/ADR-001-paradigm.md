# ADR-001: Data movement paradigm for ecommerce-daily-orders

Status: PROPOSED (needs human approval before any build)

## Decision

**ELT**

## Scores

ELT 7 vs ETL 0 (hybrid margin 3)

## Reasons

- [ELT +3] embedded analytical engine runs SQL transforms in-process at no extra cost
- [ELT +2] raw data must be kept for reprocessing and audit
- [ELT +2] transformations are SQL and the team is SQL-strong

## Consequences

- Land raw data first, transform in the warehouse; use the `pattern-elt` skill.
- Any change to this decision needs a new ADR that supersedes this one.

## Architect review (added by the architect, scorer output above is unchanged)

Status of this section: PROPOSED, same approval gate as above.

I agree with ELT. Reasons beyond the scorer's:
- All defects (duplicates, invalid dates, negative quantities, orphans) must stay reproducible
  (requirements A-15), so raw must land untouched and every correction must be a visible SQL rule
  downstream. That is ELT's strength; ETL would hide the repairs in a pre-load stage.
- Volume is about 6k rows per file; there is no scale argument for a separate transform engine.

Two observations about the scorer's reasons, neither changes the verdict (ELT 7 vs ETL 0; without
the third reason it would still be ELT 5 vs ETL 0, above the margin of 3):
- Reason 3 prints "the team is SQL-strong", but the profile says `sql_strength: medium` (a placeholder,
  requirements open question 6). The +2 fires because `transforms.style` is `sql`. The wording
  overstates what we know; it is not a basis for the decision.
- `pii: true` with `mask_before_landing: false` means the scorer's ETLT path is not triggered. I
  accept that for this synthetic, local-only scenario but do not rely on the flag alone: ADR-004
  prunes the PII columns (customer_name, email) at the raw landing boundary anyway, which is a
  light ETLT-style step. If the human answers open question 5 with "masking required", this
  ADR must be superseded by an ETLT decision.
