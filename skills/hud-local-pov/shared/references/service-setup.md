# Providing a test service

Phases X1 and X2. The POV needs a running test instance of the service on this machine,
which it is allowed to send a few requests a second to, and that the driver can reach with
a working test identity. The instance is the one `install-hud-sdk` verified locally and
left running; this page covers what the run still needs from it.

**Nothing here is a question for the user.** X1 settles every item in the tables below
(§3) from the request, the brief, the repo or its default, and tells the user what it chose.
X2 then checks the instance and signs in without asking. The tables are a checklist of
what must be settled before load, not a script to read out. The user is asked only for an
item with no possible default, as one blocker (X1 §6).

The outcome is two files, and the phase is done only when the second one passes:

| File | Holds |
| :-- | :-- |
| `.hud-local-pov/service.json` | How the test service runs and how to reach it: base URL, start and stop, dependencies, where each secret comes from (names, never values), the test identity and data setup |
| The plan's `preflight` list | The readiness checks. `python3 shared/scripts/drive.py plan.json --check` must exit 0 before any load is sent, and the driver re-runs it at the start of every run |

## 1. Where the test service runs

**On this machine, only.** The run starts from `install-hud-sdk`'s local verification on
this machine: the service and its dependencies run in Docker or natively here, and the
instance the install left running is the one the run uses. A brief whose verification was
remote, or ran on another machine, has no instance the run can use: X1 stops there and
tells the user this skill supports local runs only. Without a brief, X1 sends the user to
`install-hud-sdk` first.

Local fits when the repo shows a compose file or common infra only (Postgres, Redis, a
queue), no required third-party secret without a test mode, or a serverless app with a
local runner. Also say what decides alerts: the Hud key's environment. A `local` key draws
charts and captures forensics, but never alerts (X1).

## 2. Share what the repo shows

```bash
python3 01-check-inputs/scripts/discover_service.py --repo . [--path services/orders]
```

X1 runs it once the brief is validated; run it again only if the profile is missing. It
reads the repo without running anything and writes `.hud-local-pov/service-profile.json`
with what a test run needs beyond the brief: health routes, how users sign in, what the
service uses (databases, caches, queues, third-party APIs, and the third-party secrets it
reads), sibling services, and the serverless shape. How the
service runs (runtime, start and stop commands, port) and its Hud install come from the
brief, not from here.

In a monorepo, pass `--path` for the service; the repo-level compose file, env examples,
README and deploy config are still read (for the dependencies and env vars they name), and
sibling services are listed. Look for these in
the profile:

| Field | What it changes |
| :-- | :-- |
| `shape` | Serverless (Lambda, SAM, CDK, Serverless Framework) and any **local runner** the repo ships (`scripts/local-server.js`, serverless-offline, `sam local`). Without a local runner, a serverless service doesn't fit a run |
| `sibling_links` | Env vars that point at other services in the repo (`FRAUD_SERVICE_URL` → `services/fraud-service`): the PR's call path may need them running (A5) |
| `dependencies` | Databases, caches, queues and outside APIs, from compose, manifests and Prisma, plus each `third-party secret` the code reads (`STRIPE_API_KEY`, with whether it has a default): the A3 item and the feasibility pass's `external-service` blockers |
| `health_routes`, `auth.routes`, `auth.hints` | Candidates for the `preflight` checks (§6) and the sign-in (A1, §5) |
| `auth.readme_notes`, `auth.default_credentials` | How the README says to sign in (a header like `X-User-Id`, "any password works", a seeded admin), and secrets with a hard-coded fallback in code, which run with that value when unset: set a real one |

Then read what it points at: the README's run section, the compose file, `.env.example`,
the settings module and the seed script. The profile is a map, not a substitute for
reading. **Never read values from a real `.env` or secrets file**, only the names.

Then tell the user in a few lines what you found, as information, **framed by the local instance the install
verified**: what a run on it will need from the repo, and anything in the profile that
argues against it. *"The instance the install left on port 9000 uses Postgres, plus
optional SMTP and an OpenAI integration (`OPENAI_API_KEY` has no default, so AI features
stay off unless you say otherwise). Login is `POST /api/auth/token`, and a default admin
is created on first start."*
Don't ask whether it is right: the user can correct it at any point.

## 3. What must be settled

X1 settles each item below from the request, the brief or the repo, and takes the default
when they say nothing. Blocking items (**B**) must be settled before the service is
started or load is sent. An item is asked only when it has no possible default (A1 with no
way to sign in, L2 for a secret the service can't start without): then it is X1's one
blocker. Every value goes to `.hud-local-pov/answers.json` with where it came from, and
into X1's summary for the user.

### The test identity, data and outside services

| # | What to settle | Why | Default | |
| :-- | :-- | :-- | :-- | :-- |
| A1 | **How does a test user sign in?** Offer what the profile found: open signup, a seeded or default admin, a CLI or seed script that creates users, an API token page, SSO only | The driver needs a token it can refresh. SSO-only services need a long-lived API token or a test login | Use what the repo provides for local development. Nothing at all (no signup, seeded or default user, seed script or token page): X1's blocker | B |
| A2 | The role that user needs to reach the PR's endpoints (admin, a tenant, a group) | A user without the role gets 403 on half the scope | The highest test role available | B |
| A3 | The features that call paid or outside services (email, SMS, payments, LLMs, webhooks), from the profile, and whether each is **off**, on a **sandbox** key, or **stubbed** | Load turns them into real messages, real spend and partner traffic | Off, or a local catcher (Mailpit) for email | B |
| A4 | The data the PR's area needs to exist first (products, lists, accounts), and the seed script or dump that makes it | Endpoints over empty tables return empty results and never reach the changed code | **The app's own seeder first.** Many apps ship one as an endpoint or command (Mealie's `POST /api/groups/seeders/foods` made 583 realistic foods in 4 s). Look for it in the repo's seed scripts and admin routes, and use it in `setup`. Then let the plan's setup create the rest. Seed enough to reach the changed code, not more | |
| A5 | The service calls {siblings} (from `sibling_links`): start them too, point at a stub, or rely on its fallback when they're down | A dependency that's down turns the PR's endpoints into errors or fast fallbacks that never reach the changed code | Start the ones on the PR's call path | B (when siblings are listed) |
| A6 | A database dump before the run, or the run's records cleaned up after it | The run creates records; the report says what it left | Neither, unless the request asks for one; then the user's own method | |

**An item the repo answers is a statement in the summary.** *"The Makefile already
starts Postgres in Docker, so I'll use that"* instead of asking whether Docker is
available.

### This machine

| # | What to settle | Default | |
| :-- | :-- | :-- | :-- |
| L1 | The compose file starts {services}: start only those with `docker compose up -d {services}`, and run the app itself natively so Hud can be attached | Yes. Check that the ports are free first (`lsof -iTCP:<port> -sTCP:LISTEN`); pick others if they are taken, never stop something you didn't start | B |
| L2 | For each required variable with no default, where `{NAME}` comes from: `.env.local`, a file path, or an existing environment variable | Generated local-only values for app secrets (`SECRET_KEY` and the like: `openssl rand -hex 32`) in `.env.local`; a third-party secret's feature off (A3). Only a secret the service can't start without, with no test mode or switch, is X1's blocker | B |
| L3 | Docker is available, and the POV leaves its containers running until the end | Yes. The report ends with the command that stops them | |
| L4 | A database already running on this machine may hold data the user cares about | The POV uses its own container and database name, and never migrates, seeds or resets one it didn't create | B (when a local DB is already running) |

Also check the app's debug or inspector port (Node's 9229, a JVM debug port) along with
its HTTP port: watch-mode dev servers open one, and a second instance fails on it.

## 4. Start it again

Only when the instance the brief describes isn't running any more (X2 §1). Work from the
brief's start command and the repo's own commands. Don't invent a setup the repo doesn't
use.

1. **Dependencies.** Start only the infrastructure (`docker compose up -d postgres redis`).
   Wait for each one's healthcheck, or for its port to accept connections.
2. **Configuration.** Put values in `.env.local` and add that file to `.git/info/exclude`
   so it can't be committed. The user writes real secrets there themselves; you write only
   generated local values and non-secret settings. Never ask for a secret in the chat.
   **Check that every consumer actually reads that file**: the app's settings loader, and
   separately the ORM or migration CLI (Prisma, Alembic, Flyway), which often read only
   `.env` or nothing. When one doesn't, load the file into the shell for each command,
   and in the recorded start command: `set -a; . ./.env.local; set +a; <command>`. Don't
   write a `.env` that could overwrite the user's own or be committed.
3. **Install.** Use the repo's package manager and lockfile. Package managers that block
   install scripts (npm 11's allow-scripts, pnpm) can skip native builds; if the app then
   fails on a missing binary or generated client, run the generate step the README names
   (`npx prisma generate`), or approve the scripts.
4. **Schema and data.** Run the migrations, then the seed, with the repo's commands.
   **Check the seed worked**: count rows, or call a list endpoint. Seed scripts can fail
   partway, on a race or a unique constraint, and still exit 0.
5. **The app.** Start it with the brief's start command (the one the install loads Hud
   on), in the background, logging to a file (`.hud-local-pov/app.log`). **Prefer
   a non-watch start** (the built output, `node dist/main.js`, `java -jar target/app.jar`
rather than `bootRun` with devtools, no `--reload`). Watch-mode
   dev servers restart when files change, fork child processes and open debug ports.
   Record the PID you started, and **stop the whole process tree** when it's time, not just the
   port owner: `pkill -P <pid>`, then check the HTTP and debug ports are free.
6. **When it fails, read the log and fix one thing at a time.** Most first starts fail on
   one missing variable, one dependency that isn't up yet, or a migration. Map the error to
   its cause. Only if the fix needs the user is it a blocker: return one focused question,
   marked as a blocker that came up during the start: *"It stops on
   `KeyError: 'STRIPE_API_KEY'` at import. Set a Stripe test key, or should I start it with
   payments switched off (`PAYMENTS_ENABLED=false` exists in settings.py)?"* After three
   failed attempts at the same step, stop and show the user the log line.

Record the start and stop commands, the log path and the port in `service.json`. The
report tells the user how to stop it.

## 5. Get a test identity

Turn A1's answer into **the auth command**, the plan's `auth.cmd`. Its contract (plan-format.md, `auth`): print the full header value on stdout (`Bearer …`, `Token …`) and nothing else, exit 0; on failure, exit non-zero with the HTTP status on stderr, so a 429 doesn't look like a missing token. Save it as `.hud-local-pov/auth.py` (or `auth.sh`) when it's more than a one-liner:

```json
"auth": { "cmd": "curl -s -X POST localhost:9000/api/auth/token -d username=$POV_USER -d password=$POV_PASS | python3 -c \"import json,sys; print('Bearer ' + json.load(sys.stdin)['access_token'])\"",
          "every": "45m" }
```

When signup is open, register the test user once, then log in for the token. The
driver gives every command `BASE_URL`:

```python
# .hud-local-pov/auth.py, used as  "auth": { "cmd": "python3 .hud-local-pov/auth.py", "every": "45m" }
import json, os, urllib.error, urllib.request

def post(path, body):
    req = urllib.request.Request(os.environ['BASE_URL'] + path, json.dumps(body).encode(),
                                 {'content-type': 'application/json'})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)

user = {'email': os.environ['POV_USER'], 'password': os.environ['POV_PASS']}
try:
    post('/api/users', {'user': dict(user, username='pov-user')})
except urllib.error.HTTPError as err:
    if err.code != 422:  # 422 once it exists: fine
        raise
print('Token ' + post('/api/users/login', {'user': user})['user']['token'])
```

Use the scheme the API expects (`Bearer`, `Token`, a custom header). Credentials come from
environment variables, never from the plan file: the user exports them in the shell that
runs the driver, or keeps them in a file excluded from git and sourced first. A failing
`auth.cmd` stops the run with its error and a check of whether the service answers at all.
Set `every` below the token's lifetime: a token that expires mid-run turns the second half
into 401s. Leave `every` off for a token that doesn't expire (DRF tokens, API keys).

**The driver caches the token** (`.hud-local-pov/auth.cache`, mode 600), so the many
`--check`, sweep, probe and run invocations sign in once, not once each. That matters because **login
endpoints are often rate limited** (paperless-ngx allows 5 token requests a minute): an
uncached sign-in per run fails a healthy readiness check with a 429. Look for a throttle
on the login route while reading the repo (`throttle`, `ratelimit`, `limiter`), and tell the
user if there is one. When the database is recreated, the cached token stops working; the
driver notices the 401 and signs in again. If the only way in is SSO, ask for a long-lived API token or a service account.
If there is none, tell the user the run cannot be authenticated and stop.

## 6. Prove it is ready

Write the readiness checks into the plan before writing anything else in it:

```json
"preflight": [
  { "name": "service answers",            "method": "GET",  "path": "/api/app/about" },
  { "name": "test user is signed in",     "method": "GET",  "path": "/api/users/self" },
  { "name": "test user can write",        "method": "POST", "path": "/api/households/shopping/lists",
    "body": { "name": "hud-local-pov preflight {{uuid}}" }, "expect": [201] }
]
```

**No health route?** Use the cheapest route that answers without signing in: the login
page (`/accounts/login/`, `/login`), the API root, or the OpenAPI document. A redirect to a
login page is fine for "service answers" (`"expect": [200, 302]`). For the authenticated
read, prefer an admin or status route that reports the dependencies (paperless-ngx's
`/api/status/` covers the database, Redis and Celery), so one check also proves the
workers are up.

| Check | Catches |
| :-- | :-- |
| An unauthenticated health or info route | The service isn't running, wrong port or host, TLS, VPN |
| An authenticated read | A missing or wrong token, an SSO redirect, a role that isn't allowed |
| A write in the PR's area, when writes are allowed | A read-only database, failed migrations, a missing dependency behind the write path. Give unique fields a `{{uuid}}`: a fixed title fails the second check on APIs that enforce uniqueness |

After X2 picks the PR, add one request per in-scope endpoint that returns anything but
404, so a build without the PR's routes is caught before the run.

```bash
python3 shared/scripts/drive.py .hud-local-pov/plan.json --check
```

Exit 0 means ready. Exit 3 prints each failing check with what it usually means: nothing
listening, a host that doesn't resolve, a rejected token, a redirect to a login page, a
wrong path prefix, or a dependency failing behind a 500. Fix and re-run. **Send no load,
not even the coverage sweep, until `--check` passes.** Every later run re-checks
automatically and refuses to start if the service has gone away.

## 7. Write the record

`.hud-local-pov/service.json` records how to reach, run and leave the test service. Keep
the keys that apply, and never write a secret's value: name where each comes from. It
records how to stop what runs:

```json
{
  "baseUrl": "http://localhost:9000",
  "start": { "cmd": "uv run mealie", "cwd": ".", "env_file": ".env.local", "log": ".hud-local-pov/app.log", "port": 9000 },
  "stop": "pkill -P <pid of the start shell>; then confirm ports 9000 and any debug port are free",
  "dependencies": [ { "what": "PostgreSQL 15", "how": "docker compose up -d postgres", "port": 5433 } ],
  "env": [ { "name": "SECRET", "source": "generated, .env.local" },
           { "name": "OPENAI_API_KEY", "source": "unset: AI features off (A3)" } ],
  "auth": { "type": "password login, seeded admin", "cmd_env": ["POV_USER", "POV_PASS"], "token_lifetime": "1h" },
  "data": { "migrate": "alembic upgrade head (runs at start)", "seed": "none needed: plan setup creates lists" },
  "readiness": { "checked_at": "2026-10-16T14:02:11Z", "passed": true },
  "at_end": "stop the app and the postgres container"
}
```

`start.note` says what the first start had to skip, such as a
Hud launcher that wasn't installed yet.

## Stop and ask

- The target looks like production, or the user can't say whether it is.
- The service can only be signed into through SSO, and there is no API token or service
  account.
- A required third-party integration has no off switch, no sandbox and no stub, and the
  user wants it left on.
- A secret is about to be pasted into the chat. Point the user at `.env.local` or a
  secret file instead.
