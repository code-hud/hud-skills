# hud-local-pov: see what Hud does with your own code

This is a skill for an AI coding agent, such as Claude Code. Point the agent at one of your
services, and in one working session it shows you what Hud would tell you about that service.
It runs a test instance of the service with Hud, sends a short traffic run to the endpoints
a recent change touched, collecting the changed code's callers, callees and durations,
and writes up what Hud saw. You use your own code, scoped to a change your team
recently shipped, not a demo app.

## What Hud is, in two sentences

Hud is a runtime sensor: an SDK that runs inside your service and records which
functions and endpoints run, how long they take, what fails, and why. It brings that
production context into code review and into AI agents, so a pull request is judged by
how the code actually behaves, not only by how it reads.

## What the skill is for

Installing Hud proves data arrives. It doesn't show what Hud is good for, because a quiet
service gives it nothing to show. This skill makes something happen, safely, on a test
instance of your service:

- **Your recent change, running.** The agent picks a pull request your team merged
  recently (or the one you name), and tells you why. It finds the endpoints that reach the changed code and
  makes sure that code actually runs.
- **How the change runs**: which functions call the changed code and which it calls,
  how often, and how long each takes, recorded by Hud during about 2 minutes of the
  change's own requests.
- **Real errors only**: if one of those requests hits a failure that already exists in your
  code, Hud captures it with the request, the exception and the code path that led to it.
  Hud calls these captures *forensics*. The agent causes no failure or slowdown on
  purpose.

At the end you get a one-page report for your team: what Hud saw, a map of how the
change runs through your code, and what Hud's pull-request review would check on a
change like it.

## How to start

Ask your agent: "Read https://skills.hud.io/install/skill/install-hud-sdk/SKILL.md and install hud sdk". Hud's install skill
asks which services to install Hud on and how to verify the install, installs and verifies
it, and connects the Hud MCP. When you verified locally, on your machine, it then suggests
this skill. Say yes, and it hands over your services, the instance it left running, where
the key comes from and each service's Hud name, so this skill doesn't ask again. This
skill runs locally only: after a remote verification, it isn't offered, and started on its
own it stops and says so.

You can also start this skill directly. It works out what it can from the repo, and if Hud or the
Hud MCP isn't set up yet, it sends you through `install-hud-sdk` or `install-hud-mcp` first
and then carries on.

## What will happen

The agent explains the run before it starts, then runs to the end **without asking you to
approve anything**: it picks sensible defaults and tells you what it chose. It stops only
when something has no possible default, such as a test login it can't find, and asks for
just that.

1. **Check what it needs, and pick the defaults.** The service and where it runs (from the
   install skill, or you), that Hud is installed on it and the Hud MCP is connected. Then
   it settles the rest from your repo and its defaults and shows you what it will use:
   where your non-production Hud key comes from, how a test user signs in, what happens to
   paid or outside services (off by default), seed data, and the service's dependencies on
   your machine. You can correct any of it; you don't have to answer.
2. **Get the service ready and pick the change.** It reuses the instance the install left
   running, with the install exactly as it is: the same service name and Hud's default
   settings, with no code changes. It sets up a test login and runs readiness checks, and
   sends no traffic until those pass. The pull request is how the report shows Hud's data
   at work in code review, and how that evidence lets a low-risk PR be approved
   automatically. It ranks recently merged pull requests by how much of the service they
   touch, checks the five best fits side by side (which endpoints reach the change, what
   can be shown locally, what would block it), takes the best (or the one you named when you
   started the run), and
   tells you why, with the other four. It then lists every endpoint that reaches the
   change, and the results the changed code can produce on each, plus one success per
   endpoint. It sends no traffic in this step beyond the readiness checks.
3. **Run it.** It sends each request once so Hud has its first data, checks Hud sees the
   service and the changed functions, fixes or drops the requests that don't behave as
   planned, then writes the traffic plan and shows it to you. It runs it for about
   2 minutes: the change's own requests, a few a second, with nothing broken or slowed.
   Then it waits until Hud has ingested the run.
4. **Show you what Hud sees.** It builds the report and publishes it in your agent's
   artifact view when your agent has one (Claude's artifacts, Codex sites); otherwise it
   gives you the file's path to open in a browser. Then it tells you what is still running
   and how to stop it.

## What you need to prepare

**Before you start:**

- **A service to test.** It should be written in Python, Node.js/TypeScript or Java/Kotlin,
  and it can be one service in a monorepo. It should have a few months of merged pull
  requests, because the run is built around one of them. You need a local clone with that
  history.
- **A test instance on your machine.** The run is local only: the service runs on your
  machine (usually with its dependencies in Docker), as Hud's install skill verified it.
  The run sends it synthetic traffic and creates test records.
- **Hud installed on the service**, or a few minutes to install it: the agent runs Hud's
  install skill ([install-hud-sdk](https://skills.hud.io/install/skill/install-hud-sdk/SKILL.md)) first when it isn't.
- **A Hud account. This comes first.** Everything else connects to it. Without one, the
  run can't start: talk to Hud to get one.
- **A non-production Hud key from that account.** Keep it in an environment variable or a
  file. The agent reads it from there and never asks you to paste it into the chat.
- **The Hud MCP, or five minutes to connect it.** It's how the agent reads Hud's data, so
  the run can't verify or report anything without it. The install skill usually connects
  it. If it isn't set up, the agent connects it by OAuth sign-in: the
  `install-hud-mcp` skill adds it to your agent's configuration itself (Claude Code or Cursor), keeping a
  backup. Your part is signing in to your Hud account in the browser (OAuth). If your agent can't open a browser sign-in (it
  runs headless or on another machine), you use an API key from the Hud app instead
  (**Settings → API keys**). If your account has none, Hud support issues one. Adding the
  MCP may mean restarting the agent once; it saves its progress and tells you how to
  continue.
- **A way to sign in to the service as a test user**: open signup, a seeded test account, a
  command that creates one, or an API token. If the only way in is single sign-on, have an
  API token or service account ready.
- **Tools on your machine:** git and Python 3.9 or newer; the skill's scripts use only its
  standard library, so there is nothing to install. Docker if the service's dependencies
  run in containers. The GitHub CLI (`gh`) is optional and gives better pull-request
  details.

**Defaults it takes,** so you can say otherwise when you start the run:

- Features that call paid or outside services (email, payments, LLMs, webhooks) are off.
- The changed area gets its data from the app's own seeder or seed script, when it has one.
- No database dump before the run and no cleanup after it.
- The pull request is the best of the five best fits; name one when you start the run to
  use it instead.

It asks only for what no default can supply: a way to sign in when the repo shows none, or
a secret the service can't start without.

**Time:** the traffic itself takes about 2 minutes. Setting up the test instance takes from a few
minutes to an hour, depending on how easily the service runs on your machine. Add
a few minutes for the first sweep and its fixes, plus about 5 minutes for Hud to process
the data before the report.

## What it changes, and what it doesn't

- **It changes:** the Hud install, which goes into the test instance's start command or
  configuration and stays when the run ends. It also leaves the test records the traffic
  creates.
- **In your repo:** the run's working files go in `.hud-local-pov/`, which is kept out of git.
  The agent doesn't commit or push anything. If the agent had to work around one of the
  skill's own instructions, it notes that in `.hud-local-pov/skill-notes.md` for whoever
  maintains the skill; the run's messages and report stay about your service.
- **Traffic is labelled:** every request carries a `local-pov` user agent and an
  `x-local-pov-run` header, so you can filter it out of your own logs.
- **It never touches production,** never plants a bug or a fault in your code or its dependencies, and
  never sends traffic until the readiness checks pass.

**Data:** Hud's error captures include request details from the test instance. Use test
data there, not real customer data.

**Usage analytics:** so Hud can see where runs get stuck, the skill sends an event when
each step starts and ends, and the report sends one when it's opened. Each event carries a
random id made for the run (kept in `.hud-local-pov/analytics.json`), the step, and on a
failure a few words on why. It never carries your code, keys, file paths, service names or
anything from your account. To turn it off, set `HUD_SKILL_ANALYTICS=0` (or
`DO_NOT_TRACK=1`) before you start: nothing is sent and the report sends nothing either.
Sending fails silently; `HUD_SKILL_ANALYTICS_DEBUG=1` sends in the foreground instead and
prints the HTTP status or the error.

## What not to expect

- **Alerts.** The run shows how the change behaves at runtime; it doesn't try to raise
  any. Most of Hud's automatic detectors need days of history and skip brand-new
  services, and test keys (`local`) never alert at all.
- **A load test, or a fault test.** The traffic is light on purpose, enough for stable
  numbers on the changed code, and nothing is broken or slowed to see how Hud reacts.

## How the skill's files are organised

`SKILL.md` is the agent's entry point: the rules for the whole run and the order of the
steps. Each step has its own folder, numbered in run order, with its instructions
(`SKILL.md`) and the scripts and references only it uses:

| Folder | Step |
| :-- | :-- |
| `01-check-inputs/` | 1. Check the install brief, Hud and the Hud MCP; settle every input with its default |
| `02-choose-pr/` | 2. Get the service ready, pick the change from the five best fits, map its outcomes |
| `03-prepare-run/` | 3. Prepare the run: the coverage sweep, Hud's view, probes, the traffic plan |
| `04-run-traffic/` | 3. Run it |
| `05-wait-for-ingestion/` | 3. Wait for Hud to ingest the run |
| `06-report/` | 4. Write the report |
| `shared/` | Used by several steps: the per-language notes, the traffic driver and its plan format, the usage analytics helper |

## Words you'll see

| Word | Meaning |
| :-- | :-- |
| Endpoint | An entry point into your service: an HTTP route, a queue consumer, a scheduled job |
| Forensic | Hud's capture of one request: inputs, the code path, the exception, the outbound calls |
| p90 | The response time 90 % of requests beat; Hud's charts use it |
| Caller, callee | The function that called another, and the one it called, as Hud recorded them |
| Plan | The list of requests and phases the agent writes, and shows you, before any traffic |
| Readiness check | A request that must succeed before any traffic is sent |
