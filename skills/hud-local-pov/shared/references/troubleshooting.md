# Troubleshooting

Symptoms seen in the sweeps (step 3), the run (step 4) and in Hud (steps 5 and 6), and their causes.

| Symptom | Cause |
| :------ | :---- |
| Coverage sweep all `no-data` after the first entries | The create that fills the pool failed or its `capture` path is wrong — check the first entries' status and response shape |
| 401/403 everywhere | Token env var unset or expired; the driver refuses an unset `{{env:...}}`, so it is set but wrong |
| 500s in Hud, no forensics | The account's remote config has forensics off for this SDK or this service (the run changes no SDK config: report it); or every failure was the same shape and hit the dedup cap |
| Forensics have no outbound calls | The SDK does not instrument that client (e.g. Node: `pg` under ESM, global `fetch` on older SDKs, a client inlined by the bundler; Python: psycopg 3; JVM: JDBC on an agent older than v0.2.9) — a `install-hud-sdk` audit finding, not a traffic problem |
| A changed function has no calls, its endpoint does | The request takes a branch that skips the change (an empty body, an early return): fix the entry from the PR's tests (X3 §3, and pr-selection.md "Confirm before timed traffic") |
| Endpoint missing in Hud, invoked by the driver | Unmatched route (404s get no flow), or the route sits under a framework `install-hud-sdk` reported as manual and the flow was never wrapped. On the JVM a wrapped route is a Custom Flow and never appears as an endpoint (`jvm.md`) |
| Endpoints and forensics present, functions missing | App code excluded from instrumentation. Python: `exclude_dirs` is a **substring** match — `"tests"` excludes every file under a parent directory with "tests" in its name. Anchor entries with slashes (`"/tests/"`). Node: `includeModules` doesn't name the workspace packages under `node_modules`. JVM: `-Dio.hud.includeDomains` doesn't cover that package root |
| Everything missing | Wrong environment key, or a different `HUD_SERVICE` than the one being searched |
| A bucket lower than its neighbours while the driver rate was steady | A dropped batch: count `Failed to send json for session` in the SDK log (through the key mask) |
| Forensics on only some 500 shapes | The caps were spent before the timed run, by X3's coverage sweep or probes. Those forensics are real, with earlier timestamps; look for them from the coverage sweep on, and keep probing lighter next time (X3, "Mind the forensics budget") |

