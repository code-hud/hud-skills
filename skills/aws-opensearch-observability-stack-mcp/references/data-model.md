# Hud Runtime Data Schema (OpenSearch + Prometheus / OTLP data plane)

Hud's runtime sensor collects endpoint- and function-level metrics, errors, and execution
traces from your services, split across **two stores**. Both are reached through the
AWS OpenSearch Observability Stack app that exposes two query tools over two data sources — pick
the tool that matches the question:

- **Metrics** (invocations, errors, latency, runtime/process health, as time series) live in
  **Amazon Managed Prometheus (AMP)**. Query with **PromQL** through `metrics_query`. Use for
  "how often / how fast / how many errors", trends, and rates.
- **Forensics** (per-request execution traces for errors and slow requests) live in
  **OpenSearch** (the index you discover as `<forensics-index>`; see setup-check). Query with **PPL**
  through `ppl_query`. The full trace — request/response bodies, params, stacks, outbound calls — is
  on the record's `body`. Use for "why did it fail / what threw".

**Names below are the expected post-translation form.** If a query returns no series or a
`FIELD_NOT_FOUND`, confirm the live name with `describe_metrics` (metrics) / `describe_fields`
(forensics) and substitute — OTLP→AMP translation can add a suffix (e.g. `_total`) and the semconv
migration may still expose older `hud_*` / `hud_http_*` names. `setup-check` resolves these once up
front; fall back to discovery only on an empty result.

**Don't use the MCP's `traces_*` widgets (including the cross-signal join) to join metrics and forensics:** they read a separate **span** index (`<spans-index>`), while Hud forensics are OTLP **logs** (`<forensics-index>`) with no populated `traceId`, so those widgets can't see or join Hud data. A correlating external trace id may still exist inside the S3 forensic blob at `context.observability_identifiers.otel_trace_id` (only when the app runs under an external tracer — OTel/Datadog/Jaeger/X-Ray).

## Core Concepts
Every series and trace is identified by **readable string labels/fields** — scope and
correlate by these:

- **Functions**: `code.function.name` + `code.file.path` (first-party) or + `hud.function.third_party_module` (instrumented third-party). `hud.function.content_hash` is the per-version id (a body edit / deploy = new value). PromQL labels: `code_function_name`, `code_file_path`, `hud_function_content_hash` (plus `hud_function_kind`, `hud_function_short_name`, `hud_function_scope`/`callee`/`literal`).
- **Flows**: entry points; `code.flow.type` is one of `http`, `graphql`, `kafka`, `sqs`, `queue`, `websocket`, `grpc`, `custom`. **Always identified by `code.flow.name`** (label `code_flow_name`, e.g. `http|/users/:id|get`). Variant labels enrich it: HTTP `http_route` + `hud_endpoint_methods`, GraphQL the operation, messaging the topic/queue, gRPC `rpc_method`.
- **Services**: `service.name` is an OTel **resource** attribute — label `service_name` on metrics, `resource.attributes.service.name` on forensics; `service.instance.id` per process; environment `deployment.environment.name` (label `deployment_environment_name`).
- **Session tags**: optional process key/value tags, carried as a label on every series and a field on every forensic (dots -> underscores). Canonical: **`deployment_id`** (e.g. the commit sha) — the **same value across every function in one deploy**, present only when the app sets it. Never surface its value.
- **Deployments**: not a stored entity — a deploy is read from the data itself. Each `hud_function_content_hash` marks a different deployed version of that function; the `deployment_id` session tag (when set) is the same for all functions in one deploy, so it identifies the whole deploy.
- **Issues (errors)**: a group of errors sharing a signature, carried as the **`issue_fingerprint`** label on `hud_flow_issues_total` (e.g. `e2a5edce718cf0e9`). **The same value is on the forensic log record** as `attributes.hud.issue.fingerprint`, so it is a direct metrics⇔forensics join key. A function's escaped exception class is `error_type` on `code_function_errors_total`.
- **Forensics**: sampled execution traces, OTLP log records (identity and discovery in Part B).

## Query Scope Rules
- Every query MUST be scoped — PromQL label matchers (`{service_name="…", deployment_environment_name="…"}`), PPL `where` — by **service**, **time** and **environment** (plus any other relevant dimension: flow, function). **Always pin one environment** (`deployment_environment_name` on metrics, `resource.attributes.deployment.environment.name` on forensics), defaulting to `production` unless the user asked for another; the sole exception is when you're **explicitly comparing across services or environments**, where that dimension becomes a **group-by** instead of a fixed matcher. Under-filtering returns INCORRECT data (series from different services or environments summed together).
- **Identity, scoping & matching** — scope and correlate across stores by **shared VALUES**. There are no numeric row ids. **Match by flow first** — it's the primary correlation key and the scope every query should carry; the error **`issue_fingerprint`** is the second key (a real shared value: metrics label ⇔ forensic `attributes.hud.issue.fingerprint`). Correlate by value:
  - flow (**primary**): identify a flow by **service + name + path** — forensics `attributes.code.flow.name` (+ `attributes.http.route` for HTTP) ⇔ metrics `code_flow_name` (e.g. `http|/path|get`) / `http_route` (the route path).
  - error (**secondary**): metrics `error_type` / `issue_fingerprint` ⇔ forensics `attributes.exception_type` / `attributes.hud.issue.fingerprint` (`issue_fingerprint` is the shared key).
  - function: metrics `code_function_name` + `code_file_path` ⇔ forensics `attributes.function_name` + `attributes.function_file`.
- Never query by route/function name without a service — the same name can exist in several services.

**Before/after a deploy:** split the two sides by the deploy's identity — `deployment_id="<sha>"` (metrics) / `attributes.deployment_id` (forensics) per side, or the old vs new `hud_function_content_hash` series side by side; with neither available, use a PromQL `offset` to line an earlier window up against the current one.

**Set-difference (`unless`/`and`/`or`): use instant selectors, not range vectors.** To find new or changed series — e.g. a function whose `hud_function_content_hash` is new after a deploy — diff two **instant** selectors: `count by (…) (metric{…}) unless count by (…) (metric{…} offset <pre-deploy>)`. Putting a range vector on both sides (`…[15m] unless …[15m] offset <pre>`) over a high-cardinality metric (hundreds of `code_function_*` series) makes AMP fail with a `503`/`500` `PromQLQueryError` instead of returning a result. If it still errors, run each side as its own instant query and diff the label sets yourself.

---

# Part A — Metrics (PromQL / Prometheus-Cortex)

Names below are the **stored (Prometheus) form**. **Counters end in `_total`**; the duration
metrics are OTLP **exponential (native) histograms** — they have **no `_bucket` /
`_sum` / `_count` series**, so read them with `histogram_quantile` / `histogram_count` /
`histogram_sum`. The flow histogram `code_flow_duration_seconds` is in **SECONDS** (`× 1000` for ms).
A **raw duration selector returns empty** on the AMP PromQL path (it renders
float only) — always wrap it in a reducer (the three above, or `histogram_avg` /
`histogram_fraction`). Counters are delta at the SDK and
cumulative in the store, so `rate()`/`increase()` work normally. Common resource labels on every series: `service_name`,
`deployment_environment_name`, `service_instance_id`, `telemetry_sdk_*`, plus `host_*` /
`cloud_*` / `k8s_*` when present.

### `code_function_calls_total` (counter)
Exact invocation count per function — the traffic measure and the denominator for function error rate. Identity/scoping labels: `code_function_name` + `code_file_path` (or `hud_function_third_party_module` for instrumented third-party), `hud_function_content_hash` (per-version id — new value on each deploy), and the flow it ran in (`code_flow_name`). Full label set (kind, short-name/scope/callee/literal, `code_function_caller_*` lineage) via `describe_metrics`.

### `code_function_call_duration` (native histogram, nanoseconds)
Function execution time. Same labels as calls. Observations are **sampled**, so the histogram count can be lower than `code_function_calls_total`. Query percentiles with `histogram_quantile` (native — no `le`, no `_bucket`).

### `code_function_errors_total` (counter)
Invocations an exception escaped. Same identity labels plus `error_type` (the exception class name, e.g. `TypeError`).

### `code_flow_duration_seconds` (native histogram, seconds)
Flow (endpoint) duration. Flows are **not** sampled, so the histogram **count is the exact number of flows** — use `histogram_count(...)` for request/flow volume. Labels: flow identity (`code_flow_type`, `code_flow_name`, `http_route`, `hud_endpoint_methods`, …) + per-request labels (`http_response_status_code`, `hud_flow_end_reason` for http/graphql; `rpc_response_status_code` for grpc; `messaging_destination_partition_id` for kafka) + distributed context (`hud_parent_service_name`, `hud_parent_flow_name`, `hud_root_service_name`, `hud_root_flow_name`).

### `hud_flow_issues_total` (counter)
Errored flows, counted **once per flow** under its `issue_fingerprint`. Labels: `issue_fingerprint` + flow identity + `http_response_status_code` + `hud_issue_caught` / `hud_issue_framework_result` / `hud_issue_user_error`. Use for error counts and to group distinct issues. "Errored" is **flow-type-specific** (HTTP ≥ 500, GraphQL exceptions + ≥ 500, else exceptions) **but also includes framework-caught HTTP 4xx** (e.g. `401`, `404`), so it's broader than 5xx — **always split by `http_response_status_code`** before calling something a server error. Divide by flow volume (`histogram_count(code_flow_duration_seconds)`), or by `hud_flow_messages_total` for SQS.

### `hud_flow_messages_total` (counter)
Messages handled by batch and queue flows. Flow identity + per-request + distributed-context labels.

### `hud_flow_e2e_duration` (native histogram, nanoseconds)
Time from a message's produce/enqueue timestamp until its flow ended (Kafka, SQS, queues).

### Runtime / process metrics
Per-process health (resource labels only), to answer "was the host saturated". Read utilization as a fraction, or usage against its limit: `process_cpu_utilization` and `nodejs_eventloop_utilization` (gauges `0..1`) — the latter has label `thread_name`; `process_memory_usage` vs `hud_process_memory_limit` and `process_cpu_time_total` vs `hud_process_cpu_limit` (bytes / CPUs). Exact names, units and the rest (`process_uptime`, `hud_v8js_memory_heap_*`) via `describe_metrics`.

## Hud metric formulas

**Percentiles** use native-histogram `histogram_quantile` (no `le`, no `_bucket`; `rate()` inside; `code_flow_duration_seconds` is in seconds, so `× 1000` for ms):
```promql
histogram_quantile(0.90, sum by (code_flow_name) (rate(code_flow_duration_seconds{service_name="<service>", code_flow_type="http"}[5m])))
```

**Flow / request volume** is the histogram count (flows are not sampled — do NOT look for a `_count` series):
```promql
sum by (code_flow_name) (histogram_count(rate(code_flow_duration_seconds{service_name="<service>"}[5m])))
```

**Error rate** — flow-type-specific (mirrors Hud's `endpoint_error_rate`); `hud_flow_issues_total` already encodes the per-type definition above, so dividing it by flow volume works for **every** type (for HTTP, `http_response_status_code=~"5.."` is the exact equivalent). The denominator is the flow count for every type **except SQS, which divides by messages** (`hud_flow_messages_total`):
```promql
sum by (code_flow_name) (rate(hud_flow_issues_total{service_name="<service>"}[5m]))
/
sum by (code_flow_name) (histogram_count(rate(code_flow_duration_seconds{service_name="<service>"}[5m])))
```

**Per-issue error rate** — a window usually mixes several distinct issues (`issue_fingerprint`, once per errored flow); decompose the total into each issue's own rate (they sum back to the total):
```promql
sum by (code_flow_name, issue_fingerprint) (rate(hud_flow_issues_total{service_name="<service>"}[5m]))
/ on (code_flow_name) group_left
sum by (code_flow_name) (histogram_count(rate(code_flow_duration_seconds{service_name="<service>"}[5m])))
```

**Function error rate** uses the exact `code_function_calls_total` as denominator (the duration histogram is sampled):
```promql
sum by (code_function_name, code_file_path) (rate(code_function_errors_total{service_name="<service>"}[5m]))
/
sum by (code_function_name, code_file_path) (rate(code_function_calls_total{service_name="<service>"}[5m]))
```

---

# Part B — Forensics (PPL / OpenSearch)

Forensic records are OTLP **log records** in your OpenSearch **domain** (managed cluster) or **collection** (Serverless) — the data source the MCP connects to. There is no fixed index name: discover the pattern that holds them (see setup-check) and use it as `<forensics-index>` in `source=`. A record's kind is tagged by **one of two fields** — some records set **`hud.forensic.type`** (`error` / `duration`), others set **`trigger_type`** (`Error` / `Duration` / `Baseline`) — so scope to forensics (with the service name set) by matching **either**.

**Field naming — two conventions.** Depending on indexing, fields are either **dotted** (`attributes.a.b.c`, service `resource.attributes.service.name`) or **`@`-flattened** (`log.attributes.a@b@c`, service `serviceName`). **Run `describe_fields` first, use whichever the index exposes** (the other is empty), and keep it throughout. Examples below use the dotted form — swap to the `@` twin if needed (e.g. `attributes.hud.issue.fingerprint` → `log.attributes.issue@fingerprint`). Backtick-quote any field with `.` or `@`.

## Forensic record fields
Each record is one sampled flow (an error, a slow request, or a healthy Baseline — see the two type fields below).
| Field (PPL path) | Description |
|---|---|
| `time` / `@timestamp` | Record timestamp (flow start), `date_nanos`; scope by it. Flow end = `time` + `attributes.duration_ms` |
| `attributes.hud.forensic.type` / `attributes.trigger_type` | Why the flow was sampled — a record sets **one** of these: `error`/`Error` (**failed**), `duration`/`Duration` (**slow**), or `Baseline` (a **healthy** control, see below). **Filter errors on both:** `` `…hud.forensic.type` = 'error' or `…trigger_type` = 'Error' ``. |
| `resource.attributes.service.name` | Service — primary scope |
| `attributes.code.flow.name` | The flow identifier (for HTTP, `type\|route\|method`, e.g. `http\|/forensics/search\|post`); same value as metrics `code_flow_name`. **Always include it.** |
| `attributes.http.route` / `attributes.http.request.method` | **`http.route` is the cross-store correlation key for HTTP** (⇔ metrics `http_route`); filter/group by it |
| `attributes.http.response.status_code` | HTTP status (numeric) — split server errors from framework-caught 4xx |
| `attributes.hud.issue.fingerprint` | Error signature id — **direct join key** to metrics `issue_fingerprint` |
| `attributes.exception_type` | First exception's class (empty for framework-caught 4xx) — matches metrics `error_type` |
| `attributes.function_name` / `attributes.function_file` | The throwing function — matches metrics `code_function_name` / `code_file_path` |
| `attributes.duration_ms` | Flow duration (float, ms) |
| `_id` | OpenSearch document id — reference or dedupe a record |
| `body` | The full investigation as **structured JSON** (see below) |

Other descriptive fields (`severityText`, `attributes.environment_name` / `service_runtime`, `resource.attributes.service.instance.id`, `attributes.flow_type`, `attributes.execution_flow`, `attributes.function_key`) are discoverable with `describe_fields`.

## Reading the forensic `body`
The `body` is the whole investigation as a **JSON object** (nested, not flat `key=value`) — read nested keys directly. Tools **truncate long field values in display** (~180 chars), so slice `body` into chunks with `substring(body, <start>, 150)` (check `length(body)` first) to read it in full.
| To answer… | Read in `body` |
|---|---|
| Which endpoint failed, and how? | `context.type`, `context.route`, `context.method`, `context.status_code` |
| What did the caller send and get back? | `context.request_body`, `context.query`, `context.params`, `context.response_body` |
| What threw, and where in the code? | `exceptions[]` (`.name`, `.message`, `.stackTrace`, `.executionFlow`), `totalExceptionsCount` — empty array when the request was framework-caught (e.g. a 401/404) |
| Did a dependency cause it (DB/API call, its duration)? | `outbounds[]`, `totalOutboundsCount` |
| Was the host unhealthy at the time? | `context.machine_metrics` (cpu_usage, memory_usage, event_loop_utilization), `context.system_info` |
| Was it caught, and by what? | `caught`, `errorBreakdown.framework_result` (e.g. `"401"`) |
| Which external trace to correlate logs on? | `context.observability_identifiers` (`otel_trace_id`, `datadog_trace_id`/`datadog_span_id`, `jaeger_trace_id`, `amazon_trace_id`, b3 `trace_id`) |

Notes: `exceptions` can hold several entries (`totalExceptionsCount`); in each `executionFlow`, index `0` is where the error occurred and the last entry is the top caller; `duration` keys are human-readable, `durationNs`/`durationThreshold` are numeric; not every forensic has `outbounds`. `Baseline` records (`attributes.trigger_type = 'Baseline'`) are healthy-run samples — pull one when you need a control to compare a failing forensic against.

## Querying forensics
Scope every forensic query by the kind fields (match both `hud.forensic.type` and `trigger_type`), the service and the time window:
```ppl
source=<forensics-index>
| where `attributes.hud.forensic.type` = 'error' or `attributes.trigger_type` = 'Error'
| where `resource.attributes.service.name` = "<service>"
| where time > DATE_SUB(NOW(), INTERVAL 1 HOUR)
| stats count() as errors by `attributes.hud.issue.fingerprint`, `attributes.http.response.status_code`, `attributes.code.flow.name`
| sort -errors
| head 20
```

**Read the full trace content** — `body` is JSON; pull a region out with `parse` (capture-group names must be alphanumeric), or slice it with `substring`:
```ppl
source=<forensics-index>
| where `resource.attributes.service.name` = "<service>" and `attributes.hud.issue.fingerprint` = "<fingerprint>"
| sort - time | head 1
| eval blen = length(body), p1 = substring(body, 1, 150), p2 = substring(body, 151, 150)
| fields _id, blen, p1, p2
```

**Cluster errors** into distinct issues — group by `hud.issue.fingerprint` (the direct signature id):
```ppl
source=<forensics-index>
| where (`attributes.hud.forensic.type` = 'error' or `attributes.trigger_type` = 'Error') and `resource.attributes.service.name` = "<service>"
| stats count() as c, values(_id) as sample_ids by `attributes.hud.issue.fingerprint`, `attributes.exception_type`, `attributes.code.flow.name`
| sort -c
```

Get error/traffic frequency from the metrics (`hud_flow_issues_total`, `code_flow_duration_seconds` count), not from sampled forensics — forensics are a **sampled** subset.
