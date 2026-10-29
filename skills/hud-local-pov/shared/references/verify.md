# Hud MCP queries the steps share

Run these through `hud-query` with `serviceScope: "all"` and `environmentName` set to the
environment the key belongs to (`""` for all environments, in which case add
`environment_name` to every `GROUP BY`). Replace `SERVICE` with the service's own
`HUD_SERVICE` (the run doesn't rename it) and the time range. Use `HighResolution` tables
for anything under 10 hours.

**The run's data is the key's environment within the run's window.** The service reports
under its production name, so the environment (a `local` or staging key's) and the time
window keep production data out: `COVERAGE_START_UTC` (X3's coverage sweep, `service.json`'s
`coverage.startUtc`) or `RUN_START_UTC` (the timed run's `phase-start` in `run.jsonl`) to the
run's end.

**The Hud MCP is the only data path.** Every claim about what Hud saw comes from a query.
SDK logs can add detail, but they are not evidence that data arrived.

## Visibility: the MCP can see the service

X1, X3 and X6 run it. Zero rows means the MCP is connected to a different Hud account, or
the service or environment name differs from the one the key reports to. It doesn't mean
missing data. Stop and ask the user to fix the connection (see the last section).

```sql
SELECT service_name, environment_name, max(timestamp_end) AS last_seen, SUM(invocations) AS inv
FROM EndpointMetricsHighResolution
WHERE timestamp_end >= now() - interval 3 hour AND service_name = 'SERVICE'
GROUP BY service_name, environment_name LIMIT 10
```

## The PR's changed code ran

X3 runs it after the coverage sweep (`02-choose-pr/references/pr-selection.md`, "Confirm
before timed traffic"). Match the changed functions by **`hudFunctionId`**, which X3
recorded in `scope.json`; matching names with `LIKE` misses renamed or overloaded functions
and catches look-alikes.

The metric tables can return the same row more than once, which doubles every `SUM`, so the
query reads them through a subquery that keeps one row per key. The key includes the row's
own values, not just (session, entity, time): the Python SDK writes one row per status code
for the same session, entity and time, and a key without the values drops those real rows.

```sql
SELECT f.function_id, f.function_signature, f.file, SUM(m.invocations) AS calls
FROM Functions f
LEFT JOIN (SELECT * FROM FunctionMetricsHighResolution
           WHERE service_name = 'SERVICE' AND timestamp_end >= toDateTime('COVERAGE_START_UTC')
           LIMIT 1 BY session_id, function_id, caller_function_id, wrapped_endpoint_id, timestamp_end, invocations, total_duration_ms) m
  ON m.function_id = f.function_id
WHERE f.function_id IN (HUD_FUNCTION_IDS)  -- scope.json changedFunctions[].hudFunctionId
GROUP BY f.function_id, f.function_signature, f.file
LIMIT 100
```

A changed function with zero calls means the plan never reached it. Read its call chain in
`scope.json` and add the entry or flow that does, before the timed run. A changed
function with no `hudFunctionId` wasn't declared in Hud when X3 ran (new code the service
hasn't loaded, or a file the install doesn't map): find it by `file` and
`function_signature` instead, and say which ones you matched that way.

## When the MCP can't see the service

Stop. Don't fall back to SDK logs or dashboard instructions: neither shows what Hud
ingested. Tell the user which check failed and what usually causes it:

- **The MCP is connected to another Hud account.** It is bound to one account at a time,
  and the binding can change during a session. Run the services query in
  `install-hud-mcp` §4 and show the user what it sees. They reconnect to the account
  the key belongs to (§4 says how).
- **The environment name differs.** Query with `environmentName: ""` and group by
  `environment_name` to see where the service's data went.
- **The service name differs.** Search `Endpoints` for one of the service's routes and
  read its `services` column.

Carry on once the visibility query returns the service.
