---
name: hud-local-pov-04-run-traffic
description: >-
  Step 4 (phase X4) of the hud-local-pov skill: run the plan's one timed phase, about two
  minutes of the PR's mapped requests, with the bundled driver. Run it from
  hud-local-pov's SKILL.md, not on its own.
---

# Step 4 (X4) — Run it

Part of `hud-local-pov`. The orchestrator (`SKILL.md` at the skill root) holds the overview,
the guardrails and the order of steps. Every path here is relative to the skill root.
User-facing: the middle of **Step 3 of 4**.

**Usage analytics.** Run `python3 shared/scripts/analytics.py start 4` as you begin. Before
you return `GATE_PASSED`, run `python3 shared/scripts/analytics.py end 4 --ok`; before you
return `BLOCKED`, run `python3 shared/scripts/analytics.py end 4 --failed "<reason>"`, where
the reason is a few plain words on what failed, with no names, paths, URLs, keys or output.
Run nothing on `NEEDS_USER`: the step isn't over. The helper returns at once and never fails.

| | |
| :-- | :-- |
| **Consumes** | `.hud-local-pov/plan.json`, `.hud-local-pov/pools.json` |
| **Produces** | `.hud-local-pov/run.jsonl` (the run's timeline) |
| **Gate to step 5** | The `run` phase completed: no `INCOMPLETE PHASE`, and the guard didn't abort (exit 0) |

**Files**

| File | Use |
| :--- | :-- |
| `shared/scripts/drive.py`, `shared/references/plan-format.md` | The driver and the plan schema |
| `shared/references/troubleshooting.md` | Symptoms in the run and in Hud, and their causes |

Run against the service as it is: don't restart it. Start it again only when it is down
or unhealthy (X2's restart rule). The driver re-runs the readiness checks before anything
else and exits 3, sending nothing, if one fails:

```bash
python3 shared/scripts/drive.py .hud-local-pov/plan.json --pools .hud-local-pov/pools.json \
  --phase run --log .hud-local-pov/run.jsonl
```

The run takes about 2 minutes. Before you start it, check the service and its
dependencies are up.

If the machine sleeps or the process is suspended mid-phase, the driver flags it. It does not replay the missed traffic, which would
show in Hud as a spike nobody planned. The phase output then reads `INCOMPLETE PHASE:
sent N of M planned`, with matching `clock-jump`/`incomplete-phase` lines in the timeline.
**An incomplete phase is not data:** re-run it.

- **Watch the driver output.** `shed` requests mean the service cannot keep up — the rate
  you planned is not what it received; report the achieved rate. An `unexpected` the
  sweep and probes didn't show (a 5xx on an entry that succeeded in X3) is a real finding
  for the report, not a reason to change the plan mid-run.
- **Watch the service log** for the SDK's own lines — rate-limit drops ("Max ...
  investigations reached") on a flow X3 already captured are expected and confirm the
  cap, not a failure.

The run changes nothing beyond the records its requests create: there is no fault to
undo. A6's cleanup, if the request asked for one, is in X6's hand-off.
