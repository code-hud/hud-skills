---
name: aws-opensearch-observability-stack-mcp
description: Use when investigating production errors, slow endpoints, or deployment regressions with Hud runtime data (hud_* metrics in Prometheus/AMP, hud.investigation forensics in OpenSearch) through the AWS OpenSearch Observability MCP app.
---

# Hud on the AWS OpenSearch Observability MCP app

The playbooks for this stack are served from skills.hud.io, not from this
skill. They change without notice, so read them fresh every time.

1. Before you query anything, fetch https://skills.hud.io/aws-opensearch-observability-stack-mcp?via=skill-v1. Use
   `curl -sfL <url>` if you have a shell, otherwise your web-fetch tool.
2. Read the page, then follow its playbook index. The page sets the query
   rules, including the read-only rule.
3. Re-fetch it in each session. Do not cache it across sessions.
4. If the fetch fails, tell the user that Hud's playbooks are unreachable
   and stop. Do not write queries from memory.
