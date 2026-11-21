---
name: observability-slo
description: Freshness, volume and latency monitors tied to SLAs. Use when defining monitors and alerts.
---

# Observability and SLOs

1. **Start from the SLA** in `project-profile.yaml`; every monitor traces to one.
2. **Four basics:** freshness (max age), volume (row count versus recent baseline), schema (columns and types), run health (failures, duration).
3. **Thresholds with numbers,** an owner, and a runbook link. An alert with no action is noise.
4. **Severity:** page only on consumer-visible breach; ticket the rest.
5. **Prove it fires** with a seeded fault before trusting it.
