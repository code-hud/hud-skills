# Investigate Flow Errors with Hud (OpenSearch + Prometheus)

> **Read \`data-model\` first.** It defines the two stores (metrics = Prometheus/PromQL, forensics = OpenSearch/PPL), the metric/label and forensic field names and how to resolve their live spelling, the scope rules (service + time + \`deployment_environment_name\`), and the error-rate definitions (\`endpoint_error_rate\` / \`function_error_rate\` parity, incl. the SQS messages denominator). Apply all of it as you investigate.

Investigate service endpoint errors - HTTP 5xx, GraphQL, gRPC, Temporal workflow/activity, queue, or custom flow errors - by any filter: timeframe, status code, exception, or involved functions.

## Investigation essentials
- Use **metrics** for the error *rate / counts / trends*, and **forensics** for *why it failed / what threw* (one sampled \`hud.investigation\` record per flow; the full trace - request/response bodies, params, machine metrics, outbound calls, stack - is on \`body\`).
- **Always separate two error rates** (exact queries in the schema and in example 1 below):
  - **Total endpoint error rate** = errored flows / volume - how broken the endpoint is overall.
  - **Per-issue error rate** - a single endpoint in one window usually mixes several **distinct issues** (\`issue_fingerprint\`), each with a different root cause, exception and message. Break the total down per issue: each issue's **share of the endpoint's errors** and its **own rate** (issue / volume). These decompose the total, so you can tell whether one root cause dominates or many add up.
  - Report **both**: the total says how bad the endpoint is; the per-issue split says what's driving it.
- Forensic records carry the error signature id directly as \`attributes.hud.issue.fingerprint\` (the same value as metrics \`issue_fingerprint\`) - cluster by it, and reference a specific record by its \`_id\`. The throwing class + function are in \`attributes.hud.issue.sources\` (\`ExceptionClass@file:function\`); for caught 4xx there's no exception, so read the outcome from \`body\` (\`caught\`, \`errorBreakdown.framework_result\`).
- **Filter to error forensics** when chasing a specific issue - match the kind field \`attributes.hud.forensic.type = 'error'\`. \`duration\` (slow) and \`baseline\` (healthy control) records carry no error signature and would pollute your clustering. Use a \`baseline\` record (\`attributes.hud.forensic.type = 'baseline'\`) of the same flow as a **healthy control** to diff against a failing one when the root cause isn't obvious (example 5).

## Workflow
The analysis workflow has three steps:
1. Identify error patterns - the affected endpoints and their error rate, then decompose each endpoint's **total** error rate into its **per-issue** rates (\`issue_fingerprint\`) to see whether one root cause dominates or many issues add up (use **metrics** for the true rates, **PPL** to cluster/identify the issues).
2. Find forensic records that match those signatures or specific endpoints/exceptions.
3. Review 2-3 forensic records per signature to find the common pattern and the root cause.

## Query Examples

### 1. Endpoint error rate - total vs per-issue (PromQL - metrics MCP, `metrics_query`)
**Total error rate** - errored flows / flow volume (any flow type):
\`\`\`promql
sum by (code_flow_name) (rate(hud_flow_issues_total{service_name="<service>", deployment_environment_name="production"}[5m]))
/
sum by (code_flow_name) (histogram_count(rate(code_flow_duration_seconds{service_name="<service>", deployment_environment_name="production"}[5m])))
\`\`\`
HTTP equivalent - 5xx (status >= 500) responses / all flows:
\`\`\`promql
sum by (code_flow_name) (histogram_count(rate(code_flow_duration_seconds{service_name="<service>", deployment_environment_name="production", http_response_status_code=~"5.."}[5m])))
/
sum by (code_flow_name) (histogram_count(rate(code_flow_duration_seconds{service_name="<service>", deployment_environment_name="production"}[5m])))
\`\`\`
SQS divides by messages instead of flows:
\`\`\`promql
sum by (code_flow_name) (rate(hud_flow_issues_total{service_name="<service>", deployment_environment_name="production", code_flow_type="sqs"}[5m]))
/
sum by (code_flow_name) (rate(hud_flow_messages_total{service_name="<service>", deployment_environment_name="production", code_flow_type="sqs"}[5m]))
\`\`\`
**Per-issue breakdown** for one endpoint - a window usually mixes several distinct issues (\`issue_fingerprint\`), each a different root cause/exception. \`hud_flow_issues_total\` carries \`issue_fingerprint\` (counted once per errored flow), so the pieces add up cleanly.
Each issue's **share of the endpoint's errors** (issue / errors - sums to 1 across issues):
\`\`\`promql
sum by (code_flow_name, issue_fingerprint) (rate(hud_flow_issues_total{service_name="<service>", deployment_environment_name="production", code_flow_name="http|<path>|post"}[5m]))
/ on (code_flow_name) group_left
sum by (code_flow_name) (rate(hud_flow_issues_total{service_name="<service>", deployment_environment_name="production", code_flow_name="http|<path>|post"}[5m]))
\`\`\`
Each issue's **own error rate** (issue / invocations - these sum to the total error rate above):
\`\`\`promql
sum by (code_flow_name, issue_fingerprint) (rate(hud_flow_issues_total{service_name="<service>", deployment_environment_name="production", code_flow_name="http|<path>|post"}[5m]))
/ on (code_flow_name) group_left
sum by (code_flow_name) (histogram_count(rate(code_flow_duration_seconds{service_name="<service>", deployment_environment_name="production", code_flow_name="http|<path>|post"}[5m])))
\`\`\`
Report **both** the total and the per-issue split. \`issue_fingerprint\` is internal - don't surface it; resolve each dominant issue to its exception / message / throwing function via the forensics (step 2) before reporting.
Function-level errors (errors/s by function and type):
\`\`\`promql
sum by (code_function_name, code_file_path, error_type) (rate(code_function_errors_total{service_name="<service>", deployment_environment_name="production"}[5m]))
\`\`\`

### 2. Cluster errors into signatures (PPL - OpenSearch MCP `ppl_query`, POST /_plugins/_ppl)
> Examples use the dotted field convention; if \`describe_fields\` shows the \`@\`-flattened form, substitute (e.g. \`attributes.hud.forensic.type\` → \`log.attributes.hud@forensic@type\`, \`resource.attributes.service.name\` → \`serviceName\`). See data-model for the full mapping.
\`\`\`ppl
source=<forensics-index>
| where \`attributes.hud.forensic.type\` = 'error'
| where \`resource.attributes.service.name\` = '<service>'
| where time > DATE_SUB(NOW(), INTERVAL 1 HOUR)
| stats count() as errors, max(time) as last_seen by \`attributes.hud.issue.fingerprint\`, \`attributes.http.response.status_code\`, \`attributes.hud.issue.sources\`, \`attributes.code.flow.name\`
| sort - errors
| head 20
\`\`\`
Forensics are **sampled** - take the true error frequency from the metrics in step 1, not from counting records.

### 3. Find forensic records for a signature / endpoint (PPL)
\`\`\`ppl
source=<forensics-index>
| where \`attributes.hud.forensic.type\` = 'error'
| where \`resource.attributes.service.name\` = '<service>'
| where \`attributes.hud.issue.fingerprint\` = '<fingerprint>' and \`attributes.http.route\` = '<path>'
| sort - time
| head 3
| fields _id, time, \`attributes.http.response.status_code\`, \`attributes.hud.issue.sources\`, \`attributes.code.flow.name\`, body
\`\`\`
\`_id\` is the record's unique id - use it to look up or dedupe a specific trace. \`body\` is the full investigation as JSON - slice it with \`substring(body, <start>, 150)\` (tools truncate long values at ~180 chars).

### 4. Error trend over time (choose one surface)
PromQL range query (metrics MCP) on the step-1 5xx ratio, **or** a PPL bucketed count:
\`\`\`ppl
source=<forensics-index>
| where \`attributes.hud.forensic.type\` = 'error'
| where \`resource.attributes.service.name\` = '<service>'
| stats count() as errors by span(time, 1h) as bucket, \`attributes.hud.issue.fingerprint\`
| sort bucket
\`\`\`

### 5. Compare against a healthy baseline (optional - only if the error forensics don't reveal the root cause)
Pull a \`baseline\` record for the **same flow** and diff its \`body\` against the failing one to spot what differs (request/params, \`outbounds.*\`, \`context.machine_metrics.*\`):
\`\`\`ppl
source=<forensics-index>
| where \`attributes.hud.forensic.type\` = 'baseline'
| where \`resource.attributes.service.name\` = '<service>' and \`attributes.http.route\` = '<path>'
| sort - time
| head 3
| fields _id, time, \`attributes.http.response.status_code\`, body
\`\`\`

## Forensics Analysis
Each matched record's \`body\` is the whole investigation as a **JSON string** (a single \`string\` field, not an object — \`body.key\` won't resolve). Tools truncate long field values (~180 chars), so slice \`body\` with \`substring(body, <start>, 150)\` (check \`length(body)\` first) to read it in full. Go straight to the keys that answer your question:

> **Two tools extract values out of \`body\` (or any string field):** **\`parse <field> '<regex>'\`** to extract then aggregate (e.g. count errors by exception class) — the regex must match the **whole** value, so pad both ends with \`.*\`: \`parse body '.*"name":"(?<exc>[A-Za-z]+Error).*' | stats count() as c by exc\` (unpadded fragment → blank column, no error; \`.\` doesn't cross newlines). **\`locate\` + \`substring\`** when the value has no clean regex shape or you're carving a region out of a large/multiline body: \`eval p = locate('"name":"', body) + 8 | eval rest = substring(body, p, 80) | eval val = substring(rest, 1, locate('"', rest) - 1)\`. See data-model for details.

\`body\` has a stable shape — jump straight to the key for your question (if a key isn't there, example 3 returns \`body\` to re-check the shape):

| To answer… | Read in \`body\` |
|---|---|
| Which endpoint failed, and how? | \`context.type\` · \`context.method\` · \`context.route\` · \`context.status_code\` |
| What did the caller send and get back? | \`context.request_body\` · \`context.query\` · \`context.params\` · \`context.response_body\` |
| What threw, and where in the code? | \`exceptions.0.name\` · \`.message\` · \`.stackTrace\` · \`.executionFlow\` · \`totalExceptionsCount\` |
| Did a dependency cause it (DB/API call)? | \`outbounds\` · \`totalOutboundsCount\` (+ \`outboundAggregation\`) |
| Was the host unhealthy at the time? | \`context.machine_metrics\` (cpu_usage, memory_usage, event_loop_utilization) · \`context.system_info\` |
| Was it caught, and by what? | \`caught\` · \`errorBreakdown.framework_result\` |
| Which external trace to correlate logs on? | \`context.observability_identifiers\` (\`otel_trace_id\`, \`amazon_trace_id\`, \`datadog_trace_id\`/\`_span_id\`, \`jaeger_trace_id\`; b3 in \`b3_propagation\`, w3c in \`w3c_trace_context\`) |

To filter by body content, full-text match the JSON, e.g. \`| where body like '%"status_code":502%'\`.

## Analysis Questions
1. Do you see something in the forensics that can explain the error scenario and root cause?
2. Can you find specific patterns or clusters where this error is more common? e.g. specific flows, specific request params, etc.
3. Is this error caused by the service it popped on, or does it originate in an internal service or outbound request (e.g. DB queries, API calls)? Check the \`outbounds.*\` and machine metrics in the body.
   - When it's unclear, pull a healthy \`baseline\` record of the same flow (example 5) and diff it against the failing error: what's different in the request/params, the \`outbounds.*\` (a missing/slow/failed dependency call), or \`context.machine_metrics.*\`?
4. Look at the code - can you spot the exact spot where the error happens (use \`attributes.hud.issue.sources\` and the \`body\` stack trace)? Is there a fix that solves the root cause, not just "hides" it?
5. If logging systems MCPs are available (e.g. Datadog, Coralogix, etc.) - correlate by **trace id**: take it from the forensic's \`context.observability_identifiers.*\` (e.g. \`otel_trace_id\`, \`datadog_trace_id\`/\`datadog_span_id\`, \`jaeger_trace_id\`, \`amazon_trace_id\`, or b3 \`trace_id\`) and query the matching vendor's MCP for that exact trace to pull the request's logs.

## Guidelines

Your analysis should include 3 tiers:
1. Understand the issue and its characteristics
2. Find the root cause
3. Suggest a fix

Start with Tier 1, don't immediately try to find the root cause.
After you complete Tier 1 (and write the results to the user) - you can try to also provide the next tiers.
However - The additional tiers are not a must, do it only if you think you can provide *reliable and correct* results.
Make sure to suggest a root cause that makes sense based on all the data you have access to.
If you are not sure - just don't suggest anything, be humble.
Keep your answers simple, short, and direct. No fluff, no crap, be as professional as possible.
