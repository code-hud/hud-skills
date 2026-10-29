#!/usr/bin/env python3
"""Suggest the PRs to scope the run on, from prs.json and the files Hud sees in the service.

Step 2 of PR selection (see 02-choose-pr/references/pr-selection.md). No call graph and no
endpoint analysis up front: Hud declares the service's functions when its code loads, so
the files it lists are the running service. A PR is a candidate when most of its source
files are among them. Then:

  filter   bots, reverts and PRs reverted later, maintenance types (chore, docs, ci, deps),
           mostly renames, mostly outside the service, and outside the size range (lines
           changed in service files only)
  sort     PRs the user wrote or reviewed first; then by the story signals that match
           (routes in the changed files, database or HTTP calls in the changed lines, tests
           changed, feat/fix/perf, focused on few directories, fixed by a later PR, a real
           description); then the most recent

Usage:
  python3 rank_prs.py --prs prs.json --hud-files hud-files.json [--me ID ...] [--path SUBDIR]
                      [--weights pr-weights.json] [--top N] [--out ranked.json]
  python3 rank_prs.py --prs prs.json --hud-files hud-files.json --pick N --scope-out scope.json
                      [--routes routes.json] [--hud-functions hud-functions.json] [--why TEXT]

  --hud-files F      Source files Hud declared for the service. Without it, prints the query
                     that lists them (needs --service)
  --service NAME     The HUD_SERVICE name, for the queries this script prints
  --top N            How many candidates go to the feasibility pass (default 5). ranked.json
                     lists them under `top`, each with its reasons
  --me ID            The user's GitHub login, git name or email; repeatable. Default: the gh
                     login, and git config user.name and user.email
  --path SUBDIR      Only files under SUBDIR count as the service (one service in a monorepo)
  --pick N           The PR to scope the run on: the feasibility pass's winner, or the one the
                     user named (number, or merge SHA prefix). With --scope-out, writes
                     scope.json: the PR, the endpoints traced from its diff, and the changed
                     functions
  --why TEXT         Why this PR won (the rubric's reason, pr-choice.json); kept in scope.json.
                     Default: the ranker's reasons
  --routes F         The endpoints the agent traced from the PR's diff (02-choose-pr/SKILL.md §4;
                     default .hud-local-pov/routes.json)
  --hud-functions F  Hud's functions in the PR's files and the route files. Without it, --pick
                     prints the query that lists them, and scope.json has no changed functions

Hud results may be a JSON list of strings or of row objects (a `file` column; for functions
also `function_id`, `function_signature`, `start_line`, `end_line`), a JSON object with the
rows under `rows`, `data` or `result`, or plain text with one file per line.
Standard library only (Python 3.9+).
"""

import argparse
import collections
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', re.M)
GENERIC_NAMES = {'index', 'main', 'app', 'server', 'config', 'settings', 'utils', 'util', 'helpers', 'types',
                 'constants', 'models', 'views', 'urls', 'routes', 'service', '__init__', 'mod', 'lib', 'application'}
ISSUE_LINK = re.compile(r'(#\d+|\b(closes|fixes|resolves)\b|https?://\S+/issues/\d+|\b[A-Z][A-Z0-9]+-\d+\b)', re.I)


def git(repo, *args, check=True):
    r = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def read_blobs(repo, wants):
    """{(ref, path): text} for many files, through one `git cat-file --batch` process."""
    wants = list(wants)
    if not wants:
        return {}
    p = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    p.stdin.write(''.join(f'{ref}:{path}\n' for ref, path in wants).encode())
    p.stdin.close()
    out = {}
    for want in wants:
        header = p.stdout.readline().decode().split()
        if len(header) < 3 or header[1] == 'missing':
            continue
        out[want] = p.stdout.read(int(header[2])).decode('utf8', 'replace')
        p.stdout.read(1)
    p.wait()
    return out


def q(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


# --- Hud's view of the service ------------------------------------------------------

def load_rows(path):
    text = open(path, encoding='utf8', errors='replace').read()
    try:
        data = json.loads(text)
    except ValueError:
        return [{'file': ln.strip()} for ln in text.splitlines() if ln.strip()]
    if isinstance(data, dict):
        data = next((data[k] for k in ('rows', 'data', 'result', 'results') if isinstance(data.get(k), list)), [])
    return [r if isinstance(r, dict) else {'file': str(r)} for r in data]


def segs(path):
    """'/app/dist/routes/x.js' -> ['app', 'dist', 'routes', 'x']: no extension, so compiled
    .js matches its .ts source."""
    return os.path.splitext(path.replace('\\', '/').strip('/'))[0].split('/')


class HudFiles:
    """Matches repo paths to the paths Hud recorded, which carry the process's own prefix
    (/app/, /usr/src/app/, a site-packages path) and, for compiled code, the build output."""

    def __init__(self, paths):
        self.files = collections.defaultdict(list)   # suffix tuple -> Hud paths
        self.dirs = set()
        for p in paths:
            s = segs(p)
            for k in range(1, len(s) + 1):
                self.files[tuple(s[-k:])].append(p)
            for k in range(1, len(s)):
                self.dirs.add(tuple(s[-k - 1:-1]))

    def hud_paths(self, repo_path):
        """Hud paths sharing the longest suffix with repo_path: at least 2 segments, or the
        file name alone when one Hud file has it and it isn't generic (a file at the root of
        src/ lands at the root of dist/)."""
        s = segs(repo_path)
        for k in range(len(s), 1, -1):
            hit = self.files.get(tuple(s[-k:]))
            if hit:
                return hit
        hit = self.files.get((s[-1],), [])
        return hit if len(hit) == 1 and s[-1].split('.')[0].lower() not in GENERIC_NAMES else []

    def match(self, repo_path):
        """'file' when Hud has the file; 'dir' when it has others in the same directory (a
        module loaded lazily, not yet imported when the query ran); None otherwise."""
        if self.hud_paths(repo_path):
            return 'file'
        parent = segs(repo_path)[:-1]
        if len(parent) >= 2 and any(tuple(parent[-k:]) in self.dirs for k in range(len(parent), 1, -1)):
            return 'dir'
        return None


def files_query(service):
    return (f"SELECT DISTINCT f.file AS file\nFROM Functions f\n"
            f"WHERE arrayExists(s -> s.2 = {q(service)}, f.services) AND f.third_party = ''\n"
            f"ORDER BY file LIMIT 20000")


def functions_query(service, paths):
    # By file name only: Hud's directories differ from the repo's (/app/dist/ for src/), and
    # the rows are matched back to repo paths afterwards. The bare form catches a JVM file
    # recorded without a directory (jvm.md, "PR ranking").
    likes = sorted({x for p in paths for x in ('%/' + segs(p)[-1] + '.%', segs(p)[-1] + '.%')})
    cond = '\n   OR '.join(f'f.file LIKE {q(x)}' for x in likes) or '0'
    return (f"SELECT f.function_id AS function_id, f.function_signature AS function_signature, f.file AS file,\n"
            f"       f.start_line AS start_line, f.end_line AS end_line\nFROM Functions f\n"
            f"WHERE arrayExists(s -> s.2 = {q(service)}, f.services) AND f.third_party = ''\n"
            f"  AND ({cond})\nLIMIT 20000")


# --- People -------------------------------------------------------------------------

def person_tokens(s):
    """'Dana Levi <dana@x.io>' -> {'dana levi <dana@x.io>', 'dana levi', 'dana@x.io'};
    a GitHub noreply address also yields the login."""
    s = (s or '').strip().lower()
    if not s:
        return set()
    out = {s}
    m = re.match(r'(.*?)\s*<([^>]+)>$', s)
    if m:
        out |= {m.group(1).strip(), m.group(2).strip()} - {''}
    for e in [x for x in out if '@' in x]:
        n = re.match(r'(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$', e)
        if n:
            out.add(n.group(1))
    return out


def default_me(repo):
    me = []
    try:
        r = subprocess.run(['gh', 'api', 'user', '--jq', '.login'], capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            me.append(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    for key in ('user.name', 'user.email'):
        v = git(repo, 'config', key, check=False).strip()
        if v:
            me.append(v)
    return me


def involvement(c, me):
    mine = set().union(*(person_tokens(x) for x in me)) if me else set()
    if not mine:
        return None
    if mine & (person_tokens(c.get('author')) | person_tokens(c.get('authorEmail'))
               | set().union(set(), *(person_tokens(x) for x in c.get('coAuthors') or []))):
        return 'author'
    if mine & set().union(set(), *(person_tokens(x) for x in c.get('reviewers') or [])):
        return 'reviewer'
    return None


# --- Changed lines ------------------------------------------------------------------

def new_lines(hunks):
    """Changed lines on the new side. A pure deletion (newLines 0) sits between newStart and
    newStart + 1."""
    out = set()
    for h in hunks:
        if h['newLines']:
            out.update(range(h['newStart'], h['newStart'] + h['newLines']))
        else:
            out.update((h['newStart'], h['newStart'] + 1))
    return out


def diff_hunks(repo, a, b, path):
    out = []
    for m in HUNK.finditer(git(repo, 'diff', '-U0', a, b, '--', path, check=False)):
        out.append((int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1)))
    return out


def translate(line, hunks):
    """A line at the merge commit -> the same line at HEAD, or None when a later change
    rewrote it."""
    shift = 0
    for old_start, old_n, _, new_n in hunks:
        if old_n == 0:                  # pure insertion after old_start
            if line > old_start:
                shift += new_n
                continue
            break
        if line < old_start:
            break
        if line < old_start + old_n:
            return None
        shift += new_n - old_n
    return line + shift


# --- Ranking ------------------------------------------------------------------------

def label_of(c):
    return f"#{c['number']}" if c.get('number') else (c.get('mergeSha') or '?')[:9]


def reverted_set(changes):
    titles, shas, numbers = set(), set(), set()
    for d in changes:
        m = re.match(r'Revert "(.+)"', d.get('title') or '')
        if m:
            titles.add(m.group(1).strip())
        body = d.get('body') or ''
        shas |= set(re.findall(r'This reverts commit ([0-9a-f]{7,40})', body))
        numbers |= {int(n) for n in re.findall(r'\breverts?\s+#(\d+)', body, re.I)}
    return titles, shas, numbers


def follow_up_fixes(c, changes):
    if not c.get('number'):
        return []
    mention = re.compile(rf'(?<![\w/])#{c["number"]}\b')
    fix = re.compile(r'^fix|\bfix(es|ed)?\b|hotfix|regression', re.I)
    return [label_of(d) for d in changes
            if d is not c and (d.get('mergedAt') or '') > (c.get('mergedAt') or '')
            and mention.search(f"{d.get('title') or ''}\n{d.get('body') or ''}") and fix.search(d.get('title') or '')]


def rank(prs, hud, me, W, path):
    F = W['filters']
    P = W['preferences']
    src_ext = tuple(W['sourceExt'])
    test = re.compile(W['testPath'])
    skip = re.compile(W['skipPath'])
    bot = re.compile(W['botAuthor'], re.I)
    mt, ml = re.compile(W['maintenanceTitle'], re.I), re.compile(W['maintenanceLabel'], re.I)
    ft, fl = re.compile(W['featureTitle'], re.I), re.compile(W['featureLabel'], re.I)
    routes_rx = re.compile(W['routePattern'])
    data_rx = re.compile(W['dataAccessPattern'], re.I)
    repo = prs['repo']['root']
    changes = prs['changes']
    rev_titles, rev_shas, rev_numbers = reverted_set(changes)

    def in_service(p):
        if path and not p.startswith(path.strip('/') + '/'):
            return None
        return hud.match(p)

    # First pass: the cheap filters. Only PRs that pass have their files read from git.
    passed, excluded, near = [], collections.Counter(), []
    for c in changes:
        title, labels = c.get('title') or '', c.get('labels') or []
        sha = c.get('mergeSha') or ''
        if c.get('bot') or bot.search(c.get('author') or ''):
            excluded['bot'] += 1
            continue
        if title.startswith('Revert "') or title.lower().startswith('revert '):
            excluded['a revert'] += 1
            continue
        if title in rev_titles or c.get('number') in rev_numbers or (sha and any(sha.startswith(s) for s in rev_shas)):
            excluded['reverted later in the window'] += 1
            continue
        if mt.search(title) or any(ml.search(l) for l in labels):
            excluded['maintenance (chore, docs, ci, deps...)'] += 1
            continue
        src = [f for f in c['files'] if f['path'].endswith(src_ext) and f['status'] != 'removed'
               and not test.search(f['path']) and not skip.search(f['path'])]
        if not src:
            excluded['no source change (tests, docs, config only)'] += 1
            continue
        svc = [(f, m) for f in src for m in [in_service(f['path'])] if m]
        share = len(svc) / len(src)
        if len(svc) < F['minServiceFiles'] or share < F['minServiceShare']:
            excluded['mostly outside the running service'] += 1
            continue
        renamed = sum(1 for f in src if f['status'] == 'renamed')
        if renamed / len(src) > F['maxRenameShare']:
            excluded['mostly renames or moves'] += 1
            continue
        lines = sum(f['additions'] + f['deletions'] for f, _ in svc)
        r = {'c': c, 'src': src, 'svc': svc, 'share': share, 'lines': lines}
        if lines < F['minLines'] or lines > F['maxLines']:
            excluded[f"size outside {F['minLines']}-{F['maxLines']} lines in service files"] += 1
            if F['minLines'] / 2 <= lines <= F['maxLines'] * 1.5:
                near.append(r)
            continue
        passed.append(r)

    # Second pass: the story signals, from the service files at each PR's merge commit.
    wants = {(r['c']['mergeSha'], f['path']) for r in passed + near if r['c'].get('localCommit') for f, _ in r['svc']}
    blobs = read_blobs(repo, wants)
    results = []
    for r in passed + near:
        c = r['c']
        who = involvement(c, me)
        sig = {}
        route_files, data_lines = [], 0
        for f, _ in r['svc']:
            text = blobs.get((c.get('mergeSha'), f['path']))
            if text is None:
                continue
            if routes_rx.search(text):
                route_files.append(f['path'])
            lines = text.split('\n')
            data_lines += sum(1 for ln in new_lines(f['hunks']) if 0 < ln <= len(lines) and data_rx.search(lines[ln - 1]))
        sig['routes'] = bool(route_files)
        sig['dataAccess'] = data_lines > 0
        sig['tests'] = any(f['path'].endswith(src_ext) and test.search(f['path']) for f in c['files'])
        sig['type'] = bool(ft.search(c.get('title') or '') or any(fl.search(l) for l in c.get('labels') or []))
        dirs = sorted({os.path.dirname(f['path']) for f, _ in r['svc']})
        sig['focused'] = len(dirs) <= W['focusedMaxDirs']
        fixes = follow_up_fixes(c, changes)
        sig['followUpFix'] = bool(fixes)
        body = (c.get('body') or '').strip()
        sig['described'] = len(body) >= W['describedMinChars'] or bool(ISSUE_LINK.search(body))
        points = sum(P[k] for k, v in sig.items() if v)
        rec = {
            'pr': label_of(c), 'number': c.get('number'), 'title': c.get('title'), 'url': c.get('url'),
            'author': c.get('author'), 'reviewers': c.get('reviewers') or [], 'mergedAt': c.get('mergedAt'),
            'involvement': who, 'points': points, 'signals': sig,
            'sourceFiles': len(r['src']), 'serviceFiles': len(r['svc']),
            'serviceFilesByDir': sum(1 for _, m in r['svc'] if m == 'dir'),
            'share': round(r['share'], 2), 'lines': r['lines'], 'dirs': dirs,
            'routeFiles': route_files, 'dataAccessLines': data_lines, 'fixedBy': fixes,
            'contentRead': bool(c.get('localCommit')),
        }
        rec['why'] = why(rec)
        results.append(rec)
    key = lambda x: (x['involvement'] is None, -x['points'], -(int(re.sub(r'\D', '', x['mergedAt'] or '') or 0)))
    ranked = sorted([x for x in results if x['pr'] in {label_of(r['c']) for r in passed}], key=key)
    near_out = sorted([x for x in results if x not in ranked], key=key)
    return ranked, near_out, excluded


def why(r):
    s = r['signals']
    bits = []
    if r['involvement']:
        bits.append('you wrote it' if r['involvement'] == 'author' else 'you reviewed it')
    bits.append(f"{r['serviceFiles']} of {r['sourceFiles']} source files are in the running service, "
                f"{r['lines']} lines changed there")
    if s['routes']:
        bits.append(f"registers routes in {', '.join(r['routeFiles'][:2])}" + (' ...' if len(r['routeFiles']) > 2 else ''))
    if s['dataAccess']:
        n = r['dataAccessLines']
        bits.append('1 changed line builds queries or calls a database or HTTP client' if n == 1 else
                    f'{n} changed lines build queries or call a database or HTTP client')
    if s['tests']:
        bits.append('changes tests')
    if s['type']:
        bits.append('a feature, fix or performance change')
    if s['focused']:
        bits.append(f"focused: {len(r['dirs'])} director{'y' if len(r['dirs']) == 1 else 'ies'}")
    if s['followUpFix']:
        bits.append(f"fixed later by {', '.join(r['fixedBy'][:3])}")
    if not s['described']:
        bits.append('little description')
    if not r['contentRead']:
        bits.append('merge commit not local: route and data signals not checked')
    return '; '.join(bits)


def print_report(ranked, near, excluded, total, hud_count, top):
    print(f"{total} changes, {len(ranked)} eligible; Hud lists {hud_count} source files in the service")
    for reason, n in sorted(excluded.items(), key=lambda x: -x[1]):
        print(f'  excluded {n:4d}  {reason}')
    if ranked:
        print(f'\nTop {min(top, len(ranked))}, for the feasibility pass (02-choose-pr/SKILL.md §3):')
    for i, r in enumerate(ranked[:top], 1):
        print(f"\n{i}. {r['pr']}  {r['title']}  ({r['points']} points)")
        if r['url']:
            print(f"   {r['url']}")
        print(f"   {r['why']}")
    rest = ranked[top:top + 10]
    if rest:
        print('\nAlso eligible:')
        for r in rest:
            who = {'author': 'you wrote', 'reviewer': 'you reviewed'}.get(r['involvement'], '')
            print(f"  {r['pr']:>7}  {r['points']:2d} pts  {r['lines']:4d} lines  {who:12s}  {(r['title'] or '')[:70]}")
    if near:
        print('\nJust outside the size range (pass --pick to use one anyway):')
        for r in near[:5]:
            print(f"  {r['pr']:>7}  {r['points']:2d} pts  {r['lines']:5d} lines  {(r['title'] or '')[:70]}")


# --- Scope for the picked PR --------------------------------------------------------

def short_sig(sig):
    return (sig or '').split('(')[0]


def write_scope(args, prs, hud, W, ranked, near):
    repo = prs['repo']['root']
    want = args.pick.lstrip('#')
    c = next((c for c in prs['changes'] if str(c.get('number')) == want or (c.get('mergeSha') or '').startswith(want)), None)
    if c is None:
        sys.exit(f'PR {args.pick} is not in {args.prs}')
    rec = next((r for r in ranked + near if r['pr'] == label_of(c)), None)
    if rec is None:
        print(f'note: {label_of(c)} did not pass the filters; scoping it anyway because it was picked', file=sys.stderr)
    test, skip = re.compile(W['testPath']), re.compile(W['skipPath'])
    src = [f for f in c['files'] if f['path'].endswith(tuple(W['sourceExt'])) and f['status'] != 'removed'
           and not test.search(f['path']) and not skip.search(f['path'])]

    # The endpoints the agent traced from the PR's changed functions up to their entry points.
    # The confirm step after the sweep checks the trace.
    routes_path = args.routes or os.path.join(repo, '.hud-local-pov', 'routes.json')
    if not os.path.exists(routes_path):
        sys.exit(f"{routes_path} not found: trace the PR's endpoints from its diff and write it first "
                 '(02-choose-pr/SKILL.md §4)')
    traced = json.load(open(routes_path, encoding='utf-8'))
    routes = traced['routes']
    side = re.compile(W['sideEffectPath'], re.I)
    endpoints = []
    for r in routes:
        http = bool(r.get('method') and r.get('path'))
        via = r.get('via') or []
        hops = max(len(via) - 1, 0)
        endpoints.append({'methods': [r['method'].upper()] if http else None, 'path': r['path'] if http else None,
                          'kind': 'http' if http else r.get('kind', 'other'), 'file': r.get('file'), 'line': r.get('line'),
                          'hops': hops, 'core': bool(r.get('core', hops <= 1)), 'handler': r.get('handler'),
                          'chain': via or ['traced from the diff'], 'trigger': r.get('trigger'),
                          'flow': None if http else r.get('flow'),
                          'sideEffect': bool(http and side.search(r['path']))})
    endpoints.sort(key=lambda e: (not e['core'], e['path'] or '', e['methods'] or []))

    # The local service runs the working tree, so changed lines are matched at HEAD.
    ref = 'HEAD'
    ref_sha = git(repo, 'rev-parse', '-q', '--verify', f'{ref}^{{commit}}', check=False).strip()
    changed_functions, function_changes, lost = [], [], 0
    if args.hud_functions:
        rows = [x for x in load_rows(args.hud_functions) if x.get('file') and x.get('start_line') is not None]
        by_hud_file = collections.defaultdict(list)
        for x in rows:
            by_hud_file[x['file']].append(x)
        fn_files = HudFiles(by_hud_file)

        def rows_for(repo_path):
            return [x for p in fn_files.hud_paths(repo_path) for x in by_hud_file[p]]

        def innermost(rs, line):
            hit = [x for x in rs if int(x['start_line']) <= line <= int(x.get('end_line') or x['start_line'])]
            return max(hit, key=lambda x: int(x['start_line'])) if hit else None

        local = bool(c.get('mergeSha')) and ref_sha and subprocess.run(
            ['git', 'cat-file', '-e', f"{c['mergeSha']}^{{commit}}"], cwd=repo, capture_output=True).returncode == 0
        seen = set()
        for f in src:
            rs = rows_for(f['path'])
            moved = diff_hunks(repo, c['mergeSha'], ref_sha, f['path']) if local else []
            for ln in sorted(new_lines(f['hunks'])):
                at = translate(ln, moved) if local else ln
                if at is None:
                    lost += 1
                    continue
                x = innermost(rs, at)
                if x is None:
                    continue
                fid = f"{f['path']}::{short_sig(x.get('function_signature') or x.get('signature'))}"
                if fid in seen:
                    continue
                seen.add(fid)
                qual = fid.split('::', 1)[1]
                changed_functions.append({'id': fid, 'file': f['path'], 'qualname': qual, 'name': qual.split('.')[-1],
                                          'hudFunctionId': x.get('function_id')})
                function_changes.append({'id': fid, 'status': 'added' if f['status'] == 'added' else 'modified',
                                         'inRange': True})
        # The handler: the first function Hud declared at or after the route's line, before
        # the file's next route (a decorated def, an inline arrow function), else the one
        # containing the line (a route table inside a function).
        by_file = collections.defaultdict(list)
        for e in endpoints:
            if e['file'] and e['line']:
                by_file[e['file']].append(e['line'])
        for e in endpoints:
            if not (e['file'] and e['line']):
                continue
            rs = rows_for(e['file'])
            nxt = min((l for l in by_file[e['file']] if l > e['line']), default=10 ** 9)
            after = [x for x in rs if e['line'] <= int(x['start_line']) < nxt]
            x = min(after, key=lambda x: int(x['start_line'])) if after else innermost(rs, e['line'])
            if x is not None:
                e['handler'] = f"{e['file']}::{short_sig(x.get('function_signature') or x.get('signature'))}"

    scope = {
        'pr': {'pr': label_of(c), 'number': c.get('number'), 'title': c.get('title'), 'url': c.get('url'),
               'author': c.get('author'), 'mergedAt': c.get('mergedAt')},
        'why': args.why or (rec['why'] if rec else 'picked by the user'),
        'scopeBy': 'call trace', 'depth': max((e['hops'] for e in endpoints), default=0), 'ref': ref, 'indexedSha': ref_sha,
        'changedFiles': [{'path': f['path'], 'status': f['status'], 'lines': f['additions'] + f['deletions'],
                          'inService': hud.match(f['path'])} for f in src],
        'endpoints': endpoints,
        'alsoReached': traced.get('alsoReached') or [],
        'changedFunctions': changed_functions,
        'functionChanges': function_changes,
        'outboundByKind': {},
    }
    with open(args.scope_out, 'w', encoding='utf-8') as fh:
        json.dump(scope, fh, indent=1)

    core = sum(e['core'] for e in endpoints)
    print(f"scope for {scope['pr']['pr']} {scope['pr']['title']}: {len(endpoints)} endpoints "
          f"({core} core, {len(endpoints) - core} linked), "
          f"{len(changed_functions)} changed functions -> {args.scope_out}")
    for e in endpoints[:25]:
        print(f"  {'/'.join(e['methods'] or [e['kind']])} {e['path'] or e['flow'] or e['handler']}  [via {e['chain'][0]}]"
              f"{'  [side effect]' if e['sideEffect'] else ''}")
    if len(endpoints) > 25:
        print(f'  ... and {len(endpoints) - 25} more')
    if lost:
        print(f'  {lost} changed lines were rewritten after the merge, at {ref}: not matched to functions')
    if not args.hud_functions:
        if not args.service:
            sys.exit('pass --service NAME to print the query for the changed functions')
        print('\nNo --hud-functions: run this through the Hud MCP, save the rows, and re-run with --hud-functions:\n')
        print(functions_query(args.service, [f['path'] for f in src] + sorted({e['file'] for e in endpoints if e['file']})))
    elif not changed_functions:
        print('  no changed line falls inside a function Hud declared: check the paths and lines Hud records '
              '(compiled code, a different ref) before the confirm step')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--prs', required=True)
    ap.add_argument('--hud-files')
    ap.add_argument('--service')
    ap.add_argument('--me', action='append')
    ap.add_argument('--path')
    ap.add_argument('--weights', default=os.path.join(HERE, 'pr-weights.json'))
    ap.add_argument('--top', type=int, default=5)
    ap.add_argument('--out')
    ap.add_argument('--pick')
    ap.add_argument('--scope-out')
    ap.add_argument('--routes')
    ap.add_argument('--hud-functions')
    ap.add_argument('--why')
    args = ap.parse_args()

    if not args.hud_files:
        if not args.service:
            sys.exit('--hud-files is required (or pass --service NAME to print the query that lists them)')
        print('Run this through the Hud MCP, save the rows as a JSON list, and pass the file as --hud-files:\n')
        print(files_query(args.service))
        return
    W = json.load(open(args.weights, encoding='utf-8'))
    prs = json.load(open(args.prs, encoding='utf-8'))
    hud_paths = [r['file'] for r in load_rows(args.hud_files) if r.get('file')]
    if not hud_paths:
        sys.exit(f'{args.hud_files} lists no files: is Hud attached to the service, and the service name right?')
    hud = HudFiles(hud_paths)
    me = args.me if args.me is not None else default_me(prs['repo']['root'])
    ranked, near, excluded = rank(prs, hud, me, W, args.path)

    if args.out:
        with open(args.out, 'w', encoding='utf-8') as fh:
            json.dump({'repo': prs['repo'], 'me': me, 'hudFiles': len(hud_paths), 'total': len(prs['changes']),
                       'eligible': len(ranked), 'excluded': dict(excluded), 'top': ranked[:args.top],
                       'ranked': ranked, 'nearMisses': near},
                      fh, indent=1)
    if args.pick:
        if not args.scope_out:
            sys.exit('--pick needs --scope-out')
        write_scope(args, prs, hud, W, ranked, near)
    else:
        if not me:
            print('note: no identity found (--me); the "you wrote or reviewed it" preference is off', file=sys.stderr)
        print_report(ranked, near, excluded, len(prs['changes']), len(hud_paths), args.top)


if __name__ == '__main__':
    main()
