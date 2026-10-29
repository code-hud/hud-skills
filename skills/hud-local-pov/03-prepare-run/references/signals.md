# What Hud needs to see — coverage, durations and SDK limits

**Read against source on 2026-10-14**: `code-hud/hud@main` (`apps/detection`), and the
`main` branches of the Node, Python and Java SDKs. Where a value matters for a demo,
confirm it with the account's remote config. During a run, a value that doesn't hold goes
in the skill notes (SKILL.md, Guardrails).

## What the run shows: runtime data, not detections

The run sends the mapped requests for the PR's endpoints for about 2 minutes, with no
fault. What it delivers is **visible in Hud within minutes of the traffic** (any
environment, any service age): the changed functions invoked, the callers and callees
Hud recorded around them, invocation counts, p50/p90 durations per function and endpoint,
the status codes each endpoint returned, and forensics for any real 5xx a plain request
reaches.

**It raises no detection (issue or alert), and doesn't try to.** Hud's detectors run on a
schedule, against windows of minutes to weeks, behind gates: duration degradations need
days of hourly history, error rules need a service at least 3 days old, and **rules are
off on `local` environments** unless the account enables them. Say so before the run, not
after: the report shows what Hud recorded about the PR's code, not alerts.

## Coverage

- Functions are **declared when code loads** (Node parses each file, Python rewrites at
  import, the JVM agent declares a class when it first loads), so they appear without
  traffic. Durations, call relationships and forensics appear only for **invoked** code.
  Coverage is "invoked", not "declared": a changed function no request reaches stays
  empty however long the run lasts.
- **Callers and callees** come from the invoked calls: Hud records each caller → callee
  pair under the endpoint that wrapped it, with its count and duration. A branch of the
  changed code that only one outcome takes (a validation 4xx, a variant) gets its edges
  only from that outcome's request, which is why X2 maps the outcomes on the changed paths.
- Endpoint names are **route templates** (`/orders/{id}`), so random ids in paths do not
  create cardinality. Custom (manual) flows are capped at 1000 names per process.
- Node and Python declare endpoints with a delay (Node: first send after 2 min). An
  endpoint invoked in the first seconds still lands; it just shows up late.
- Manual flows differ by SDK: Node and Python have queue flows (`type: 'queue'`,
  `FlowType.QUEUE`); every JVM manual flow is a Custom Flow, so a JVM consumer is never a
  queue and an unsupported JVM HTTP framework's routes are never endpoints (`jvm.md`).

## Metrics granularity

| What | Value |
| :--- | :---- |
| Node SDK metrics flush | Every 5 min by default, or what the account's remote config sets; the first at ~30 s. The run doesn't change it (`node.md`) |
| Python SDK metrics flush | Every 30 s |
| JVM agent metrics flush | Every few seconds |
| High-resolution metrics (what charts and short-window queries use) | One row per SDK flush (`timestamp_end` is the flush time); charts group them into ~5 min buckets. Kept 3 months |

Percentiles come from log-scaled duration buckets about 20 % apart, so p90 moves in
steps (… 396 → 476 → 571 → 685 ms …). Changes smaller than one step are invisible.

**Consequence for the run.** Every invocation, call edge, duration and forensic of the
2-minute run lands, but the whole run shares one or two chart buckets: read it as run
totals, not as a shape over time. At about 1 entry/s, each mapped request runs dozens of
times, enough for stable per-function p90s.

## Forensics (all three SDKs)

| Fact | Value |
| :--- | :---- |
| Switched on by | The account's remote config (or an install that sets it itself). The run changes no SDK config, so the account's setting is what the run gets |
| Triggered by | Status matching `^5\d\d$` (remotely configurable), a framework error, or the user-error API. A handled exception that returns 500 **does** trigger. A 4xx **does not** |
| Unmatched routes (404 from the router) | No flow, no forensics |
| Error cap per process | Node / Python: 2 error investigations per 60 s, 5 of all kinds per 60 s. JVM: 25 error-based per 6 h |
| Same-shape dedup | 2 per (flow, error shape) per window — 1 h on Node/JVM, 6 h on Python. Shape = error type + first app function + caught/framework/user-error flags |
| Duration-triggered forensics | Only for endpoints with a threshold configured in Hud's backoffice; none by default |
| Baseline forensics (successful requests) | On by default; ~2 per minute, 20 min cooldown per endpoint |
| Captured | Exceptions with stack and execution flow, route, method, params, query, request body, response body (on by default), selected outbound calls. Function arguments only on Node with `enableInvestigationArgsSending`. Outbound payloads (the SQL, the HTTP body) are off by default, and the run leaves them off |
| Shipped | Every ~5 s after the first 30 s |

**The table above is SDK defaults; the account's remote config overrides them.** Read
the effective values from the service's startup log under `HUD_DEBUG` (the
`investigationRateLimits` block). On the reference run (account `field-test`, Oct 2026)
remote config set `maxSameInvestigations: 1` per **6 h** and `maxTotalInvestigations: 2`
per minute, tighter than the Node defaults.

The run injects no failure, so error forensics come only from a real 5xx that a mapped
request reaches on a changed path. **Dedup and caps live in process memory**, and the run
doesn't restart the service: what X3's coverage sweep and probes spend is gone for the
timed run, and a shape they already captured keeps that forensic, with its earlier
timestamp. Count forensics per flow in the report (X6's `forensic_counts`), and do not assume every
failing flow got one.

### Proof in SDK logs (needs `HUD_DEBUG=true`, plus `HUD_VERBOSE=true` for console)

**The Node SDK (1.10.6, 1.11.0) prints the full `HUD_KEY`** once at startup under
`HUD_VERBOSE=true` (`node.md`, "Logging hazards"). **The Python SDK (0.5.6) logs the full `HUD_KEY`** as `'session_key': ('local_<64 hex>', …)`
in `New process connected` lines and in every `Failed to send json for session` error.
Never `grep` or `cat` a debug log raw. Read it through a mask, or print counts only:

```bash
python3 -c "import sys; k = open('KEYFILE').read().strip(); sys.stdout.writelines(l.replace(k, '<HUD_KEY>') for l in open('app.log', errors='replace') if 'Max same investigation' in l)"
grep -c 'Failed to send json for session' app.log
```

Keep debug off for the run (`install-hud-sdk` turned it off after its verification), and scrub or delete old and rotated logs that
hold the key (`02-choose-pr/SKILL.md` §1).

| SDK | Forensics sent | Capped |
| :-- | :------------- | :----- |
| Node | `Stored object successfully` then `sent batched events successfully` with type `FlowInvestigation` | dropped silently to the limited logger; look for `Resetting global investigation counter` |
| Python | `Storing object` with key `investigation-…json`, then `Sending request` with type `FlowInvestigation` | `Max error-based investigations reached`, `Max same investigation reached` |
| JVM | count them through the MCP (X6's `forensic_counts` query) | `Max error-based investigations reached`, `Max same investigation reached` |
