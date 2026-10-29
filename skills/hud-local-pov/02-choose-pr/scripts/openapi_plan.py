#!/usr/bin/env python3
"""Draft a drive.py plan from an OpenAPI 3 spec.

For an API too large to plan by hand (hundreds of operations), this writes one entry
per operation with a schema-generated body, wires path parameters to pools filled by
the matching create call, and orders the sweep creates -> reads/updates -> deletes.
The output is a DRAFT: one success request per operation. Add the outcome variants on the
PR's changed paths by hand, then sweep and probe it in step 3.

Usage:
  python3 openapi_plan.py SPEC(.json path or URL) [--base-url URL] [--exclude REGEX]
                          [--auth-env NAME] [--routes routes.json] > plan.json

  --exclude REGEX   Skip operations whose "METHOD /path" matches (case-insensitive).
                    Always skipped as well: logout, password and token changes,
                    backups/restore, and DELETE on users, groups and households.
  --auth-env NAME   Send "authorization: Bearer {{env:NAME}}" on every request.
  --routes FILE     Keep only the PR's operations: the routes in FILE (step 2's routes.json,
                    or a scope.json), plus the creates that fill the pools they read.
                    Traced routes the spec lacks are listed. Leave it off to map the whole
                    service.

Python 3.9+, standard library only.
"""
import json
import re
import sys
import urllib.request

# Cloudflare-fronted hosts reject urllib's default user agent (403, error 1010).
USER_AGENT = 'hud-openapi-plan/1'
USAGE = 'Usage: python3 openapi_plan.py SPEC [--base-url URL] [--exclude REGEX] [--auth-env NAME] [--routes FILE]'

# Operations that make a run destructive or lock the driver out. A plan can add them back
# deliberately; a draft never includes them.
ALWAYS_SKIP = re.compile(
    r'logout|password|reset|/tokens?\b|backup|restore|^DELETE /api/(admin/)?(users|groups|households)\b',
    re.IGNORECASE)
TPL = re.compile(r'\{\{(pool|take):(.+?)\}\}')
METHODS = ('get', 'post', 'put', 'patch', 'delete')


def parse_args(argv):
    if argv and argv[0] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)
    opts = {'exclude': None, 'auth_env': None, 'base_url': None, 'routes': None, 'spec': None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('--exclude', '--auth-env', '--base-url', '--routes'):
            i += 1
            if i >= len(argv):
                sys.exit(USAGE)
            if a == '--exclude':
                opts['exclude'] = re.compile(argv[i], re.IGNORECASE)
            elif a == '--auth-env':
                opts['auth_env'] = argv[i]
            elif a == '--routes':
                opts['routes'] = argv[i]
            else:
                opts['base_url'] = argv[i]
        else:
            opts['spec'] = a
        i += 1
    if not opts['spec']:
        sys.exit(USAGE)
    return opts


def load_spec(source):
    if source.startswith('http'):
        request = urllib.request.Request(source, headers={'user-agent': USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    with open(source, encoding='utf-8') as fh:
        return json.load(fh)


class Drafter:
    def __init__(self, spec):
        self.spec = spec

    def deref(self, schema):
        while isinstance(schema, dict) and schema.get('$ref'):
            node = self.spec
            for key in schema['$ref'].split('/')[1:]:
                node = node[key]
            schema = node
        return schema

    def example(self, schema, name='', depth=0):
        """Example request value for a schema: required fields plus optional ones the spec
        gives an example for (a column can be NOT NULL in the DB yet optional in the spec),
        first enum value, templated strings so repeated creates do not collide on unique
        names. Top-level readOnly fields are left out: a create that sends `id: 1` updates
        row 1 instead. Nested ones stay, because there they are references (`type: { id }`)."""
        schema = self.deref(schema)
        if not isinstance(schema, dict) or depth > 6:
            return None
        if 'example' in schema:
            return schema['example']
        if schema.get('default') is not None:
            return schema['default']
        if schema.get('enum') is not None:
            return schema['enum'][0] if schema['enum'] else None
        for k in ('allOf', 'anyOf', 'oneOf'):
            if schema.get(k) is not None:
                options = schema[k]
                resolved = [self.deref(s) for s in options]
                pick = next((s for s in resolved if isinstance(s, dict) and s.get('type') != 'null'), None)
                if pick is None:
                    pick = options[0] if options else None
                return self.example(pick, name, depth + 1)
        t = schema.get('type')
        if isinstance(t, list):
            t = next((x for x in t if x != 'null'), None)
        if t == 'object' or schema.get('properties') is not None:
            out = {}
            props = schema.get('properties') or {}
            keys = list(dict.fromkeys(list(schema.get('required') or [])
                                      + [k for k in props if 'example' in (self.deref(props[k]) or {})]))
            for key in keys:
                if depth == 0 and (self.deref(props.get(key) or {}) or {}).get('readOnly'):
                    continue
                out[key] = self.example(props.get(key) or {}, key, depth + 1)
            return out
        if t == 'array':
            return []
        if t in ('integer', 'number'):
            return 1
        if t == 'boolean':
            return False
        if t == 'string':
            fmt = schema.get('format')
            if fmt in ('uuid', 'uuid4'):
                return '{{uuid}}'
            if fmt == 'date':
                return '2026-12-01'
            if fmt == 'date-time':
                return '{{now}}'
            if fmt == 'email' or re.search('email', name, re.IGNORECASE):
                return '{{email}}'
            if re.search('url|uri', name, re.IGNORECASE) or fmt == 'uri':
                return 'https://example.test/{{word}}'
            return '%s {{word}} {{seq}}' % (name or 'value')
        return None


def pool_for(path, param):
    """"/api/recipes/{slug}/comments/{id}" -> collection for each param: the path up to it."""
    prefix = path[:path.find('{%s}' % param)]
    if prefix.endswith('/'):
        prefix = prefix[:-1]
    return prefix + '#slug' if re.search('slug', param, re.IGNORECASE) else prefix


def last_seg(pool):
    return re.sub(r'#slug$', '', pool).split('/')[-1]


def draft(spec, opts):
    d = Drafter(spec)
    entries = []
    creators = []
    for path, item in spec['paths'].items():
        for method, op in item.items():
            if method not in METHODS:
                continue
            m = method.upper()
            sig = '%s %s' % (m, path)
            if ALWAYS_SKIP.search(sig) or (opts['exclude'] and opts['exclude'].search(sig)):
                continue

            params = [d.deref(p) for p in list(item.get('parameters') or []) + list(op.get('parameters') or [])]
            concrete = path
            for p in params:
                if p.get('in') != 'path':
                    continue
                kind = 'take' if m == 'DELETE' and path.endswith('{%s}' % p['name']) else 'pool'
                concrete = concrete.replace('{%s}' % p['name'], '{{%s:%s}}' % (kind, pool_for(path, p['name'])), 1)
            query = {}
            for p in params:
                if p.get('in') == 'query' and p.get('required'):
                    query[p['name']] = d.example(p.get('schema'), p['name'])

            entry = {
                'name': '%s %s' % (m, path),
                'method': m,
                'path': concrete,
                'tags': [(op.get('tags') or [None])[0] or 'untagged', 'read' if m == 'GET' else 'write'],
            }
            if query:
                entry['query'] = query
            body = None
            if op.get('requestBody'):
                content = (d.deref(op['requestBody']) or {}).get('content') or {}
                body = (content.get('application/json') or {}).get('schema')
            if body is not None:
                entry['body'] = d.example(body)
            elif op.get('requestBody'):
                entry['tags'].append('non-json-body')  # multipart, form: curate by hand
            # A POST to a collection is the create that fills the pool for its item routes. A
            # nested collection (/owners/{ownerId}/pets) counts: its last segment is not a parameter.
            if m == 'POST' and not path.endswith('}'):
                entry['capture'] = {path: 'id', path + '#slug': 'slug'}
                if path not in creators:
                    creators.append(path)
            entries.append(entry)

    # Creates first, then everything else by how many ids it needs, deletes last. The sort
    # is stable, so spec order holds within a rank.
    def rank(e):
        ids = len(re.findall(r'\{\{(?:pool|take):', e['path']))
        if e.get('capture'):
            return 0
        return 100 + ids if e['method'] == 'DELETE' else 1 + ids

    entries.sort(key=rank)

    # A pool with no create at its own prefix borrows one that ends in the same collection:
    # /api/pets/{petId} is filled by POST /api/owners/{ownerId}/pets.
    def borrow(match):
        kind, pool = match.group(1), match.group(2)
        if re.sub(r'#slug$', '', pool) in creators:
            return match.group(0)
        alt = next((c for c in creators if last_seg(c) == last_seg(pool)), None)
        if not alt:
            return match.group(0)
        return '{{%s:%s%s}}' % (kind, alt, '#slug' if pool.endswith('#slug') else '')

    for e in entries:
        e['path'] = TPL.sub(borrow, e['path'])

    # A body field named after a collection the plan creates (`petId`, `owner_id`) takes an
    # id from that pool instead of the spec's example id, which may not exist.
    def pool_for_field(key):
        m = re.match(r'^(\w+?)_?[Ii]d$', key, re.ASCII)
        if not m:
            return None
        plural = m.group(1).lower() + 's'
        return next((c for c in creators if c.split('/')[-1].lower().replace('-', '') == plural), None)

    def link_ids(v):
        if isinstance(v, list):
            return [link_ids(x) for x in v]
        if not isinstance(v, dict):
            return v
        out = {}
        for k, x in v.items():
            pool = pool_for_field(k) if x is not None and not isinstance(x, (dict, list)) else None
            out[k] = '{{pool:%s}}' % pool if pool else link_ids(x)
        return out

    for e in entries:
        if e.get('body'):
            e['body'] = link_ids(e['body'])

    servers = spec.get('servers') or [{}]
    base_url = opts['base_url'] or (servers[0] or {}).get('url') or 'http://localhost:8000'
    plan = {
        '_draft': 'Generated by openapi_plan.py: one success request per operation. Add the 4xx/5xx variants '
                  'whose path runs through the PR\'s changed functions (tag them changed-path), then sweep and '
                  'probe it in step 3.',
        'baseUrl': base_url,
        'headers': {'authorization': 'Bearer {{env:%s}}' % opts['auth_env']} if opts['auth_env'] else {},
        'endpoints': entries,
        'phases': [{'name': 'coverage', 'mode': 'sweep', 'repeat': 1}],
    }
    return plan, entries, creators


def norm(path):
    path = TPL.sub('{}', path or '')
    path = re.sub(r'\{[^{}]*\}', '{}', path)
    path = re.sub(r'(?<=/):[A-Za-z_]\w*', '{}', path)
    return path.rstrip('/') or '/'


def route_keys(path):
    """'METHOD /path' keys for the HTTP routes in routes.json (`routes`) or scope.json (`endpoints`)."""
    with open(path, encoding='utf-8') as fh:
        scope = json.load(fh)
    keys = []
    for r in scope.get('routes') or []:
        if r.get('method') and r.get('path'):
            keys.append('%s %s' % (r['method'].upper(), norm(r['path'])))
    for e in scope.get('endpoints') or []:
        for m in (e.get('methods') or []) if e.get('path') else []:
            keys.append('%s %s' % (m.upper(), norm(e['path'])))
    return list(dict.fromkeys(keys))


def pools_read(entry):
    text = json.dumps([entry.get('path'), entry.get('query'), entry.get('body')], ensure_ascii=False)
    return [re.sub(r'#slug$', '', m.group(2)) for m in TPL.finditer(text)]


def keep_routes(plan, entries, keys):
    """The entries for `keys`, plus the creates that fill the pools they read, transitively."""
    wanted = set(keys)
    keep = [e for e in entries if '%s %s' % (e['method'], norm(e['name'].split(' ', 1)[1])) in wanted]
    kept = {id(e) for e in keep}
    queue = [p for e in keep for p in pools_read(e)]
    seen = set()
    while queue:
        pool = queue.pop(0)
        if pool in seen:
            continue
        seen.add(pool)
        for e in entries:
            if id(e) not in kept and pool in (e.get('capture') or {}):
                kept.add(id(e))
                queue.extend(pools_read(e))
    found = {'%s %s' % (e['method'], norm(e['name'].split(' ', 1)[1])) for e in keep}
    plan['endpoints'] = [e for e in entries if id(e) in kept]
    return plan['endpoints'], [k for k in keys if k not in found]


def main():
    opts = parse_args(sys.argv[1:])
    spec = load_spec(opts['spec'])
    plan, entries, creators = draft(spec, opts)
    missing = []
    if opts['routes']:
        entries, missing = keep_routes(plan, entries, route_keys(opts['routes']))
    sys.stdout.write(json.dumps(plan, indent=2, ensure_ascii=False) + '\n')
    for k in missing:
        print('NOT IN SPEC: %s  (write its entry by hand)' % k, file=sys.stderr)

    pooled = [e for e in entries if re.search(r'\{\{(pool|take):', e['path'])]
    orphan = [e for e in pooled
              if not all(re.sub(r'#slug$', '', m.group(2)) in creators for m in TPL.finditer(e['path']))]
    print('%d entries (%d paths); %d need ids, %d have no create call in the spec to fill their pool'
          % (len(entries), len(spec['paths']), len(pooled), len(orphan)), file=sys.stderr)


if __name__ == '__main__':
    main()
