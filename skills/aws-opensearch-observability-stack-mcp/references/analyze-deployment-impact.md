# Analyze Deployment Impact with Hud (OpenSearch + Prometheus)

> **Read \`data-model\` first.** It defines the two stores (metrics = Prometheus/PromQL, forensics = OpenSearch/PPL), the metric/label and forensic field names and how to resolve their live spelling, the scope rules, and how a deploy shows up in the data (a new \`hud_function_content_hash\` per changed function, plus the \`deployment_id\` session tag when the app sets it). Apply all of it as you analyze the deployment's impact.

Investigate whether a deployment caused performance degradation or increased errors by comparing metrics before and after the change. There is **no deployment table** - the deploy is identified from the data itself (see \`data-model\`).

## Critical Warning

**Do not automatically blame deployments.** Many performance issues coincide with deployments but have unrelated root causes:
- Traffic spikes or pattern changes
- Infrastructure issues (CPU, memory, network)
- External dependency slowdowns
- Cache warmup effects
- Database query changes

**Always verify**: Did the affected functions actually belong to the deploy (same \`deployment_id\`, or a new \`hud_function_content_hash\`)? Do the code changes logically explain the observed impact? A hash *first seen* in the window may just be a code path that ran for the first time (e.g. an error handler during an incident), not a deploy — corroborate with the actual metric change (and \`deployment_id\` when present).

## Workflow

1. **Identify the deployment** - find the \`deployment_id\` (if set) and/or the functions with a new \`hud_function_content_hash\`, and when it appeared.
2. **Compare before/after metrics** - quantify latency / error-rate / throughput change across the deploy time.
3. **Verify the relationship** - confirm the affected functions actually changed and run in the affected endpoints.
4. **Review code changes** - if you have a Git MCP, do the changes match the observed behavior?
5. **Rule out alternatives** - traffic, infrastructure, or dependencies?

## Query Examples

Run every PromQL query below through `metrics_query` (pass `dataSourceId` = the Prometheus `data-connection` id); the forensics block in step 6 uses `ppl_query`. Both are tools on the same MCP. Keep windows scoped — AMP caps a query at 30 s / 1440 points (15 s min step).

### 1. Find recent deployments for a service (PromQL)
If the app sets \`deployment_id\`, list the recent deploys (a range query shows when each \`deployment_id\` first appeared = deploy time):
\`\`\`promql
count by (deployment_id) (code_function_calls_total{service_name="<service>", deployment_environment_name="production"})
\`\`\`
Functions whose source hash is newly seen (works with or without \`deployment_id\`) - a new hash series appearing marks the deploy:
\`\`\`promql
count by (code_function_name, code_file_path, hud_function_content_hash) (code_function_calls_total{service_name="<service>", deployment_environment_name="production"})
\`\`\`

### 2. Compare endpoint performance before/after deployment (PromQL \`offset\`)
Pick an \`offset\` equal to the gap between your "after" and "before" windows (e.g. deployed ~1h ago -> compare now vs \`offset 1h\`). p90 latency ratio per endpoint (>2 means p90 doubled):
\`\`\`promql
(
  histogram_quantile(0.9, sum by (code_flow_name) (rate(code_flow_duration_seconds{service_name="<service>", deployment_environment_name="production"}[10m])))
/
  histogram_quantile(0.9, sum by (code_flow_name) (rate(code_flow_duration_seconds{service_name="<service>", deployment_environment_name="production"}[10m] offset 1h)))
)
\`\`\`
Absolute p90 for either side (drop/keep \`offset\`):
\`\`\`promql
histogram_quantile(0.9, sum by (code_flow_name) (rate(code_flow_duration_seconds{service_name="<service>", deployment_environment_name="production"}[10m])))
\`\`\`
5xx error-rate change: run the HTTP 5xx ratio (\`histogram_count\` on \`code_flow_duration_seconds\` with \`http_response_status_code=~"5.."\`; see the investigate-flow-error skill) for the "after" window and again with \`offset\`. For exact windows, instead run two **instant** queries at the before/after timestamps (pass \`time\`).

When \`deployment_id\` is set, prefer it over a time \`offset\` for the split: compare the deploy to the previous one by adding \`deployment_id="<new-sha>"\` vs \`deployment_id="<prev-sha>"\` to the matchers (same window), instead of now-vs-\`offset\`. Add \`deployment_id="<sha>"\` to any matcher below to scope a query to one deploy.

### 3. Compare function performance before/after deployment (PromQL)
Function p90 by version - the new hash sits next to the old one, so the slow version is obvious without a JOIN:
\`\`\`promql
histogram_quantile(0.9, sum by (code_function_name, code_file_path, hud_function_content_hash) (rate(code_function_call_duration_seconds{service_name="<service>", deployment_environment_name="production"}[10m])))
\`\`\`
Function error rate (errors / calls), matched on the shared labels:
\`\`\`promql
sum by (code_function_name, code_file_path) (rate(code_function_errors_total{service_name="<service>", deployment_environment_name="production"}[10m]))
/
sum by (code_function_name, code_file_path) (rate(code_function_calls_total{service_name="<service>", deployment_environment_name="production"}[10m]))
\`\`\`
Throughput over the window: \`increase(code_function_calls_total{service_name="<service>", deployment_environment_name="production"}[10m])\`.

### 4. Identify which deployed functions actually degraded (PromQL)
Rank functions by current p90, grouped so each function shows per \`hud_function_content_hash\`:
\`\`\`promql
topk(20, histogram_quantile(0.9, sum by (code_function_name, code_file_path, hud_function_content_hash) (rate(code_function_call_duration_seconds{service_name="<service>", deployment_environment_name="production"}[10m]))))
\`\`\`
Then keep only functions whose current hash is newly seen and that have meaningful traffic:
\`\`\`promql
sum by (code_function_name, code_file_path) (increase(code_function_calls_total{service_name="<service>", deployment_environment_name="production"}[10m])) > 100
\`\`\`

### 5. Confirm the impact reached the endpoint (PromQL)
Function series carry \`code_flow_name\` / \`http_route\` (the flow the function ran in), so you can tie a degraded function to the endpoints it runs in:
\`\`\`promql
sum by (code_function_name, code_flow_name) (increase(code_function_calls_total{service_name="<service>", deployment_environment_name="production", code_function_name="<function>", code_file_path="<file>"}[10m]))
\`\`\`
Then re-check those flows' own p90 / error rate (query #2) to confirm the degradation surfaced there.

### 6. Forensics around the deploy (PPL - optional, OpenSearch MCP)
Error and slow-request forensics after the deploy time, scoped to the service, environment and time window. Scope to forensics by matching the kind field (\`attributes.hud.forensic.type\`; see data-model), and keep both the \`error\` and \`duration\` kinds so a deploy that made the flow slower *or* more error-prone shows up (dotted field names are examples — resolve the live spelling per data-model):
\`\`\`ppl
source=<forensics-index>
| where \`attributes.hud.forensic.type\` in ('error', 'duration')
| where \`resource.attributes.service.name\` = '<service>'
| where \`resource.attributes.deployment.environment.name\` = 'production'
| where time >= '2026-01-27 10:00:00'
| stats count() as c by \`attributes.hud.forensic.type\`, \`attributes.code.flow.name\`, \`attributes.hud.issue.sources\`
| sort - c
| head 20
\`\`\`
Correlate a failing/slow forensic back to the metrics **by value** — \`attributes.code.flow.name\` ⇔ metrics \`code_flow_name\`, and the throwing class + function in \`attributes.hud.issue.sources\` (\`ExceptionClass@file:function\`) ⇔ \`code_function_name\` / \`code_file_path\` — to confirm a degraded function from query #4 is the one in the slow/failing traces (forensics carry no content hash). The p90 slowdown comes from the metrics (query #2), not from the forensics — the per-flow duration lives in \`body.duration\`, not a top-level field. If the app sets the \`deployment_id\` session tag, scope straight to the deploy by adding it as a field (confirm the exact name with \`describe_fields\`) instead of (or in addition to) the time filter. The MCP's trace widgets can't help here — Hud forensics are logs with no \`traceId\` (see data-model), so this step stays on \`ppl_query\`.

### 7. Present the verdict (interactive hosts)
In an interactive MCP host, render the before/after as a widget so the user sees it: `report_dashboard` `kind: comparison` (before vs after sides) or `kind: query` with the two `offset` series side by side. In a headless run, skip the widget and report the p90-ms / error-rate / throughput numbers from the text tools (and ignore the widget preamble).

## Guidelines

Be data-driven and skeptical. Present evidence objectively:
1. **Timeline** - When did the deploy land (a new \`deployment_id\` and/or a new \`hud_function_content_hash\`) and when did the issue occur?
2. **Metrics** - Quantify the performance change (p90 ms, error-rate, throughput), before vs after.
3. **Relationship** - Which newly-deployed functions are affected, and do they run in the affected endpoints?
4. **Evidence** - What proves causation beyond correlation (the deploy boundary lines up with the change; forensics name the new version)?
5. **Alternatives** - What else could explain this (traffic, infra, dependencies)?

Keep analysis concise and actionable. Avoid speculation without evidence.