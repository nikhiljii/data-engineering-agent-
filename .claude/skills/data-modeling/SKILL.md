---
name: data-modeling
description: Guidance for choosing and applying Kimball, Data Vault or flat modeling. Use when designing or changing the model layer.
---

# Data modeling

1. **Pick by need.** Kimball star schemas for BI consumers; Data Vault when many sources change often and auditability matters; flat wide tables only for a single, stable use.
2. **Grain first.** Write the grain and the primary key of each table before columns.
3. **Keys.** Surrogate keys for dimensions; keep natural keys; foreign keys declared in `architecture/foreign-keys.yaml`.
4. **History.** Decide per dimension: overwrite (SCD1) or keep history (SCD2 with valid_from, valid_to, is_current). Record the choice in the data architecture.
5. **Facts.** Additive measures; document units and currency; no mixed grains in one fact.
6. **Naming.** snake_case; `stg_` staging, `int_` intermediate, `dim_`/`fct_` marts.
