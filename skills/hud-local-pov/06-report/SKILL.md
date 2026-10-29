---
name: hud-local-pov-06-report
description: >-
  Step 6 (phase X6) of the hud-local-pov skill: build the one-page HTML run report from
  generated queries run through the Hud MCP, publish it through the host's artifact
  feature when there is one, summarise the run per signal, and hand off what the run left
  behind. Run it from hud-local-pov's SKILL.md, not on its own.
---

# Step 6 (X6) — Report

Part of `hud-local-pov`. The orchestrator (`SKILL.md` at the skill root) holds the overview,
the guardrails and the order of steps. Every path here is relative to the skill root.
User-facing: **Step 4 of 4**.

**Usage analytics.** Run `python3 shared/scripts/analytics.py start 6` as you begin. Before
you return `GATE_PASSED`, run `python3 shared/scripts/analytics.py end 6 --ok`; before you
return `BLOCKED`, run `python3 shared/scripts/analytics.py end 6 --failed "<reason>"`, where
the reason is a few plain words on what failed, with no names, paths, URLs, keys or output.
Run nothing on `NEEDS_USER`: the step isn't over. The helper returns at once and never fails.

| | |
| :-- | :-- |
| **Consumes** | `.hud-local-pov/run.jsonl`, `scope.json`, `prs.json`, `service.json`, `plan.json`, the service log |
| **Produces** | `.hud-local-pov/report-data/queries.json` and one CSV per query; the generator's three input CSVs (`endpoint-functions.csv`, `function-callers.csv`, `modified-functions.csv`); `insights.md` (the report's last section, "Finding in this run", which you write from the run's Hud data); `report-sections.json`, `report.json`; `.hud-local-pov/report.html`, published as an artifact when the host has the feature; the per-signal summary and the hand-off in the conversation |
| **Gate** | The last step. The report is published (or its local path given), and the run closes with what it left behind |

**Files**

| File | Use |
| :--- | :-- |
| `06-report/references/report-build.md` | The report procedure: generate the queries, run them through the Hud MCP, build the page; the generator's CSV contract; and what each section is built from |
| `06-report/references/insights.md` | How to write the "Finding in this run" section: a bottleneck and a failure from the run, each backed by Hud's data and forensics |
| `06-report/scripts/report_queries.py` | Writes the exact SQL the report needs, from the run's timeline and scope |
| `06-report/scripts/save_result.py` | Saves each query result into `report-data/`, and refuses a copy whose row count or id and count sums differ from Hud's |
| `06-report/scripts/build_report.py` | Writes `modified-functions.csv` and the page's other sections from the query results and the run's files, then runs the generator |
| `06-report/scripts/impact_graph.py`, `fill_report.py` | The generator, a Python port of Hud's report app scripts: builds the impact graph from the three CSVs, then fills the page template |
| `06-report/assets/report-template.html` | The page: Hud's report app as one self-contained HTML file, with an empty slot for the report data |

**One report**, per signal. For the reader the run was for (lead engineer, architect,
technical director), build the run report with `06-report/scripts/`: generated queries, run verbatim
through the Hud MCP, the insights you write from them, then a script that renders one HTML page. See `06-report/references/report-build.md`.
The table below is the short version for the conversation. Everything in it, and in the
page, is about the service and what Hud showed; nothing about this skill or the SDK
(SKILL.md, Guardrails). "Not produced, and why" names the run's shape or a documented Hud
limit (a forensics cap, metric granularity), stated as how Hud works, not as a defect:

| Signal | Driven | Hud shows | Not produced, and why |
| :----- | :----- | :-------- | :-------------------- |

Plus:

- **The coverage table from X2 and X3**: the outcomes on the PR's changed paths plus one
  success per route, now with a "seen in Hud" column. An outcome not seen is unreachable
  code, an entry point that needs a manual flow (say which, as a step for their install),
  or one the run didn't reach (list it as not exercised). List the unreachable outcomes
  X3's probes dropped and the `silent-noop` ones they confirmed together, each with its
  reason. Outcomes off the changed paths were never mapped: don't list them as unreached.
- **The changed code at runtime**: each changed function Hud saw under the primary
  endpoint, its callers and callees, calls and durations (`endpoint-functions.csv`,
  `function-callers.csv`), and the changed functions that didn't run there, with why.
- **The timeline** — the run's UTC window, so anyone can find it in Hud.
- **What is a chart and what is a detection.** The run aims at no alert: its charts and
  forensics are the result. Never report a chart as an alert.
- **Every error is the code as it is today.** The run applied no fault, so a 5xx or an
  exception it hit is a real finding on the request that reached it.

**Publish the report.** Once `report.html` is built, publish it through the host's
artifact feature when there is one, without asking: the report is what the run was for.

- **Is there one?** Look at your own tools and instructions, nothing else: a tool or
  built-in capability that shows a file or HTML page to the user as an artifact or site
  (Claude's artifacts, Codex sites, or a host's equivalent that renders a self-contained
  HTML page). Don't ask the user, and don't install a CLI, sign in to a service or upload
  to a host the user hasn't connected to find one.
- **Publish the file as it is.** The page is one self-contained, ASCII-only HTML file
  (claude.ai's artifact publisher rejects a literal U+FFFD; the build writes none). Don't
  edit it to fit. Where the host offers a choice, keep it visible to the user only, not a
  public link: the page names the service's endpoints and functions.
- **Then** give the user the artifact (its link or name) and the local path. When there is
  no artifact feature, or the host rejects the page, give the absolute path of
  `.hud-local-pov/report.html` to open in a browser, as before.

**Close with what the run left behind**, from `service.json`, so the user can decide what
to keep. The run doesn't tear anything down:

- The Hud install (with debug already off since X2), and the records the run created,
  with the dump or cleanup the request asked for (A6), if any, and its command.
- What is still running, with the command that stops it (`service.json`'s `at_end`): the
  app and its containers.
- `.env.local`, which holds their secrets; it's theirs to keep or delete.
