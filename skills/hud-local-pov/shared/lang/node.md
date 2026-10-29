# Language pack: Node.js and TypeScript

Read in X1 when the service is Node.js or TypeScript, and follow it wherever SKILL.md says
"the language pack". Every pack uses the same headings; `signals.md` holds the cross-SDK
tables.

**Install skill:** `install-hud-sdk-node`, which gives hud-sdk 1.8.12 as the minimum for HTTP
and custom flows and 1.8.14 for queue flows.

## Install check (when Hud maps none of the app's code)

`install-hud-sdk` confirmed the app's code is mapped; use this only to diagnose a run where
the PR's changed functions get no data in Hud after X3's coverage sweep.

1. **Primary: ask Hud.** Count the service's functions by file through the Hud MCP. App
   files must be there:

   ```sql
   SELECT multiIf(f.file LIKE '%/<app source folder>/%', 'app', f.third_party != '', 'third-party', 'other') AS kind,
          count() AS functions
   FROM Functions f WHERE arrayExists(s -> s.2 = 'SERVICE', f.services) GROUP BY kind LIMIT 10
   ```

   Pick the pattern from what Hud recorded, not from the start command: first list a few
   of the service's own files (`SELECT DISTINCT f.file FROM Functions f WHERE
   arrayExists(s -> s.2 = 'SERVICE', f.services) AND f.third_party = '' LIMIT 20`). When
   the build ships source maps, Hud records the mapped source paths (`src/…/x.ts`), so
   `%/dist/%` finds nothing even though the app runs from `dist/`. Without maps it records
   the compiled `dist/…/x.js`. If unsure, count both (`f.file LIKE '%/src/%' OR f.file LIKE
   '%/dist/%'`).
2. **Local fallback: the debug log** (`HUD_VERBOSE=true`, which prints the key: read it
   through the mask, "Logging hazards" below). It must show `Hud: Your service is sending data
   successfully`, and `FunctionDeclaration` and `Invocations` in its sends. A log showing
   only `Session started` means the app's code isn't mapped. The four lines come in order (`Hud session
   started`, `First code mapping sent`, `First invocation sent`, `Your service is sending
   data`), and where they stop is the diagnosis (`install-hud-sdk-node` Phase 4).

Built TypeScript runs from `dist/`. The declared files are the compiled ones unless source
maps ship with the build, in which case they are the mapped sources.

**A real failure looks like this** (`install-hud-sdk-node`, Troubleshooting): endpoints arrive
but the app's functions are missing, because `includeModules` doesn't name the workspace
packages symlinked into `node_modules`. Or the mapping is sent and no invocation follows,
because an ESM app imports Hud at the top of its entry file, after its frameworks have
loaded. `verbose: true` in `register()` (or `HUD_VERBOSE=true`) logs which files loaded
before Hud attached.

**Manual flows that record nothing.** The service's output shows, once, `Hud: Warning -
wrapFlow() was called before register() and was skipped. … E1126`, and the flow never
appears in Hud. Two causes (`install-hud-sdk-node`, Troubleshooting):

- **`wrapFlow` ran before `register()`.** It returns the handler unwrapped, and that handler
  never records a flow, even after `register()` runs. Wrap where the handler is registered,
  in code that runs after the init file (`--import` loads it before anything else).
- **Two copies of `hud-sdk` in one process.** Flow state lives in each copy, so a `wrapFlow`
  from the copy that never ran `register()` skips with the same warning. `npm ls hud-sdk`
  (or `pnpm why hud-sdk`, `yarn why hud-sdk`) must show one resolved copy.

## Metric flush

The SDK default is every **5 minutes**; the account's remote config can change it (180 s on
Hud's own accounts in Oct 2026). The run doesn't change it, so the 2-minute run
lands in one or two metric rows per process: report the run's totals. The first flush always
comes about 30 s after start, whatever the interval.

Hud stores one metric row per flush, so with 60 s the rows sit a minute apart: that is the
interval working, not a separate bucketing. To read the interval in effect, look at the
spacing of `timestamp_end` for one session in `EndpointMetricsHighResolution`. The startup
log's `Current dynamic configuration` block shows the remote or default value, not the
override.

A short-lived process can exit before it flushes: run `main` in a flow and exit cleanly,
not through `process.exit()` mid-flight (`install-hud-sdk-node`, Troubleshooting). `wrapFlow`
returns the wrapped function and runs nothing itself: `const run = wrapFlow('script:<name>',
main); await run();`.

## Forensics

- **Switched on by** the account's remote config. The run doesn't change it.
- **Caps:** 2 error investigations per 60 s and 5 of all kinds per process. Dedup is
  2 per (flow, error shape) per **1 h**.
- **Budget:** the counts live in process memory, and the run doesn't restart the service,
  so what X3's coverage sweep and probes spend is gone for the timed run.
- **Remote config** can tighten all of this. Read the startup log's
  `investigationRateLimits` block, through the mask ("Logging hazards").
- **Log lines:** forensics sent shows `Stored object successfully`, then
  `sent batched events successfully` with type `FlowInvestigation`. Capped forensics are
  dropped silently; look for `Resetting global investigation counter`.
- **Function arguments** are captured only with `enableInvestigationArgsSending`.

## Logging hazards

**The Node SDK prints the full `HUD_KEY` when `HUD_VERBOSE=true`:** once at startup, in the
`Initializing hud userConfig` block (`"key"`, then `"value": "<the key>"`). Seen on hud-sdk
1.10.6 and 1.11.0. `HUD_DEBUG=true` alone prints nothing to the console; it only raises the
level of what `HUD_VERBOSE` prints. So:

- Don't turn on either for the run. The install check above asks Hud through the MCP and needs neither.
- If the install check must fall back to the log, set them for that one start, send the
  service's output to a file rather than the conversation, and read it only through the
  mask in `signals.md`, or print counts.
- Turn them off as soon as the diagnosis is done, and delete that log (or scrub the key from it).
  Never paste a raw Node SDK log into the conversation, the report or a skill note.

## How the process serves requests (sizing the rate)

One event loop per process. A slow endpoint whose rate × latency approaches 1 s of work per
second makes every endpoint queue behind it. On the `node-todo` run, an unpaginated list
over a large table took every endpoint's p90 to about 16 s, with timeouts. Keep it under
about 0.3 per process by lowering that entry's weight. Cluster mode and PM2 multiply
the processes, and with them the forensics caps.

## Outbound calls the SDK records

- **`pg`:** recorded under CommonJS, but **not patched under ESM**. An ESM app's SQL won't
  appear.
- **The `undici` package (6.7.0 or newer) and Redis (ioredis, node-redis):** from SDK
  1.11.0. Global `fetch` is not recorded (`install-hud-sdk-node`, Troubleshooting).
- **Bundled dependencies:** a client inlined into the bundle is no longer loaded as a
  module, so it isn't patched. This is what makes database calls go quiet in an
  aggressively bundled build (`install-hud-sdk-node`, Bundlers).

Check the app's module type (`"type": "module"`, `.mjs`) and the SDK version before X2.
Don't count a PR's database or HTTP changes as part of its story when Hud can't see them.

## Metric rows (counting)

X6's report compares one plain entry's Hud count with what the driver sent (its count
check). When you count by hand, check an entry that returns mixed statuses the same way.
If the two disagree, trust neither number until the count check explains the difference.

## Endpoint names

Route templates. Express declares `/api/articles/:slug` and Hud records that form; the
skill's own tools normalise it to `/api/articles/{slug}`. Use Hud's form in scope files and
report queries.

Routes Hud can't infer get a manual `setHttpFlow('/orders/{id}', 'GET')`, called inside a
request Hud already tracks, with a template, never a concrete path. Consumers wrapped with
`type: 'queue'` are queue flows; other `wrapFlow` names are custom flows (`install-hud-sdk-node`,
Phase 3).

**Meteor.** DDP methods and publications run over Meteor's WebSocket, which no Hud
framework covers, so each one is a manual custom flow, wrapped where it is registered:
`Meteor.methods({ 'orders.add': wrapFlow('ddp:orders.add', addOrder) })`. The wrapper
passes `this` through, so `this.userId` still works. Their flow names go in `routes.json`
as `flow` (`02-choose-pr/SKILL.md` §4), and the report matches them by that name. `meteor run` starts the
server as its own child process; Hud has to load in that process, so prefer the built
bundle (`meteor build`, then `node --import ./hud-init.js main.js` in its output) and
confirm with the install check that the app's files are mapped.

## Start command and images

- **Preload the init file:** `node --import ./hud-init.js dist/index.js`. `--import`
  instruments both ESM and CommonJS; `--require ./hud-init.cjs` works only when the app and
  every dependency are CommonJS. Where the command can't change, set
  `NODE_OPTIONS="--import ./hud-init.js"`. An import at the top of the entry file is the
  last resort: in an ESM app the frameworks load first. See `install-hud-sdk-node` Phase 1 for
  `hud-init.js` and `includeModules`.
- **ESM with OpenTelemetry:** set `HUD_USE_ASYNC_ESM_HOOKS=true`. Not with `dd-trace`.
- Prefer the built start (`node dist/main.js`) over watch mode (service-setup.md §4).
- **Several processes in one image** (an API and a worker): each process needs its own
  `register()` (`install-hud-sdk-node`, Audit). Wrap every process on the request path, and
  decide before X2, the same as `python.md`.

## PR ranking (X2)

- **Compiled TypeScript.** Without source maps Hud records the file that runs:
  `/app/dist/routes/x.js` for the repo's `src/routes/x.ts`; with maps shipped it records the
  mapped source. The ranker matches without the extension and by path suffix, so either
  file list works. Line numbers are another matter: without maps a changed function in
  `x.ts` sits at different lines in `x.js`,
  and `scope.json` gets no `changedFunctions`. Check one known function's `start_line` in
  Hud against the `.ts` source. If they differ, confirm the PR by function name instead
  (`shared/references/verify.md`).
- **Bundled builds** (esbuild, webpack, `ncc`) collapse the app into one or a few files.
  Hud's list then doesn't name the source files, and no PR matches. Rank with
  `--hud-files` built from the repo's own file list for the service, and say so.
- **Tracing to a route** (`02-choose-pr/SKILL.md` §4). NestJS injects services through
  constructors, so search for the service's class name in other constructors, then the
  `@Get`/`@Post` method that calls it; the path is `setGlobalPrefix` + `@Controller` +
  the method's decorator. In Express, Koa and Hono, follow the router file up through
  `app.use('/prefix', router)` (or `app.route()`); in Fastify, through
  `register(plugin, { prefix })`. A changed middleware reaches every route mounted after it.
