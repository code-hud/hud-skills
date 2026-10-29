#!/usr/bin/env python3
"""Write the exact Hud queries a run report needs, for one run.

Step 1 of the run report (see 06-report/references/report-build.md). Reads the run's timeline (run.jsonl) and
the PR scope (scope.json) and writes queries.json: one SQL statement per result file. An
agent runs each one verbatim through the Hud MCP's `hud-query` tool and saves the tool's
CSV output, unchanged, as report-data/<name>.csv. build_report.py reads those files.

The SQL is fully determined by the dataset, so two agents running the same dataset get the
same queries, the same results and the same report.

Usage:
  python3 report_queries.py --dataset .hud-local-pov --service SERVICE --env ENV
                            [--out .hud-local-pov/report-data/queries.json]

  --env   The Hud environment the key reports to (the SDK startup log names it; a key
          starting `local_` reports to `local`)

Standard library only.
"""

import argparse
import datetime as dt
import json
import os
import sys

from build_report import route_key

TS = '%Y-%m-%d %H:%M:%S'
# Endpoint types the report counts as queues; every other type is an endpoint.
QUEUE_TYPES = "'KAFKA', 'SQS', 'ARQ', 'CELERY', 'QUEUE'"


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(dt.timezone.utc).replace(tzinfo=None)


def run_phases(path):
    """Timed phases from run.jsonl: name, start, end, rate, note.

    A file holding several runs (reruns) keeps the last occurrence of each phase, which is
    the one the report describes.
    """
    phases, open_ = {}, {}
    for line in open(path):
        if not line.strip():
            continue
        ev = json.loads(line)
        t, name = ev.get('type'), ev.get('phase')
        if t == 'phase-start' and ev.get('mode') != 'sweep':
            open_[name] = {'name': name, 'start': parse_ts(ev['ts']), 'note': ev.get('note'),
                           'rate': ev.get('rate')}
        elif t == 'phase-end' and name in open_:
            p = open_.pop(name)
            p['end'] = parse_ts(ev['ts'])
            phases[name] = p
    return sorted(phases.values(), key=lambda p: p['start'])


# build_report.PLACEHOLDER without the lookbehind, which ClickHouse's RE2 lacks (the `:x`
# form is replaced separately in the SQL).
SQL_PLACEHOLDER = r'\{\{[^}]*\}\}|\{[^}/]*\}|\(\?P<[^>]+>[^)]*\)|<[^>/]+>'


# The columns a result's check query sums: ids and counts, where a retyped digit changes
# what the page shows. save_result.py sums the same columns of the saved CSV.
CHECK_COLUMNS = r'(^|_)id$|^invocations$|^calls$|^forensics$|^changed_hits$'


def check_sql(sql):
    return f"SELECT count() AS rows, COLUMNS({q(CHECK_COLUMNS)}) APPLY(sum) FROM ({' '.join(sql.split())})"


def q(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dataset', required=True)
    ap.add_argument('--service', required=True)
    ap.add_argument('--env', required=True)
    ap.add_argument('--out')
    args = ap.parse_args()

    ds = args.dataset
    phases = run_phases(os.path.join(ds, 'run.jsonl'))
    if not phases:
        sys.exit('run.jsonl has no completed timed phases')
    scope = json.load(open(os.path.join(ds, 'scope.json')))
    # The window: first timed phase to last, plus one flush interval for late batches.
    start, end = phases[0]['start'], phases[-1]['end'] + dt.timedelta(minutes=5)
    W = f"m.timestamp_end > toDateTime({q(start.strftime(TS))}) AND m.timestamp_end <= toDateTime({q(end.strftime(TS))})"
    S = f"m.service_name = {q(args.service)} AND m.environment_name = {q(args.env)}"
    svc_fn = f"arrayExists(s -> s.2 = {q(args.service)}, f.services)"

    phase_case = 'multiIf(' + ', '.join(
        f"m.timestamp_end > toDateTime({q(p['start'].strftime(TS))}) AND m.timestamp_end <= toDateTime({q(p['end'].strftime(TS))}), {q(p['name'])}"
        for p in phases) + ", '')"

    # Scope paths and Hud's names spell placeholders and trailing slashes differently
    # (/api/documents/{id}/ in a scope, /api/documents/(?P<pk>[^/.]+) in Hud on Django), so
    # both sides are compared in one form: every placeholder as {}, no trailing slash.
    scope_eps = [(m, '/' + '/'.join(route_key(e['path']))) for e in scope['endpoints'] if e.get('path')
                 for m in (e.get('methods') or [])]
    hud_route = (f"replaceRegexpOne(replaceRegexpAll(replaceRegexpAll(e.endpoint_name, {q(SQL_PLACEHOLDER)}, '{{}}'), "
                 f"'/:[A-Za-z_][A-Za-z0-9_]*', '/{{}}'), '(.)/+$', '\\\\1')")
    # Non-HTTP entry points (DDP methods, queue consumers, custom flows) are matched by the flow
    # name Hud records, which has no HTTP method.
    scope_flows = sorted({e['flow'] for e in scope['endpoints'] if not e.get('path') and e.get('flow')})
    scope_cond = ' OR '.join([f"({hud_route} = {q(p)} AND has(e.http_methods, {q(m)}))" for m, p in scope_eps]
                             + [f"(e.endpoint_name = {q(n)} AND e.type != 'HTTP')" for n in scope_flows]) or '0'
    changed_fids = sorted({int(c['hudFunctionId']) for c in scope['changedFunctions'] if c.get('hudFunctionId')}) or [0]

    # Metric rows can come back more than once, which doubles every count. Read each table
    # through a subquery that keeps one row per key. The key includes the row's own values:
    # the Python SDK writes one row per status code for the same session, entity and time,
    # and a key without them drops those real rows (about 20-30 % on mixed-status endpoints).
    EM = (f"(SELECT * FROM EndpointMetricsHighResolution m WHERE {S} AND {W} "
          f"LIMIT 1 BY m.session_id, m.endpoint_id, m.timestamp_end, toString(m.status_codes), m.invocations, m.total_duration_ms)")
    FM = (f"(SELECT * FROM FunctionMetricsHighResolution m WHERE {S} AND {W} "
          f"LIMIT 1 BY m.session_id, m.function_id, m.caller_function_id, m.wrapped_endpoint_id, m.timestamp_end, m.invocations, m.total_duration_ms)")
    F_WIN = (f"service_name = {q(args.service)} AND environment_name = {q(args.env)} "
             f"AND timestamp_start >= toDateTime({q(start.strftime(TS))}) - INTERVAL 2 HOUR "
             f"AND timestamp_start <= toDateTime({q(end.strftime(TS))})")
    # The primary endpoint: of the PR's endpoints, the one whose recorded calls ran the most
    # changed functions (ties: most calls, then lowest id). The impact graph is drawn for it.
    PRIMARY = f"""
cand AS (SELECT e.endpoint_id AS endpoint_id FROM Endpoints e
         WHERE arrayExists(s -> s.2 = {q(args.service)}, e.services) AND ({scope_cond})),
hits AS (SELECT m.wrapped_endpoint_id AS endpoint_id,
                uniqExactIf(m.function_id, m.function_id IN ({', '.join(map(str, changed_fids))})) AS changed_hits
         FROM {FM} AS m WHERE m.wrapped_endpoint_id IN (SELECT endpoint_id FROM cand) GROUP BY endpoint_id),
reqs AS (SELECT m.endpoint_id AS endpoint_id, SUM(m.invocations) AS calls
         FROM {EM} AS m WHERE m.endpoint_id IN (SELECT endpoint_id FROM cand) GROUP BY endpoint_id),
pick AS (SELECT r.endpoint_id AS endpoint_id, r.calls AS calls, h.changed_hits AS changed_hits
         FROM reqs r LEFT JOIN hits h ON h.endpoint_id = r.endpoint_id
         ORDER BY changed_hits DESC, calls DESC, endpoint_id LIMIT 1)"""
    # Per-call figures for the report generator's CSV contract: milliseconds, error rate 0–1.
    STATS = ("SUM(m.invocations) AS invocations, "
             "round(SUM(m.total_duration_ms) / nullIf(SUM(m.invocations), 0), 3) AS avg_duration_ms, "
             "round(percentileMS(m.duration_buckets, 90), 3) AS p90_duration_ms, "
             "round(function_error_rate(sumMap(m.exceptions), SUM(m.invocations)), 4) AS error_rate")

    queries = [
        ('summary', f"""
WITH ep AS (SELECT m.endpoint_id AS endpoint_id, SUM(m.invocations) AS calls FROM {EM} AS m GROUP BY m.endpoint_id),
     fn AS (SELECT m.function_id AS function_id, SUM(m.invocations) AS calls FROM {FM} AS m GROUP BY m.function_id),
     decl AS (SELECT e.endpoint_id AS endpoint_id, e.type IN ({QUEUE_TYPES}) AS is_queue
              FROM Endpoints e WHERE arrayExists(s -> s.2 = {q(args.service)}, e.services))
SELECT
  (SELECT countIf(NOT is_queue) FROM decl) AS endpoints_declared,
  (SELECT countIf(NOT is_queue) FROM decl WHERE endpoint_id IN (SELECT endpoint_id FROM ep WHERE calls > 0)) AS endpoints_called,
  (SELECT countIf(is_queue) FROM decl) AS queues_declared,
  (SELECT countIf(is_queue) FROM decl WHERE endpoint_id IN (SELECT endpoint_id FROM ep WHERE calls > 0)) AS queues_called,
  (SELECT countIf(f.third_party = '') FROM Functions f WHERE {svc_fn}) AS functions_declared,
  (SELECT countIf(f.third_party = '') FROM Functions f WHERE {svc_fn} AND f.function_id IN (SELECT function_id FROM fn)) AS functions_called,
  (SELECT SUM(calls) FROM fn WHERE function_id IN (SELECT f.function_id FROM Functions f WHERE {svc_fn} AND f.third_party = '')) AS functions_invocations,
  (SELECT SUM(calls) FROM fn) AS function_calls
LIMIT 1"""),
        ('endpoints', f"""
SELECT m.endpoint_id AS endpoint_id, any(e.endpoint_name) AS name, any(e.http_methods) AS methods,
  any(m.endpoint_type) AS ep_type, SUM(m.invocations) AS calls, sumMap(m.status_codes) AS statuses,
  round(percentileMS(m.duration_buckets, 50), 1) AS p50_ms, round(percentileMS(m.duration_buckets, 90), 1) AS p90_ms
FROM {EM} AS m JOIN Endpoints e ON e.endpoint_id = m.endpoint_id
GROUP BY m.endpoint_id ORDER BY calls DESC LIMIT 300"""),
        ('primary_endpoint', f"""
WITH {PRIMARY}
SELECT p.endpoint_id AS endpoint_id, e.endpoint_name AS name, e.http_methods AS methods, p.calls AS calls,
  p.changed_hits AS changed_hits
FROM pick p JOIN Endpoints e ON e.endpoint_id = p.endpoint_id LIMIT 1"""),
        # The report generator's input contract (06-report/references/report-build.md): exact
        # file names and columns.
        ('endpoint-functions', f"""
WITH {PRIMARY},
     mm AS (SELECT m.function_id AS fid, {STATS}
            FROM {FM} AS m WHERE m.wrapped_endpoint_id IN (SELECT endpoint_id FROM pick) GROUP BY fid)
SELECT f.function_id AS function_id, f.function_signature AS function_signature, f.file AS file,
  mm.invocations AS invocations, mm.avg_duration_ms AS avg_duration_ms, mm.p90_duration_ms AS p90_duration_ms,
  mm.error_rate AS error_rate
FROM mm JOIN Functions f ON f.function_id = mm.fid
ORDER BY invocations DESC, function_id LIMIT 5000"""),
        ('function-callers', f"""
WITH {PRIMARY},
     cc AS (SELECT m.caller_function_id AS caller_id, m.function_id AS callee_id, {STATS}
            FROM {FM} AS m WHERE m.wrapped_endpoint_id IN (SELECT endpoint_id FROM pick) AND m.caller_function_id != 0
            GROUP BY caller_id, callee_id)
SELECT cc.caller_id AS caller_function_id, cf.function_signature AS caller_signature,
  cc.callee_id AS callee_function_id, f.function_signature AS callee_signature,
  cc.invocations AS invocations, cc.avg_duration_ms AS avg_duration_ms, cc.p90_duration_ms AS p90_duration_ms,
  cc.error_rate AS error_rate
FROM cc JOIN Functions f ON f.function_id = cc.callee_id JOIN Functions cf ON cf.function_id = cc.caller_id
ORDER BY invocations DESC, caller_function_id, callee_function_id LIMIT 5000"""),
        ('forensics', f"""
SELECT forensic_id, timestamp_start, endpoint_id, trigger_type, round(duration_ms, 1) AS duration_ms,
  exceptions, fingerprint, length(functions) AS function_count
FROM Forensics
WHERE {F_WIN} AND trigger_type = 'Error'
ORDER BY timestamp_start LIMIT 200"""),
        ('forensic_counts', f"""
SELECT trigger_type, endpoint_id, count() AS forensics
FROM Forensics
WHERE {F_WIN}
GROUP BY trigger_type, endpoint_id ORDER BY trigger_type, endpoint_id LIMIT 500"""),
        # Names for every endpoint a forensic mentions, including ones with no calls in the window.
        ('endpoint_names', f"""
SELECT e.endpoint_id AS endpoint_id, e.endpoint_name AS name, e.http_methods AS methods
FROM Endpoints e
WHERE e.endpoint_id IN (SELECT endpoint_id FROM Forensics WHERE {F_WIN})
ORDER BY endpoint_id LIMIT 500"""),
    ]

    out = {
        'service': args.service, 'env': args.env,
        'window': {'start': start.strftime(TS), 'end': end.strftime(TS)},
        'phases': [{**p, 'start': p['start'].strftime(TS), 'end': p['end'].strftime(TS)} for p in phases],
        'mcp': {'tool': 'hud-query', 'environmentName': '', 'serviceScope': 'all'},
        'queries': [{'name': n, 'file': f'{n}.csv', 'sql': ' '.join(s.split()), 'check': check_sql(s)} for n, s in queries],
    }
    path = args.out or os.path.join(ds, 'report-data', 'queries.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as fh:
        json.dump(out, fh, indent=1)
    print(f'{len(queries)} queries for {args.service} ({args.env}), {out["window"]["start"]} to {out["window"]["end"]} UTC -> {path}')
    for qq in out['queries']:
        print(f"  {qq['name']:<20} -> {os.path.join(os.path.dirname(path), qq['file'])}")


if __name__ == '__main__':
    main()
