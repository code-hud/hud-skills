# Finding in this run

The report's last section, titled "Finding in this run", is the one part of the report you write yourself. Look at what Hud recorded
during the run and write up to two findings a technical reader would act on: one about **time**
(a bottleneck, or an optimization the data supports) and one about a **failure** (an error the
run hit, explained to its root cause with Hud's forensics). Write it after the queries in
`report-build.md` step 2 and before step 3 builds the page, as
`.hud-local-pov/report-data/insights.md`. The build stops if the file is missing or empty, and
puts it in the page verbatim, followed by the run's own "How this run was made" section.

## Where to look

Start with the results step 2 saved in `.hud-local-pov/report-data/`; they cover the run window
and the PR's endpoints already:

| File | What it gives you |
| :-- | :-- |
| `endpoints.csv` | Calls, statuses, p50 and p90 per endpoint over the run |
| `endpoint-functions.csv` | Every function under the primary endpoint: calls, average and p90 time, error rate |
| `function-callers.csv` | Who calls whom under that endpoint, how often, and how long each call takes |
| `forensics.csv` | The forensics captured in the window: id, endpoint, trigger (`Error`, `Duration`), exception messages |

Then ask Hud for what those leave open, through the Hud MCP, filtered to the run's service, the
run window and the environment the key reports to (as in `shared/references/verify.md`):

- `hud-query` on `Forensics` for the ids, functions (`exceptions_data_enriched` names the
  functions an exception passed through) and timings of a failure or a slow request.
- `hud-forensics-search` for what a forensic holds: outbound calls (`outbounds`: type and
  duration of each database, cache or HTTP call), exception names and stack traces. Read Hud's
  `search-forensics` skill (`hud-get-skill`) before the first search.
- `hud-get-forensics` for one whole trace, when the two above leave the cause open. It returns
  50–500 KB of JSON: read it in a sub-agent and bring back only the fields you need.

## What to look for

**Time: a bottleneck or an optimization.** Find where the request's time goes and whether the
code could spend less of it:

- the function or call that takes the largest share of the endpoint's time (time per call × calls
  per request, from `endpoint-functions.csv` and `function-callers.csv`);
- calls made once per item where one batched call would do (calls per request far above 1);
- independent calls that run one after another;
- slow requests (`Duration` forensics) and what their outbound calls say: a slow database call, or
  time spent outside any call (CPU, waiting on the event loop or a pool).

**Failures, root-caused.** Pick the failure a reader learns the most from: an unexpected 5xx
over an expected validation error. The run applied no fault, so every error is the code as it
is today. From its forensic, give the
exception, the function that threw it and the path it took up to the endpoint, the call or input
that caused it, and why. Then say what the forensic added beyond a status code or a log line: the
failing function and its callers, the outbound call that failed and how long it waited, or an
error the endpoint returned as a success.

Use what the run actually showed. Every number comes from a query result or a forensic, every name
from Hud (functions and files as Hud records them), and every suggestion says what evidence it
rests on. If the run shows nothing worth mentioning for a kind, write one sentence saying what you
checked. Don't pad the section.

## How to write it

One `##` heading per finding, then a label and two to four sentences, in this shape:

```markdown
## <What happened, in plain words>

**<Label>.** <What, where, the evidence (numbers), and the cause.> <What to change, for a
time finding.>

| <Optional: up to five rows of evidence> |
| :-- |

**What the forensic added:** <for a failure: what Hud showed that a status code or log wouldn't>.
**In Hud:** forensic `<forensic_id>`, <UTC time>.
```

- **Labels:** **Real finding** (the code as it is today), **Suggestion** (an optimization the
  data supports but the run didn't prove).
- **Names in code spans:** functions, files, endpoints, exception names and messages. The page
  shows raw HTML outside code as text.
- **About 250 words in all**, tables aside. No headings other than the findings: the page
  titles the section "Finding in this run", and the build adds "How this run was made" after
  them.
- **Nothing private from a forensic.** Quote exception names, the first line of a message,
  function names, files, durations, counts, forensic ids and times. Never request or response
  bodies, headers, query strings, user context, outbound arguments or results, or anything that
  looks like a key, token, email or customer name.

## Example

From the `POST /sink/batch` sample in Hud's report app (`apps/local-run-report/samples/sink-batch`):

```markdown
## Kafka produce is most of every batch request

**Suggestion.** `CoreController.batch` averages 10.9 ms, and 7.1 ms of it is
`CoreService.sendEventToKafka`: enqueueing takes 0.25 ms, and the rest is waiting for
`KafkaRecordBatcher` to produce the batch. Before that, `CoreService.wasSentAlready` spends 2.2 ms
on a Redis `putValueIfNotExist`, one call after the other. Together they are about 85% of the
request. Running the dedupe write alongside the produce, or answering once the batch is enqueued
(if clients can take an acknowledgement before Kafka confirms), would take most of it off the
request path.

| Function | Calls per request | Avg | p90 |
| :-- | --: | --: | --: |
| `CoreService.sendEventToKafka` | 1.0 | 7.12 ms | 12.36 ms |
| `RedisService.putValueIfNotExist` | 1.0 | 2.23 ms | 3.44 ms |
| `SharedThrottlingSessionStateService.isSessionMarkedForKill` | 0.4 | 2.64 ms | 4.14 ms |

## A session lookup waited 30 s for MongoDB

**Real finding.** One request ran 30,002 ms and failed with `MongoServerSelectionError`, thrown by
`Collection.findOne()` in `CoreService.getSession`, through `SessionService.getSession` up to
`CoreController.batch`. `getSession` goes to MongoDB only on a cache miss (2.4% of calls, 5.2 ms
on average), and when no MongoDB server answered, the driver waited out its 30-second server
selection timeout. A shorter `serverSelectionTimeoutMS` on this path would fail the request fast.

**What the forensic added:** the failing call, the path it took, and how long it waited. The
endpoint's metrics show none of it: a single slow request, and no error rate.
**In Hud:** forensic `D0815BC84C67F34B81DFDF58D7DDD936`, 2026-09-25 03:31:55 UTC.
```
