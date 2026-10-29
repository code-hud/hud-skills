# Choosing the PR that frames the run

A run scoped to **one well-chosen PR** makes a better story than load spread over every
endpoint. The surface is small enough to plan properly (auth, state and data stay
bounded), and the result has a narrative a prospect recognises: *this is a change your
team shipped, these are the endpoints it reaches, and this is what Hud shows about them.*
The aim is not a before-and-after comparison. The PR is the **scope**: it decides which
endpoints the plan drives, and the report is organised around it.

Either the user named the PR in their request (or several), or the skill ranks the
repo's recent merged PRs, runs a light feasibility pass on the top five, and picks the
winner itself with a fixed rubric, saying which and why, with the other four listed.
**Never pick silently**, and never ask which PR or whether to pick one: tell the user the
pick and why, as information.

## Pipeline

No call graph for the ranking. Hud already knows which source files the running service
is made of: it declares the service's functions when its code loads, so after
`install-hud-sdk`'s verification the `Functions` table lists them. A PR is a candidate when
most of its source files are among them. The feasibility pass reads only enough of each of
the top five to compare them; endpoints and the outcomes on their changed paths are mapped
for the winner only. Steps 1–5 run in X2, which sends no load; step 6 runs in X3, after its
coverage sweep.

| Step | Script | Reads | Writes |
| :-- | :-- | :-- | :-- |
| 1 | `02-choose-pr/scripts/fetch_prs.py` | GitHub (`gh`) or local git history | `prs.json`: metadata, reviewers, changed files, hunks, merge SHA |
| 2 | The Hud MCP | The `Functions` table | `hud-files.json`: the service's source files, as Hud records them |
| 3 | `02-choose-pr/scripts/rank_prs.py` | `prs.json`, `hud-files.json`, `pr-weights.json` | Ranked candidates, each with its reasons; the top five under `top` (`ranked.json`) |
| 4 | Five feasibility sub-agents, or you in turn (`02-choose-pr/SKILL.md` §3) | Each top candidate's description, tests and diff, `service-profile.json`, `answers.json` | `feasibility/<pr>.json` per candidate; `pr-choice.json`: the winner by the rubric, why, and the other four |
| 5 | You, reading the diff (`02-choose-pr/SKILL.md` §4) | The winner's description, tests and diff | `routes.json`: the endpoints its changed functions reach, each with its call chain |
| 6 | `rank_prs.py --pick N`, then the Hud MCP (X3, after the sweep) | The winner, `routes.json`, `hud-functions.json` | `scope.json`: the PR, its endpoints, its changed functions |

Every script needs only Python 3.9+ and `git` (plus `gh` for GitHub repos), and works the
same for every language: nothing parses code beyond a regex.

## 1. Fetch

```bash
python3 02-choose-pr/scripts/fetch_prs.py --repo . --days 60 --out .hud-local-pov/prs.json
python3 02-choose-pr/scripts/fetch_prs.py --repo . --pr 8712 --pr 8690 --out .hud-local-pov/prs.json   # user-named PRs
python3 02-choose-pr/scripts/fetch_prs.py --repo . --days 60 --path services/orders                    # one service in a monorepo
```

- **Source.** `gh` when the repo's `origin` is on GitHub and `gh` is logged in, otherwise
  plain git (`--source git` forces it). From gh it uses only `gh pr list` (`gh pr view` for
  `--pr`) and `gh api --paginate --jq` for each PR's files; a failed call for one PR falls
  back to that PR's local merge commit, and only a failing `gh pr list` moves the default
  source to git. On the git source with a working `gh pr list`, each change with a PR number
  gets its `createdAt` and `url` from GitHub. The git source reads first-parent commits on the
  default branch and treats each one as a change: a merge commit, or a squash-merged PR.
  It finds the PR number in the commit subject (`(#123)`, `Merge pull request #123`).
  Rebase-merged PRs show up as one change per commit there. If `origin/HEAD` points at a
  stale branch, fix it first (`git remote set-head origin --auto`).
- **What it records per change:** number, title, body (truncated), author (and email on
  the git source) and whether it is a bot, **reviewers** (GitHub's latest reviews and
  pending review requests; on the git source, `Reviewed-by:`/`Approved-by:` trailers),
  co-authors (`Co-authored-by:`), merge time, merge SHA, labels, URL. Per file: path, old
  path, status, additions, deletions, and **hunks** (new-side line ranges).
- **It keeps everything.** Bots, docs-only and test-only PRs are filtered in step 3, so
  the filter can change without a re-fetch.
- **History must be local.** Step 3 reads each changed file at its merge commit. A
  shallow clone fails that quietly, so fetch checks first. If the clone's history stops short of the window, it stops and prints
  the command that deepens it (`git fetch --shallow-since=<window start> origin <branch>`,
  plus the tag). A merge commit that still isn't local is counted, not dropped.

## 2. Ask Hud which files the service runs

Once the Hud MCP is connected (X1) and the service has run with Hud attached (the install's
verification, or an earlier run), run this
through the Hud MCP and save the rows as a JSON list in `.hud-local-pov/hud-files.json`.
`rank_prs.py --service NAME` without `--hud-files` prints the same query.

```sql
SELECT DISTINCT f.file AS file
FROM Functions f
WHERE arrayExists(s -> s.2 = 'SERVICE', f.services) AND f.third_party = ''
ORDER BY file LIMIT 20000
```

The ranker accepts a JSON list of strings or of `{ "file": ... }` rows, an object with the
rows under `rows`/`data`/`result`, or plain text with one path per line.

- **Hud's paths are the process's paths.** `/app/mealie/routes/foods.py` for the repo's
  `mealie/routes/foods.py`; `/app/dist/routes/x.js` for a compiled `src/routes/x.ts`. The
  ranker matches on the longest common path suffix of at least two segments, ignoring the
  extension. A file at the root of `src/` (`app.module.ts` → `/app/dist/app.module.js`)
  matches by file name when exactly one Hud file has that name and it isn't a generic one
  (`index`, `main`, `config`, `Application`…, in any case). The JVM may record bare file
  names, which match only this way (`jvm.md`). Check that a few known files match before trusting a
  ranking with no candidates.
- **Lazily loaded code is missing until it runs.** The JVM loads classes on first use, and
  Python and Node can import inside a function. A file Hud hasn't seen still counts when
  other files in its directory are listed (`inService: "dir"`). If the list looks short,
  query again after X3's coverage sweep: the scope's functions query (§4) runs then anyway.
- **A monorepo** lists only this service's files, which is the point. Pass `--path` to
  both scripts when another service has files with the same names.

## 3. Rank

```bash
python3 02-choose-pr/scripts/rank_prs.py --prs .hud-local-pov/prs.json \
    --hud-files .hud-local-pov/hud-files.json --top 5 --out .hud-local-pov/ranked.json
```

Thresholds, weights and patterns are in `02-choose-pr/scripts/pr-weights.json`; change
them and re-run. Nothing has to be fetched again.

**Filters.** A PR is dropped when it is:

| Filter | Default |
| :-- | :-- |
| By a bot | dependabot, renovate, `[bot]` authors |
| A revert, or reverted later in the window | `Revert "…"` titles, `This reverts commit …`, `reverts #N` |
| Maintenance | `chore:`, `docs:`, `ci:`, `build:`, `deps:`, `style:`, `test:` titles, or the matching labels |
| No source change | Only tests, docs, config, lockfiles or generated code |
| **Mostly outside the running service** | Fewer than **60 %** of its source files, or fewer than **2**, are among Hud's files |
| Mostly renames or moves | More than half of its source files renamed |
| **Outside the size range** | Fewer than **100** or more than **1,000** lines changed, counted **in the service's files only**, so lockfiles, tests and other services don't count |

PRs just outside the size range are listed separately: the user may name one to use anyway.

**Order.** PRs the user **wrote or reviewed** come first: they know the change, and can
tell whether what Hud shows is right. Identity comes from `--me` (repeatable: a GitHub
login, a git name or an email), or by default from `gh api user` and `git config
user.name`/`user.email`. Within each group, PRs with more **story signals** come first,
then the most recent:

| Signal | Points | How it's checked |
| :-- | :-- | :-- |
| Registers routes | 2 | A changed service file contains a route registration (`routePattern` in `pr-weights.json`). The change is reachable over HTTP in one step |
| Data access | 2 | The changed lines build queries or call a database or HTTP client. The richest call graphs and durations, including query-shape changes |
| Changes tests | 1 | Its tests are the best source of valid request bodies and flows for X3 |
| Feature, fix or perf | 1 | Conventional-commit title (`feat:`, `fix:`, `perf:`) or label |
| Focused | 1 | Its service files sit in at most 3 directories: one feature, not a sweep |
| Fixed by a later PR | 1 | A later PR whose title says fix mentions this one: a ready-made "here's how Hud would have shown it" story |
| Described | 1 | A description of 80+ characters, or a linked issue. The report needs the narrative |

**Output.** The top five (`--top`), each with its number, title, link, and one line of
reasons: who wrote or reviewed it, how many of its files are in the service and how many
lines changed there, and which signals matched. `ranked.json` holds them under `top`; they
go to the feasibility pass (`02-choose-pr/SKILL.md` §3), which decides among them on what
the ranker can't see: whether the change can be reached and broken locally. Then the next
eligible PRs and the near misses, so the user can redirect the run to one the ranking missed.

## 4. Scope the plan to the chosen PR

X2 traces the winner's endpoints from its diff and writes `.hud-local-pov/routes.json`
(`02-choose-pr/SKILL.md` §4). After its coverage sweep, X3 runs (`03-prepare-run/SKILL.md`
§2):

```bash
python3 02-choose-pr/scripts/rank_prs.py --prs .hud-local-pov/prs.json --hud-files .hud-local-pov/hud-files.json \
    --pick 8509 --scope-out .hud-local-pov/scope.json --service SERVICE \
    --why "<pr-choice.json's why>"
```

This turns `routes.json` into the scope's endpoints, keeping each one's `core` flag and
its `via` chain (as `chain`, with `hops` the number of calls from the changed function to
the entry point). Without `--hud-functions`, it also prints a query for Hud's functions
in the PR's files and the endpoints' files. Run it through
the Hud MCP, save the rows as `.hud-local-pov/hud-functions.json`, and run the same command
again with `--hud-functions .hud-local-pov/hud-functions.json`. That fills in:

- **`changedFunctions`**: the Hud functions that contain a changed line. The lines come
  from the merge commit and are moved to `HEAD` (the working tree the local service runs)
  first, so later commits don't shift them. A line a later commit rewrote is counted and skipped.
- **`handler`** per route: the first function Hud declared at or after the route's line,
  before the file's next route.

Both use Hud's own function signatures, which is what the confirm step and the report
match on. If no changed line falls in a Hud function, Hud's line numbers differ from the
repo's (compiled code without source maps, a different build). Say so, and confirm by
function name in the step below.

`scope.json` holds the PR, its changed files, its endpoints, and the changed functions the
confirm step looks for.

**Narrowing an existing plan** happens in X2, before any of this, from `routes.json`
(`scope_plan.py` reads either file):

```bash
python3 02-choose-pr/scripts/scope_plan.py .hud-local-pov/plan.json .hud-local-pov/routes.json > .hud-local-pov/plan-pr.json
```

For each route it keeps the entries tagged `changed-path` (outcomes whose path runs
through a changed function) plus one success request when none of those is one, tagged
`pr-scope`; the creates that fill the pools they read (`support`, down-weighted in rate
phases); and the setup. It lists the outcome variants it dropped and every in-scope route
the plan has no entry for. `openapi_plan.py --routes` does the same for a spec draft.

**Why trace instead of listing routes by file.** Scoping by "routes in a file that
imports a changed file" over-reaches: a route file that imports a shared model "reaches"
every change to it. On Mealie #8225, which changes `repository_factory.py`, that pulled in
170 routes where the PR's own route files held 17. Tracing from the changed functions
keeps only the endpoints that run the changed code.

**Then curate the scope deeply.** A plan curated for the whole service is shallow on
purpose; the PR's endpoints need their *success* paths through the changed code, not
just a status code, and the outcomes the changed code decides, not every outcome the route
has. Read the PR's description and its tests. On Mealie #8509 the full
plan had both `DELETE` item routes missing, and the recipe-on-list routes pinned to `404`,
because nothing filled their pool. So the PR's core change never ran. The PR's own
integration test spelled out the scenario (add a recipe, check items off, uncheck them,
remove the recipe), and two script entries built from it covered all 9 endpoints. See
"Script entries" in `plan-format.md`.

**A wide scope gets two depths.** Shared models pull in routes the PR barely touches.
Mealie #8265 reached 48 endpoints by import graph, about a third of them only through
shared Pydantic models (invitations, webhooks, notifications, storage). Split them:

- **Core:** endpoints whose handler's own service or repository path runs the changed
  code. Drive these deeply, with flows built from the PR's tests and success paths
  through the change.
- **Linked:** endpoints that reach the change only through a shared model, helper or
  import. Give each one plain read or create in the plan, and nothing more.

Show the user the split. When the core is more than a handful of endpoints, narrow it to
the endpoints that run the most changed functions, and say which ones you left as linked.

**A request that succeeds is not coverage of the PR.** A drafted or probed entry can
return 200 without reaching the changed code. On #8265, 31 of 52 probed entries passed,
but many sent empty bodies (`PUT /api/recipes []`, `parser/ingredients {ingredients: []}`)
that return early. Only the confirm step below shows whether the changed functions ran.
For the core endpoints, go straight to flows built from the PR's tests rather than
trusting a probe's `ok`.

### End to end on Mealie #8509 (2026-10-15, local service on `main`)

Pick → `scope_plan.py` on the existing 182-entry Mealie plan → 7 entries for 7 of 9
endpoints, and both `DELETE` routes reported missing → two script flows built from the
PR's integration tests → **9 of 9 endpoints, every changed function on a call path**. Run
with Hud (Python SDK, local key):

| Phase | Result |
| :-- | :-- |
| `coverage` | Both flows 200 on every step, plus the in-scope `ForeignKeyViolation` 500 on `POST /api/households/shopping/items` |
| `run` | About 2 minutes at about 1 entry/s: the two flows and each route's success request, plus the `ForeignKeyViolation` 500 entry, which a plain request reaches on the changed path |

Not done: the confirm step and checking ingestion in Hud, because the Hud MCP was not
authorised in this session.

## Confirm before timed traffic

In X3, after the coverage sweep, check the chosen PR against Hud's runtime view, using
`scope.json`'s `endpoints` and `changedFunctions` (query in `shared/references/verify.md`):

1. Every endpoint in scope was invoked and appears in Hud.
2. The PR's changed functions show invocations under those endpoints.

A PR whose functions never run under load is the wrong PR. Pick the next candidate, or
widen the plan to the endpoints that do reach them.

## Checked against the hud monorepo (30 days, 321 changes)

Measured on 2026-10-20 against `origin/main`, git source, with Hud's file list simulated
from `apps/workflow-manager/src` as `/app/dist/**/*.js` (TypeScript, NestJS). Fetch 23 s,
rank 1 s. 254 changes mostly outside the service, 55 with no source change, 3 reverted,
4 outside the size range: **5 eligible.** The user's own #8122 (2 of 2 files in the
service, 109 lines, a controller and its service) ranked first; #8177 and #8054 followed
on the same 7 points. The pick mapped 14 routes by import graph (the scoping this skill
used before tracing): 6 in the changed controller, 8 through imports.

## Open questions

- **The 60 % share in a monorepo.** A full-stack PR (UI and API together) falls under it
  even when its backend half is a good story: 254 of 321 hud changes did. Lower
  `minServiceShare` when the service sits next to its frontend.
- **Several PRs.** Whether to frame a run on one PR, or the union of the top two or three
  when each one alone reaches too few endpoints.
