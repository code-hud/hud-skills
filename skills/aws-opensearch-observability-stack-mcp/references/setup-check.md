# Setup check

Run this when a playbook's first call fails, or before the first investigation on a new machine. Stop at the first failing step and report it.

## 1. Endpoint

Call `list_opensearch_ui_endpoints`.

- One entry: you can omit `endpoint` on every other tool.
- Several entries: pick the one the user names and pass its `id` as `endpoint` on every call.
- Empty list: ask the user for their OpenSearch UI URL (`application-<id>.<region>.opensearch.amazonaws.com`). In an interactive client, call `opensearch_login` with that URL as `endpoint`. In a headless run, ask them to set `OS_UI_ENDPOINT` for the MCP server.

## 2. Workspace and data sources

Call `list_workspaces`, then `list_data_sources`.

- You need one entry with `kind: data-connection` and type `Prometheus`. Its `id` is `<prom>` for every PromQL call. AMP is only available in an Observability workspace.
- You need one entry with `kind: data-source` (the OpenSearch cluster). If there is exactly one, PPL tools resolve it on their own.

## 3. Hud metrics

Call `describe_metrics` with `dataSourceId: <prom>`, `kind: metrics`, `keyword: hud_`, `timeRange: 24h`.

Expect `code_function_calls_total`, `code_function_errors_total`, `code_function_call_duration`, `code_flow_duration_seconds` and `hud_flow_issues_total`, or close variants (OTLP→AMP translation can add a `_total` suffix). Use the returned names from now on. An empty list means no Hud metrics reached AMP in the last 24 hours: check the Hud SDK's OTLP exporter and the AMP remote-write pipeline.

## 4. Hud forensics

Find the index that holds Hud forensics first — there is no fixed name. List the data source's indices with `list_index_patterns` (or `cluster_diagnostics`, `kind: cat-indices`) and pick the one carrying OTLP log records; call it `<forensics-index>`. Confirm its fields with `describe_fields` (`dataset: <forensics-index>`, `keyword: attributes`). Fields appear in **one of two conventions** depending on indexing — resolve which the index exposes and use it throughout:

- **Dotted:** `attributes.hud.forensic.type` / `attributes.trigger_type`, `attributes.http.route`, `attributes.exception_type`, `attributes.hud.issue.fingerprint`; service `resource.attributes.service.name`.
- **`@`-flattened:** `log.attributes.hud@forensic@type` / `log.attributes.trigger_type`, `log.attributes.http@route`, `log.attributes.exception_type`, `log.attributes.issue@fingerprint`; service `serviceName`.

Then count forensics over the last 24 hours with `ppl_query`, grouped by both kind fields (a record sets one or the other; use the convention your index exposes). The count must be non-zero:

```ppl
source = <forensics-index> | where `time` > DATE_SUB(NOW(), INTERVAL 24 HOUR) | stats count() as records by `attributes.hud.forensic.type`, `attributes.trigger_type`
```

Zero rows with metrics present usually means the SDK's log exporter is off, or you picked the wrong index — re-list with `cluster_diagnostics` (`kind: cat-indices`) to see which indices exist.

## 5. Access errors

- **"SSO sign-in is required"** in an interactive client: call `opensearch_login` once, then retry. Never call it before a tool asks.
- **403 / AccessDenied in a headless run** (CI, GitHub app, sandbox): `opensearch_login` needs a browser, so it won't work. The credentials the MCP server runs with need SigV4 access: `es:ESHttpGet` and `es:ESHttpPost` on the OpenSearch UI application, plus `es:GetApplication`, `es:DescribeApplication` and `es:ListApplications`. Tell the user which permission is missing.
- **PromQL errors about the data source**: you probably passed an OpenSearch `data-source` id. Use the `data-connection` name from step 2.

## Result

Report the endpoint, `<prom>`, the Hud metric names you found, and the 24-hour investigation count. Then continue with the playbook the user asked for, from the [index](index.md).
