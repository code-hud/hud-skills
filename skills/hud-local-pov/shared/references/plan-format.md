# Plan format for `shared/scripts/drive.py`

The plan is the skill's main artifact: one JSON file per service that records every
endpoint outcome X2 mapped on the PR's changed paths (as X3 confirmed it) and the traffic
phases that will run in X4. The driver
contains nothing specific to any service. If traffic is wrong, fix the plan.

The driver needs Python ≥ 3.9 and nothing else: standard library only, no installs.

```bash
python3 shared/scripts/drive.py plan.json --dry-run                 # resolve every request once, send nothing
python3 shared/scripts/drive.py plan.json --log run.jsonl           # the real run, with a timeline
python3 shared/scripts/drive.py plan.json --phase run --base-url http://localhost:8081
python3 shared/scripts/drive.py plan.json --phase coverage --pools pools.json   # setup once, reuse its pools
python3 shared/scripts/drive.py plan-draft.json --probe plan-probed.json        # learn bodies from validation errors
```

Exit codes: 0 done, 1 bad usage or a plan error, 2 the guard aborted a phase, 3 not ready
(the readiness checks failed), 130 stopped by Ctrl-C or SIGTERM.

**`setup` runs on every invocation** unless told otherwise: each sweep, probe and `--phase`
run creates another set of records. On Mealie, a setup seeding 40 lists left 200 behind
after a few invocations. Two flags stop it:

| Flag | Does |
| :-- | :-- |
| `--pools FILE` | After setup, writes the pools to `FILE` as JSON. On a later run, if `FILE` exists, loads the pools from it and skips every setup step whose pools it already holds. A step added since (a new seed) runs once and is saved. Use one file for the sweep, the probes and the real run. Delete it (and the records) to start fresh |
| `--skip-setup` | Skips setup. Pools start empty unless `--pools` loads them |

The file holds the pools as setup left them. If an entry `take`s from a setup pool (a
delete), later runs get that id back from the file and 404 on it; deletes should take from
pools the plan's own creates fill.

**Relative paths.** A `script` path resolves against the **plan file's directory**.
`auth.cmd` runs in the shell from the **driver's current directory**. With the plan in
`.hud-local-pov/` and the driver run from the repo root, that is `"./flows.py#x"` but
`"python3 .hud-local-pov/auth.py"`. It runs through the platform shell (`/bin/sh`, or
`cmd.exe` on Windows), so keep it POSIX `sh`, not bash. It gets `BASE_URL` (the target,
after `--base-url`) and `LOCAL_POV_RUN` in its environment.

## Top level

| Key | Meaning |
| :-- | :------ |
| `baseUrl` | Where the service listens on this machine. `--base-url` overrides it (another port, say) |
| `headers` | Sent on every request. Put secrets in env: `"authorization": "Bearer {{env:API_TOKEN}}"`. A header from a pool that is still empty is left off, so `"Bearer {{pool:token}}"` works with a setup login that captures `token`: the register and login before it go without |
| `auth` | `{ "cmd": "python3 .hud-local-pov/auth.py", "every": "45m", "header": "authorization" }`. `cmd` is a shell command that prints **the full header value** on stdout (`Bearer eyJ…`, `Token 9f2…`), nothing else, and exits 0; on failure it exits non-zero with the reason (the HTTP status) on stderr. The driver sends the value in `header` (default `authorization`) on every request. `every` re-runs `cmd` that often; set it below the token's lifetime. A burst of 401s also re-runs it. **The value is cached** in `auth.cache` next to the plan (mode 600) and reused by later driver runs while it is younger than `every` (always, without `every`), so checks, the sweep, probes and the run sign in once. A readiness 401 on a cached value signs in again by itself. `"cache": false` turns it off; `"cache": "name"` names the file. Delete the file to force a new sign-in |
| `cookies` | `true` keeps one cookie jar for the run: a session cookie from a login form, and the CSRF cookie a form page sets, go on every later request. For server-rendered apps |
| `timeoutMs` | Per request, default 30000 |
| `concurrency` | Max in-flight requests, default 100. Above it, requests are **shed** and the driver says so |
| `preflight` | Readiness checks run before anything else; a failure stops the run before any load (below) |
| `setup` | Requests run once, in order, before any phase: log in, create the parent records later requests need |
| `endpoints` | One entry per **outcome** (not per route): see below |
| `phases` | Run in order |

## Readiness checks: `preflight`

Requests that must succeed before any load is sent. The driver runs them after auth and
before setup, on every invocation, and exits 3 without sending load if one fails. Each
failure is printed with its usual cause: nothing listening, a host that doesn't resolve,
TLS, a rejected token, a redirect to a login page, a wrong path prefix, or a 500 from a
missing dependency. `--check` runs only these and exits 0 (ready) or 3.

```json
"preflight": [
  { "name": "service answers",        "method": "GET", "path": "/health" },
  { "name": "test user is signed in", "method": "GET", "path": "/api/me" },
  { "name": "test user can write",    "method": "POST", "path": "/api/lists", "body": { "name": "preflight {{run}}" }, "expect": [201] }
]
```

Entries take the same keys as an endpoint entry; `expect` defaults to any 2xx, and
`"expect": "any-http"` accepts any answer below 500. Without a `preflight` list the gate is
one GET of the base URL that must get an HTTP answer. `shared/references/service-setup.md` says
which checks to write.

## An endpoint entry is one outcome of one route

`POST /orders` usually has several entries — created, rejected for validation, out of
stock, and the path that 500s. That is what turns "every endpoint" into "every code path".

| Key | Meaning |
| :-- | :------ |
| `name` | Unique. Shows up in the driver's table and the timeline |
| `method`, `path` | `path` is the concrete path with templates: `/orders/{{pool:orderId}}` |
| `query`, `headers`, `body` | Objects; any string value may hold templates. A body is JSON unless `rawBody: true` |
| `form` | In place of `body`: sent as `application/x-www-form-urlencoded`. For HTML form posts |
| `csrf` | `{ "from": "/path" }`: GET that page first and send its CSRF token with the form. Finds `csrf_token`, `csrfmiddlewaretoken`, `authenticity_token`, `_token`, `_csrf` inputs and a `<meta name="csrf-token">`. `{ "from": "/p", "field": "x" }` names a field it misses. Needs `cookies: true` |
| `expect` | Status codes that mean "this outcome happened". Default: any 2xx. Redirects are not followed: a 302 is recorded as 302 with its `Location`, so a session that expired mid-run (302 to a login page) shows up instead of passing as the login page's 200. Set `expect: [302]` where a redirect is the outcome |
| `capture` | `{ "poolName": "dot.path" }` from a JSON response, e.g. `{ "orderId": "id" }` or `{ "itemIds": "items.0.id" }`. An array value adds every element. `"**.slug"` takes the first `slug` anywhere in the reply, for wrapped replies like `{ "article": { "slug": … } }`. A value captured by a request whose path used other pools remembers them: `/articles/{{pool:a}}/comments` captures comment ids tied to their article, a later `{{take:…comments:id}}` picks a matching pair, and taking an article drops its comments. Values one reply fills into several pools (a tool's id and its slug) are tied the same way: taking the id drops the slug, and an update that renames the record refreshes it. A reference to the same pool twice in one request (`/labels/{{pool:L}}` with `{ "id": "{{pool:L}}" }`) is one value |
| `weight` | Relative frequency in rate phases, default 1 |
| `tags` | Free-form labels phases select on. Use at least: `read`, `write`, `client-error`, `server-error` (a 5xx a plain request reaches), `changed-path` (an outcome whose path runs through a function the PR changed), and `silent-noop` for an outcome that should be a 404 but returns 2xx and changes nothing (a finding: Hud records it as healthy) |
| `timeoutMs` | Override for one slow entry |

**`expect` is how you know a code path was reached.** An entry built to hit the 409 branch
that comes back 400 never reached the branch, and the driver marks it `unexpected` with
the response body logged. Treat every `unexpected` in the coverage sweep as a plan bug to
fix before the timed run.

### Script entries: flows one request can't express

Some outcomes need several requests that depend on each other's responses: read a list,
change its items, write them back; fetch a CSRF token, then post a form; walk an order
through its states. A plain entry can't change a captured object, so these go in a script
entry:

```json
{ "name": "flow: check, uncheck, remove", "script": "./flows.py#recipe_check_cycle",
  "weight": 4, "tags": ["pr-scope", "write", "flow"] }
```

`flows.py` sits next to the plan and defines `def recipe_check_cycle(ctx)`. Without
`#name`, the driver calls `main(ctx)`. The file is plain Python ≥ 3.9 on the standard
library, and may import other files in its own directory.

```python
class FlowFailed(Exception):
    pass


def recipe_check_cycle(ctx):
    slug = ctx.take("recipes")
    try:
        r = ctx.request("GET", f"/api/recipes/{slug}")
        if not r.ok:
            raise FlowFailed(f"read {r.status}")
        items = r.json["ingredients"]
        items[0]["checked"] = True
        return ctx.request("PUT", f"/api/recipes/{slug}", body={"ingredients": items}).status
    finally:
        ctx.put("recipes", slug)
```

| `ctx.` | Does |
| :-- | :-- |
| `request(method, path, query=None, body=None, headers=None)` | Sends with the plan's base URL, headers and auth, and the entry's `timeoutMs`. Templates work in `path`, `query`, `body` and `headers`. Returns a response with `.status`, `.ok` (2xx), `.json` (`None` when the body is not JSON) and `.text`. Redirects are not followed |
| `pool(name)`, `take(name)`, `put(name, value)` | The plan's pools; `pool` and `take` return `None` when the pool is empty. `take` + `put` in a `finally` gives a flow exclusive use of a record, so concurrent flows don't edit the same list |
| `env(name)`, `run`, `base_url` | An environment variable (raises if unset); the run id; the target after `--base-url` |

Flows run on worker threads, several at once in a rate phase. Share state between calls
through the pools, not through module globals.

**Rates count entries, not requests.** A flow that makes 10 requests turns 20 entries/s
into about 200 requests a second. On Mealie #8509 that deadlocked the database pool (15
connections `idle in transaction`, every request timing out). The driver's guard
aborted the phase, and the service took 4.5 minutes to drain its backlog after the
driver stopped. Size each rate phase by *requests per second*: count the requests in each flow and
weight them in.

Return the status that stands for the whole flow (compared with `expect`, default 2xx;
returning nothing counts as 200); raise to fail it, and the message goes to the timeline. The driver's table shows one row
per flow; **Hud sees every request inside it as its own endpoint call**, which is what
the run is for. The repo's integration tests are the best source for a flow: they already
encode the order, the bodies and what each step should return.

**Pools hold at most 5,000 values each**, newest kept; the driver says so when one fills.
Seed into **one pool of many values** (`foods`), not one pool per record (`food:<id>`):
the pool is the unit the driver picks from and lists.

## Templates

| Template | Value |
| :------- | :---- |
| `{{uuid}}` `{{word}}` `{{email}}` `{{now}}` `{{seq}}` | Generated |
| `{{int:MIN:MAX}}` | Random integer, inclusive |
| `{{env:NAME}}` | Environment variable. The run fails if it is unset; it never sends an empty token |
| `{{pool:NAME}}` | A random value captured earlier into `NAME` |
| `{{take:NAME}}` | The same, removed from the pool. Use it on deletes so later reads do not 404 |
| `{{unknown:NAME}}` | A value nobody knows yet (an enum the source didn't resolve). The entry is not sent and shows as `unresolved`. `--probe` drops the field, and refills it if the service's validation error asks for it or lists allowed values |

A string that is exactly one template keeps the value's type: `"quantity": "{{int:1:5}}"`
sends a number. An entry whose pool is still empty is counted as `no-data` and skipped,
not sent with a hole in the path.

## Phases

| Key | Meaning |
| :-- | :------ |
| `name` | Shows in the output and the timeline |
| `mode` | `sweep` (every selected entry, in plan order, `repeat` times) or `rate` (default when `rate` is set). In a sweep, an entry whose pool is empty runs the create that fills it first, so a merge that needs two records still runs; in a rate phase it is counted as `no-data` |
| `repeat` | Sweep repetitions, default 3. The `coverage` phase uses 1: X3 sends it once, as the run's one coverage pass |
| `rate` | Entries per second, steady for the whole phase |
| `duration` | `90`, `"90s"`, `"20m"`, `"1h"` |
| `include`, `exclude` | Entry names or tags |
| `weights` | Per-phase weights by entry name or tag, e.g. `{ "read": 3, "client-error": 0.2, "server-error": 0 }`. A name match wins outright; otherwise the weights of **all** matching tags multiply into the entry's own `weight`, so tag order never matters |
| `concurrency` | Override for this phase |
| `note` | Free text written to the timeline, e.g. what the phase sends |

The driver runs no phase hooks: a phase with `before`, `pulse` or `after` is a plan error.
The run sends the mapped requests only, with no fault.

Sweep order matters: put creates before the reads that consume their captured ids.

## The timeline

`--log run.jsonl` appends one line per phase start and end, plus the first three
unexpected responses per entry per phase, all with UTC timestamps. The report lines up
these timestamps against Hud's data; without them a change in the dashboard can't be
tied to the phase that produced it.

## Concurrency

Each request in flight holds a worker thread, so `concurrency` is also the thread count.
On a laptop it held 200 requests a second with every planned request sent.

## Probing a drafted plan: `--probe OUT`

A plan written from code with no spec (see `plan-without-spec.md`) will have some fields
wrong or missing. `--probe` sends each entry once, in plan order, after setup. When the
reply is a 400 or 422, it reads the validation error, fixes the body and retries, up to
six times per entry. It writes the fixed plan to `OUT` and marks each entry with
`_probe: { status, ok, learned, note }`.

It understands FastAPI/Pydantic `detail[]`, Spring `errors[]`/`fieldErrors`,
express-validator, class-validator/Nest `message[]`, Zod `issues[]`, Joi `details[]`,
Rails-style `{ errors: { field: [...] } }`, and plain messages naming a field. From those
it adds missing fields, takes the first allowed value of an enum, swaps a value of the
wrong type (number, boolean, list, email), and makes a value unique (`{{uuid}}`) when the
error says it is taken. It starts by removing `{{unknown:…}}` fields, so a required one
comes back in the error with its allowed values. An update that renames its record (a new title
gives a new slug) gets a capture, so later entries use the new value.

It also fixes the request's references to other records:
- **Ids.** A field the service wants as a UUID takes an id from the matching pool:
  `label_id` → the labels pool, `from_food` → the foods pool. `from_`/`source_` fields
  `take`, because a merge or a move consumes that record.
- **Updates.** A body `id` on an update is the path's own record. A required field with no
  pool, such as the record's group or owner, is read from a GET of the same record. Names
  stay generated, so they remain unique.
- **Empty or stale pools.** An entry whose pool is empty re-runs the create that fills it,
  as many times as the request reads from it. A 404 on a pooled id drops the stale value
  and retries once with a fresh record.

The probe sends real requests and creates real records: run it only against the test
service, in X3, on the entries its coverage sweep showed wrong. Read every entry whose
`_probe` is not `ok`, and resend only the entries you fixed, never the whole sweep.
