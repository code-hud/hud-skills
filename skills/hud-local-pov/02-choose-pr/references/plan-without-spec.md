# Writing a plan without a spec

Most services have no OpenAPI or Swagger spec, or have one that is stale. This page is
how you write plan entries for the chosen PR's endpoints from the code alone: X2 writes
them (§1–§5), X3 checks them against the service (§6). Use it whenever `openapi_plan.py` has nothing to read. Where a spec exists and is
current, prefer it.

Write the entries yourself, into `.hud-local-pov/plan-draft.json` (the top level copied
from `plan.json`: `baseUrl`, `headers`, `auth`, `preflight`, `setup`, `limits`, `phases`).
You already read every handler along the changed paths for the outcome list (X2 §5), so
you know the fields, the validation and the branches; a request is the same reading
written down. X2 checks the draft offline and sends nothing; X3 sweeps it, then probes
what the sweep shows wrong:

```bash
python3 shared/scripts/drive.py .hud-local-pov/plan-draft.json --dry-run                  # X2
python3 shared/scripts/drive.py .hud-local-pov/plan.json --phase coverage --pools .hud-local-pov/pools.json   # X3 §1
python3 shared/scripts/drive.py .hud-local-pov/plan.json --probe .hud-local-pov/plan-probed.json --pools .hud-local-pov/pools.json   # X3 §3
```

The endpoints come from `routes.json` (`02-choose-pr/SKILL.md` §4). Each entry is one
outcome on a changed path from X2 §5 (tagged `changed-path`), plus one success request per
route, not every outcome of every route. Non-HTTP entry points become script entries
(plan-format.md).

## 1. Take the request from the best source

In this order:

1. **The PR's own tests.** They exercise the changed code on purpose, with bodies that
   pass validation.
2. **Other integration and e2e tests**, and the helpers, fixtures and factories they use.
   A test helper that wraps a route (`createOrder(token, payload)`) holds the real bodies
   at its call sites, not in its definition.
3. **Request files and docs:** `*.http` and `*.rest` files, Postman, Insomnia and Bruno
   collections, `curl` lines in the README or docs.
4. **The handler's schema:** a DTO or record (`@RequestBody`), a Pydantic model, a Zod or
   Joi schema, a class-validator class, a WTForms form, or the fields the handler reads
   (`req.body.x`, destructuring). Follow the body into the functions the handler passes it
   to when the handler itself reads nothing.
5. **A recording from the user**, as a blocker (hud-local-pov's "No questions, only defaults": no default can supply it), when nothing above shows the fields (a generic
   validator, a schema loaded at runtime). Ask them to open DevTools, go to Network, use
   the feature, and choose "Save all as HAR". Keep the method, path, query and body; drop
   cookies and auth headers. Never ask for the HAR to be pasted into chat.

Record where each entry came from in `_source`:
`{"confidence": "example" | "inferred" | "guess", "from": ["tests/orders.test.ts:42"]}`.
`example` is a real request from the repo or a HAR, `inferred` is built from the schema,
`guess` has the route and nothing else. X3's sweep and probe fix guesses first.

Match the wire format the framework expects. Spring with `UNWRAP_ROOT_VALUE` on wraps a
DTO under its `@JsonRootName` (`{"user": {...}}`). A server-rendered form posts
urlencoded fields (`form`), not JSON.

## 2. Fill values the way the code expects them

- **Enums** get one of the values the enum defines. Read the definition.
- **Identity fields** (email, username, password) share `{{run}}`, so a register and the
  login after it agree: `pov.{{run}}@example.com`, `pov{{run}}`, `Pov-{{run}}-pw`.
- **Names, titles and slugs** get `{{uuid}}`, so creates don't collide on a unique index.
- **Numbers, dates and lists** follow the type and the validation rules (a minimum, a
  maximum, a future date).
- **A value you can't find** becomes `{{unknown:field}}`. The driver won't send that entry
  until the probe or you fill it in. Never invent a plausible-looking value instead: it
  hides the gap.

Templates are listed in plan-format.md ("Templates").

## 3. Wire the records together

- A create **captures** the new record's id (or slug) into a pool:
  `"capture": {"/api/orders:id": "**.id"}`.
- An entry that reads or updates a record uses `{{pool:/api/orders:id}}`; a delete uses
  `{{take:/api/orders:id}}`, so it never deletes a record another entry still reads.
- A body field that holds another record's id (`labelId`, `list_id`) points at that
  record's pool, not a random uuid. Keep a random-id variant only as its own entry when it
  is a real outcome (a 404, or a 500 worth showing).
- A path parameter nothing in the plan creates (`/profiles/{username}`) gets the signed-in
  user's own value.
- **Order:** creates, parents before children; then reads and updates; then deletes,
  children before parents. Give deletes a low `weight` (about 0.3), so they don't empty
  the pools the creates fill.

## 4. Sign in the way the tests do

Find how the tests get an identity: a fixture user, a register and login, a seeded
token, a dev login endpoint. Then:

- **JSON token APIs.** Put the register and the login in `setup`. The login captures the
  token from the reply under the field the code returns (`token`, `access_token`, `jwt`),
  and a plan header sends it with the scheme the auth middleware checks
  (`authorization: Bearer {{pool:token}}`, or `Token`, or a custom header). The register
  expects `[200, 201, 409, 422]`, so a second run with the same identity still passes.
- **Server-rendered forms** (Flask/WTForms, Django, Rails-style apps). Set
  `cookies: true`. Each form post fetches its page first for the CSRF token (`csrf`) and
  expects a redirect (`[302, 303]`); a 200 usually means the form came back with errors.
- **Anything else** (SSO, an API key, a seeded admin): write the identity by hand
  (X2 §6). Prefer `auth.cmd` for a token that expires.

Never update a password, email or token during the run: it locks out the setup login of
the next run. Never use a real user's credentials; if no test path exists, that is a blocker.

## 5. Build flows for multi-step scenarios

When the PR's tests chain requests (create a list, add items, check them off, remove
one), write the chain as a script entry ("Script entries" in plan-format.md) rather than
separate entries that hope the pools line up. This is how the PR's core endpoints get
their success paths through the changed code.

## 6. Check against the service: the probe (X3)

`drive.py --probe OUT` sends each entry once against the test service. When a reply is
a 400 or 422, it reads the validation error and fixes the body. It handles FastAPI,
Spring, express-validator, class-validator, Zod, Joi and Rails-style errors.
plan-format.md has the details. The probe also:

- re-runs a create when a later entry finds its pool empty
- adds a capture when an update renames its record

Its summary lists every entry it could not fix and why: 401/403 (the test user's role),
404 (wrong path, or data that does not exist yet), or a 5xx. A 5xx may be a real failure
path; keep it as a `server-error` entry.

A probe `ok` means the request is valid, not that it reached the PR's code. An empty list
or a minimal body often returns 200 early. For the PR's core endpoints, build the request
from the PR's tests, and check the changed functions ran (pr-selection.md, "Confirm
before timed traffic").

The probe creates real records. Run it only against the test service, and only on the
entries the sweep showed wrong. Every 5xx it hits also spends the SDK's forensics caps,
which nothing resets before the timed run (X3, "Mind the forensics budget").

## What "done" looks like

After the probe, at least four in five of the PR's entries should succeed with no hand
edits. Each of the rest should carry a note that says what is missing. These results were
measured with the drafting script this skill used to ship, which applied the same rules
by pattern-matching; writing the entries from the code should do at least as well:

| Service | Framework | Scope | After the probe |
| :-- | :-- | :-- | :-- |
| RealWorld (node-express) | Express + Prisma, TypeScript | a PR touching the article service: 11 routes | 11 of 11; a coverage sweep then had 33 of 33 as expected |
| fin-demo trading-api | Express | PR #121: 20 routes | 16 drafted from test helpers, including the order body |
| microblog | Flask + WTForms, CSRF, session login | 5 routes | setup register and login 302, then 5 of 5 |
| RealWorld (spring-boot) | Spring MVC, Lombok DTOs, `@JsonRootName`, JWT | all 19 routes: register and login to setup, 17 entries | 17 of 17; three sweeps of 51 requests, none unexpected |
| RealWorld (node-express), whole service | Express, JWT, no auth hints given | all 20 routes: register and login to setup, 18 entries | 18 of 18; a sweep with none unexpected |
| Mealie, PR #8225 (foods, tools, labels) | FastAPI + Pydantic, admin token from `auth.cmd` | 170 routes by import graph, trimmed to the 3 route files the PR changes: 17 | 17 of 17, including a merge of two foods and an update that needs the record's group; two sweeps of 55, none unexpected |

Below that bar, the usual causes are:
- **Auth:** the test user lacks a role.
- **Seed data the service expects:** an existing tenant or a catalogue. Add a `setup`
  that creates it.
- **A body the probe cannot learn:** the error does not name the field. Read the handler
  again and fill it in.

**Trim a wide scope.** A PR that changes a module everything imports (Mealie #8225 changes
`repository_factory.py`) reaches most of the service. Keep the endpoints the PR's tests
and description point to in `routes.json`, move the rest to `alsoReached`, and say so to
the user. When mapping a whole service with no spec, write the entries one route file at
a time and probe each batch.
