---
name: hud-local-pov-05-wait-for-ingestion
description: >-
  Step 5 (phase X5) of the hud-local-pov skill: wait until Hud has ingested the timed
  run's metrics, then hand off to the report. Run it from hud-local-pov's SKILL.md, not
  on its own.
---

# Step 5 (X5) — Wait for Hud ingestion

Part of `hud-local-pov`. The orchestrator (`SKILL.md` at the skill root) holds the overview,
the guardrails and the order of steps. Every path here is relative to the skill root.
User-facing: the end of **Step 3 of 4**.

**Usage analytics.** Run `python3 shared/scripts/analytics.py start 5` as you begin. Before
you return `GATE_PASSED`, run `python3 shared/scripts/analytics.py end 5 --ok`; before you
return `BLOCKED`, run `python3 shared/scripts/analytics.py end 5 --failed "<reason>"`, where
the reason is a few plain words on what failed, with no names, paths, URLs, keys or output.
The helper returns at once and never fails.

| | |
| :-- | :-- |
| **Consumes** | `.hud-local-pov/run.jsonl` (the `run` phase's start and end), the language pack's "Metric flush", the `HUD_SERVICE` name and the key's environment |
| **Produces** | Nothing on disk; the hand-off to X6 |
| **Gate to step 6** | Hud holds a metric row for the service from the run's window that ends at or after the run's end, or the polling bound ran out with rows from the window in Hud |

SDKs batch their metrics, and Hud buffers rows before they can be queried. X6's queries
run once, so this step makes sure the run's data is there first. It checks nothing else:
what the data shows is X6's.

### 1. The run's window

Read the `run` phase's `phase-start` and `phase-end` times from `run.jsonl`:

```bash
python3 -c "import json; [print(e['type'], e['ts']) for e in map(json.loads, open('.hud-local-pov/run.jsonl')) if e.get('phase') == 'run' and e.get('type') in ('phase-start', 'phase-end')]"
```

These are `RUN_START_UTC` and `RUN_END_UTC` (written as `YYYY-MM-DD HH:MM:SS` in the SQL
below). When `run.jsonl` holds the phase more than once, use the last pair.

### 2. Wait

Wait until **5 minutes after `RUN_END_UTC`**, or the runtime's flush interval after it
when that is longer (the language pack's "Metric flush": on Node, what the account's
remote config sets, as the startup log shows). Count from `RUN_END_UTC`, not from now: if
that time has already passed, go straight to §3.

### 3. Poll lightly

Run this through the Hud MCP's `hud-query` (`serviceScope: "all"`, `environmentName` the
key's environment), once a minute, for at most **10 minutes**:

```sql
SELECT count() AS rows, max(timestamp_end) AS last_row
FROM EndpointMetricsHighResolution
WHERE service_name = 'SERVICE' AND environment_name = 'ENV'
  AND timestamp_end > toDateTime('RUN_START_UTC')
LIMIT 1
```

- **`last_row` at or after `RUN_END_UTC`:** a flush after the run's last request has
  landed, so the whole run is in. Hand off to X6.
- **Rows, but `last_row` still before `RUN_END_UTC`:** the last flush hasn't landed yet.
  Poll again in a minute. When the 10 minutes run out, hand off anyway and tell X6 the
  data may stop before the run's end.
- **No rows when the 10 minutes run out:** return `BLOCKED` with "no data from the run in
  Hud after the wait". Don't diagnose it here; the orchestrator tells the user.

Sleep between polls (`sleep 60`); don't send traffic or restart anything while waiting.
