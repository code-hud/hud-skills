#!/usr/bin/env python3
"""Traffic driver for the hud-local-pov skill.

Reads a plan (JSON) describing a service's endpoints and a sequence of traffic
phases, and drives them against a base URL. Standard library only; Python >= 3.9.

The plan is written per repository by the skill. This script only executes it,
so everything specific to a service lives in the plan, not here.

Usage:
  python3 drive.py plan.json [--base-url URL] [--phase NAME[,NAME]] [--log run.jsonl]
                             [--dry-run] [--skip-setup] [--pools FILE]

  --base-url URL  Override plan.baseUrl (e.g. another local port)
  --phase NAMES   Run only these phases, comma-separated, in plan order
  --log FILE      Append a JSONL timeline: phase start/end + every non-expected response
  --dry-run       Resolve and print one request per endpoint, send nothing
  --stop-file P   Stop cleanly as soon as file P exists.
                  Give the prospect `touch P` as their kill switch
  --run-id ID     Label for this run (default: UTC timestamp). Sent in the user-agent and
                  x-local-pov-run headers, available as {{run}}, written to the log
  --check         Run only the readiness checks (plan.preflight) and exit: 0 ready, 3 not
  --probe OUT     For a plan written from code, with no spec: send each entry and learn from the
                  service's validation errors (missing fields, allowed values, uniqueness,
                  types), then write the improved plan to OUT with notes per entry
  --skip-setup    Do not run plan.setup. Pools start empty unless --pools loads them
  --pools FILE    After setup, save the pools to FILE as JSON. If FILE already exists, load
                  the pools from it and skip setup. Without one of these, every invocation
                  (each sweep, each --phase run) re-runs setup and leaves its records behind

Exit codes: 0 done, 1 bad usage or error, 2 the guard aborted, 3 not ready, 130 signal.

Plan shape (see the skill's plan reference for the full description):
  {
    "baseUrl": "http://localhost:3000",
    "headers": { "authorization": "Bearer {{env:API_TOKEN}}" },
    "setup":   [ <request>, ... ],            // run once, in order, before phases
    "endpoints": [ <request>, ... ],
    "phases":  [ { "name", "mode": "sweep"|"rate", ... }, ... ]
  }
    "auth":    { "cmd": "...", "every": "45m", "header": "authorization" },   // optional
    "limits":  { "maxRps": 50, "abortOn": { "401": 0.2, "429": 0.05 } },     // optional
  <script entry> = { name, script: "./flows.py#function_name", expect?, weight?, tags? }
    A multi-step flow in Python, for what one request can't express: read-modify-write,
    CSRF, multipart, state machines. See "Script entries" in the plan reference.
  <request> = { name, method, path, query?, headers?, body?, expect?: [codes],
                capture?: { poolName: "dot.path" }, weight?, tags?: [..], timeoutMs? }

Templates, usable in path, query, headers and body strings:
  {{uuid}} {{int:MIN:MAX}} {{word}} {{email}} {{now}} {{seq}} {{run}} {{env:NAME}}
  {{pool:NAME}}   a random value previously captured into pool NAME
  {{take:NAME}}   the same, but removes it from the pool (use on deletes)
  A string that is exactly one template keeps the value's type (numbers stay numbers).

Script entries: "./flows.py#name" calls def name(ctx) from flows.py (path relative to the
plan file; without "#name" it calls main). ctx has:
  ctx.request(method, path, query=None, body=None, headers=None) -> response with
      .status, .ok, .json (None if the body is not JSON), .text. Same base URL, headers
      and auth as every other entry; redirects are not followed
  ctx.pool(name), ctx.take(name), ctx.put(name, value)   the plan's pools
  ctx.env(name), ctx.run, ctx.base_url
The function returns the status that stands for the whole flow (default 200) and raises
to fail it. Flows run on worker threads, concurrently in rate phases.
"""

import copy
import errno
import http.client
import importlib.machinery
import importlib.util
import json
import math
import os
import random
import re
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qsl, quote, quote_plus, urlencode, urlsplit

USAGE = ('Usage: python3 drive.py plan.json [--base-url URL] [--phase A,B] [--log run.jsonl] [--dry-run] '
         '[--stop-file P] [--run-id ID] [--skip-setup] [--pools FILE] [--check] [--probe OUT]')

# Stands for JavaScript's undefined where the plan or the log tells it apart from null.
UNDEF = object()


class DriveError(Exception):
    pass


class MissingPool(DriveError):
    pass


# {{unknown:field}}: the field is known but its value isn't. The entry is skipped, never
# sent with a placeholder, until a probe or a human fills it in.
class Unresolved(DriveError):
    pass


# --- JSON and value helpers ---------------------------------------------------
#
# Plans, pools files and logs are shared with tools that read them as JSON written by
# JavaScript, so numbers, strings and key order follow its rules.

def _num(s):
    f = float(s)
    return int(f) if f.is_integer() and abs(f) < 1e21 else f


def load_json(text):
    return json.loads(text, parse_float=_num)


def to_json(value, indent=None):
    seps = (',', ':') if indent is None else (',', ': ')
    return json.dumps(value, ensure_ascii=False, separators=seps, indent=indent)


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def js_str(v):
    if v is UNDEF:
        return 'undefined'
    if v is None:
        return 'null'
    if v is True:
        return 'true'
    if v is False:
        return 'false'
    if isinstance(v, float):
        if v != v:
            return 'NaN'
        if math.isinf(v):
            return 'Infinity' if v > 0 else '-Infinity'
        return str(int(v)) if v.is_integer() and abs(v) < 1e21 else repr(v)
    if isinstance(v, list):
        return js_join(v, ',')
    if isinstance(v, dict):
        return '[object Object]'
    return str(v)


def js_join(items, sep):
    return sep.join('' if x is None or x is UNDEF else js_str(x) for x in items)


def truthy(v):
    if v is None or v is UNDEF or v is False:
        return False
    if v is True:
        return True
    if is_number(v):
        return v == v and v != 0
    if isinstance(v, str):
        return v != ''
    return True


def js_or(*vals):
    for v in vals:
        if truthy(v):
            return v
    return vals[-1]


def nn(v, default):
    return default if v is None or v is UNDEF else v


def same(a, b):
    if is_number(a) or is_number(b):
        return is_number(a) and is_number(b) and a == b
    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        return a is b
    return type(a) is type(b) and a == b


def index_same(items, v):
    return next((i for i, x in enumerate(items) if same(x, v)), -1)


INDEX_KEY = re.compile(r'0|[1-9][0-9]*')


def js_order(d):
    """Integer-like keys first, ascending, as a JavaScript object orders them."""
    ints = sorted((k for k in d if INDEX_KEY.fullmatch(k)), key=int)
    out = {k: d[k] for k in ints}
    out.update((k, v) for k, v in d.items() if not INDEX_KEY.fullmatch(k))
    return out


def js_get(o, k):
    k = js_str(k)
    if isinstance(o, dict):
        return o.get(k, UNDEF)
    if isinstance(o, (list, str)):
        if INDEX_KEY.fullmatch(k) and int(k) < len(o):
            return o[int(k)]
        return len(o) if k == 'length' else UNDEF
    return UNDEF


def js_set(o, k, v):
    if isinstance(o, dict):
        o[k] = v
    elif isinstance(o, list) and INDEX_KEY.fullmatch(k):
        i = int(k)
        o.extend([None] * (i + 1 - len(o)))
        o[i] = v


def js_round(x):
    return int(math.floor(x + 0.5))


def now_iso(t=None):
    t = t or datetime.now(timezone.utc)
    return t.strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (t.microsecond // 1000)


def now_ms():
    return time.time() * 1000


def eprint(*a):
    print(*a, file=sys.stderr, flush=True)


# --- Args -----------------------------------------------------------------------

def print_help_and_exit(code):
    eprint(USAGE)
    sys.exit(code)


def number(s):
    if s is None:
        return math.nan
    try:
        return _num(s.strip()) if s.strip() else 0
    except ValueError:
        return math.nan


def parse_args(argv):
    o = SimpleNamespace(base_url=None, phases=None, log=None, dry_run=False, stop_file=None,
                        run_id=None, skip_setup=False, check=False, probe=None, pools=None, plan_path=None)
    i = 0

    def value():
        nonlocal i
        i += 1
        return argv[i] if i < len(argv) else None

    while i < len(argv):
        a = argv[i]
        if a == '--base-url':
            o.base_url = value()
        elif a == '--phase':
            o.phases = (value() or '').split(',')
        elif a == '--log':
            o.log = value()
        elif a == '--dry-run':
            o.dry_run = True
        elif a == '--stop-file':
            o.stop_file = value()
        elif a == '--run-id':
            o.run_id = value()
        elif a == '--skip-setup':
            o.skip_setup = True
        elif a == '--check':
            o.check = True
        elif a == '--probe':
            o.probe = value()
        elif a == '--pools':
            o.pools = value()
        elif a in ('-h', '--help'):
            print_help_and_exit(0)
        elif not o.plan_path:
            o.plan_path = a
        else:
            eprint(f'Unknown argument: {a}')
            print_help_and_exit(1)
        i += 1
    if not o.plan_path:
        print_help_and_exit(1)
    return o


opts = None
plan = None
base_url = ''
run_id = ''
limits = {}
plan_dir = ''

# One lock for everything shared between requests in flight: pools, the cookie jar, stats,
# the guard. Building a request is synchronous in the original single-threaded driver;
# holding this keeps the pool bookkeeping exactly as it was there. Network I/O runs outside it.
lock = threading.RLock()
log_lock = threading.RLock()

# --- Templates --------------------------------------------------------------------

pools = {}
POOL_MAX = 5000
capped = set()


def pool_list(names):
    if len(names) > 8:
        return f"{', '.join(names[:8])} +{len(names) - 8} more"
    return ', '.join(names) or 'none'


# used: pool values the request being built read, by pool; build() copies it onto what it
# returns. forced: child values remember the parent values they were created under
# ({ v, with: { pool: value } }), so /articles/{slug}/comments/{id} gets an id that belongs
# to that slug. undo: pool changes a take made while building, undone if the request cannot
# be built after all, so a merge that finds its second pool empty does not eat the first record.
B = SimpleNamespace(used=[], forced={}, undo=[], seq=0, sweeping=False)

WORDS = ['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot', 'golf', 'hotel',
         'india', 'juliet', 'kilo', 'lima', 'mike', 'november', 'oscar', 'papa']


def is_linked(x):
    return isinstance(x, dict) and 'with' in x and 'v' in x


def unwrap(x):
    return x['v'] if is_linked(x) else x


def links_of(x):
    w = x.get('with') if isinstance(x, dict) else None
    return w if isinstance(w, dict) and truthy(w) else None


def resolve_token(expr):
    kind, sep, rest = expr.partition(':')
    args = rest.split(':') if sep else []
    if kind == 'uuid':
        return str(uuid.uuid4())
    if kind == 'int':
        lo = number(args[0]) if len(args) > 0 else 0
        hi = number(args[1]) if len(args) > 1 else 1000
        return lo + math.floor(random.random() * (hi - lo + 1))
    if kind == 'word':
        return random.choice(WORDS)
    if kind == 'email':
        return f'{random.choice(WORDS)}.{math.floor(random.random() * 1e6)}@example.com'
    if kind == 'now':
        return now_iso()
    if kind == 'seq':
        B.seq += 1
        return B.seq
    if kind == 'run':
        return run_id
    if kind == 'env':
        v = os.environ.get(rest)
        if v is None:
            raise DriveError(f'env var {rest} is not set')
        return v
    if kind in ('pool', 'take'):
        name = rest
        p = pools.get(name)
        if name in B.forced:
            B.used.append((name, B.forced[name]))
            return B.forced[name]
        if not p:
            raise MissingPool(name)
        # The same pool twice in one request is the same record: /labels/{{pool:L}} with { "id": "{{pool:L}}" }.
        if kind == 'pool':
            v = unwrap(random.choice(p))
            B.used.append((name, v))
            B.forced[name] = v
            return v
        # take: remove the value, so a delete never leaves a dead id for later reads, and
        # forget the children created under it.
        raw = p.pop(random.randrange(len(p)))
        v = unwrap(raw)
        B.undo.append(lambda: p.append(raw))
        for k, lst in list(pools.items()):
            if k != name:
                pools[k] = [x for x in lst if not (links_of(x) and same(links_of(x).get(name, UNDEF), v))]
                B.undo.append(lambda k=k, lst=lst: pools.__setitem__(k, lst))
        B.used.append((name, v))
        return v
    if kind == 'unknown':
        raise Unresolved(rest)
    raise DriveError('Unknown template {{' + expr + '}}')


TEMPLATE = re.compile(r'\{\{(.+?)\}\}')
POOL_TOKEN = re.compile(r'\{\{(pool|take):(.+?)\}\}')


def render(value):
    if isinstance(value, str):
        # Non-greedy, so a pool name may hold single braces: {{pool:/owners/{ownerId}/pets}}
        whole = TEMPLATE.fullmatch(value)
        if whole and '}}' not in whole.group(1):
            return resolve_token(whole.group(1))
        return TEMPLATE.sub(lambda m: js_str(resolve_token(m.group(1))), value)
    if isinstance(value, list):
        return [render(x) for x in value]
    if isinstance(value, dict):
        return {k: render(v) for k, v in value.items()}
    return value


# Plan headers, each on its own: one from a pool not filled yet (a token from a setup
# login, before the login has run) is left off rather than failing the request.
def plan_headers():
    out = {}
    for k, v in (plan.get('headers') or {}).items():
        try:
            out[k] = render(v)
        except MissingPool:
            pass
    return out


def dig(obj, path):
    path = js_str(path)
    if path in ('', '.'):  # the whole body, e.g. a create that returns a bare id
        return obj
    # "**.slug": the first "slug" anywhere in the reply, for creates whose shape isn't known
    # in advance ({ "article": { "slug": ... } }, { "data": { "id": ... } }).
    if path.startswith('**.'):
        key = path[3:]
        queue = deque([obj])
        while queue:
            o = queue.popleft()
            if isinstance(o, dict):
                if key in o and not isinstance(o[key], (dict, list)):
                    return o[key]
                queue.extend(o.values())
            elif isinstance(o, list):
                queue.extend(o)
        return None
    o = obj
    for k in path.split('.'):
        if o is None or o is UNDEF:
            return None
        o = js_get(o, k)
    return None if o is UNDEF else o


def capture(req, data, built=None):
    # Parent values this request's path used: a child captured here remembers them.
    req_path = js_str(req.get('path', UNDEF))
    parents = {pool: v for pool, v in (built.used if built else []) if pool in req_path}
    cap = req.get('capture') or {}
    whole = not isinstance(data, (dict, list))
    # Values one reply fills into several pools (a tool's id and its slug) are the same record:
    # each remembers the others, so deleting it by id also drops its slug.
    got = {}
    for name, path in cap.items():
        v = data if whole else dig(data, path)
        if v is not None and not isinstance(v, (dict, list)):
            got[name] = v
    for name, path in cap.items():
        # A create that answers with a bare id or slug feeds every pool it names.
        v = data if whole else dig(data, path)
        for x in (v if isinstance(v, list) else [v]):
            if x is None:
                continue
            p = pools.get(name)
            if p is None:
                p = []
            links = {k: val for k, val in list(parents.items()) + list(got.items()) if k != name}
            p.append({'v': x, 'with': links} if links else x)
            if len(p) > POOL_MAX:  # bounded: old ids may have been deleted anyway
                p.pop(0)
                if name not in capped:
                    capped.add(name)
                    print(f'  note: pool {name} holds its newest {POOL_MAX} values; older ones are dropped')
            pools[name] = p


# --- HTTP -------------------------------------------------------------------------
#
# One keep-alive connection per thread and host, so rate phases reuse sockets the way
# Node's fetch does.

_local = threading.local()


class HttpResponse:
    def __init__(self, status, headers, data):
        self.status = status
        self.ok = 200 <= status < 300
        self.headers = headers
        text = data.decode('utf-8', 'replace')
        self.text = text[1:] if text.startswith('\ufeff') else text

    @property
    def location(self):
        return self.headers.get('location')


def _connection(scheme, host, port, timeout):
    if scheme == 'https':
        return http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl.create_default_context())
    return http.client.HTTPConnection(host, port, timeout=timeout)


def http_send(url, method, headers, body, timeout):
    parts = urlsplit(url)
    target = (parts.path or '/') + (f'?{parts.query}' if parts.query else '')
    key = (parts.scheme, parts.hostname, parts.port)
    conns = _local.__dict__.setdefault('conns', {})
    payload = body.encode('utf-8') if isinstance(body, str) else body
    hdrs = {'accept': '*/*'}
    hdrs.update((k, js_str(v)) for k, v in headers.items())
    for attempt in (1, 2):
        conn = conns.get(key)
        reused = conn is not None
        if conn is None:
            conn = conns[key] = _connection(parts.scheme, parts.hostname, parts.port, timeout)
        conn.timeout = timeout
        if conn.sock is not None:
            conn.sock.settimeout(timeout)
        try:
            conn.request(method, target, body=payload, headers=hdrs)
            res = conn.getresponse()
            data = res.read()
        except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
            conn.close()
            conns.pop(key, None)
            # A kept-alive socket the server already closed: one fresh try.
            if reused and attempt == 1:
                continue
            raise
        except BaseException:
            conn.close()
            conns.pop(key, None)
            raise
        if res.will_close:
            conn.close()
            conns.pop(key, None)
        return HttpResponse(res.status, res.msg, data)


def net_code(e):
    if isinstance(e, ssl.SSLError):
        return str(e)
    if isinstance(e, socket.gaierror):
        if e.errno == getattr(socket, 'EAI_AGAIN', None):
            return 'EAI_AGAIN'
        if e.errno in (getattr(socket, 'EAI_NONAME', None), getattr(socket, 'EAI_NODATA', None)):
            return 'ENOTFOUND'
        return str(e)
    if isinstance(e, http.client.RemoteDisconnected):
        return 'ECONNRESET'
    if isinstance(e, OSError) and e.errno in errno.errorcode:
        return errno.errorcode[e.errno]
    return str(e) or type(e).__name__


def is_timeout(e):
    return isinstance(e, (socket.timeout, TimeoutError))


PATH_SAFE = "/%:@!$&'()*+,;=~[]^|"
QUERY_SAFE = ''.join(chr(c) for c in range(0x21, 0x7f) if chr(c) not in '"#<>\'')


def make_url(raw, query):
    parts = urlsplit(raw)
    path = quote(parts.path, safe=PATH_SAFE) or '/'
    if query:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        for k, v in query.items():
            if isinstance(v, list):
                pairs.extend((k, js_str(x)) for x in v)
                continue
            i = next((n for n, (pk, _) in enumerate(pairs) if pk == k), -1)
            pairs = [p for p in pairs if p[0] != k]
            pairs.insert(i if i >= 0 else len(pairs), (k, js_str(v)))
        qs = urlencode(pairs, quote_via=quote_plus, safe='*')
    else:
        qs = quote(parts.query, safe=QUERY_SAFE)
    return f'{parts.scheme}://{parts.netloc}{path}' + (f'?{qs}' if qs else '')


# --- Cookies and CSRF (server-rendered apps) -----------------------------------------
#
# plan.cookies: true keeps one cookie jar for the run, so a session from a login form
# carries through. An entry with csrf: { from: "/page" } first GETs that page and sends
# its CSRF token with the form: the hidden input Flask-WTF, Django, Rails, Laravel and
# Spring Security render, or a <meta name="csrf-token">.

jar = {}


def store_cookies(res):
    for c in res.headers.get_all('set-cookie') or []:
        pair = c.split(';')[0]
        i = pair.find('=')
        if i > 0:
            jar[pair[:i].strip()] = pair[i + 1:].strip()


def cookie_header():
    return '; '.join(f'{k}={v}' for k, v in jar.items())


CSRF_NAMES = 'csrf_token|csrfmiddlewaretoken|authenticity_token|_token|_csrf|__RequestVerificationToken|csrf'


def csrf_token(entry):
    csrf = entry['csrf']
    with lock:
        frm = js_str(render(js_or(csrf.get('from'), entry.get('path'))))
        headers = {'cookie': cookie_header()} if plan.get('cookies') else {}
    headers['user-agent'] = f'local-pov/{run_id}'
    res = http_send(base_url + frm, 'GET', headers, None, 15)
    if plan.get('cookies'):
        with lock:
            store_cookies(res)
    html = res.text
    field = csrf.get('field')
    names = re.escape(field) if field else CSRF_NAMES
    m = re.search(rf'<input[^>]*name=["\']({names})["\'][^>]*value=["\']([^"\']+)["\']', html, re.I)
    if m:
        return {'name': m.group(1), 'value': m.group(2)}
    m = re.search(rf'<input[^>]*value=["\']([^"\']+)["\'][^>]*name=["\']({names})["\']', html, re.I)
    if m:
        return {'name': m.group(2), 'value': m.group(1)}
    m = re.search(r'<meta[^>]*name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
    return {'header': 'x-csrf-token', 'value': m.group(1)} if m else None


# --- One request ----------------------------------------------------------------------

def build(req):
    with lock:
        B.undo = []
        try:
            return build_request(req)
        except BaseException:
            for u in reversed(B.undo):
                u()
            raise


def build_request(req):
    B.used = []
    B.forced = {}
    raw_path = req.get('path')
    raw_path = '' if raw_path is None else raw_path
    # A path that reads a parent and a child pool: pick the child first, then use its parent.
    toks = [(m.group(1), m.group(2)) for m in POOL_TOKEN.finditer(js_str(raw_path))]
    names = [n for _, n in toks]
    for kind, name in reversed(toks):
        p = pools.get(name) or []
        linked = [x for x in p if links_of(x) and any(o != name and o in x['with'] for o in names)]
        if not linked:
            continue
        choice = random.choice(linked)
        if kind == 'take':
            del p[next(i for i, x in enumerate(p) if x is choice)]
        B.forced[name] = choice['v']
        for k, v in choice['with'].items():
            if k in names:
                B.forced[k] = v
        break
    path = js_str(render(raw_path))
    # Never put template text on the wire; it reaches the app as a junk id and reads as a real 4xx/500.
    if '{{' in path:
        raise DriveError(f'unresolved template in path of "{js_str(req.get("name", UNDEF))}": {path}')
    url = make_url(base_url + path, render(req.get('query') or {}))
    # Synthetic traffic says so, and carries the run id: the prospect can filter it out of
    # their own logs and analytics. A plan header of the same name overrides.
    headers = {'user-agent': f'local-pov/{run_id}', 'x-local-pov-run': run_id}
    if auth.value:
        headers[auth.header] = auth.value
    if plan.get('cookies') and jar:
        headers['cookie'] = cookie_header()
    headers.update(plan_headers())
    headers.update(render(req.get('headers') or {}))
    body = None
    if req.get('form') is not None:
        # An HTML form post: application/x-www-form-urlencoded.
        form = render(req['form'])
        body = urlencode([(k, js_str(v)) for k, v in form.items()], quote_via=quote_plus, safe='*')
        if headers.get('content-type') is None:
            headers['content-type'] = 'application/x-www-form-urlencoded'
    elif 'body' in req:
        if isinstance(req['body'], str) and req.get('rawBody'):
            body = js_str(render(req['body']))
        else:
            body = to_json(render(req['body']))
            if headers.get('content-type') is None:
                headers['content-type'] = 'application/json'
    method = js_str(js_or(req.get('method'), 'GET')).upper()
    return SimpleNamespace(url=url, method=method, headers=headers, body=body, used=list(B.used))


# A request that read a value from pool P and captures into P (an update that renames its
# record: a new slug) replaces the old value instead of adding a second one.
# After an update of /tools/{{pool:id}}: values tied to that record (its slug) take the
# reply's new value, so a rename does not leave a stale slug behind.
def refresh_linked(built, data):
    for pool, v in built.used:
        for k, lst in pools.items():
            if k == pool:
                continue
            now = dig(data, '**.' + reply_field(k[k.rfind(':') + 1:]))
            if now is None or isinstance(now, (dict, list)):
                continue
            for x in lst:
                w = links_of(x)
                if w and same(w.get(pool, UNDEF), v) and not same(x['v'], now):
                    x['v'] = now


def replace_renamed(req, built):
    cap = req.get('capture')
    for pool, old in built.used:
        if not isinstance(cap, dict) or pool not in cap:
            continue
        p = pools.get(pool) or []
        vals = [unwrap(x) for x in p]
        i = index_same(vals, old)
        last = len(vals) - 1 - index_same(vals[::-1], old) if i >= 0 else -1
        if i >= 0 and last == i and not same(vals[-1], old):
            del p[i]
            # Records created under the old value now live under the new one: a comment on an
            # article whose slug changed is reached through the new slug.
            now = vals[-1]
            for k, lst in pools.items():
                if k == pool:
                    continue
                for x in lst:
                    w = links_of(x)
                    if w and same(w.get(pool, UNDEF), old):
                        w[pool] = now


# --- Script entries ---------------------------------------------------------------
#
# An entry { script: "./flows.py#name" } calls def name(ctx) from that file.
# ctx.request() sends through the same base URL, headers and auth as every other entry;
# ctx.pool / ctx.take / ctx.put share the plan's pools. The function returns the status
# that stands for the whole flow (compared against `expect`, default 2xx) and raises to
# fail it. Hud sees each request inside the flow as its own endpoint call.

scripts = {}
modules = {}


def load_script(ref):
    with lock:
        if ref in scripts:
            return scripts[ref]
        file, _, name = ref.partition('#')
        name = name or 'main'
        path = os.path.normpath(os.path.join(plan_dir, file))
        mod = modules.get(path)
        if mod is None:
            mod_name = f'local_pov_flow_{len(modules)}'
            # SourceFileLoader accepts any extension; spec_from_file_location alone needs .py.
            loader = importlib.machinery.SourceFileLoader(mod_name, path)
            spec = importlib.util.spec_from_file_location(mod_name, path, loader=loader)
            mod = importlib.util.module_from_spec(spec)
            if os.path.dirname(path) not in sys.path:
                sys.path.insert(0, os.path.dirname(path))
            loader.exec_module(mod)
            modules[path] = mod
        fn = getattr(mod, name, None)
        if not callable(fn):
            raise DriveError(f'{ref}: no function {name}')
        scripts[ref] = fn
        return fn


class FlowResponse:
    def __init__(self, res):
        self.status = res.status
        self.ok = res.ok
        self.text = res.text
        try:
            self.json = load_json(res.text)
        except ValueError:
            self.json = None


class FlowContext:
    def __init__(self, req, stats=None):
        self._req = req
        self._stats = stats
        self.run = run_id
        self.base_url = base_url

    def env(self, name):
        with lock:
            return resolve_token(f'env:{name}')

    def pool(self, name):
        with lock:
            p = pools.get(name)
            return unwrap(random.choice(p)) if p else None

    def take(self, name):
        with lock:
            p = pools.get(name)
            return unwrap(p.pop(random.randrange(len(p)))) if p else None

    def put(self, name, value):
        with lock:
            capture({'capture': {name: ''}}, value)

    def request(self, method, path, query=None, body=None, headers=None):
        req = {'name': self._req.get('name'), 'method': method, 'path': path}
        if query is not None:
            req['query'] = query
        if body is not None:
            req['body'] = body
        if headers is not None:
            req['headers'] = headers
        built = build(req)
        if self._stats:
            self._stats.count_request(built.method, built.url)
        res = http_send(built.url, built.method, built.headers, built.body, timeout_of(self._req))
        return FlowResponse(res)


def timeout_of(req):
    return js_or(req.get('timeoutMs'), plan.get('timeoutMs'), 30000) / 1000


def ms_since(t0):
    return (time.perf_counter() - t0) * 1000


def send_script(req, stats):
    t0 = time.perf_counter()
    try:
        status = load_script(req['script'])(FlowContext(req, stats))
        stats.record(req, status if is_number(status) else 200, ms_since(t0))
    except MissingPool:
        stats.record(req, 'no-data', 0)
    except Exception as e:
        stats.record(req, 'timeout' if is_timeout(e) else 'script-error', ms_since(t0), str(e) or type(e).__name__)


def send(req, stats):
    if req.get('script'):
        return send_script(req, stats)
    if req.get('csrf'):
        try:
            tok = csrf_token(req)
            if tok and tok.get('name'):
                req = dict(req, form=dict(req.get('form') or {}, **{tok['name']: tok['value']}))
            elif tok and tok.get('header'):
                req = dict(req, headers=dict(req.get('headers') or {}, **{tok['header']: tok['value']}))
        except Exception:
            pass  # the post goes without a token and fails visibly
    built = None
    try:
        built = build(req)
    except Exception as e:
        # In a sweep, an empty pool is refilled from its create, as many records as the request reads.
        maker = creator_of(str(e)) if B.sweeping and isinstance(e, MissingPool) else None
        if maker is not None and maker is not req:
            refs = max(1, to_json(req).count(f':{e}' + '}}'))
            for _ in range(refs):
                send(maker, stats)
            try:
                built = build(req)
            except MissingPool:
                pass
        if built is None:
            if isinstance(e, MissingPool):
                stats.record(req, 'no-data', 0)
                return
            if isinstance(e, Unresolved):
                stats.record(req, 'unresolved', 0, f'field {e} has no value yet')
                return
            raise e
    t0 = time.perf_counter()
    try:
        # A followed redirect hides what happened: an expired session's 302 to a login page
        # would come back as that page's 200. Record the 3xx itself.
        res = http_send(built.url, built.method, built.headers, built.body, timeout_of(req))
        status = res.status
        with lock:
            if plan.get('cookies'):
                store_cookies(res)
            text = res.text
            if 300 <= status < 400:
                text = f'redirect to {js_str(res.location)}'
            if truthy(req.get('capture')) and res.ok:
                try:
                    parsed = load_json(text)
                except ValueError:
                    parsed = text.strip()  # plain-text id
                capture(req, parsed, built)
                replace_renamed(req, built)
            if res.ok and built.method in ('PUT', 'PATCH'):
                try:
                    refresh_linked(built, load_json(text))
                except ValueError:
                    pass  # not JSON
            stats.record(req, status, ms_since(t0), text)
    except Exception as e:
        stats.record(req, 'timeout' if is_timeout(e) else 'neterr', ms_since(t0), net_code(e))


# --- Stats ------------------------------------------------------------------------

ID_SEGMENT = re.compile(r'^(\d+|[0-9a-fA-F-]{32,36}|[0-9a-fA-F]{24})$')


class Stats:
    def __init__(self, phase):
        self.phase = phase
        self.by = {}
        # Requests sent from inside script flows, as "METHOD /path" with id-like segments as
        # {id}. A flow is one entry in `by`, however many requests it sends; these count the
        # requests themselves, so the report can compare Hud's per-route counts exactly.
        self.flow_requests = {}

    def count_request(self, method, url):
        path = urlsplit(url).path
        segs = ['{id}' if ID_SEGMENT.match(s) else s for s in path.split('/')]
        key = f"{method.upper()} {'/'.join(segs)}"
        with lock:
            self.flow_requests[key] = self.flow_requests.get(key, 0) + 1

    def record(self, req, status, ms, detail=None):
        with lock:
            name = req.get('name')
            s = self.by.get(name) or {'n': 0, 'codes': {}, 'lat': [], 'unexpected': 0}
            s['n'] += 1
            code = js_str(status)
            s['codes'][code] = s['codes'].get(code, 0) + 1
            if is_number(status):
                s['lat'].append(ms)
            # Default: 2xx. A redirect is an outcome to plan for (expect: [302]), not a success.
            expect = req.get('expect')
            if expect is not None:
                expected = isinstance(expect, list) and any(same(x, status) for x in expect)
            else:
                expected = is_number(status) and status < 300
            if self.phase != 'setup':
                guard.observe(status, expected)
            if not expected:
                s['unexpected'] += 1
                # Log the first few unexpected responses per endpoint per phase, not all of them.
                if s['unexpected'] <= 3:
                    log({'type': 'unexpected', 'phase': self.phase, 'endpoint': name, 'status': status,
                         'ms': js_round(ms), 'detail': ('' if detail is None else js_str(detail))[:300]})
            self.by[name] = s

    def sent(self):
        return js_order({js_str(n): x['n'] for n, x in self.by.items()})

    def print(self):
        def pct(a, p):
            return str(js_round(a[min(len(a) - 1, math.floor(p * len(a)))])) if a else '-'

        rows = []
        for name, s in self.by.items():
            lat = sorted(s['lat'])
            codes = ' '.join(f'{c}:{n}' for c, n in js_order(s['codes']).items())
            rows.append([js_str(name), s['n'], codes, pct(lat, 0.5), pct(lat, 0.9), pct(lat, 0.99),
                         f"!{s['unexpected']}" if s['unexpected'] else ''])
        head = ['endpoint', 'n', 'status', 'p50', 'p90', 'p99', 'unexpected']
        w = [max([len(h)] + [len(str(r[i])) for r in rows]) for i, h in enumerate(head)]

        def line(r):
            return '  '.join(str(c).ljust(w[i]) for i, c in enumerate(r))

        print(line(head))
        for r in rows:
            print(line(r))


def log(entry):
    if not opts.log:
        return
    rec = {'ts': now_iso()}
    rec.update((k, v) for k, v in entry.items() if v is not UNDEF)
    with log_lock:
        with open(opts.log, 'a', encoding='utf-8') as f:
            f.write(to_json(rec) + '\n')


# --- Auth ---------------------------------------------------------------------------
#
# plan.auth.cmd prints the full header value on stdout ("Bearer eyJ..."). It runs before
# setup, every `every`, and once more when 401s start (a token that expired mid-run). The
# value is never logged.
#
# The value is cached in auth.cache next to the plan (mode 600), and a new driver process
# reuses it instead of signing in again: --check, the sweep, probes and the run share one
# sign-in, so a login rate limit (5 a minute is common) can't fail a healthy service.
# A cached value older than `every` is not reused. When a readiness check gets a 401 on a
# cached value (the database was recreated, the user changed), the driver signs in once
# more and runs the checks again. "cache": false turns it off.

auth = SimpleNamespace(header='authorization', value=None, at=0, cached=False)


def auth_cache_path():
    c = (plan.get('auth') or {}).get('cache', True)
    if c is False:
        return None
    return os.path.join(plan_dir, c if isinstance(c, str) else 'auth.cache')


def read_auth_cache():
    path = auth_cache_path()
    if not path or not os.path.exists(path):
        return None
    every = (plan.get('auth') or {}).get('every')
    if every and time.time() - os.path.getmtime(path) >= parse_duration(every):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip() or None
    except OSError:
        return None


def write_auth_cache(value):
    path = auth_cache_path()
    if not path:
        return
    tmp = path + '.tmp'
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(value + '\n')
        os.replace(tmp, path)
    except OSError as e:
        print(f'  auth cache not written ({e.__class__.__name__}); signing in on every run')


# Every command the driver runs (auth.cmd) gets BASE_URL and
# LOCAL_POV_RUN, so one plan works whatever --base-url points at.
def cmd_env():
    env = dict(os.environ)
    env['BASE_URL'] = base_url
    env['LOCAL_POV_RUN'] = run_id
    return env


def _text(b):
    return b.decode('utf-8', 'replace') if isinstance(b, bytes) else (b or '')


def run_shell(cmd, timeout):
    """Run through the platform shell (/bin/sh, cmd.exe). Status is None when killed or timed out."""
    try:
        r = subprocess.run(cmd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout, env=cmd_env())
    except subprocess.TimeoutExpired as e:
        return None, _text(e.stdout), _text(e.stderr)
    return (r.returncode if r.returncode >= 0 else None), _text(r.stdout), _text(r.stderr)


def refresh_auth(reason):
    cmd = (plan.get('auth') or {}).get('cmd')
    if not cmd:
        return False
    if reason == 'start':
        v = read_auth_cache()
        if v:
            auth.value, auth.at, auth.cached = v, now_ms(), True
            log({'type': 'auth-refresh', 'reason': 'cache'})
            return True
    status, out, err = run_shell(cmd, 120)
    v = out.strip()
    if status != 0 or not v:
        err = err.strip()[:300]
        log({'type': 'auth-failed', 'reason': reason, 'status': status, 'err': err})
        extra = ('\n    ' + err.replace('\n', '\n    ')) if err else ''
        print(f'  auth refresh failed ({reason}): exit {js_str(status)}{extra}')
        return False
    auth.value, auth.at, auth.cached = v, now_ms(), False
    write_auth_cache(v)
    log({'type': 'auth-refresh', 'reason': reason})
    return True


# --- Guard ---------------------------------------------------------------------------
#
# Stops the run instead of hammering a service that is refusing it. Over a sliding 60 s
# window, if a status that was NOT expected makes up more than its share of responses, the
# active phase stops and the driver exits 2. Expected statuses never count,
# so an entry planned to return 403 does not trip the 403 rule.

ABORT_DEFAULTS = {'401': 0.2, '403': 0.5, '407': 0.05, '429': 0.05, 'neterr': 0.2, 'timeout': 0.3}


class Guard:
    def __init__(self):
        rules = dict(ABORT_DEFAULTS)
        rules.update((str(k), v) for k, v in (limits.get('abortOn') or {}).items())
        self.rules = js_order(rules)
        self.window = deque()
        self.bad = {}
        self.reason = None

    def observe(self, status, expected):
        now = now_ms()
        code = js_str(status)
        self.window.append((now, code, expected))
        if not expected:
            self.bad[code] = self.bad.get(code, 0) + 1
        while self.window and now - self.window[0][0] > 60000:
            _, old, ok = self.window.popleft()
            if not ok:
                self.bad[old] -= 1
        if self.reason or len(self.window) < 20:
            return
        for rule, share in self.rules.items():
            n = self.bad.get(rule, 0)
            if n / len(self.window) <= share:
                continue
            # A burst of 401s is first treated as an expired token, once per minute.
            if (rule == '401' and (plan.get('auth') or {}).get('cmd') and now - auth.at > 60000
                    and refresh_auth('401 burst')):
                self.window = deque(x for x in self.window if x[1] != '401')
                self.bad.pop('401', None)
                return
            self.stop(f'{n} of the last {len(self.window)} responses were unexpected {rule}')
            return

    def stop(self, reason):
        if self.reason:
            return
        self.reason = reason
        print(f'\nABORT: {reason}')
        log({'type': 'abort', 'reason': reason})

    def check(self):
        if not self.reason and opts.stop_file and os.path.exists(opts.stop_file):
            with lock:
                self.stop(f'stop file {opts.stop_file} exists')
        return self.reason


guard = None

# --- Phases --------------------------------------------------------------------------


def parse_duration(d):
    if is_number(d):
        return d
    m = re.fullmatch(r'(\d+(?:\.\d+)?)(s|m|h)?', js_str(d), re.A)
    if not m:
        raise DriveError(f'Bad duration: {js_str(d)}')
    return _num(m.group(1)) * {'s': 1, 'm': 60, 'h': 3600}[m.group(2) or 's']


def matches(e, t):
    return e.get('name') == t or t in (e.get('tags') or [])


def included(phase, e):
    inc, exc = phase.get('include'), phase.get('exclude')
    return (not inc or any(matches(e, t) for t in inc)) and (not exc or not any(matches(e, t) for t in exc))


def select(phase):
    eps = [e for e in plan['endpoints'] if included(phase, e)]
    if not eps:
        raise DriveError(f"Phase {js_str(phase.get('name'))} selects no endpoints")
    # Per-phase weight overrides by endpoint name or tag; otherwise the endpoint's own weight.
    # An entry-name weight wins outright; otherwise every matching tag's weight multiplies
    # into the entry's own, so { read: 3, "server-error": 0 } zeroes a read that 500s.
    w = phase.get('weights') or {}

    def weight_of(e):
        if e.get('name') in w:
            return w[e['name']]
        weight = nn(e.get('weight'), 1)
        for t in e.get('tags') or []:
            if t in w:
                weight *= w[t]
        return weight

    weighted = [(e, weight_of(e)) for e in eps]
    weighted = [(e, x) for e, x in weighted if x > 0]
    total = sum(x for _, x in weighted)

    def pick():
        r = random.random() * total
        for e, x in weighted:
            r -= x
            if r <= 0:
                return e
        return weighted[-1][0]

    return pick


def run_sweep(phase, stats):
    # Every selected endpoint, in plan order, `repeat` times. Order matters: creates
    # come before the reads that consume their captured ids.
    eps = [e for e in plan['endpoints'] if included(phase, e)]
    B.sweeping = True
    try:
        for _ in range(math.ceil(nn(phase.get('repeat'), 3))):
            for e in eps:
                if guard.check():
                    return
                send(e, stats)
    finally:
        B.sweeping = False


def run_rate(phase, stats):
    duration = parse_duration(phase.get('duration', UNDEF))
    rate = nn(phase.get('rate'), 0)

    def due_by(t):
        return rate * t

    max_in_flight = nn(phase.get('concurrency'), nn(plan.get('concurrency'), 100))
    nxt = select(phase)
    flight = threading.Lock()
    state = {'in_flight': 0}
    errors = []

    def done(fut):
        with flight:
            state['in_flight'] -= 1
        if fut.exception() is not None:
            errors.append(fut.exception())

    issued = shed = skipped = 0
    stalled = 0.0
    start = last_tick = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=max(1, int(max_in_flight)))
    try:
        while True:
            now = time.monotonic()
            gap = now - last_tick
            # A tick far later than 20 ms means the process was suspended (laptop sleep,
            # SIGSTOP) or starved. Traffic due in that gap was never sent.
            if gap > 5:
                stalled += gap
                log({'type': 'clock-jump', 'phase': phase.get('name'), 'seconds': js_round(gap)})
            jumped = gap > 5
            last_tick = now
            t = now - start
            if t >= duration or guard.check() or errors:
                break
            # Requests due by time t at the phase's rate.
            due = math.floor(due_by(t))
            # After a suspension, drop what was missed instead of firing it all at once:
            # a catch-up burst would show in Hud as a spike nobody planned.
            if jumped:
                skipped += due - issued
                issued = due
            while issued < due:
                issued += 1
                with flight:
                    if state['in_flight'] >= max_in_flight:
                        shed += 1
                        continue
                    state['in_flight'] += 1
                pool.submit(send, nxt(), stats).add_done_callback(done)
            time.sleep(0.02)
    finally:
        pool.shutdown(wait=True)
    if errors:
        raise errors[0]
    if shed:
        print(f'  shed {shed} requests: {js_str(max_in_flight)} in flight. The service is slower than the rate asks for.')
    planned = js_round(due_by(duration))
    sent = issued - shed - skipped
    if stalled > 0 or sent < planned * 0.8:
        msg = f'INCOMPLETE PHASE: sent {sent} of {planned} planned requests'
        if stalled > 0:
            msg += f'; the process was suspended for {js_round(stalled)}s (system sleep?)'
        print(f'  {msg}')
        log({'type': 'incomplete-phase', 'phase': phase.get('name'), 'sent': sent, 'planned': planned,
             'stalledSeconds': js_round(stalled)})


def on_signal(signum, _frame):
    name = 'SIGINT' if signum == signal.SIGINT else 'SIGTERM'
    try:
        print(f'\n{name}: stopping')
        sys.stdout.flush()
    finally:
        # os._exit: requests still in flight on worker threads must not hold the exit.
        os._exit(130)


# --- Probe: learn a drafted plan from the service ------------------------------------
#
# A plan written from code holds what the code shows. The service knows the rest, and
# its validation errors say it: which fields are missing, which values an enum allows,
# which must be unique, which type a field has. The probe sends each entry, reads the
# error, fixes the request and tries again, up to six times, then writes the plan back.

def value_for(name, hint=''):
    n, h = js_str(name).lower(), js_str(hint).lower()
    if (re.search(r'bool', h)
            or re.search(r'^(is|has|can|enable|allow)[_a-z]|(enabled|active|done|completed|public|checked)$', n)):
        return False
    if (re.search(r'number|integer|numeric|\bint\b|float|decimal', h, re.A)
            or re.search(r'(count|quantity|qty|amount|price|total|limit|size|rating|number)$', n)):
        return 1
    if re.search(r'array|list', h) or re.search(r'(list|tags|ids|items)$', n):
        return []
    if 'email' in n + h:
        return '{{email}}'
    if 'password' in n:
        return f'Pov-{uuid.uuid4()}'
    if 'date' in n + h:
        return '2026-12-01'
    if re.search(r'(url|uri|link|image)$', n):
        return 'https://example.test/pov'
    # Templates, not literals: the plan sends them many times, and names are often unique.
    if re.search(r'(title|name|slug|username|subject)$', n):
        return f'pov {n} ' + '{{uuid}}'
    return f'pov {n}'


MSG_MISSING = re.compile(r'required|blank|empty|missing|must be provided|cannot be null|must not be (null|blank|empty)'
                         r'|undefined|should not be empty|is not allowed to be empty', re.I)
MSG_UNIQUE = re.compile(r'unique|already (exists|taken|been taken|in use|registered)|duplicate|conflict', re.I)
MSG_ENUM = re.compile(r'one of|is not included|not a valid (choice|enum)|should be [\'"]|expected [\'"]'
                      r'|allowed values|permitted values', re.I)


def allowed_values(msg, ctx):
    if isinstance(ctx, dict) and isinstance(ctx.get('options'), list):
        return ctx['options']
    if isinstance(ctx, dict) and isinstance(ctx.get('expected'), str):
        return re.findall(r"'([^']+)'", ctx['expected'])
    m = re.search(r'(?:one of|values?|choices?)\s*:?\s*\[?([^\]]+)\]?', js_str(msg), re.I)
    src = m.group(1) if m else msg
    quoted = re.findall(r'[\'"`]([^\'"`]+)[\'"`]', js_str(src))
    if quoted:
        return quoted
    if not m:
        return []
    vals = (re.sub(r'\.$', '', x.strip()) for x in re.split(r',|\bor\b|\|', m.group(1), flags=re.A))
    return [x for x in vals if re.fullmatch(r'[\w-]+', x, re.A)]


ERROR_KEYS = ['detail', 'errors', 'issues', 'details', 'fieldErrors', 'violations', 'message', 'error', 'validation']


# Field problems from the common validation error shapes: FastAPI/Pydantic, Spring,
# express-validator, class-validator/NestJS, Zod, Joi, Rails, and plain messages.
def error_fields(data, text):
    out = []

    def add(path, msg, ctx=None, type_=None):
        if isinstance(path, list):
            parts = [js_str(x) for x in path]
        else:
            parts = re.sub(r'\[(\d+)\]', r'.\1', js_str(path), flags=re.A).split('.')
        parts = [x for x in parts if x and x not in ('body', 'query', 'path', 'json', 'form')]
        if parts:
            out.append({'path': parts, 'msg': js_str(msg) if truthy(msg) else '', 'ctx': ctx,
                        'type': js_str(type_) if truthy(type_) else '',
                        'where': 'query' if isinstance(path, list) and path and path[0] == 'query' else 'body'})

    def walk_list(items):
        for e in items:
            if isinstance(e, str):
                m = (re.match(r'["\']?([\w.\[\]]+)["\']?\s+(should|must|is|has|can\'t|cannot|may)\b', e, re.I | re.A)
                     or re.search(r'(?:field|parameter|property)\s+["\']?([\w.]+)', e, re.I | re.A))
                if m:
                    add(m.group(1), e)
            elif isinstance(e, dict):
                context = e.get('context')
                path = js_or(e.get('loc'), e.get('path'), e.get('field'), e.get('param'), e.get('property'),
                             e.get('propertyPath'), e.get('name'), e.get('key'),
                             context.get('key') if isinstance(context, dict) else None)
                cons = e.get('constraints')
                if truthy(cons) and isinstance(cons, dict):
                    cons = js_join(cons.values(), '; ')
                msg = js_or(e.get('msg'), e.get('message'), e.get('defaultMessage'), cons, e.get('code'))
                if path is not None:
                    add(path, msg, js_or(e.get('ctx'), e), js_or(e.get('type'), e.get('code')))
                children = e.get('children')
                if isinstance(children, list) and children:
                    parent = js_str(e.get('property', UNDEF))
                    walk_list([dict(c if isinstance(c, dict) else {},
                                    property=f"{parent}.{js_str(c.get('property', UNDEF) if isinstance(c, dict) else UNDEF)}")
                               for c in children])

    if isinstance(data, (dict, list)):
        for key in ERROR_KEYS:
            v = data.get(key) if isinstance(data, dict) else None
            if v is None:
                err = data.get('error') if isinstance(data, dict) else None
                v = err.get(key) if isinstance(err, dict) else None
            if isinstance(v, list):
                walk_list(v)
            elif isinstance(v, dict):
                for f, m in v.items():
                    add(f, js_join(m, '; ') if isinstance(m, list) else m)
            elif isinstance(v, str):
                walk_list([v])
    elif truthy(text):
        walk_list([text])
    return out


def set_path(obj, path, value):
    o = obj
    for k in path[:-1]:
        cur = js_get(o, k)
        if not truthy(cur) or not isinstance(cur, (dict, list)):
            js_set(o, k, {})
            cur = js_get(o, k)
        o = cur
    js_set(o, path[-1], value)


def get_path(obj, path):
    o = obj
    for k in path:
        if o is None or o is UNDEF:
            return UNDEF
        o = js_get(o, k)
    return o


def strip_unknown(v):
    if isinstance(v, str) and v.startswith('{{unknown:'):
        return UNDEF
    if isinstance(v, list):
        return [None if y is UNDEF else y for y in map(strip_unknown, v)]
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            y = strip_unknown(x)
            if y is not UNDEF:
                out[k] = y
        return out
    return v


# The field a create's reply carries for a path parameter: {item_id} -> id, {tool_slug} -> slug.
def reply_field(param):
    if re.search(r'(^|_)id$|[a-z]Id$', param):
        return 'id'
    if re.search(r'(^|_)slug$', param):
        return 'slug'
    return param


# The pool a field naming another record reads: label_id, from_food -> /labels:id, /foods:item_id.
def pool_for_field(name):
    stem = re.sub(r'_?(ids?|Ids?|uuid)$', '', js_str(name), count=1)
    stem = re.sub(r'^(from|to|parent|source|target)_?', '', stem, count=1, flags=re.I).lower()
    if not stem:
        return None
    names = list(pools) + [k for x in plan['endpoints'] for k in (x.get('capture') or {})]
    for pool in dict.fromkeys(names):
        i = pool.rfind(':')
        last = re.sub(r'/$', '', pool[:i]).split('/')[-1].lower().replace('-', '_')
        is_id = reply_field(pool[i + 1:]) == 'id'
        if is_id and last in (stem, f'{stem}s', f'{stem}es', f'{stem[:-1]}ies' if stem.endswith('y') else ''):
            return pool
    return None


# The create entry that fills a pool: run it again when a probe finds the pool empty.
def creator_of(pool):
    return next((x for x in plan['endpoints']
                 if isinstance(x.get('capture'), dict) and pool in x['capture'] and not x.get('script')), None)


def encode_uri_component(s):
    return quote(s, safe="-_.!~*'()")


def probe_entry(entry, depth=0):
    e = copy.deepcopy(entry)
    field = 'form' if 'form' in e else 'body'
    if field in e:
        stripped = strip_unknown(e[field])
        if stripped is UNDEF:
            del e[field]
        else:
            e[field] = stripped
    learned = []
    status, text, retried404 = None, '', False

    def result(st, note=None, **extra):
        r = {'e': e, 'status': st, 'learned': learned}
        if note is not None:
            r['note'] = note
        r.update(extra)
        return r

    for attempt in range(1, 7):
        built = None
        try:
            built = build(e)
        except Exception as err:
            maker = creator_of(str(err)) if isinstance(err, MissingPool) and depth == 0 else None
            if not maker:
                return result('no-data' if isinstance(err, MissingPool) else 'unbuildable', str(err))
            # refill the pool (a merge needs two records), then retry this entry
            last = err
            n = 1
            while n <= 3 and not built:
                m = creator_of(str(last)) or maker
                # as many records as the request reads from that pool: a merge takes one and reads another
                refs = to_json(e).count(f':{last}' + '}}')
                for _ in range(max(1, refs)):
                    probe_entry(m, depth + 1)
                try:
                    built = build(e)
                    learned.append(f're-ran "{js_str(m.get("name"))}" to refill {last}')
                except MissingPool as err2:
                    last = err2
                n += 1
            if not built:
                return result('no-data', f'pool {last} is still empty after re-running its create')
        # Which pool value went into the path, to spot an update that renames the record.
        used = [m.group(1) for m in re.finditer(r'\{\{(?:pool|take):(.+?)\}\}', js_str(e.get('path', UNDEF)))]
        try:
            res = http_send(built.url, built.method, built.headers, built.body, js_or(e.get('timeoutMs'), 15000) / 1000)
            status, text = res.status, res.text
        except Exception as err:
            return result('neterr', net_code(err))
        try:
            data = load_json(text)
        except ValueError:
            data = UNDEF
        method = js_str(e.get('method', UNDEF))
        if status < 300:
            if truthy(e.get('capture')):
                with lock:
                    capture(e, text.strip() if data is UNDEF or data is None else data, built)
            # An update that answers with a different id/slug than it was sent renamed the record:
            # capture the new value, or later reads of the old one will 404.
            if re.fullmatch(r'PUT|PATCH|POST', method) and truthy(data) and used:
                pool = used[-1]
                key = reply_field(pool.split(':')[-1])
                now = dig(data, f'**.{key}')
                sent_path = urlsplit(built.url).path
                if now is not None and encode_uri_component(js_str(now)) not in sent_path.split('/'):
                    e['capture'] = dict(e.get('capture') or {}, **{pool: f'**.{key}'})
                    with lock:
                        capture({'capture': {pool: f'**.{key}'}, 'path': e.get('path')}, data, built)
                        replace_renamed(e, built)
                    learned.append(f'this request changes {key} (renames the record): now captures the new {key}')
            return result(status, attempts=attempt)
        if status in (401, 403):
            return result(status, 'not allowed: check the test user and its role')
        # A 404 on a pooled id: the record is gone (merged, deleted, renamed elsewhere). Drop the
        # stale value, make a fresh one, and try once more.
        if status == 404 and depth == 0 and built.used and not retried404:
            for pool, v in built.used:
                with lock:
                    pools[pool] = [x for x in (pools.get(pool) or []) if not same(unwrap(x), v)]
                maker = creator_of(pool)
                if maker and not pools.get(pool):
                    probe_entry(maker, depth + 1)
            retried404 = True
            learned.append('a pooled id was stale (404): dropped it and retried with a fresh one')
            continue
        if status == 404:
            if re.search(r'\{\{(pool|take):', js_str(entry.get('path', UNDEF))):
                return result(status, 'not found: the id is from a pool this user cannot see, or nothing created one yet')
            return result(status, 'not found: wrong path, or the route needs data')
        if status >= 500:
            return result(status, f'server error: {text[:140]} (a real failure path? keep it as a server-error entry)')
        # 400, 409, 422: learn what we can from the reply
        problems = error_fields(None if data is UNDEF else data, text)
        # An update of /x/{{pool:P}}: the record's own current values are the safest fill for
        # fields it requires (its group, its owner), so read it once.
        record = [UNDEF]

        def from_record(nm):
            if not re.fullmatch(r'PUT|PATCH', method) or not used:
                return UNDEF
            if record[0] is UNDEF:
                try:
                    r = http_send(built.url, 'GET', built.headers, None, 15)
                    record[0] = load_json(r.text) if r.ok else None
                except Exception:
                    record[0] = None
            rec = record[0]
            if not isinstance(rec, (dict, list)):
                return UNDEF
            variants = [nm, re.sub(r'_([a-z])', lambda m: m.group(1).upper(), nm),
                        re.sub(r'[A-Z]', lambda m: '_' + m.group(0).lower(), nm)]
            for v in variants:
                x = js_get(rec, v)
                if x is not UNDEF and x is not None and not isinstance(x, (dict, list)):
                    return x
            return UNDEF

        changed = False
        target = e.get(field)
        if target is None:
            target = e[field] = {}
        keys = list(target) if isinstance(target, dict) else []
        wrapper = keys[0] if len(keys) == 1 and isinstance(target[keys[0]], dict) else None
        for pr in problems:
            path = pr['path']
            if pr['where'] == 'query':
                if e.get('query') is None:
                    e['query'] = {}
                store = e['query']
            else:
                store = target
            if pr['where'] != 'query' and wrapper and path[0] != wrapper and get_path(target, path) is UNDEF:
                path = [wrapper] + path
            name = path[-1]
            dotted = '.'.join(path)
            cur = get_path(store, path)
            msg_type = pr['msg'] + pr['type']
            if MSG_ENUM.search(pr['msg']) or re.search(r'enum|literal', pr['type']):
                vals = allowed_values(pr['msg'], pr['ctx'])
                if vals and not same(cur, vals[0]):
                    set_path(store, path, vals[0])
                    learned.append(f'{dotted} = {js_str(vals[0])} (allowed: {js_join(vals[:5], ", ")})')
                    changed = True
            elif MSG_UNIQUE.search(pr['msg']) or status == 409:
                if isinstance(cur, str) and '{{uuid}}' not in cur:
                    set_path(store, path, cur + ' {{uuid}}')
                    learned.append(f'{dotted} must be unique: added ' + '{{uuid}}')
                    changed = True
            elif cur is UNDEF or MSG_MISSING.search(pr['msg']) or re.search(r'missing', pr['type']):
                if cur is UNDEF or cur is None or cur == '':
                    generated = re.search(r'(title|name|slug|subject|email)$', name, re.I)  # must stay unique per request
                    had = UNDEF if generated else from_record(name)
                    v = had if had is not UNDEF else value_for(name, pr['msg'])
                    set_path(store, path, v)
                    changed = True
                    learned.append(f'{dotted} required: {to_json(v)}' + (" (the record's own value)" if had is not UNDEF else ''))
            elif re.search(r'uuid', msg_type, re.I) and not (isinstance(cur, str) and cur.startswith('{{')):
                # Another record's id: take it from that record's pool (from_food -> the foods pool).
                # A body id on /x/{{pool:P}} is that record's id; a from_ id is consumed (merge, move).
                own = used[-1] if name == 'id' and used else None
                pool = own or pool_for_field(name)
                kind = 'take' if not own and re.match(r'(from|source)_?', name, re.I) else 'pool'
                had = UNDEF if pool else from_record(name)
                if pool:
                    set_path(store, path, '{{' + f'{kind}:{pool}' + '}}')
                    learned.append(f'{dotted} is an id from {pool}')
                elif had is not UNDEF:
                    set_path(store, path, had)
                    learned.append(f"{dotted} is the record's own value")
                else:
                    set_path(store, path, '{{uuid}}')
                    learned.append(f'{dotted} is a uuid (no pool holds these: check it is optional)')
                changed = True
            elif re.search(r'number|integer|int_|float', msg_type, re.I) and not is_number(cur):
                set_path(store, path, 1)
                learned.append(f'{dotted} is a number')
                changed = True
            elif re.search(r'bool', msg_type, re.I) and not isinstance(cur, bool):
                set_path(store, path, False)
                learned.append(f'{dotted} is a boolean')
                changed = True
            elif re.search(r'array|list', msg_type, re.I) and not isinstance(cur, list):
                set_path(store, path, [])
                learned.append(f'{dotted} is a list')
                changed = True
            elif re.search(r'email', pr['msg'], re.I):
                set_path(store, path, '{{email}}')
                learned.append(f'{dotted} is an email')
                changed = True
        if not changed:
            return result(status, f'could not learn from: {text[:160]}')
    return result(status, 'still failing after 6 attempts')


def probe(out):
    results = []
    for entry in plan['endpoints']:
        if entry.get('script'):
            results.append({'name': entry.get('name'), 'skipped': 'script entry'})
            continue
        r = probe_entry(entry)
        ok = is_number(r['status']) and r['status'] < 300
        info = {'status': r['status'], 'ok': ok, 'learned': r['learned']}
        if r.get('note'):
            info['note'] = r['note']
        nxt = dict(r['e'])
        nxt['_probe'] = info
        if ok and 'expect' not in nxt and r['status'] not in (200, 201, 204):
            nxt['expect'] = [r['status']]
        entry.update(nxt)
        results.append({'name': entry.get('name'), 'status': r['status'], 'ok': ok, 'learned': r['learned'],
                        'note': r.get('note')})
        log({'type': 'probe', 'endpoint': entry.get('name'), 'status': r['status'], 'learned': r['learned'],
             'note': r.get('note', UNDEF)})
    with open(out, 'w', encoding='utf-8') as f:
        f.write(to_json(plan, indent=1))
    good = len([r for r in results if r.get('ok')])
    print(f"\n== probe: {good} of {len([r for r in results if not r.get('skipped')])} entries now succeed -> {out}")
    for r in results:
        if r.get('skipped'):
            continue
        print(f"  {'ok  ' if r['ok'] else 'TODO'} {js_str(r['status']).ljust(5)} {js_str(r['name'])}")
        for line in r['learned']:
            print(f'         learned: {line}')
        if r['note'] and not r['ok']:
            print(f"         {r['note']}")


# --- Readiness gate ------------------------------------------------------------------
#
# plan.preflight: requests that must come back as expected before any load is sent. They
# run after auth and before setup, on every invocation, and the driver exits 3 if any
# fails, with what the failure usually means. Without a preflight list, the gate is one
# GET of the base URL that must get an HTTP answer below 500.

def diagnose(status, detail):
    d = js_str(nn(detail, ''))
    if status == 'neterr':
        if re.search(r'ECONNREFUSED', d):
            return 'nothing is listening there: the service is not running, or it is on another port'
        if re.search(r'ENOTFOUND|EAI_AGAIN', d):
            return 'the host name does not resolve: a typo, or it needs a VPN or an internal DNS'
        if re.search(r'CERT|SSL|TLS|self.signed|UNABLE_TO_VERIFY', d, re.I):
            return 'TLS failed: an internal or self-signed certificate (set SSL_CERT_FILE to its CA file)'
        if re.search(r'ECONNRESET|EPIPE|socket hang up', d, re.I):
            return 'the connection was dropped: a proxy, a load balancer, or plain HTTP sent to an HTTPS port'
        return f'network error: {d}'
    if status == 'timeout':
        return 'no answer in time: the service is starting, overloaded, or a firewall drops the traffic'
    if not is_number(status):
        return f'unexpected status {status}'
    if 300 <= status < 400:
        return f'redirected ({d}): usually a login or SSO page, or http vs https'
    if status == 401:
        return 'authentication was rejected: the token is missing, expired, or for another environment'
    if status == 403:
        return 'authenticated but not allowed: the test user lacks a role, or an IP allowlist or WAF blocks this machine'
    if status == 404:
        return 'not found: wrong base URL or path prefix, or this build does not have the route'
    if status == 429:
        return 'rate limited before the run started: ask for the limit or an exemption for the test user'
    if status >= 500:
        return 'the service answered but failed: a dependency (database, cache, a secret) is missing; read its log'
    return f'unexpected status {status}'


last_preflight = []


def preflight():
    checks = plan.get('preflight') or [{'name': 'service answers', 'method': 'GET', 'path': '/', 'expect': 'any-http'}]
    rows = []
    for c in checks:
        t0 = time.perf_counter()
        try:
            b = build(c)
            res = http_send(b.url, b.method, b.headers, b.body, js_or(c.get('timeoutMs'), 15000) / 1000)
            status = res.status
            detail = res.location if 300 <= status < 400 else res.text[:200]
        except Exception as e:
            status = 'timeout' if is_timeout(e) else 'neterr'
            detail = net_code(e)
        ms = js_round(ms_since(t0))
        expect = c.get('expect')
        if expect == 'any-http':
            ok = is_number(status) and status < 500
        elif expect:
            ok = isinstance(expect, list) and any(same(x, status) for x in expect)
        else:
            ok = is_number(status) and 200 <= status < 300
        rows.append({'name': c.get('name'), 'status': status, 'ms': ms, 'ok': ok, 'why': '' if ok else diagnose(status, detail)})
    last_preflight[:] = rows
    failed = [r for r in rows if not r['ok']]
    print(f'\n== readiness ({len(rows) - len(failed)}/{len(rows)} passed)')
    for r in rows:
        why = f"  -> {r['why']}" if r['why'] else ''
        print(f"  {'ok  ' if r['ok'] else 'FAIL'} {js_str(r['name'])}: {js_str(r['status'])} {r['ms']}ms{why}")
    log({'type': 'preflight', 'target': base_url, 'passed': not failed, 'checks': rows})
    return not failed


# --- Main ------------------------------------------------------------------------------

def save_pools():
    with open(opts.pools, 'w', encoding='utf-8') as f:
        f.write(to_json(pools, indent=1))
    print(f'pools saved to {opts.pools}')


def every_loop(seconds, fn):
    def loop():
        while True:
            time.sleep(seconds)
            fn()
    threading.Thread(target=loop, daemon=True).start()


def run():
    if opts.dry_run:
        for e in (plan.get('setup') or []) + plan['endpoints']:
            # A script entry has no path to build, and loading it would run its imports.
            if e.get('script'):
                print(f"{js_str(e.get('name'))}: script {e['script']} (not run in dry-run)")
                continue
            try:
                b = build(e)
                print(f"{js_str(e.get('name'))}: {b.method} {b.url}" + (f' {b.body[:200]}' if b.body else ''))
            except Exception as err:
                why = f'pool {err} empty until a capture fills it' if isinstance(err, MissingPool) else str(err)
                print(f"{js_str(e.get('name'))}: (unresolved: {why})")
        return

    print(f'target {base_url}  run {run_id}')
    if opts.stop_file:
        print(f'kill switch: touch {opts.stop_file}')
    log({'type': 'run-start', 'runId': run_id, 'target': base_url, 'phases': [p.get('name') for p in plan['phases']]})
    plan_auth = plan.get('auth') or {}
    if plan_auth.get('cmd') and not refresh_auth('start'):
        # The usual cause is a service that isn't up, so say whether anything answers at all.
        saved = plan.get('preflight')
        plan['preflight'] = [{'name': 'service answers (before sign-in)', 'method': 'GET', 'path': '/', 'expect': 'any-http'}]
        preflight()
        plan['preflight'] = saved
        eprint('\nNOT READY: auth.cmd printed no token (its error is above), so no load was sent.')
        sys.exit(3)
    if plan_auth.get('every'):
        every_loop(parse_duration(plan_auth['every']), lambda: refresh_auth('interval'))
    ready = preflight()
    if not ready and auth.cached and any(r.get('status') == 401 for r in last_preflight):
        print('  the cached sign-in was rejected: signing in again')
        if refresh_auth('cache rejected'):
            ready = preflight()
    if opts.check:
        sys.exit(0 if ready else 3)
    if not ready:
        eprint('\nNOT READY: no load sent. Fix the failures above (Phase X2 in the skill), then run --check again.')
        sys.exit(3)
    if opts.pools and os.path.exists(opts.pools):
        # Reuse the records an earlier setup made instead of creating another set.
        with open(opts.pools, encoding='utf-8') as f:
            pools.update(load_json(f.read()))
        print(f'pools loaded from {opts.pools}: {pool_list(list(pools))}')
        log({'type': 'pools-loaded', 'file': opts.pools, 'pools': list(pools)})
        # A setup step added since the file was written (a new seed) runs once; the rest are skipped.
        fresh = [e for e in (plan.get('setup') or []) if any(k not in pools for k in (e.get('capture') or {}))]
        if fresh and not opts.skip_setup:
            s = Stats('setup')
            for e in fresh:
                send(e, s)
            print(f'\n== setup (only steps whose pools are missing from {opts.pools})')
            s.print()
            save_pools()
        else:
            print('setup skipped: every setup pool is in the file')
    elif opts.skip_setup:
        print('setup skipped (--skip-setup): pools start empty')
    elif plan.get('setup'):
        s = Stats('setup')
        for e in plan['setup']:
            send(e, s)
        print('\n== setup')
        s.print()
        if opts.pools:
            save_pools()
    if opts.probe:
        probe(opts.probe)
        return

    phases = [p for p in plan['phases'] if not opts.phases or p.get('name') in opts.phases]
    timed = sum(parse_duration(p['duration']) for p in phases if p.get('duration') is not None)
    ends = now_iso(datetime.now(timezone.utc) + timedelta(seconds=timed))[11:16]
    print(f'timed phases: {js_round(timed / 60)} min, plus sweeps; ends about {ends} UTC')
    for phase in phases:
        name = phase.get('name')
        stats = Stats(name)
        mode = phase.get('mode') or ('rate' if truthy(phase.get('rate')) else 'sweep')
        if mode == 'sweep':
            desc = f"sweep x{js_str(nn(phase.get('repeat'), 3))}"
        else:
            secs = parse_duration(phase.get('duration', UNDEF))
            desc = f"{js_str(phase.get('rate', UNDEF))} rps for {js_str(secs)}s"
        print(f'\n== {name} ({desc}) started {now_iso()}')
        log({'type': 'phase-start', 'phase': name, 'mode': mode, 'rate': phase.get('rate', UNDEF),
             'note': phase.get('note', UNDEF)})
        if mode == 'sweep':
            run_sweep(phase, stats)
        else:
            run_rate(phase, stats)
        # What the driver sent, per entry: the report checks Hud's counts against it.
        log({'type': 'phase-end', 'phase': name, 'sent': stats.sent(), 'flowRequests': js_order(stats.flow_requests)})
        stats.print()
        if guard.reason:
            print(f'\nstopped: {guard.reason}')
            sys.exit(2)
    print(f'\ndone {now_iso()}')


def main():
    global opts, plan, base_url, run_id, limits, plan_dir, guard
    sys.stdout.reconfigure(line_buffering=True)
    opts = parse_args(sys.argv[1:])
    with open(opts.plan_path, encoding='utf-8') as f:
        plan = load_json(f.read())
    base_url = re.sub(r'/$', '', opts.base_url or plan.get('baseUrl') or '')
    if not base_url:
        eprint('No baseUrl in plan and no --base-url given')
        sys.exit(1)
    run_id = opts.run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    limits = plan.get('limits') or {}
    for p in plan['phases']:
        hooks = [k for k in ('before', 'pulse', 'after') if p.get(k) is not None]
        if hooks:
            eprint(f"Phase {js_str(p.get('name'))} has {', '.join(hooks)}: the driver runs no hooks. "
                   "The run sends the mapped requests only, with no fault; remove them.")
            sys.exit(1)
        peak = nn(p.get('rate'), 0)
        if truthy(limits.get('maxRps')) and peak > limits['maxRps']:
            eprint(f"Phase {js_str(p.get('name'))} runs at {js_str(peak)} rps, above limits.maxRps "
                   f"{js_str(limits['maxRps'])}. Lower it or raise the agreed limit.")
            sys.exit(1)
    plan_dir = os.path.dirname(os.path.abspath(opts.plan_path))
    auth.header = js_str((plan.get('auth') or {}).get('header') or 'authorization').lower()
    guard = Guard()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, on_signal)
    try:
        run()
    except DriveError as e:
        eprint(f'Error: {e}')
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
