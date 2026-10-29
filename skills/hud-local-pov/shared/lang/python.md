# Language pack: Python

Read in X1 when the service is Python, and follow it wherever SKILL.md says "the language
pack". Every pack uses the same headings; `signals.md` holds the cross-SDK tables.

**Install skill:** `install-hud-sdk-python`. **SDK last checked:** hud-sdk 0.5.6.
**POVs run with this version:** Mealie (FastAPI), SimpleLogin (Flask), Paperless-ngx
(Django + DRF, Celery), microblog (Flask, plan only).

## Install check (when Hud maps none of the app's code)

What proves Hud mapped **the app's own code**, not just the framework and the SDK:

`install-hud-sdk` confirmed the app's code is mapped; use this only to diagnose a run where
the PR's changed functions get no data in Hud after X3's coverage sweep.

1. **Primary: ask Hud.** Count the service's functions by file through the Hud MCP. App
   files must be there:

   ```sql
   SELECT multiIf(f.file LIKE '%/<app package>/%', 'app', f.third_party != '', 'third-party', 'other') AS kind,
          count() AS functions
   FROM Functions f WHERE arrayExists(s -> s.2 = 'SERVICE', f.services) GROUP BY kind LIMIT 10
   ```

   Needs no debug logging, so no key-bearing log (see "Logging hazards"). Use the path the
   app actually runs from: a wheel or image install lives under
   `…/site-packages/<package>/`, not the repo path.
2. **Local fallback: the debug log.** `Monitoring file: <path>` lines for the app's own
   files. Count them **per process** (`[HUD]:<pid>`), not across the log: one process
   that mapped nothing hides behind another that mapped plenty.

**False alarms to ignore.** `Hud: No code has been mapped so far` from a process that never
imports the app is normal:
- the gunicorn master without `--preload`, because the app loads in the workers after fork;
- a granian or uvicorn supervisor;
- the multiprocessing resource tracker and the SDK's own exporter.

Judge it on the serving processes (the workers, the Celery main process). In an
image without `ps`, map PIDs with
`for p in /proc/[0-9]*; do echo "${p#/proc/} $(tr '\0' ' ' < $p/cmdline)"; done`.

**A real failure looks like this:** endpoints, 500s and forensics all arrive, but only
third-party functions are declared. On Mealie the only declared file was the SDK's own
`psycopg2.py`: `exclude_dirs=["tests"]` matched the clone's parent directory
(`…/hud-install-tests/…`) and excluded the whole app. On SDK 0.4.11 with Python 3.9, nothing
mapped at all.

## Metric flush

Every 30 s. The run needs nothing set.

A short-lived process can exit before it flushes: a Celery worker that lives under 5 s
can stay silent. The run doesn't change that setting; drive the HTTP service instead. Wrap a short script's `main()` in a flow and exit cleanly (`install-hud-sdk-python`, Phase 2).

## Forensics

- **Switched on by** the account's remote config. The run doesn't change it.
- **Caps:** 2 error investigations per 60 s and 5 of all kinds per process. Dedup is
  2 per (flow, error shape) per **6 h**.
- **Budget:** the counts live in process memory, and the run doesn't restart the service,
  so what X3's coverage sweep and probes spend is gone for the timed run.
  A pre-fork server keeps one budget per worker.
- **Remote config** can tighten all of this (`field-test`: 1 per shape per 6 h). Read the
  effective values from the startup log's `investigationRateLimits` block.
- **Log lines:** forensics sent shows `Storing object` (`investigation-…json`) then
  `Sending request` with type `FlowInvestigation`. When capped, the log shows
  `Max error-based investigations reached` or `Max same investigation reached`.

## Logging hazards

**SDK 0.5.6 writes the full `HUD_KEY` into debug output**: `'session_key': (…)` in
`New process connected` lines and in every `Failed to send json for session` error. So:

- Prefer the MCP query above, and leave `HUD_DEBUG`/`HUD_VERBOSE` off where you can. Agents in
  auto mode may be refused permission to enable them, for this reason.
- If you do enable them, read logs only through the key mask in `signals.md`, and turn them
  off once the diagnosis is done.
- **In a container, the debug lines go to stdout**, so `docker logs` holds the key until
  the container is recreated. Never read `docker logs` unmasked.
  Afterwards, run `docker compose up -d --force-recreate` for that service.

## How the process serves requests (sizing the rate)

The GIL means one Python process serves one slow request at a time. An endpoint that is
slow on its own whose rate × latency approaches 1 s of work per second makes every other
endpoint queue behind it. Keep it under about 0.3 per worker by lowering that entry's
weight. Gunicorn, uvicorn
and granian workers multiply that by the worker count. So do the forensics caps above,
which apply per process.

## Outbound calls the SDK records

0.5.6 records **psycopg2 and asyncpg only**. It records no psycopg 3, HTTP clients, Redis,
kombu/Celery publishing or Channels.

Check before X2: list the SDK's integrations in the target environment
(`hud_sdk/instrumentation/outbound/frameworks/`) and compare them with `pip list`.

- **Postgres through psycopg 3** (Paperless): no SQL appears in flows or forensics. Don't
  count a PR's database changes as part of its story when picking it, and say up front in
  the report that forensics will carry no SQL.
- The impact map's Outbound column is empty for the same reason. Say why, don't leave it
  blank.

## Metric rows (counting)

The SDK writes **one `EndpointMetricsHighResolution` row per status code** for the same
(session, endpoint, minute).

That's why the deduplicating subquery in `shared/references/verify.md` and
`06-report/scripts/report_queries.py` keys on the row's values too (`toString(status_codes)`,
`invocations`, `total_duration_ms`). A key of (session, endpoint, time) alone dropped 20–30 %
of calls on mixed-status endpoints. Use the same key in any query you write by hand, and
check a count by hand on an entry that returns mixed statuses, since that is where a wrong
key shows (X6's count check compares one plain entry).

## Endpoint names

- **FastAPI and Flask:** route templates (`/api/foods/{item_id}`, `/user/<username>`).
- **Django:** the route's **regex pattern**, with no trailing slash
  (`/api/documents/(?P<pk>[^/.]+)`). It is **one endpoint for every method** of a DRF viewset
  route.

Scope files and report queries must use Hud's form. A scope whose endpoints match none of
Hud's gives an empty timeline.

Entry points Hud doesn't auto-cover get a manual `sync_flow`/`async_flow`: `FlowType.QUEUE`
for consumers (with `enqueued_at` and `message_count` where the broker gives them),
`FlowType.CUSTOM` otherwise. `asyncio` is instrumented for attribution only and creates no
flows (`install-hud-sdk-python`, Phase 0 and 3).

## Start command and images

- **Wrap the start command with `hud-run`:**
  `hud-run --config hud_config.py <command>`, with `HUD_KEY` and `HUD_SERVICE` in the
  environment (`set -a; . ./.env.local; set +a` first, or the container's env file).
  **Don't pass `--key`:** it puts the key in the process list (`ps`, `/proc/<pid>/cmdline`)
  of every wrapped process. `hud-run` reads both variables from the environment when the
  flags are absent (SDK 0.5.6). This works for gunicorn, uvicorn, Celery, `manage.py` and
  `python -m`. See
  `install-hud-sdk-python` for `hud_config.py` and `include_modules`.
- **Several processes in one image** (a web server, a Celery worker, a scheduler): wrap
  every process on the request path. Decide before X2, because the PR is picked after the
  install. Wrapping the web server and the task worker by default keeps every PR choice
  open.

## PR ranking (X2)

- **Paths.** Hud records the source `.py` path as the process sees it
  (`/app/mealie/routes/foods.py`, or a `site-packages/` path for an installed package).
  Both match the repo's files by suffix. An app installed as a wheel runs from
  `site-packages/<package>/`, which still ends in the repo's package path.
- **Lazy imports.** A module imported inside a function is declared only once that
  function runs. Its directory usually matches anyway (`inService: "dir"`).
- **Tracing to a route** (`02-choose-pr/SKILL.md` §4). FastAPI's `Depends(...)` is a
  caller too: a changed dependency reaches every route that declares it. Compose the path
  from `APIRouter(prefix=)` and every `include_router(prefix=)` above it; Flask from
  `Blueprint(url_prefix=)` and `register_blueprint(url_prefix=)`. In Django and DRF, find
  the view or viewset in `urls.py` (and router `register` calls), then follow `include()`
  up to the root `urls.py` for the prefix. A running FastAPI app's `/openapi.json` settles
  any prefix you can't resolve from the code.
- **Changes inside a decorator's wrapper** (an auth check) reach every decorated route.
  Grep for the decorator when you trim the scope.
