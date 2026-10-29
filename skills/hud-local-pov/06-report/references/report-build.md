# Run report

Turns one hud-local-pov run into a single HTML page for a technical reader (lead engineer,
architect, technical director): what Hud's SDK saw, the PR the run was built around, its
runtime impact, what Hud's PR review would check, and the findings from the run.

**The report is repeatable, except for its insights.** The queries are generated from the
dataset, run verbatim, and the report data is built by a script with fixed rules. Two agents
given the same dataset produce the same report; only its creation date and the "Finding in
this run" section differ. That section is yours: you write it from the run's Hud data
(`06-report/references/insights.md`). Don't edit the queries, the results, the rest of the
report data or the page by hand. If something is wrong, fix the script and rebuild.

## Inputs

The dataset directory the run left behind (normally `.hud-local-pov/`):

| File | From |
| :-- | :-- |
| `run.jsonl` | `drive.py --log`: phase times, rates, and what the driver sent per entry |
| `scope.json` | `rank_prs.py --pick N --scope-out`: the PR, its endpoints, handlers, changed functions |
| `prs.json` | `fetch_prs.py`: PR history (time-to-merge needs the `gh` source) |
| `plan.json` (optional) | The plan: the count check compares one of its entries against Hud |
| `report-data/insights.md` | You, after step 2: the "Finding in this run" section (`06-report/references/insights.md`) |
| the service log (optional) | Only counted: dropped SDK batches. Never printed |

## Steps

**1. Generate the queries.**

```bash
python3 06-report/scripts/report_queries.py --dataset .hud-local-pov --service SERVICE --env ENV
```

`ENV` is the Hud environment the key reports to. This writes
`.hud-local-pov/report-data/queries.json`: the run window, the phases, and one SQL query per
result file.

**2. Run each query through the Hud MCP and save its output.** First, run the visibility
query from `shared/references/verify.md` for the service. If it doesn't list the service, the MCP
is on another account (`install-hud-mcp` §5): stop and ask the user to reconnect.
Don't run the batch. Run the same check again after the last query. If it fails then,
discard the results and start the batch again, because an empty result from a switched
account looks exactly like "no data".

For every entry in
`queries.json`, call `hud-query` with the entry's `sql` as `query`, `environmentName: ""`
and `serviceScope: "all"` (the SQL filters service and environment itself). Save the
result with `06-report/scripts/save_result.py`, never by writing the file yourself:

- **A result that comes back as a path** ("Query result saved to /…/…csv"):
  `python3 06-report/scripts/save_result.py --dataset .hud-local-pov NAME --from /…/…csv`.
  Don't open it and retype it.
- **A result that comes back inline** (most do, up to a few hundred rows): first run the
  entry's `check` query the same way. It returns one line: the row count and the sums of
  the id and count columns, computed by Hud. Then pipe the result in unchanged, in a quoted
  heredoc, with the check's output:

  ```bash
  python3 06-report/scripts/save_result.py --dataset .hud-local-pov NAME \
      --check '"rows","sum(function_id)","sum(invocations)"
  96,4410317795,48032' <<'CSV'
  …the tool's CSV output, exactly as returned…
  CSV
  ```

  The script saves nothing if the row count or a sum differs, which is what a dropped row
  or a mistyped id or count looks like: copy the result again. Names aren't summed, so copy
  them exactly too.

Don't reformat, trim or summarise a result. If a query errors, stop and report the error;
don't adjust the SQL.

**3. Write the insights.** Follow `06-report/references/insights.md`: read the results from
step 2, ask the Hud MCP what they leave open, and write `.hud-local-pov/report-data/insights.md`.

**4. Build the page.**

```bash
python3 06-report/scripts/build_report.py --dataset .hud-local-pov --plan .hud-local-pov/plan.json \
    --sdk-log app.log --out .hud-local-pov/report.html
```

The script needs only Python's standard library. It writes `modified-functions.csv` and
`report-sections.json` (every section except the impact graph) to `.hud-local-pov/report-data/`,
then runs the generator next to it: `06-report/scripts/impact_graph.py` builds the impact
graph from the three CSVs below and writes the full report data to `report-data/report.json`,
and `06-report/scripts/fill_report.py` puts it into the bundled page,
`06-report/assets/report-template.html`, and writes the result to `--out`. The result is one
self-contained HTML file: it opens directly in a browser and loads nothing from the network.
Its one request is a best-effort `report_opened` event when it's opened, carrying the run's
random id from `.hud-local-pov/analytics.json` (the `analytics` field of the report data).
Built with `HUD_SKILL_ANALYTICS=0`, the page has no `analytics` field and sends nothing.
X6 then publishes the file through the host's artifact feature when there is one
(`06-report/SKILL.md`, "Publish the report"); an artifact host's sandbox may block that
event, which is fine.
Don't edit the template, the generator or the data in the page; they are built from Hud's
report app and replaced when the skill is updated.

## The generator's CSV contract

The impact graph and the PR card's added/modified counts come from exactly three CSVs in
`.hud-local-pov/report-data/`. The generator reads them by these names and columns. Extra
columns are ignored; a missing column stops the build.

**Format.** Comma-separated, a header row, RFC 4180 quoting (quoted fields, `""` for a quote
inside one). The header is matched case-insensitively, and a byte-order mark is allowed.
Durations are milliseconds and error rates are fractions from 0 to 1. An empty or
non-numeric number counts as 0.

| File | Written by | Columns |
| :-- | :-- | :-- |
| `endpoint-functions.csv` | the `endpoint-functions` query | `function_id`, `function_signature`, `file`, `invocations`, `avg_duration_ms`, `p90_duration_ms`, `error_rate` |
| `function-callers.csv` | the `function-callers` query | `caller_function_id`, `callee_function_id`, `invocations`, `avg_duration_ms`, `error_rate` (the query also writes `caller_signature`, `callee_signature` and `p90_duration_ms`, for reading) |
| `modified-functions.csv` | `build_report.py`, from `scope.json` | `function` (`Class.method` or a bare name), `file` (its path in the repo), `change` (`added` or `modified`) |

- **`endpoint-functions.csv`**: one row per Hud function that ran under the primary endpoint
  during the run window, with its totals over the window.
- **`function-callers.csv`**: one row per caller → callee pair recorded under that endpoint.
  Ids match `endpoint-functions.csv`; a row with an unknown id is dropped.
- **`modified-functions.csv`**: one row per function the PR changed. A function is matched to
  the graph by its name (anything from `(` on is ignored) and its file's base name.

The **primary endpoint** is chosen in SQL: of the PR's endpoints, the one whose recorded calls
ran the most changed functions (ties: most requests, then lowest id). `primary_endpoint.csv`
names it. The graph starts from the function under it that nobody else calls. The
generator draws the heaviest functions as cards and smaller calls as dots, and shows client
calls such as Redis or PostgreSQL as shared resources.

## What each section is built from

| Section | Source | Rule |
| :-- | :-- | :-- |
| Header | `run.jsonl` | Run length: first timed phase to last |
| What the Hud SDK saw | `summary` | Endpoints, queues (Kafka, SQS, ARQ, Celery and other queue consumers) and app functions called during the window, against how many Hud mapped for the service. The Functions card also shows the called app functions' invocations summed over the window (`functions_invocations`, "1,234 invocations"); a summary saved before that column existed leaves the line out. On the JVM, wrapped consumers and non-Servlet routes are Custom Flows (`shared/lang/jvm.md`), so they count under endpoints, not queues; say so in the summary |
| PRs in your repo | `prs.json` | Human PRs merged in the 7 days up to the newest PR in the file (a list of named PRs ends at the newest of them); mean creation-to-merge hours for those PRs, from GitHub's PR open times (the `gh` source, or the git source enriched from `gh pr list`). Without them the tile shows "—" with the reason under it (`avgHoursToMergeNote`: no `gh` sign-in, origin not on GitHub, or no PRs that week). "Safe to merge": 30% of the PRs that change backend code (the ranker's source files, less tests, generated and frontend files) and no database migration, schema file or infrastructure file (Terraform, Helm, Kubernetes, CDK, Pulumi, CloudFormation, Ansible; Dockerfiles, compose files and `.dockerignore`; CI config such as `.github/workflows/`, `.gitlab-ci.yml`, `.circleci/`, `Jenkinsfile`, Buildkite), as a share of the PRs counted. 20 PRs with 10 such PRs show 15% |
| PR analysis example | `scope.json`, `primary_endpoint`, the three CSVs | Title and link from `scope.json`. The summary names the primary endpoint and how many changed functions ran under it. Added/modified counts and the impact graph come from the generator, built from the three CSVs above. Affected endpoints: the PR's endpoints in `scope.json` |
| What Hud checks on a pull request | fixed text in the page | |
| Finding in this run | `report-data/insights.md`, written by you | Up to two findings from the run's Hud data: a bottleneck or optimization, and a failure root-caused with its forensic (`06-report/references/insights.md`). The build makes raw HTML in it inert |
| How this run was made (after the findings, in the same section) | `run.jsonl`, the SDK log | That the service ran on the engineer's machine; whether the environment raises alerts; the count check; dropped batches; the phases with their UTC times and rates |
| Endpoint names | `endpoints`, `endpoint_names` | Every endpoint id in the report gets its method and path, including ones with no calls in the window |

**Counts are deduplicated.** Hud's high-resolution metric tables can hold the same row
twice, which doubles every sum. Every query reads them through a subquery that keeps one
row per key, and the key includes the row's own values (status codes, invocations, total
duration): the Python SDK writes one row per status code for the same session, entity and
time, so a key without the values drops real rows. The page then checks one plain entry's
whole-run count (the run is only a few SDK flushes long) against what the driver sent (`run.jsonl`) and says whether they match. A
ratio near 2 means duplicates got through; well under 1 means dropped SDK batches.
