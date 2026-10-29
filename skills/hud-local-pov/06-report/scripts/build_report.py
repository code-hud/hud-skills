#!/usr/bin/env python3
"""Build the run report: one self-contained HTML page from a run's dataset and Hud results.

Step 2 of the run report (see 06-report/references/report-build.md). Deterministic: the same dataset and result
files always give the same report data (only `createdAt` changes, unless --created-at pins it). Every number
comes from a file. The one exception is the "Finding in this run" section, which the agent writes to
report-data/insights.md from the run's Hud data before this script runs (06-report/references/insights.md).

The page is built by Hud's report generator, next to this script: impact_graph.py builds the
impact graph from three CSVs (endpoint-functions.csv, function-callers.csv, modified-functions.csv)
and fill_report.py fills the bundled template (06-report/assets/report-template.html). This script
writes modified-functions.csv and the page's other sections, then runs the generator.

Inputs (all in the dataset directory, normally .hud-local-pov/):
  run.jsonl, scope.json, prs.json          the run's timeline, the PR scope, the PR history
  report-data/queries.json + *.csv         from report_queries.py and the hud-query runs
  report-data/insights.md                  the agent's findings, for "Finding in this run"
  --plan plan.json                         optional: the count check compares one entry against Hud
  --sdk-log FILE                           optional: counts dropped SDK batches (counts only)
  analytics.json                           the run's random id, which the page sends once when opened
                                           (made if missing; left out with HUD_SKILL_ANALYTICS=0)

Outputs:
  report-data/modified-functions.csv       the PR's changed functions, for the generator
  report-data/report-sections.json         every section except the impact graph, for the generator
  report-data/report.json                  the generator's full report data, for inspection
  --out FILE                               the page (default .hud-local-pov/report.html)

Usage:
  python3 build_report.py --dataset .hud-local-pov [--plan .hud-local-pov/plan.json]
                          [--sdk-log app.log] [--out .hud-local-pov/report.html]
                          [--template PATH] [--created-at 2026-01-31T12:00:00Z]

Python standard library only.
"""

import argparse
import ast
import collections
import csv
import datetime as dt
import json
import os
import re
import statistics
import sys

import fill_report
import impact_graph

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'shared', 'scripts'))
import analytics  # noqa: E402

TS = '%Y-%m-%d %H:%M:%S'


# --- Reading results -----------------------------------------------------------------

def cell(v):
    """hud-query CSV cells: numbers, '' for null, and map or array cells like {'200':3}."""
    if v is None or v == '' or v == '\\N' or v == 'nan':
        return None
    if v[0] in '{[(':
        try:
            return ast.literal_eval(v)
        except (ValueError, SyntaxError):
            return v
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def read_csv(path):
    if not os.path.exists(path):
        return None
    with open(path, newline='', encoding='utf-8-sig') as fh:
        text = fh.read().strip()
    if not text:
        return []
    rows = list(csv.DictReader(text.splitlines()))
    return [{k: cell(v) for k, v in r.items()} for r in rows]


def ts(s):
    return dt.datetime.strptime(s[:19], TS)


def iso(s):
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(dt.timezone.utc).replace(tzinfo=None)


def fmt_int(n):
    return f'{int(round(n)):,}' if n is not None else '—'


def statuses_5xx(m):
    return sum(v for k, v in (m or {}).items() if str(k).startswith('5'))


def sent_counts(path):
    """What the driver sent per phase: per entry, and per request inside scripted flows,
    from run.jsonl phase-end events."""
    out, flows = {}, {}
    if os.path.exists(path):
        for line in open(path):
            if '"phase-end"' in line:
                ev = json.loads(line)
                if ev.get('sent'):
                    out[ev['phase']] = ev['sent']
                flows[ev['phase']] = ev.get('flowRequests') or {}
    return out, flows


# A path segment that stands for a value, in any of the forms plans, scopes and Hud use:
# {{pool:x}} and {x} (plans, FastAPI, Spring), {id} (the driver's flow counts),
# (?P<pk>[^/.]+) (Django regexes, as Hud names them), <int:x> (Flask), :x (Express).
PLACEHOLDER = re.compile(r'\{\{[^}]*\}\}|\{[^}/]*\}|\(\?P<[^>]+>[^)]*\)|<[^>/]+>|(?<=/):[A-Za-z_]\w*')


def route_key(path):
    """A route's segments with every placeholder as {} and no leading or trailing slash, so
    /api/documents/{id}/ (a scope), /api/documents/(?P<pk>[^/.]+) (Hud on Django) and
    /api/documents/12 (a sent request) compare as the same route."""
    # Placeholders first: a Django pattern's `(?P<…>` holds a `?` that isn't a query string.
    p = PLACEHOLDER.sub('{}', str(path)).split('?')[0]
    return [s for s in p.strip('/').split('/')]


def same_route(path, hud_name):
    """True when `path` (a plan path, a scope path or a sent request) is the route Hud calls
    `hud_name`. A placeholder on either side matches any one segment."""
    a, b = route_key(path), route_key(hud_name)
    return len(a) == len(b) and all(x == y or x == '{}' or y == '{}' for x, y in zip(a, b))


# --- The model -----------------------------------------------------------------------

class Run:
    def __init__(self, args):
        ds = self.dataset = args.dataset
        data = os.path.join(ds, 'report-data')
        self.meta = json.load(open(os.path.join(data, 'queries.json')))
        need = [q['file'] for q in self.meta['queries']]
        missing = [f for f in need if not os.path.exists(os.path.join(data, f))]
        if missing:
            sys.exit('missing result files (run each query in queries.json and save its output): ' + ', '.join(missing))
        self.r = {q['name']: read_csv(os.path.join(data, q['file'])) for q in self.meta['queries']}
        self.scope = json.load(open(os.path.join(ds, 'scope.json')))
        self.sent, self.flow_requests = sent_counts(os.path.join(ds, 'run.jsonl'))
        self.prs = json.load(open(os.path.join(ds, 'prs.json')))
        self.plan = json.load(open(args.plan)) if args.plan else None
        self.sdk_log = args.sdk_log
        self.phases = [{**p, 'start': ts(p['start']), 'end': ts(p['end'])} for p in self.meta['phases']]
        self.phase = {p['name']: p for p in self.phases}
        self.endpoints = {e['endpoint_id']: e for e in self.r['endpoints']}
        # Names for endpoints with no calls in the window too (a forensic from before it).
        self.names = {e['endpoint_id']: e for e in (self.r.get('endpoint_names') or [])}
        # The PR endpoint the impact graph is drawn for, and the functions Hud recorded under it.
        self.primary = (self.r.get('primary_endpoint') or [None])[0]
        if self.primary:
            self.names.setdefault(self.primary['endpoint_id'], self.primary)
        self.ep_functions = {f['function_id']: f for f in self.r['endpoint-functions']}
        self.scope_eps = self.map_scope_endpoints()
        self.changed = self.map_changed()

    def ep_label(self, eid):
        e = self.endpoints.get(eid) or self.names.get(eid)
        if not e:
            return f'endpoint {eid}'
        methods = e.get('methods') or []
        return (f"{'/'.join(methods)} " if methods else '') + str(e['name'])

    def map_scope_endpoints(self):
        """Scope endpoints -> Hud endpoint ids: HTTP by path and method, other flows by flow name."""
        out = []
        for s in self.scope['endpoints']:
            if s.get('path'):
                eid = next((e['endpoint_id'] for e in self.endpoints.values()
                            if route_key(e['name']) == route_key(s['path'])
                            and set(s.get('methods') or []) & set(e.get('methods') or [])), None)
            elif s.get('flow'):
                eid = next((e['endpoint_id'] for e in self.endpoints.values()
                            if str(e['name']) == s['flow'] and not e.get('methods')), None)
            else:
                continue
            out.append({**s, 'endpoint_id': eid})
        return out

    def map_changed(self):
        """The PR's changed functions with their status and Hud function id (from scope.json)."""
        status = {c['id']: c.get('status') for c in self.scope.get('functionChanges') or []}
        return [{'id': c['id'], 'qualname': c['qualname'], 'file': c['file'], 'status': status.get(c['id']),
                 'fid': int(c['hudFunctionId']) if c.get('hudFunctionId') else None}
                for c in self.scope['changedFunctions']]

    def endpoint_for(self, method, path):
        for eid, e in self.endpoints.items():
            if method in (e.get('methods') or []) and same_route(path, e['name']):
                return eid
        return None

    def count_check(self):
        """Hud's count for one plain plan entry against what the driver sent, across the run.
        A ratio near 2 means duplicated metric rows; well under 1 means dropped batches."""
        # Phases of a minute or so are one or two SDK flushes long, and a flush that straddles a
        # phase edge lands in one of them: compare the whole run, not one phase.
        sent, flows = collections.Counter(), collections.Counter()
        for name, per_entry in self.sent.items():
            if name in self.phase:
                sent.update(per_entry)
                flows.update(self.flow_requests.get(name) or {})
        if not sent or not self.plan:
            return None
        entries = {e['name']: e for e in self.plan.get('endpoints', []) if not e.get('script') and e.get('path')}
        for name, n in sorted(sent.items(), key=lambda kv: -kv[1]):
            e = entries.get(name)
            eid = self.endpoint_for((e or {}).get('method', 'GET').upper(), e['path']) if e else None
            if not eid:
                continue
            got = (self.endpoints.get(eid) or {}).get('calls')
            if got:
                # Other entries can hit the same route: compare against all of them.
                total = sum(m for nm, m in sent.items() if nm in entries and
                            entries[nm].get('method', 'GET').upper() == e.get('method', 'GET').upper() and
                            same_route(entries[nm]['path'], self.endpoints[eid]['name']))
                # Requests sent from inside scripted flows to the same route count too. The driver
                # logs them per method and path; a log from before it did has none, so then the
                # flows' run counts only widen the range Hud may show.
                method = e.get('method', 'GET').upper()
                by_flow = sum(m for k, m in flows.items()
                              if k.split(' ', 1)[0] == method and same_route(k.split(' ', 1)[1], self.endpoints[eid]['name']))
                total += by_flow
                scripted = 0 if flows else sum(m for nm, m in sent.items()
                                               if any(x.get('name') == nm and x.get('script') for x in self.plan.get('endpoints', [])))
                return {'endpoint': self.ep_label(eid), 'sent': total, 'scripted': scripted, 'hud': got,
                        'ratio': got / total if total else None, 'where': 'across the run'}
        return None


# --- PR statistics -------------------------------------------------------------------

def pr_stats(run):
    q = run.prs.get('query', {})
    fetched = iso(q['fetchedAt']) if q.get('fetchedAt') else dt.datetime.utcnow()
    merged = [iso(c['mergedAt']) for c in run.prs['changes'] if c.get('mergedAt')]
    # Count back from the newest PR in the list, not the fetch: a list of named PRs ends
    # at the newest of them.
    anchor = min(max(merged), fetched) if merged else fetched
    week = [c for c in run.prs['changes'] if c.get('mergedAt') and anchor - dt.timedelta(days=7) < iso(c['mergedAt']) <= anchor]
    humans = [c for c in week if not c.get('bot')]
    hours = [(iso(c['mergedAt']) - iso(c['createdAt'])).total_seconds() / 3600 for c in humans if c.get('createdAt')]
    candidates = sum(safe_to_merge_candidate(c) for c in humans)
    return {'week': len(week), 'humans': len(humans), 'bots': len(week) - len(humans),
            'mean_hours': statistics.mean(hours) if hours else None, 'n_hours': len(hours),
            'candidates': candidates, 'safe_percent': pct(SAFE_SHARE * candidates, len(humans)),
            'fetched': fetched, 'anchor': anchor, 'repo': (run.prs.get('repo') or {}).get('github') or os.path.basename(run.prs['repo']['root'])}


def time_to_merge_note(run, ps):
    """Why the time-to-merge tile is empty, in terms of the user's repo and setup; None when it has a value."""
    if ps['mean_hours'] is not None:
        return None
    if not ps['humans']:
        return 'No PRs by people were merged that week'
    if not (run.prs.get('repo') or {}).get('github'):
        return "Needs PR open times, which come from GitHub, and this repo's origin isn't on GitHub"
    if (run.prs.get('query') or {}).get('source') == 'gh':
        return 'GitHub returned no open times for these PRs'
    return 'Needs PR open times from GitHub: sign in to the GitHub CLI (gh auth login) and fetch the PRs again'


# --- Safe to merge -------------------------------------------------------------------
# A PR Hud could approve on runtime evidence changes backend code and no database migration
# or infrastructure (infrastructure as code, container builds, CI config). Hud is taken to clear SAFE_SHARE of those: the card shows that many PRs
# as a share of every PR it counts. Source, test and generated paths are the ranker's.

SAFE_SHARE = 0.3
PR_WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '02-choose-pr', 'scripts', 'pr-weights.json')
FRONTEND = re.compile(r'\.(jsx|tsx|vue|svelte)$|(^|/)(frontend|webapp|ui)/', re.I)
MIGRATION = re.compile(r'(^|/)(migrations?|alembic|db/migrate|flyway|liquibase|changelogs?)/|'
                       r'(^|/)(schema\.prisma|schema\.rb|structure\.sql)$|(^|/)V\d+(_\d+)*__[^/]*\.sql$', re.I)
INFRA = re.compile(r'\.(tf|tfvars|hcl)$|(^|/)(terraform|helm|charts|k8s|kubernetes|kustomize|cdk|pulumi|ansible|cloudformation)/|'
                   r'(^|/)(chart|kustomization|serverless|pulumi)(\.[\w-]+)?\.ya?ml$|'
                   # containers
                   r'(^|/)(Dockerfile|Containerfile)([.-][^/]*)?$|\.dockerfile$|(^|/)\.dockerignore$|'
                   r'(^|/)(docker-)?compose([.-][^/]*)?\.ya?ml$|'
                   # CI
                   r'(^|/)(\.github/(workflows|actions)|\.circleci|\.buildkite|\.gitlab|\.teamcity|\.woodpecker)/|'
                   r'(^|/)Jenkinsfile([.-][^/]*)?$|'
                   r'(^|/)(\.gitlab-ci|\.travis|\.drone|\.woodpecker|azure-pipelines|bitbucket-pipelines|cloudbuild|'
                   r'appveyor|buildkite|codefresh|buildspec)(\.[\w-]+)?\.ya?ml$', re.I)
_weights = None


def safe_to_merge_candidate(change):
    """True when a PR changes backend source and no migration, schema or infrastructure file."""
    global _weights
    if _weights is None:
        with open(PR_WEIGHTS, encoding='utf-8') as fh:
            w = json.load(fh)
        _weights = (tuple(w['sourceExt']), re.compile(w['testPath']), re.compile(w['skipPath']))
    exts, test, skip = _weights
    paths = [p for f in change.get('files') or [] for p in (f.get('path'), f.get('oldPath')) if p]
    if any(MIGRATION.search(p) or INFRA.search(p) for p in paths):
        return False
    return any(p.endswith(exts) and not test.search(p) and not skip.search(p) and not FRONTEND.search(p) for p in paths)


# --- Markdown ------------------------------------------------------------------------
# The page renders markdown and parses raw HTML in it, so every name from the run goes in a
# code span and every other value from the run is escaped.

def code(s):
    s = str(s)
    ticks = 1
    while '`' * ticks in s:
        ticks += 1
    pad = ' ' if s.startswith('`') or s.endswith('`') else ''
    return f"{'`' * ticks}{pad}{s}{pad}{'`' * ticks}"


def md_text(s):
    s = re.sub(r'([\\`*_\[\]|#])', r'\\\1', str(s))
    return s.replace('<', '&lt;').replace('>', '&gt;')


def md_table(head, rows, align=None):
    if not rows:
        return ''
    cell_text = lambda c: str(c).replace('|', '\\|').replace('\n', ' ')
    align = align or ['---'] + ['---:'] * (len(head) - 1)
    lines = ['| ' + ' | '.join(head) + ' |', '| ' + ' | '.join(align) + ' |']
    lines += ['| ' + ' | '.join(cell_text(c) for c in r) + ' |' for r in rows]
    return '\n'.join(lines)


# --- Insights (written by the agent) -------------------------------------------------

INSIGHTS_FILE = 'insights.md'
CODE_SPANS = re.compile(r'(```.*?```|`+[^`\n]*`+)', re.S)


def agent_insights(dataset):
    """The agent's insights (06-report/references/insights.md), with raw HTML outside code made
    inert: the page parses HTML in markdown, and the text quotes names and messages from the run."""
    path = os.path.join(dataset, 'report-data', INSIGHTS_FILE)
    if not os.path.exists(path):
        sys.exit(f'missing {path}: write the run insights first (06-report/references/insights.md)')
    with open(path, encoding='utf-8') as fh:
        text = fh.read().strip()
    if not text:
        sys.exit(f'{path} is empty: write the run insights first (06-report/references/insights.md)')
    parts = CODE_SPANS.split(text)
    return ''.join(p if i % 2 else p.replace('<', '&lt;') for i, p in enumerate(parts))


def insights_markdown(run):
    """The markdown card at the end of the page: the agent's insights, then how the run was made."""
    parts = [agent_insights(run.dataset)]
    parts.append('## How this run was made')
    parts.append('Traffic was synthetic, sent by a driver that marks every request with a run id. '
                 + 'The service ran on the engineer\'s machine.'
                 + f' Hud\'s SDK reported to the **{md_text(run.meta["env"])}** environment'
                 + (', where Hud draws charts and captures forensics but raises no alerts.' if run.meta['env'] == 'local' else '.')
                 + dropped_batches(run) + count_check_text(run))
    parts.append(md_table(('Phase', 'UTC', 'Rate (entries/s)'),
                          [(code(p['name']), f"{p['start']:%H:%M}–{p['end']:%H:%M}",
                            f"{p['rate']}") for p in run.phases],
                          align=['---', '---', '---:']))
    return '\n\n'.join(parts)


def count_check_text(run):
    c = run.count_check()
    if not c or not c['ratio']:
        return ''
    if c['scripted'] and c['sent'] < c['hud']:
        # A run.jsonl from a driver that didn't log flow requests: the flows' share is unknown.
        return (f' Hud shows {fmt_int(c["hud"])} {code(c["endpoint"])} requests {c["where"]}: {fmt_int(c["sent"])} from plain entries, '
                f'and the rest likely from the {fmt_int(c["scripted"])} scripted-flow runs, which this run\'s log doesn\'t count per request.')
    if 0.8 <= c['ratio'] <= 1.25:
        return (f' Hud\'s count matches the driver\'s: Hud counted {fmt_int(c["hud"])} {code(c["endpoint"])} requests {c["where"]}, '
                f'the driver sent {fmt_int(c["sent"])}.')
    return (f' Check the counts: Hud shows {fmt_int(c["hud"])} {code(c["endpoint"])} requests {c["where"]}, the driver sent '
            f'{fmt_int(c["sent"])} ({c["ratio"]:.2f}×).')


def dropped_batches(run):
    if not run.sdk_log or not os.path.exists(run.sdk_log):
        return ''
    n = 0
    with open(run.sdk_log, errors='replace') as fh:
        for line in fh:
            n += 'Failed to send json' in line
    return (f' The SDK failed to send {n} batch{"es" if n != 1 else ""} during the run, so Hud\'s counts sit slightly '
            'below what the driver sent.') if n else ''


# --- The report document -------------------------------------------------------------
# The page's data contract (LocalRunReport, schemaVersion 1): counts are integers,
# durations milliseconds, percentages 0–100, timestamps ISO-8601. Every field is required.

SCHEMA_VERSION = 1


def pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0


def run_minutes(run):
    return (max(p['end'] for p in run.phases) - min(p['start'] for p in run.phases)).total_seconds() / 60


def ep_error_percent(e):
    return pct(statuses_5xx(e.get('statuses')), e.get('calls') or 0)


def pr_summary_markdown(run):
    reached = sum(1 for c in run.changed if c['fid'] in run.ep_functions)
    return (f'{code(run.ep_label(run.primary["endpoint_id"]))} runs the most changed code at runtime '
            f'({reached} of {len(run.changed)} changed functions). The call path below is what Hud recorded '
            'for it during the run, not a static guess.')


def sdk_coverage(S):
    """The "What the Hud SDK saw" cards from the `summary` row. `functions.invocations` is
    optional: a summary saved before the query had `functions_invocations` leaves it out."""
    functions = {'called': S['functions_called'] or 0, 'mapped': S['functions_declared'] or 0}
    if S.get('functions_invocations') is not None:
        functions['invocations'] = int(S['functions_invocations'])
    return {
        'endpoints': {'called': S['endpoints_called'] or 0, 'mapped': S['endpoints_declared'] or 0},
        'queues': {'called': S['queues_called'] or 0, 'mapped': S['queues_declared'] or 0},
        'functions': functions,
    }


def report_sections(run, created_at, dataset):
    """Every section of the page except the impact graph and the PR's added/modified counts,
    which the generator builds from the three CSVs."""
    S = run.r['summary'][0]
    ps = pr_stats(run)
    pr = run.scope['pr']
    sections = {
        'schemaVersion': SCHEMA_VERSION,
        'createdAt': created_at,
        'runDurationMinutes': round(run_minutes(run)),
        'sdk': sdk_coverage(S),
        'repoPrs': {
            'prCount': ps['humans'],
            'avgHoursToMerge': round(ps['mean_hours'], 1) if ps['mean_hours'] is not None else None,
            'safeToMergePercent': ps['safe_percent'],
        },
        'prExample': {
            'number': pr.get('number'),
            'title': pr.get('title') or pr['pr'],
            'url': pr.get('url'),
            'summaryMarkdown': pr_summary_markdown(run),
            'affectedEndpoints': len(run.scope_eps),
        },
        'insightsMarkdown': insights_markdown(run),
    }
    note = time_to_merge_note(run, ps)
    if note:
        sections['repoPrs']['avgHoursToMergeNote'] = note
    # The page sends one `report_opened` event under the run's id; left out when opted out.
    fields = analytics.report_fields(dataset)
    if fields:
        sections['analytics'] = fields
    return sections


# --- The generator ---------------------------------------------------------------------
# Hud's report generator (impact_graph.py, fill_report.py) builds the impact graph from the
# three CSVs and fills the page template. The CSV contract is in 06-report/references/report-build.md.

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TEMPLATE = os.path.join(HERE, '..', 'assets', 'report-template.html')


def write_modified_functions(run, path):
    """modified-functions.csv: one row per changed function (`function`, `file`, `change`)."""
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(('function', 'file', 'change'))
        for c in run.changed:
            w.writerow((c['qualname'], c['file'], c['status'] or 'modified'))


def build_report_json(run, data, sections, report):
    read = impact_graph.read_text
    try:
        graph = impact_graph.build_impact_graph(
            read(os.path.join(data, 'endpoint-functions.csv')), read(os.path.join(data, 'function-callers.csv')),
            read(os.path.join(data, 'modified-functions.csv')), run.meta['service'],
            endpoint_label=run.ep_label(run.primary['endpoint_id']))
    except (impact_graph.MissingCsvColumnError, impact_graph.NoEndpointFunctionError) as error:
        sys.exit(f'impact graph failed: {error}')
    with open(report, 'w', encoding='utf-8', newline='') as fh:
        fh.write(impact_graph.to_json(impact_graph.with_impact_graph(impact_graph.parse_json(read(sections)), graph), 2) + '\n')


def fill_page(report, template, out):
    try:
        html = fill_report.fill_report(impact_graph.read_text(template), impact_graph.read_text(report))
    except (fill_report.InvalidReportInputError, fill_report.ReportDataPlaceholderMissingError,
            fill_report.MalformedGraphError) as error:
        sys.exit(f'filling the report failed: {error}')
    with open(out, 'w', encoding='utf-8', newline='') as fh:
        fh.write(html)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dataset', required=True)
    ap.add_argument('--plan')
    ap.add_argument('--sdk-log')
    ap.add_argument('--out')
    ap.add_argument('--template', default=DEFAULT_TEMPLATE)
    ap.add_argument('--created-at')
    args = ap.parse_args()
    if not os.path.exists(args.template):
        sys.exit(f'not found: {args.template} (the report template ships with the published skill; '
                 'reinstall it to get it back)')
    run = Run(args)
    if not run.primary or not run.ep_functions:
        sys.exit('endpoint-functions.csv is empty: none of the PR\'s endpoints ran during the run, and the report '
                 'needs the call path of one of them')
    created_at = args.created_at or dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    data = os.path.join(args.dataset, 'report-data')
    changed_csv = os.path.join(data, 'modified-functions.csv')
    write_modified_functions(run, changed_csv)
    sections = os.path.join(data, 'report-sections.json')
    with open(sections, 'w', encoding='utf-8') as fh:
        json.dump(report_sections(run, created_at, args.dataset), fh, indent=1, ensure_ascii=False, allow_nan=False)
    report = os.path.join(data, 'report.json')
    build_report_json(run, data, sections, report)
    out = args.out or os.path.join(args.dataset, 'report.html')
    fill_page(report, args.template, out)
    ps = pr_stats(run)
    print(f"safe to merge: {ps['safe_percent']:g}% of {ps['humans']} PRs ({SAFE_SHARE:.0%} of the "
          f"{ps['candidates']} that change backend code and no migration or infrastructure)")
    print(f'report data -> {report}')
    print(f'report -> {out}')


if __name__ == '__main__':
    main()
