---
name: hud-local-pov-02-choose-pr
description: >-
  Step 2 (phase X2) of the hud-local-pov skill: confirm the test instance install-hud-sdk
  left (or start it), sign in a test user and pass readiness checks, rank recent merged PRs
  and pick one with a light feasibility pass over the top five (or take the PR the user
  named in their request), trace the winner's endpoints, and draft plan entries for the outcomes
  on its changed paths. Sends no load. Run it from hud-local-pov's SKILL.md, not on its
  own.
---

# Step 2 (X2) — Get the service ready, choose the PR, map its changed paths' outcomes

Part of `hud-local-pov`. The orchestrator (`SKILL.md` at the skill root) holds the overview,
the guardrails and the order of steps. Every path here is relative to the skill root.
User-facing: **Step 2 of 4**.

**Usage analytics.** Run `python3 shared/scripts/analytics.py start 2` as you begin. Before
you return `GATE_PASSED`, run `python3 shared/scripts/analytics.py end 2 --ok`; before you
return `BLOCKED`, run `python3 shared/scripts/analytics.py end 2 --failed "<reason>"`, where
the reason is a few plain words on what failed, with no names, paths, URLs, keys or output.
Run nothing on `NEEDS_USER`: the step isn't over. The helper returns at once and never fails.

| | |
| :-- | :-- |
| **Consumes** | `.hud-local-pov/answers.json` (the setup and the brief's instance, the install's wiring, the key source, `HUD_SERVICE`, the language pack, every input X1 settled), `.hud-local-pov/service-profile.json`, the Hud MCP (the service's files and functions), the language pack ("PR ranking") |
| **Produces** | The running test service with Hud attached; `.hud-local-pov/app.log` (when this step started it), `.env.local` at the repo root (excluded from git), the auth command (`plan.json`'s `auth.cmd`), `.hud-local-pov/plan.json` with its `preflight` checks, `service.json` (with `data.before`/`data.after`); `prs.json`, `hud-files.json`, `ranked.json`, `feasibility/<pr>.json`, `pr-choice.json`, `routes.json`, and the draft entries: `plan-draft.json` (from the code or a spec) or `plan-pr.json` (an earlier plan narrowed); the outcome table shown to the user |
| **Gate to step 3** | `python3 shared/scripts/drive.py .hud-local-pov/plan.json --check` exits 0 with Hud attached; a PR is picked by the rubric (or named by the user, or the whole service is mapped) and the reason stated, with the other candidates; `routes.json` is written; the draft passes `--dry-run`, with one entry per outcome on the PR's changed paths plus one success request per route; debug is off; the user has seen the outcome table with the non-HTTP entry points and the outcomes expected to be unreachable or silent no-ops. **No load:** past `--check` and its sign-in, this step sends nothing to the service; step 3 sends the first sweep |

**Files**

| File | Use |
| :--- | :-- |
| `shared/references/service-setup.md` | Starting the service when no instance is running (§4), the test identity (§5), readiness checks (§6), and the `service.json` record (§7) |
| `02-choose-pr/references/pr-selection.md` | How the PR that scopes the run is chosen: fetch → ask Hud for the service's files → rank → feasibility pass → trace the winner. Read before §3 |
| `02-choose-pr/references/plan-without-spec.md` | How to write the entries with no spec: where each request comes from, values, pools, sign-in, flows. Read before writing entries when there is no current spec |
| `02-choose-pr/scripts/fetch_prs.py` | Merged PRs, their reviewers, changed files and exact hunks into `prs.json` (Python 3.9+, git; for GitHub, `gh pr list` and `gh api --paginate`) |
| `02-choose-pr/scripts/rank_prs.py` | Filters and ranks PRs from `prs.json` and Hud's file list, and returns the top five with the reasons for each. Prints the Hud query it needs. (Step 3 runs its `--pick N`.) Thresholds and weights in `02-choose-pr/scripts/pr-weights.json` |
| `02-choose-pr/scripts/scope_plan.py` | Narrows a plan to the chosen PR's routes and to the outcomes on its changed paths, plus the creates they depend on; lists the variants it dropped and the routes the plan misses |
| `02-choose-pr/scripts/openapi_plan.py` | Drafts one success request per operation from an OpenAPI spec; `--routes` keeps only the PR's routes. Output is a draft to complete, never a finished plan |
| `shared/scripts/drive.py`, `shared/references/plan-format.md` | The driver (`--check`, `--dry-run`) and the plan schema |

Ask nothing: X1 settled every input. Work from `answers.json`; if something it doesn't cover
blocks you (a start that fails on a secret the repo never named, an SSO-only login), return
`NEEDS_USER` with that one blocker, marked as one.

### 1. The test instance: a quick check

`install-hud-sdk` verified Hud on this instance, so this is a check, not a setup. **Never
run a second instance next to one that already serves the port.**

- **The brief says it is still running** (its PID, port, start and stop commands, log). Check the PID still owns the port and the service answers on it. If so,
  reuse it as it is: don't restart it, it already runs with Hud and the key. Copy its start
  command, stop command and log from the brief into `service.json`.
- **It stopped** (or the brief says it isn't running): start it with the brief's start
  command, which loads Hud, giving it the `HUD_KEY` from X1's source in its env from the
  start (`.env.local`, never on a command line you print), logging to
  `.hud-local-pov/app.log`. When its dependencies are down too, bring them up first with
  `shared/references/service-setup.md` §4. Fix start failures one cause at a time, from the
  log.

**Leave the install as it is.** X2 changes no code and no Hud config: the install, its
`HUD_SERVICE` and the SDK's defaults stay as `install-hud-sdk` left them. Don't rename the
service, and don't add SDK flags or `HUD_*` settings beyond `HUD_KEY` on the start command.
The key comes from the `HUD_KEY` env var (the brief says so; X1 checked it), never from a
production key.

Debug logging (`HUD_DEBUG`, `HUD_VERBOSE`) stays off. Turn it on only to diagnose Hud
seeing none of the run, and read the log through the language pack's "Logging hazards"
(some SDKs write the full `HUD_KEY` there).

**Restart only when it has to:** the service isn't running, runs without `HUD_KEY` in its
env, or doesn't load Hud. No later step restarts it on purpose: the timed run uses the
instance as it is. When you do restart, stop whatever **owns the port**,
not the process that matches your launch command: wrappers like `hud-run` can exit while
the server they started keeps running; the new instance then fails to bind, the old one
keeps answering health checks, and the restart silently didn't happen. Afterwards, check
that the PID listening on the port is new.

**Data before and after:** follow A6 from `answers.json` (the default is neither). If the
user wants a dump or a cleanup, use their method, record it in `service.json`
(`data.before`, `data.after`) and put the command in the closing hand-off (X6).

### 2. Test identity and readiness

Follow `shared/references/service-setup.md` §5 and §6:

1. **Get a test identity** from A1 and A2 as an `auth.cmd` that prints the header and
   refreshes before the token expires.
2. **Prove it is ready.** Write the plan's `preflight` checks (a health route from the
   profile, an authenticated read, a write when writes are allowed, with `{{uuid}}` in
   unique fields) and run
   `python3 shared/scripts/drive.py .hud-local-pov/plan.json --check` until it exits 0.
   A service that was ready
   before a restart and isn't now points at the start (the command, the wrapper, a port the
   old process still holds), not at the service.
3. **Record it** in `.hud-local-pov/service.json` (service-setup.md §7).

**X2 sends no load.** `--check` and the sign-in it runs are the only requests this step
makes to the service: the coverage sweep and the probes belong to step 3,
whose forensics budget and time window start there. The driver also enforces readiness:
every run re-checks it first and sends nothing if a check fails.

### 3. Choose the PR that scopes the run

The run drives the endpoints **one recent PR reaches**, not the whole service. The scope
stays small enough to plan properly, and the report tells a story about a change the
team actually shipped. Read `02-choose-pr/references/pr-selection.md`.

**Hud decides what counts as the service.** It declares the service's functions when the
code loads, so after the install's verification its `Functions` table lists every source
file the running service is made of. A PR is a candidate when most of its source files are
among them.

- **The user named one PR in their request** (`answers.json`, source `request`): fetch it with `--pr N`, take it, and skip the
  feasibility pass: its blockers surface in the mapping below.
- **The user named several:** fetch only those with `--pr N` and run the feasibility pass
  over them.
- **Otherwise** fetch the last 60 days, rank them, and run the feasibility pass over the
  top five, and take the rubric's pick. Don't ask which PR or whether to pick one. Never
  pick silently, either: say which PR and why.

```bash
python3 02-choose-pr/scripts/fetch_prs.py --repo . --days 60 --out .hud-local-pov/prs.json
python3 02-choose-pr/scripts/rank_prs.py --prs .hud-local-pov/prs.json --service SERVICE      # prints the files query
# run it through the Hud MCP; save the rows to .hud-local-pov/hud-files.json
python3 02-choose-pr/scripts/rank_prs.py --prs .hud-local-pov/prs.json --hud-files .hud-local-pov/hud-files.json \
    --top 5 --out .hud-local-pov/ranked.json
```

If the clone is too shallow for the window, the fetch stops with the command to deepen it.

**What the fetch needs from `gh`:** only `gh pr list` (`gh pr view` for `--pr`) and
`gh api --paginate --jq`, which every gh 2.x has; it never uses `--slurp`. Don't switch to
`--source git` because one gh call failed: a PR whose files call fails (a rate limit, one
bad PR) takes its files from the local merge commit, and the fetch lists what failed at the
end. Only a failing `gh pr list` moves the default source to git. When the git source is
used and `gh pr list` works, the fetch still fills each PR's open time and link from it, so
the report's time to merge has a value.

The ranker drops bots, reverts, maintenance PRs, PRs with fewer than 60 % of their source
files in the service, and PRs outside 100–1,000 lines changed in the service's files. It
puts PRs the user wrote or reviewed first, then those with more story signals: routes in
the changed files, database or HTTP calls in the changed lines, tests, feat/fix/perf,
focused, fixed by a later PR, described (pr-selection.md §3). It prints the top five with
their reasons, and `ranked.json` lists them under `top`. If nothing is eligible, check that
a few known files match Hud's paths before loosening the thresholds in `pr-weights.json`;
if still nothing, map the whole service (§4) and say why.

#### The feasibility pass

The ranker sees the diff's shape, not whether the change can be reached and broken on this
test service. The feasibility pass checks that, lightly, for each candidate in `top` (all
of them when fewer than five are eligible), so only the winner gets the full mapping.

**Launch one sub-agent per candidate, all five in one message** (the Task tool, a
general-purpose agent), so they run in parallel. Each one's prompt carries:

- the skill root's absolute path and the user's repo root as the working directory;
- the candidate's `ranked.json` entry (number, title, URL, `why`) and the merge SHA from
  `prs.json`;
- the paths of `.hud-local-pov/service-profile.json`, `answers.json` and `service.json`;
- the instructions: *"Light feasibility pass on one PR for hud-local-pov. Read-only: read
  the PR's description, tests and diff (`git show <merge sha>`) and the code around it.
  Send no requests to the service and run no Hud queries (the run's forensics budget and
  time window start later). Spend minutes, not a full trace: follow each changed function
  up to its entry points only as far as needed to name them. Ask the user nothing. Write
  your result to `.hud-local-pov/feasibility/<number>.json` in the shape below and return
  it."*

The result, one per candidate:

```json
{"pr": "#123",
 "endpoints": [{"method": "POST", "path": "/api/orders/{id}/items", "core": true,
                "via": "src/services/orders.ts::addItem -> src/routes/orders.ts::postItem"}],
 "nonHttp": [{"kind": "queue", "flow": "kafka:order.changed", "trigger": "every item add publishes it", "easyToTrigger": false}],
 "outcomes": {"byRequest": 5, "unreachable": 2, "note": "the 409 needs two concurrent adds; the 500 needs Postgres down"},
 "blockers": [{"kind": "external-service", "what": "checkout calls Stripe; A3 says payments off, so the route returns 503 early", "hard": true}],
 "verdict": "feasible",
 "summary": "Two core routes over HTTP, five changed-path outcomes a plain request reaches; no seed data needed"}
```

`endpoints` are the HTTP entry points the changed code reaches (`core`: the handler is in
the diff or one call from it). `outcomes` counts the distinct outcomes on the core
endpoints' changed paths: reachable by a plain request, or not (a dependency failure, a
race, data nothing seeds); the run injects no fault. `blockers` lists what
stands in the way on this test service, with `kind` one of `external-service` (a paid or
partner API the A3 answer turns off or leaves unstubbed), `seed-data` (the change needs
records nothing seeds), `auth` (a role or tenant A1/A2 doesn't give), `sibling` (a service
A5 leaves down), `config` (a feature flag or setting that's off), `other`; `hard` when it
keeps the changed code from running at all. `verdict` is `feasible`, `partial` or
`infeasible`.

**No sub-agents?** When your agent can't launch them (this step may itself run as a
sub-agent that can't), do the same pass yourself, one candidate after another, in rank
order, with the same instructions and the same result file per candidate. Keep each pass as
light as above; don't start the full mapping for any of them.

#### Pick the winner with the rubric

Score each result the same way, every run:

| Rule | Points |
| :-- | :-- |
| `verdict` is `infeasible`, a `hard` blocker, or no core HTTP endpoint and no easy non-HTTP trigger | Out |
| Each core HTTP endpoint reached (up to 4) | +2 |
| Each changed-path outcome reachable by a plain request (up to 6) | +1 |
| Each non-hard blocker | −2 |

The highest score wins; a tie goes to the higher-ranked candidate. If every candidate is
out, take the next five from `ranked` and run the pass again once; then map the whole
service (§4) and say why.

Write `.hud-local-pov/pr-choice.json`: the winner, its score per rule, `why` (one or two
sentences from its result, in the user's terms), and for each of the other candidates its
score and the one reason it lost (`out: Stripe has no sandbox and A3 keeps payments off`,
`7 vs 10: one core endpoint, not three`).

**Say what the PR is for, and why this one.** In a few sentences, in your own words:

> The run is built around one recent PR, to show how Hud's runtime data helps review pull
> requests: for each change, how often the code it touches runs and how long it takes, the
> errors already captured on those paths, and real request shapes to test it against. With that
> evidence, a low-risk PR can be approved automatically and a human only reviews the rest. The
> report shows this for the PR the run is built around.

Then name the winner with its `why`, and list the other four in one line each with why they
lost, so the user can redirect the run if they know better. That is information, not a
question: carry on unless they object.

Scope the rest of X2 to the winner's endpoints. Map the whole service only when no PR
qualifies or the user asks for it. Say which, and why.

This is where coverage is won or lost. Hud declares functions when code loads, but only
**invoked** code paths get durations, call graphs and forensics — so a branch nothing
reaches stays empty however long the run lasts.

### 4. Trace the winner's endpoints

Work from the change outward, not from a route table inward. A PR of 100–1,000 lines is
small enough to read whole. Start from the winner's feasibility result: its `endpoints` are
hints to confirm, not the scope.

1. **Read the PR's title, description, linked issue and tests.** Write down every
   endpoint, URL, feature name or request they mention. These are hints to confirm in
   the code, not the scope.
2. **Pick the changed functions that matter.** Take the functions whose logic the diff
   changed, not renames, formatting, imports or types. Put first the ones that touch
   I/O (queries, outbound HTTP), validation or branching. Leave out test files.
3. **Trace each one up to an entry point.** Find its callers, then their callers, until
   you reach a route handler, a queue consumer, a scheduled job or a CLI command. Follow
   the calls a text search misses: interfaces and dependency injection (find who uses the
   interface, not the class), events (find who emits them), middleware and decorators
   (they reach every route they are mounted on), callbacks passed by reference. If one
   function reaches more than about 8 endpoints, stop: keep the ones the PR's tests or
   description point to, and list the rest as `alsoReached`.
4. **Compose each endpoint's full path.** Walk up from the handler to where the app is
   created, adding every prefix on the way: `app.use('/api', r)`, `include_router(prefix=)`,
   `register_blueprint(url_prefix=)`, a class-level `@RequestMapping` or `@Controller`,
   `setGlobalPrefix`, a Ktor `route("/x") {}`. A prefix that only exists at runtime
   (config, an env var) is unresolved: read it from the running service (or, failing that, return it as a blocker), never guess.
5. **Reconcile with the hints.** A hinted endpoint with no trace is dropped, with the
   reason. A traced endpoint no hint mentioned stays in: the code wins.

Write `.hud-local-pov/routes.json`. Paths use `{param}` form (`:id`, `<int:id>` and
`{id:[0-9]+}` all become `{id}`); `via` runs from the changed function to the entry point,
each step `file::function`. **Core** is an entry point whose handler is in the diff or one
call from it; the rest are linked.

```json
{"pr": "#123", "routes": [
  {"method": "POST", "path": "/api/orders/{id}/items", "file": "src/routes/orders.ts", "line": 42, "core": true,
   "via": ["src/services/orders.ts::addItem", "src/routes/orders.ts::postItem"]},
  {"kind": "queue", "flow": "kafka:order.changed", "handler": "src/workers/stock.ts::onOrderChanged", "file": "src/workers/stock.ts", "line": 18,
   "core": false, "via": ["src/services/orders.ts::addItem", "src/events.ts::emitOrderChanged", "src/workers/stock.ts::onOrderChanged"],
   "trigger": "consumes order.changed, which every item add publishes"}
], "alsoReached": [
  {"method": "GET", "path": "/api/reports/daily", "file": "src/routes/reports.ts", "line": 9,
   "via": ["src/services/orders.ts::totals", "src/routes/reports.ts::daily"]}
]}
```

**Not HTTP** entry points (queue consumers, scheduled jobs, CLI commands) go in with
`kind`, `handler` and `trigger` instead of `method` and `path`, plus `flow`: the flow name
Hud records for it (the name the code passes to `wrapFlow`/`startFlow`, a DDP method's or
WebSocket handler's flow name, a consumer's queue flow). Hud stores it as the endpoint name
with no HTTP method; step 3 looks it up in `Endpoints` after its sweep rather than guessing. The
report matches non-HTTP entry points by `flow`, so one without it can't be the report's
primary endpoint. If a trigger is easy and
safe, add it to the run as a script entry; if not, report it as unexercised rather than
dropping it silently.

Show the user the list with each endpoint's `via` before going on.

**Mapping the whole service** has no diff to trace from. Ask the running service for its
route table instead: Spring's `/actuator/mappings`, FastAPI's `/openapi.json`,
`flask routes`, Django's `show_urls`, NestJS's `Mapped {/x, GET}` startup lines, Fastify's
`printRoutes()`, or a one-off script that walks Express's `app._router.stack`. Never
change the service's code to do it. Write every route to `routes.json`; step 3 then skips
`--pick`. With no diff there are no changed paths: map one success request per route, plus
the 5xx paths a plain request reaches (§5).

### 5. List the outcomes on the changed paths

Map **only the outcomes whose path runs through a function the PR changed**, plus **one
success request per route**. Not every outcome of every route: a route's validation
failure that never reaches the changed code tells the PR's story no better than its
success does, and every extra entry costs a probe and part of the forensics budget.

For each route in `routes.json`, read the handler and follow its calls into the changed
functions (`via`). List the distinct outcomes whose path goes through one of them:

- each success variant that takes a different branch of the changed code (a query
  param, a flag, a body shape);
- each validation failure, not-found, conflict or auth failure that the changed code
  decides or that happens after it ran;
- every path through the changed code where a plain request produces a **5xx** (an
  unhandled input, a random id in a foreign-key field, a constraint the code doesn't
  check).

Then add **one plain success request** for each route none of those succeeds on. A route
with no outcome on a changed path (a linked route the change only reaches on one branch)
still gets that success request. The PR's tests are the best guide: the cases they assert
are the outcomes the change was written for.

Each outcome becomes **one plan entry** in §7, tagged `changed-path` (the success-only
entries aren't). An outcome you cannot reach from a plain request (it needs a dependency
to fail, a race, or data nothing seeds) gets no entry: list it as unreachable. The run
injects no fault to reach it.

### 6. Work out state and auth

- **Ordering:** which outcomes need a record to exist first. Those consume a pool that an
  earlier create fills (`capture` → `{{pool:x}}`).
- **Auth:** start from the `auth.cmd` §2 wrote. When the PR's endpoints need another role
  or tenant than the preflight user has, find how a *test* identity gets one — a seed
  script, a dev login endpoint, a fixture user. Put the token in an env var and reference
  it with `{{env:NAME}}`. Never use a real user's credentials; if no test path exists, that
  is a blocker (X1's A1 and A2 answers didn't cover it).

### 7. Write the draft entries

Turn the outcome list from §5 into entries, wired with the state and auth from §6. How you
start depends on what you have. Add one request per in-scope endpoint that returns
anything but 404 to the plan's `preflight`, so a build without the PR's routes is caught
before the run. Send none of them: step 3 sweeps and probes the draft.

Request bodies come from the repo, not from the route list: integration and e2e tests and
fixtures (the hardest thing to invent correctly), `*.http` and `*.rest` files,
Postman/Insomnia/Bruno exports, README `curl` lines, or a spec the repo or the running
service serves (`/openapi.json`, `/v3/api-docs`, `/swagger.json`; reading it is not load).

**A curated plan already exists** (`plan.json` from an earlier run): narrow it to the PR's
routes and changed paths, then add entries for every route it lists as missing and every
§5 outcome it has no entry for. Tag the kept entries that are §5 outcomes `changed-path`
first: the script keeps those and one success request per route, and lists the variants
it drops.

```bash
python3 02-choose-pr/scripts/scope_plan.py .hud-local-pov/plan.json .hud-local-pov/routes.json > .hud-local-pov/plan-pr.json
```

**No spec, or a stale one** (most services): write the entries from the code into
`.hud-local-pov/plan-draft.json`, one per §5 outcome. Read
`02-choose-pr/references/plan-without-spec.md` first. In short:
- Take each request from the PR's tests first, then other tests and their helpers, then
  request files and docs, then the handler's schema. A HAR from the user is a blocker,
  asked only when none of these shows the fields.
- Give enums real values. Mark any value you can't find `{{unknown:field}}`.
- Wire records through pools and sign in as §6 worked out.
- Write multi-step scenarios from the PR's tests as script entries.

**A current spec:** draft from it. Past a handful of endpoints, writing entries by hand
stops being practical (Mealie: 273 operations), and a spec converts mechanically.
`--routes` keeps only the PR's operations and the creates that fill their pools, and
lists traced routes the spec lacks:

```bash
python3 02-choose-pr/scripts/openapi_plan.py http://localhost:9000/openapi.json --base-url http://localhost:9000 \
  --auth-env API_TOKEN --exclude 'scrape|ai-provider|/media/|backup' \
  --routes .hud-local-pov/routes.json > .hud-local-pov/plan-draft.json
```

It writes one success entry per operation with a schema-generated body, wires path
parameters to the pool its collection's create fills, and orders creates →
reads/updates → deletes. It always skips logout, password and token changes, backups, and
deletes of users, groups and households. Leave `--routes` off when mapping the whole
service. Then add the §5 outcomes the draft lacks, tagged `changed-path`, with bodies from
the PR's tests where the schema alone can't satisfy a create.

Record each entry's source in `_source`, then check the draft offline:

```bash
python3 shared/scripts/drive.py .hud-local-pov/plan-draft.json --dry-run
```

Drive **core** endpoints deeply, with flows from the PR's tests, and give **linked** ones
one plain request each. A draft entry is a hypothesis: step 3's probes show whether it
reaches its outcome, and its confirm step whether the changed functions ran.

### Required output before X3

Show the user one table: route, outcome, the changed function it runs through (`—` for a
route's plain success request), how it is reached (request / not reachable), and
expected status. Plus the non-HTTP entry points and whether each will be exercised.
**Unreachable outcomes are findings** — report them, do not drop them.

**So are silent no-ops.** An outcome that should be a 404 but returns 2xx and changes
nothing (update or delete a missing record, remove something that isn't there) is a
finding: Hud will record it as healthy. Where the code shows one on a changed path, tag the
entry `silent-noop` and list it next to the unreachable outcomes as suspected; step 3's
probe confirms it. Mealie has six.

**Leave the service running for X3.** Nothing else needs undoing at the end of this step:
the install stays, as the point of the POV, and the run's key lives only in `.env.local`.
