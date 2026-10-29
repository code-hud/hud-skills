---
name: aws-opensearch-observability-stack-mcp
description: Use when investigating production errors, slow endpoints, or deployment regressions with runtime-sensor data (hud_* metrics in Prometheus/AMP, hud.investigation forensics in OpenSearch) through the AWS OpenSearch Observability MCP app.
---

# Runtime sensor data on the AWS OpenSearch observability stack

Everything this skill needs is in this folder. It does not load instructions from
anywhere else.

## Playbooks

**Always start with [references/data-model.md](references/data-model.md)** — it describes how to
use Hud's data — then open the playbook that matches the task.

| Task | File |
| :-- | :-- |
| How to use Hud's data: stores, metric/field names, formulas | [references/data-model.md](references/data-model.md) |
| Understand an alert, find the root cause and solve it | [references/triage-hud-alert.md](references/triage-hud-alert.md) |
| Find which flows error, which function throws, and why | [references/investigate-flow-error.md](references/investigate-flow-error.md) |
| Did a deploy make things slower or more error-prone | [references/analyze-deployment-impact.md](references/analyze-deployment-impact.md) |
| Build an OpenSearch Dashboards link for the user | [references/create-dashboard-link.md](references/create-dashboard-link.md) |

## Updates

This skill is versioned and installed from the `code-hud/hud-skills` repository.
It never downloads, applies or checks for updates itself. If the user asks whether
a newer version exists, tell them they can run `npx skills@1.7.4 check` and, if
they want it, `npx skills@1.7.4 update aws-opensearch-observability-stack-mcp`.
The user decides whether to update.
