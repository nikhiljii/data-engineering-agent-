---
name: finops
description: Cost model and tuning for pipelines and warehouses. Use for cost or performance work.
---

# FinOps for data

1. **Measure before tuning:** bytes scanned, runtime, slot or credit use per model or flow.
2. **Biggest levers:** partition and cluster pruning, incremental instead of full refresh, view versus table choice, right-sized compute, auto-suspend, retention of raw and temp data.
3. **Attribute spend** with tags per project and flow.
4. **Quote savings as ranges** with the method, never as certainties.
5. **Guardrail:** no saving that weakens a quality contract or raw retention.
