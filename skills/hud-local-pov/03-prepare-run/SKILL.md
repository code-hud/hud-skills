---
name: hud-local-pov-03-prepare-run
description: >-
  Step 3 (phase X3) of the hud-local-pov skill: prepare the run. Send the coverage sweep
  (the run's one coverage pass), check that Hud sees the service and match the PR's
  changed functions to Hud's, probe each outcome and fix or drop what doesn't reach the
  changed code, then write the final two-minute plan: one phase of the mapped requests.
  Run it from hud-local-pov's SKILL.md, not on its own.
---

# Step 3 (X3) — Prepare the run

Part of `hud-local-pov`. The orchestrator (`SKILL.md` at the skill root) holds the overview,
the guardrails and the order of steps. Every path here is relative to the skill root.
User-facing: the first part of **Step 3 of 4**.

**Usage analytics.** Run `python3 shared/scripts/analytics.py start 3` as you begin. Before
you return `GATE_PASSED`, run `python3 shared/scripts/analytics.py end 3 --ok`; before you
return `BLOCKED`, run `python3 shared/scripts/analytics.py end 3 --failed "<reason>"`, where
the reason is a few plain words on what failed, with no names, paths, URLs, keys or output.
Run nothing on `NEEDS_USER`: the step isn't over. The helper returns at once and never fails.

| | |
| :-- | :-- |
| **Consumes** | Step 2's `routes.json`, draft entries (`plan-draft.json` or `plan-pr.json`) and outcome table; `prs.json`, `hud-files.json`, `pr-choice.json`; `.hud-local-pov/plan.json` (its `preflight` and `auth`); `service.json`; the Hud MCP (the service's endpoints and functions); the language pack ("Metric flush", "Forensics") |
| **Produces** | `.hud-local-pov/plan.json` with its `coverage` and `run` phases; `pools.json` from the sweep; the sweep's UTC window in `service.json` (`coverage.startUtc`, `coverage.endUtc`); `hud-functions.json`, `scope.json`; `plan-probed.json` (when probed); the findings: unreachable outcomes, confirmed `silent-noop` entries and dropped entries, each with its reason |
| **Gate to step 4** | The coverage sweep was sent once and the Hud MCP lists the service under its `HUD_SERVICE` name in the sweep's window; `scope.json` is written and the changed functions ran (or why not is stated); every entry either behaves as planned or was fixed, or dropped and listed with its reason; the final `plan.json` has one timed phase (`run`, about 2 minutes, no hooks) and passes `--dry-run`. No trial runs beyond the sweep and the probes. The plan is shown to the user before it runs, as information, not a question: the user started the run (root `SKILL.md`, "X3 to X4") |

**Files**

| File | Use |
| :--- | :-- |
| `shared/references/plan-format.md` | The plan schema and driver flags. Read before writing a plan |
| `shared/references/example-plan.json` | A full example plan |
| `03-prepare-run/references/signals.md` | What Hud records from the run (coverage, call edges, durations) and the SDK forensics limits. Read before §4 |
| `02-choose-pr/references/pr-selection.md` | §4 (the scope and the functions match) and "Confirm before timed traffic" |
| `02-choose-pr/references/plan-without-spec.md` | For a plan drafted without a spec: what the probe learns |
| `02-choose-pr/scripts/rank_prs.py` | `--pick N` turns `routes.json` into `scope.json` and matches the changed functions to Hud's |
| `shared/references/verify.md` | The visibility query, `COVERAGE_START_UTC`, and the changed-functions query for "Confirm before timed traffic" |
| `shared/scripts/drive.py` | Zero-dependency traffic driver (Python ≥ 3.9, standard library only). Executes a plan; knows nothing about any service |
| `shared/references/troubleshooting.md` | Symptoms in the sweeps and in Hud, and their causes |

Keep every plan outside the commit: `.hud-local-pov/` at the repo root, added to
`.git/info/exclude` (local-only, no change to `.gitignore`), or a scratch directory.

**Mind the forensics budget.** Forensics go to the first hits of each 500 shape, wherever
they happen. Every SDK allows only a few per (flow, error shape), counted in process
memory, and the window is long: the language pack's "Forensics" has the numbers. The
service keeps running as the install left it, with nothing resetting those counts, so the
sweep and the probes in this step spend the same budget as the timed run. So:

1. Sweep once (§1): it is the run's coverage pass, and the timed run doesn't repeat it.
2. Keep probing of 500s to what the plan needs: resend only the entries you fixed, never
   the whole sweep again.
3. Start the timed run without coverage: `--phase run`.

A shape whose forensics were spent before the timed run still has them: the forensic is
real either way, and the report gives each forensic's own timestamp. Where the account's remote config tightens the cap (1 per shape per 6 h on
`field-test`), the first hit wins wherever it happens.

### 1. The coverage sweep

Merge step 2's draft into `.hud-local-pov/plan.json`: keep its `preflight` and `auth`, take
the draft's `setup` and `endpoints`, and give it one phase, `coverage` (`sweep`,
`repeat: 1`). Check it offline, then send it once:

```bash
python3 shared/scripts/drive.py .hud-local-pov/plan.json --dry-run
date -u '+%Y-%m-%d %H:%M:%S'   # coverage.startUtc
python3 shared/scripts/drive.py .hud-local-pov/plan.json --phase coverage --pools .hud-local-pov/pools.json --log .hud-local-pov/coverage.jsonl
date -u '+%Y-%m-%d %H:%M:%S'   # coverage.endUtc
```

Entries with `{{unknown:…}}` are not sent until a value is filled in (§3). Record both
times in `service.json` as `coverage.startUtc` and `coverage.endUtc`: the service reports
under its production name, so this window and the key's environment are what keep other
data out of every query (`COVERAGE_START_UTC` in `shared/references/verify.md`).

**This sweep is the coverage pass.** It invokes every reachable changed path under Hud, so
Hud has durations, call graphs and forensics to read; the timed run invokes the same
entries again in its mix. Don't send a second sweep, here or before the run.

Pass the same `--pools` to every later probe and the real run, so `setup` runs
once. Without it every invocation re-runs setup and leaves its records behind
(plan-format.md).

### 2. Hud sees it: visibility and function IDs

Once Hud has ingested the sweep (a few minutes), run the visibility query
(`shared/references/verify.md`, first query) for the service's `HUD_SERVICE` over the
sweep's window. Zero rows means the MCP is on another account, or the name or environment
differs: `install-hud-mcp` §4. `install-hud-sdk` already showed Hud maps the app's own
code; don't repeat that check here. Look up each non-HTTP entry point's `flow` in
`Endpoints` now, rather than guessing it.

Then build the scope and match the changed functions to Hud's (pr-selection.md §4). Ask
for the functions after the sweep, so files the service loads lazily are declared:

```bash
python3 02-choose-pr/scripts/rank_prs.py --prs .hud-local-pov/prs.json --hud-files .hud-local-pov/hud-files.json \
    --pick N --scope-out .hud-local-pov/scope.json --service SERVICE --why "<pr-choice.json's why>"   # prints the functions query
# run it through the Hud MCP; save the rows to .hud-local-pov/hud-functions.json, then:
python3 02-choose-pr/scripts/rank_prs.py --prs .hud-local-pov/prs.json --hud-files .hud-local-pov/hud-files.json \
    --pick N --scope-out .hud-local-pov/scope.json --hud-functions .hud-local-pov/hud-functions.json --why "<pr-choice.json's why>"
```

When mapping the whole service, skip `--pick`. Then run pr-selection.md's "Confirm before
timed traffic" over the sweep's window: a request that succeeds is not coverage of the PR
until it shows the changed functions ran. An endpoint that returns 2xx but never runs a
changed function was traced wrong: fix `routes.json`, re-scope, and fix or drop its
entries in §3.

### 3. Probe each outcome: fix it or drop it

Read the sweep's output and `coverage.jsonl`: every `unexpected`, `no-data` and
`unresolved` entry is a plan bug or a finding, and each draft entry was a hypothesis about
an outcome. For a plan written without a spec, or entries the sweep shows with wrong
bodies, probe them (a plan holding just those entries, when the rest behaved):

```bash
python3 shared/scripts/drive.py .hud-local-pov/plan.json --probe .hud-local-pov/plan-probed.json --pools .hud-local-pov/pools.json
```

The probe sends each entry once and fixes bodies from the validation errors; every entry
it can't fix says why. It aims at success, so check error outcomes (`client-error`,
`server-error`) by their status, not by the probe's verdict. What a fix usually takes, in
the order it usually matters:

1. **Seed what everything references.** A `setup` that reads the caller's own ids
   (`/users/self` or equivalent) and creates one of each parent resource into pools.
2. **Wire foreign-key fields.** A generated body sends a random uuid for `recipeId`,
   `listId` and similar; point each at the pool that holds real ones. Keep the random
   version as a separate entry if it 500s — that is a real failure path, not a plan bug.
3. **Alias pools** for routes keyed by something the create does not return (a slug, the
   caller's own id).
4. **Valid bodies from the repo's tests** for creates the schema alone cannot satisfy.
5. **Pin what is left.** A 403/404/422 the handler really returns on a changed path is a
   branch: set `expect` to it and tag `client-error`. A 5xx is tagged `server-error`.

An entry built for a 409 that returns 400 never reached its branch, and the timed run
would repeat the miss. When you test a request by hand (a merge, a delete), use a record
you create for the test, never one from a pool: a pooled record that disappears turns into
a 404 `unexpected` later. To confirm a fix, resend only the entries you changed, once (a
plan holding just them, `--phase coverage`), never the whole sweep.

**Drop what can't be reached**, and list it as a finding with the reason: an outcome no
plain request reaches (one that needs a dependency to fail, or data nothing seeds) is an
**unreachable outcome**. The run injects no failure to reach it. So is an entry the
confirm step (§2) shows never runs a changed function after its fix.
**Confirm silent no-ops:** an entry step 2 tagged `silent-noop` that returns 2xx and
changes nothing is a finding; keep it in the plan with `expect` set to what it returns. One
that returns its 404 after all loses the tag. Entries that never get data are reported as
unreached. On Mealie, curating a spec draft this way took it from 113 to 144 of 182
entries behaving as planned, and found nine real 500 shapes from plain requests.

### 4. Write the final plan

Read `shared/references/plan-format.md` and `03-prepare-run/references/signals.md`, then
add the timed phase to `plan.json`, keeping the `coverage` phase as the record of §1.

**One phase, about 2 minutes.** The run exists to gather Hud's runtime data on the PR's
code: the changed functions invoked, their callers and callees, and their durations. It
sends the mapped requests for the modified endpoints, and nothing else: no fault, no
slowdown, no baseline.

| # | Phase | Shape | Length | Produces |
| :- | :---- | :---- | :-- | :------- |
| 1 | `coverage` | `sweep`, every entry, `repeat` 1 | Sent once in §1, not again | Every reachable changed path invoked; plan bugs surface as `unexpected` |
| 2 | `run` | About **1 entry/s** (a few requests/s): each route's success request, plus the diff-related outcomes a plain request reaches (a validation 4xx, a variant on the changed code) | 2 min | Invocations, call edges and p50/p90 for the changed functions and their endpoints; forensics for any real 5xx on a changed path |
| | **Timed run** | `--phase run` | **~2 min** | |

**Light exercise, not a load test.** The POV needs the PR's endpoints and changed
functions to run often enough for stable numbers. Volume beyond that adds nothing, and it
risks taking the service down: about 120 requests/s deadlocked Mealie's database pool.
Keep the rate above. Weight the success requests of **core** endpoints highest, and keep
`client-error` entries to a small share (`weights`, e.g. `{"client-error": 0.2}`) so most
calls take the success path through the changed code. A `server-error` entry stays only
when a plain request reaches it; don't raise its weight to make more 5xx.

**Check the language pack's "Metric flush".** The whole run fits in one or two of Hud's
~5-minute chart buckets, and Node's flush (the account's remote config, 5 minutes when it
sets none) may land it in a single row per process. Plan the results as run totals.

**Time budget.** The run takes **2 minutes**. Add Hud's ingestion lag before checking
(5+ minutes). State how long it takes with the plan.

```bash
python3 shared/scripts/drive.py .hud-local-pov/plan.json --dry-run
```

`--dry-run` is the last check: the `run` phase isn't sent before X4. Show the
user the plan with the findings from §3.
