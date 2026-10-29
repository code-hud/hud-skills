---
name: hud-local-pov-01-check-inputs
description: >-
  Step 1 (phase X1) of the hud-local-pov skill: validate the brief install-hud-sdk handed
  over (services, runtime, Hud wiring, verification setup), check the Hud MCP is connected,
  pick the language pack, read the repo for what a test run needs, and settle every input
  the run needs with its default, asking nothing unless an input has no possible default.
  Run it from hud-local-pov's SKILL.md, not on its own.
---

# Step 1 (X1) — Check the inputs and settle the defaults

Part of `hud-local-pov`. The orchestrator (`SKILL.md` at the skill root) holds the overview,
the guardrails and the order of steps. Every path here is relative to the skill root.
User-facing: **Step 1 of 4**.

**Usage analytics.** Run `python3 shared/scripts/analytics.py start 1` as you begin. Before
you return `GATE_PASSED`, run `python3 shared/scripts/analytics.py end 1 --ok`; before you
return `BLOCKED`, run `python3 shared/scripts/analytics.py end 1 --failed "<reason>"`, where
the reason is a few plain words on what failed, with no names, paths, URLs, keys or output.
Run nothing on `NEEDS_USER` (a blocker): the step isn't over. The helper returns at once and never fails.

| | |
| :-- | :-- |
| **Consumes** | `install-hud-sdk`'s hand-off brief (in the conversation, or `.hud-install-brief.md` at the repo root): services, runtime, `HUD_SERVICE`, where the key is read from, how Hud is wired in, whether the install was verified, the verification setup (local on this machine, with its instance), whether the MCP is connected; the repo; `.hud-local-pov/answers.json` on a resume |
| **Produces** | `.hud-local-pov/service-profile.json` (what a test run needs from the repo); `.hud-local-pov/answers.json` with the target service, its setup and instance, how its Hud install reads `HUD_KEY` and `HUD_SERVICE` and where Hud is wired in, the `HUD_KEY` source, the service's `HUD_SERVICE` as the install set it, the language pack, and every other input the run needs, each with its value and where it came from |
| **Gate to step 2** | The user has a Hud account; a brief exists, its verification ran locally on this machine, and it passes §2; the Hud MCP answers; the service's runtime has a pack; the service fits a run; every input is settled (from the brief, the repo, the request or its default) and saved. Otherwise stop, or send the user to `install-hud-sdk` or `install-hud-mcp` (below) |

**Files**

| File | Use |
| :--- | :-- |
| `01-check-inputs/scripts/discover_service.py` | Reads the repo, runs nothing, once the brief is validated. Writes the test-run facts the brief doesn't carry: health routes, auth and sign-in, dependencies and outside services, sibling services, serverless shape |
| `shared/references/service-setup.md` | The inputs (§3) and their defaults, and the setup (§1) |
| `shared/lang/python.md`, `shared/lang/node.md`, `shared/lang/jvm.md` | **The language pack**. Read the service's pack in X1 |
| `shared/scripts/key_check.py` | Which environment a key is for, without printing it |

Sections 1 to 5 read and check. Section 6 settles every input with its default and tells
the user what it chose. **Ask nothing for approval:** the user started the run, and every
input has a default. Ask only when the run truly can't go on without something no default
can supply (§6, "A real blocker").

**On a resume**, `.hud-local-pov/answers.json` already exists: read it, redo the checks in
§2 and §3 (the MCP may have just been connected), and settle only what is still missing.

## 1. The brief

**Trust `install-hud-sdk`'s brief; don't re-derive it.** It lists each service it verified
(path, runtime, `HUD_SERVICE`, where the key is read from, how Hud is wired in, whether it
was verified), the verification setup (local or remote; when local, the machine, and the
start and stop commands, port, PID and log of the instance it left running), the key's
source and environment, and whether the Hud MCP is connected. Read
`.hud-install-brief.md` at the repo root when the conversation doesn't carry it.

**No brief** (none in the conversation, no `.hud-install-brief.md`): return `BLOCKED` with
"No `install-hud-sdk` brief for this repo: run `install-hud-sdk` for the service", **before**
anything else. That holds when Hud looks installed
already: `install-hud-sdk` audits and verifies an existing install and writes the brief
this step needs. The orchestrator runs it and then starts this step again.

**Local runs only.** When the brief's verification setup was remote, ran on another
machine, or records no local instance on this machine (no start command and port), return
`BLOCKED` with "`hud-local-pov` supports local runs only: `install-hud-sdk` verified
`<service>` <remotely | on another machine | without a local instance>. Verify it locally
on this machine with `install-hud-sdk`, then run `hud-local-pov` again", **before**
anything else.

## 2. Validate the brief

Check what the brief says, not the code behind it: this skill doesn't detect runtimes,
processes or a Hud install itself.

- **Which service.** One service per run; a monorepo run exercises the named service, and
  its in-repo dependencies only as dependencies. One in the brief: use it. Several: pick the
  one that suits a run best: it already runs (the brief's local instance), and it serves
  endpoints the driver can call: an HTTP API rather than a queue worker, a cron
  job or a front end. When none clearly suits better, take the first in the brief. Say
  which and why in §6's summary.
- **Complete.** The chosen service has a path that exists in the repo, a runtime, a
  `HUD_SERVICE` name, a key source and its wiring; the setup is local on this machine, with
  a start command and a port. A missing field sends the user back to `install-hud-sdk`.
- **Verified.** `Verified: yes`, with the MCP's confirm part (the service visible and its
  app code mapped). Anything else returns `BLOCKED` with "Hud's install on `<service>`
  isn't verified: run `install-hud-sdk`'s audit for it".
- **The key comes from the `HUD_KEY` env var.** A key written in the repo returns `BLOCKED`:
  wiring it to the env var is `install-hud-sdk`'s job (R4), not this skill's.

Note in `answers.json`, from the brief: where `HUD_KEY` and `HUD_SERVICE` are read from,
the name the install uses, how Hud is wired in (the init file, the launcher and which start
commands carry it), and the setup with the instance's start and stop commands, port, PID,
log and whether it is still running. X2 runs the install as it is and changes none of it.

**The setup** is the install's local verification setup on this machine (`service-setup.md`
§1).

**The user needs a Hud account.** The key comes from it, and every result is read back
from it through the Hud MCP. If they have none, return `BLOCKED`: creating one is a
conversation with Hud, not something the skill does.

## 3. Is the Hud MCP connected?

Every later step reads Hud through it. Call `hud-get-schema`.

- **It answers:** run the visibility query (`shared/references/verify.md`, first query)
  for the service's `HUD_SERVICE`: the service listed proves the MCP and the key are on the
  same account (the install's verification sent data under that name).
- **It doesn't:** connect it by OAuth sign-in (`install-hud-mcp` §2), the default; an API
  key only when the request or the brief says so. After §6, save `answers.json` and return
  `BLOCKED` with "Connect the Hud MCP with `install-hud-mcp`, connect part, by
  `<OAuth | API key>`", noting that one agent restart may be needed. The orchestrator runs
  it and resumes this step.

## 4. The language pack

Take it from the brief's runtime: `shared/lang/python.md` for Python,
`shared/lang/node.md` for Node.js and TypeScript, or `shared/lang/jvm.md` for the JVM
(Java and Kotlin share it). Wherever this skill says "the language pack", follow that file.
For a runtime with no pack, return `BLOCKED`: this skill doesn't cover it yet. A JVM service
written substantially in Scala, Groovy or Clojure is one: `install-hud-sdk-jvm` calls them
untested.

## 5. What a test run needs from the repo

Only now, with the brief validated, run
`python3 01-check-inputs/scripts/discover_service.py --repo . [--path <service>]`. It writes
`.hud-local-pov/service-profile.json`: health routes, how users sign in (auth libraries,
login routes, default credentials, README notes), the dependencies and outside services the
service calls (and the third-party secrets it reads), sibling services in the repo and the
env vars that point at them, and whether it is serverless (with any local runner).

The service **doesn't fit** when it is serverless with no local runner, or needs a managed
service nobody can stand up on this machine for a test. If it doesn't fit, take another
service from the brief that does, and say why in §6's summary. If none fits, return
`BLOCKED` with why.

## 6. Settle every input, then say what you chose

Read `shared/references/service-setup.md` §2 and §3 now, and the profile's auth,
dependencies and siblings. Settle each input below from, in order: the user's request, the
brief, the repo, and the default in service-setup §3. Don't ask the user to confirm or
choose: take the value and say what it is.

1. **The service**, when the brief lists several (§2), and why.
2. **The `HUD_KEY` source**: the brief's verification key source (an env var or a secret
   file; never the value, never in the chat). Hud takes the environment from the key, and a
   **`local`** key draws charts and captures forensics but never alerts (the run aims at no
   alert). Check a key in a file with `python3 shared/scripts/key_check.py --env-file
   .env.local` (or `--file <path>`); exit 2 means a production key, which the run can't use.
   (A key the user pasted is already in `.env.local`: the orchestrator put it there.)
3. **The Hud MCP**, if §3 found it missing: OAuth, unless the request or brief says API key.
4. **The test service** (service-setup.md §3): "The test identity, data and outside
   services" (A1 to A6), then "This machine" (L1 to L4), each from the repo or its default.
5. **The run.** The plan lives in `.hud-local-pov/`. About 2 minutes of the PR's own
   requests (each endpoint's success request and the outcomes the change decides), no
   faults and no slowdowns, with results after Hud's 5+ minute ingestion lag.
6. **The PR.** The one the user named in their request (or several), if any. Otherwise X2
   ranks the last 60 days, checks the five best fits and takes the rubric's pick; it tells
   the user which and why.

Write everything to `.hud-local-pov/answers.json`: each value with where it came from
(`request`, `brief`, `repo`, `default`, or `user` for a blocker's answer). Never a key's
value. Then return `GATE_PASSED` with a short summary for the user, worded as information,
not a question: a few lines on what the repo shows for the setup (service-setup §2), then
what the run will use and where each came from (*"Sign-in: the seeded admin from
`scripts/seed.py` (repo). Email: off (default). The compose file starts Postgres, so the
run uses that."*). Don't end it with a question.

**A real blocker.** Ask the user only when an input has no possible default and the run
can't go on without it: sign-in credentials the agent can't find or create (no signup, no
seeded or default user, no seed script, SSO only), no `HUD_KEY` source anywhere, or a
secret the service can't start without and that has no test mode or switch to turn its
feature off. Return `NEEDS_USER` with **one** minimal blocker: what is missing, why the run
needs it, and how to provide it (a secret goes in `.env.local` or a file, never the chat).
Settle and save everything else first, so the blocker is the only thing asked. When the
reply comes, record it with source `user` and return `GATE_PASSED` with the summary.
