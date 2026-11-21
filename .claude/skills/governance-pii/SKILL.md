---
name: governance-pii
description: PII classification, masking, access and GDPR basics for pipelines. Use when a source is sensitive or columns are added.
---

# Governance and PII

1. **Classify** every column: public, internal, confidential, PII. Unknown means PII until proven otherwise.
2. **Mask where the profile says.** If `mask_before_landing` is true, mask or tokenize before raw. Hashing without salt is not masking for low-cardinality values.
3. **Minimize.** Do not carry PII to marts unless a consumer needs it; document the need.
4. **Access.** Least privilege by role; PII columns restricted; access reviewed on change.
5. **Retention and deletion.** Record retention per table and how a deletion request propagates.
6. **Lineage.** Every table traces to its sources in the catalog.
7. **Not legal advice.** Legal basis questions go to a human.
