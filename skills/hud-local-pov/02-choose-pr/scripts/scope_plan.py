#!/usr/bin/env python3
"""Narrow a drive.py plan to one PR's endpoints, and to the outcomes on its changed paths.

Reads a plan (a curated plan, or an openapi_plan.py draft) and the PR's routes: the
routes.json traced in step 2, or the scope.json that rank_prs.py --pick N --scope-out
writes. Keeps:
  pr-scope   for each endpoint the PR reaches, the entries tagged `changed-path` (outcomes
             whose path runs through a function the PR changed), plus one success request
             when none of those is one; every other outcome variant is dropped and listed
  support    the creates that fill the pools those entries read, transitively; they run
             so the in-scope entries have data, and are down-weighted in rate phases
  setup      unchanged
Endpoints in the scope with no entry in the plan are listed: add them by hand, or the
PR's code paths behind them go unexercised.

Usage: python3 scope_plan.py PLAN ROUTES|SCOPE [--support-weight W] > scoped-plan.json
  --support-weight W   multiplier on support entries in rate phases (default 0.3)

Python 3.9+, standard library only.
"""
import json
import re
import sys

USAGE = 'Usage: python3 scope_plan.py PLAN SCOPE [--support-weight W]'

# Same template syntax as drive.py: pool names may hold single braces.
TPL = re.compile(r'\{\{(pool|take):(.+?)\}\}')


def norm(path):
    path = TPL.sub('{}', path or '')
    path = re.sub(r'\{[^{}]*\}', '{}', path)
    path = re.sub(r'(?<=/):[A-Za-z_]\w*', '{}', path)
    return path.rstrip('/') or '/'


def pools_read(entry):
    text = json.dumps([entry.get('path'), entry.get('query'), entry.get('body'), entry.get('headers')],
                      ensure_ascii=False, separators=(',', ':'))
    return [m.group(2) for m in TPL.finditer(text)]


def method_of(entry):
    return (entry.get('method') or 'GET').upper()


def key_of(entry):
    return '%s %s' % (method_of(entry), norm(entry.get('path')))


def is_success(entry):
    tags = entry.get('tags') or []
    if 'client-error' in tags or 'server-error' in tags:
        return False
    return all(c < 400 for c in entry.get('expect') or [200])


def scope_endpoints(scope):
    """The scope's endpoints, from scope.json (`endpoints`) or routes.json (`routes`)."""
    if 'endpoints' in scope:
        return scope['endpoints']
    out = []
    for r in scope.get('routes') or []:
        http = bool(r.get('method') and r.get('path'))
        out.append({'methods': [r['method'].upper()] if http else None, 'path': r['path'] if http else None,
                    'kind': 'http' if http else r.get('kind'), 'handler': r.get('handler'), 'flow': r.get('flow')})
    return out


def pr_label(scope):
    pr = scope.get('pr')
    if isinstance(pr, dict):
        return '%s %s' % (js_str(pr.get('pr')), js_str(pr.get('title')))
    return js_str(pr)


def tag(entry, name):
    tags = list(entry.get('tags') or [])
    if name not in tags:
        tags.append(name)
    return dict(entry, tags=tags)


def js_str(value):
    return '' if value is None else str(value)


def scope_label(endpoint):
    methods = endpoint.get('methods')
    if methods is None:
        methods = [endpoint.get('kind')]
    target = endpoint.get('path')
    if target is None:
        target = endpoint.get('handler')
    return '%s %s' % ('/'.join(js_str(m) for m in methods), js_str(target))


def parse_args(argv):
    if argv and argv[0] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)
    support_weight = 0.3
    files = []
    i = 0
    while i < len(argv):
        if argv[i] == '--support-weight':
            i += 1
            try:
                support_weight = float(argv[i])
            except (IndexError, ValueError):
                sys.exit(USAGE)
            if support_weight.is_integer():
                support_weight = int(support_weight)
        else:
            files.append(argv[i])
        i += 1
    if len(files) != 2:
        sys.exit(USAGE)
    return files[0], files[1], support_weight


def main():
    plan_path, scope_path, support_weight = parse_args(sys.argv[1:])
    with open(plan_path, encoding='utf-8') as fh:
        plan = json.load(fh)
    with open(scope_path, encoding='utf-8') as fh:
        scope = json.load(fh)

    endpoints = scope_endpoints(scope)
    wanted = {}
    for ep in endpoints:
        if ep.get('path') is None:
            continue  # non-HTTP entry points: triggered some other way
        for m in ep.get('methods') or []:
            wanted['%s %s' % (m, norm(ep['path']))] = ep

    by_key = {}
    for e in plan['endpoints']:
        if key_of(e) in wanted:
            by_key.setdefault(key_of(e), []).append(e)
    in_scope, dropped = [], {}
    for k, entries in by_key.items():
        keep = [e for e in entries if 'changed-path' in (e.get('tags') or [])]
        if not any(is_success(e) for e in keep):
            success = next((e for e in entries if is_success(e)), None)
            if success is not None:
                keep.insert(0, success)
        in_scope.extend(keep)
        rest = [e for e in entries if all(e is not x for x in keep)]
        if rest:
            dropped[k] = rest
    in_scope_ids = {id(e) for e in in_scope}
    covered = {key_of(e) for e in in_scope}

    filled_by_setup = {pool for s in plan.get('setup') or [] for pool in (s.get('capture') or {})}
    creators = {}
    for e in plan['endpoints']:
        if method_of(e) == 'DELETE':
            continue
        for pool in e.get('capture') or {}:
            creators.setdefault(pool, []).append(e)

    support = {}
    unfilled = []
    queue = [pool for e in in_scope for pool in pools_read(e)]
    seen_pools = set()
    while queue:
        pool = queue.pop(0)
        if pool in seen_pools or pool in filled_by_setup:
            continue
        seen_pools.add(pool)
        makers = [e for e in creators.get(pool, []) if not any(c >= 400 for c in e.get('expect') or [])]
        if not makers:
            if pool not in unfilled:
                unfilled.append(pool)
            continue
        for e in makers:
            if id(e) in in_scope_ids or e.get('name') in support:
                continue
            support[e.get('name')] = e
            queue.extend(pools_read(e))

    phases = []
    for ph in plan.get('phases') or []:
        if ph.get('rate') or ph.get('mode') == 'rate':
            weights = dict(ph.get('weights') or {})
            if weights.get('support') is None:
                weights['support'] = support_weight
            ph = dict(ph, weights=weights)
        phases.append(ph)

    out = dict(plan)
    out['scope'] = {
        'pr': scope.get('pr'),
        'why': scope.get('why'),
        'endpoints': [scope_label(p) for p in endpoints],
        'changedFunctions': scope.get('changedFunctions'),
    }
    out['endpoints'] = [tag(e, 'support') for e in support.values()] + [tag(e, 'pr-scope') for e in in_scope]
    out['phases'] = phases
    out.pop('_draft', None)
    sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False) + '\n')

    missing = [k for k in wanted if k not in covered]
    err = sys.stderr
    print(pr_label(scope), file=err)
    print('  %d in-scope entries for %d of %d endpoints; %d support entries'
          % (len(in_scope), len(wanted) - len(missing), len(wanted), len(support)), file=err)
    for k, rest in dropped.items():
        print('  dropped %d outcome variant(s) of %s: not tagged changed-path (%s)'
              % (len(rest), k, ', '.join(js_str(e.get('name')) for e in rest)), file=err)
    for k in missing:
        print('  NOT IN PLAN: %s  (add an entry, or this path of the PR goes unexercised)' % k, file=err)
    for p in unfilled:
        print('  no create fills pool %s: entries reading it will be no-data' % p, file=err)
    for p in endpoints:
        if p.get('path') is None:
            print('  not HTTP: %s %s  (trigger it another way, or report it unexercised)'
                  % (js_str(p.get('kind')), js_str(p.get('handler'))), file=err)


if __name__ == '__main__':
    main()
