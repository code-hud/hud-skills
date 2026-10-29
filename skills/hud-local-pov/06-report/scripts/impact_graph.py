#!/usr/bin/env python3
"""Build the report's impact graph from three CSV exports.

The call-flow graph the report page draws for the PR's endpoint: the heaviest functions (and the
heaviest changed ones) become cards, functions on the path between two cards become wrapper beads,
every other function in the flow becomes a small dot tied to its heaviest caller, and client calls
and gateways become dependencies. The CSV contract is in 06-report/references/report-build.md.

Inputs:
  --functions      function_id, function_signature, file, invocations, avg_duration_ms, p90_duration_ms, error_rate (0-1)
  --calls          caller_function_id, callee_function_id, invocations, avg_duration_ms, error_rate (0-1)
  --changed        function (Class.method or name), optional file and change (added / modified)
  --service        service name shown on the endpoint card
  --endpoint       endpoint label, e.g. "POST /sink/batch" (default: the root function's signature)
  --into           a report JSON: writes it with this graph and the PR's added/modified counts
                   (default: write only the graph, for prExample.graph)
  --cards          cards picked by weight before changed functions are added (default 4)
  --changed-cards  heaviest changed functions added as cards (default 3)
  --dots-per-card  small dots kept under each card, changed functions always kept (default 8)

Usage:
  python3 impact_graph.py --functions endpoint-functions.csv --calls function-callers.csv \\
      --changed modified-functions.csv --service NAME --output graph.json [--endpoint LABEL] [--into report.json]

Python standard library only. Numbers follow JavaScript semantics (Number(), Math.round,
JSON.stringify) so the output matches Hud's TypeScript generator exactly.
"""

import argparse
import decimal
import json
import math
import os
import re
import sys


class MissingCsvColumnError(Exception):
    def __init__(self, file, columns):
        super().__init__(f'The {file} CSV is missing column(s): {", ".join(columns)}')


class NoEndpointFunctionError(Exception):
    def __init__(self):
        super().__init__('No endpoint function found: every function with outgoing calls also has a caller')


# --- JavaScript semantics ------------------------------------------------------------

JS_WHITESPACE = '\t\n\v\f\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a' \
                '\u2028\u2029\u202f\u205f\u3000\ufeff'
DECIMAL_LITERAL = re.compile(r'[+-]?(Infinity|([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?)')
RADIX_LITERAL = re.compile(r'0([xX][0-9a-fA-F]+|[oO][0-7]+|[bB][01]+)')
RADIX = {'x': 16, 'o': 8, 'b': 2}


def js_trim(s):
    return s.strip(JS_WHITESPACE)


def js_number(s):
    """Number(s): NaN for anything JavaScript would not read as a number."""
    s = js_trim(s)
    if s == '':
        return 0.0
    if DECIMAL_LITERAL.fullmatch(s):
        return float(s.replace('Infinity', 'inf'))
    if RADIX_LITERAL.fullmatch(s):
        return float(int(s[2:], RADIX[s[1].lower()]))
    return math.nan


def truthy(n):
    return n == n and n != 0


def to_number(s):
    """`Number(s) || 0`."""
    n = js_number(s)
    return n if truthy(n) else 0.0


def or_one(n):
    """`n || 1`."""
    return n if truthy(n) else 1


def js_spread(value):
    """The own properties `{...value}` copies."""
    if isinstance(value, dict):
        return value
    if isinstance(value, (list, str)):
        return {str(i): v for i, v in enumerate(value)}
    return {}


def js_max(a, b):
    return math.nan if a != a or b != b else max(a, b)


def js_round(x):
    """Math.round: halves round up, not to even."""
    if not math.isfinite(x):
        return x
    r = math.floor(x)
    return float(r + 1 if x - r >= 0.5 else r)


def js_number_text(x):
    """Number.prototype.toString for a finite number."""
    if x == 0:
        return '0'
    if x < 0:
        return '-' + js_number_text(-x)
    # repr is the shortest round-tripping decimal, as in JavaScript; only the layout differs.
    _, raw, exp = decimal.Decimal(repr(x)).as_tuple()
    digits = ''.join(map(str, raw)).rstrip('0')
    k = len(digits)
    n = len(raw) + exp
    if k <= n <= 21:
        return digits + '0' * (n - k)
    if 0 < n <= 21:
        return digits[:n] + '.' + digits[n:]
    if -6 < n <= 0:
        return '0.' + '0' * -n + digits
    e = n - 1
    return digits[0] + ('.' + digits[1:] if k > 1 else '') + 'e' + ('+' if e >= 0 else '-') + str(abs(e))


def js_string_literal(s):
    out = ['"']
    for ch in s:
        c = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == '\\':
            out.append('\\\\')
        elif ch in '\b\f\n\r\t':
            out.append({'\b': '\\b', '\f': '\\f', '\n': '\\n', '\r': '\\r', '\t': '\\t'}[ch])
        elif c < 0x20 or 0xd800 <= c <= 0xdfff:
            out.append(f'\\u{c:04x}')
        else:
            out.append(ch)
    out.append('"')
    return ''.join(out)


ARRAY_INDEX = re.compile(r'0|[1-9][0-9]*')


def js_key_order(obj):
    """Property order of a JavaScript object: array-index keys first, ascending, then insertion order."""
    index = sorted((k for k in obj if ARRAY_INDEX.fullmatch(k) and int(k) < 2 ** 32 - 1), key=int)
    taken = set(index)
    return index + [k for k in obj if k not in taken]


def to_json(value, indent=None, _depth=0):
    """JSON.stringify(value, null, indent)."""
    if value is None:
        return 'null'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if isinstance(value, (int, float)):
        try:
            x = float(value)
        except OverflowError:
            return 'null'
        return js_number_text(x) if math.isfinite(x) else 'null'
    if isinstance(value, str):
        return js_string_literal(value)
    if isinstance(value, dict):
        items = [(k, value[k]) for k in js_key_order(value)]
        parts = [js_string_literal(k) + (': ' if indent else ':') + to_json(v, indent, _depth + 1) for k, v in items]
        return _wrap('{', '}', parts, indent, _depth)
    if isinstance(value, (list, tuple)):
        return _wrap('[', ']', [to_json(v, indent, _depth + 1) for v in value], indent, _depth)
    raise TypeError(f'cannot serialize {type(value).__name__}')


def _wrap(open_, close, parts, indent, depth):
    if not parts:
        return open_ + close
    if not indent:
        return open_ + ','.join(parts) + close
    inner = '\n' + ' ' * (indent * (depth + 1))
    return open_ + inner + (',' + inner).join(parts) + '\n' + ' ' * (indent * depth) + close


def parse_json(text):
    """JSON.parse: no NaN / Infinity literals."""
    def reject(name):
        raise ValueError(f'Unexpected token {name}')
    return json.loads(text, parse_constant=reject)


# --- CSV -----------------------------------------------------------------------------

def parse_csv(text):
    """RFC 4180-style CSV (quoted fields, `""` escapes, CRLF, optional byte-order mark) into rows
    keyed by the trimmed, lower-cased header. Blank lines are skipped."""
    records = []
    record = []
    field = []
    quoted = False
    source = text[1:] if text.startswith('\ufeff') else text

    def end_record():
        nonlocal record, field
        record.append(''.join(field))
        field = []
        if len(record) > 1 or record[0] != '':
            records.append(record)
        record = []

    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if quoted:
            if ch == '"' and i + 1 < n and source[i + 1] == '"':
                field.append('"')
                i += 1
            elif ch == '"':
                quoted = False
            else:
                field.append(ch)
        elif ch == '"':
            quoted = True
        elif ch == ',':
            record.append(''.join(field))
            field = []
        elif ch == '\n' or ch == '\r':
            if ch == '\r' and i + 1 < n and source[i + 1] == '\n':
                i += 1
            end_record()
        else:
            field.append(ch)
        i += 1
    if field or record:
        end_record()

    if not records:
        return []
    columns = [js_trim(name).lower() for name in records[0]]
    return [{column: (row[i] if i < len(row) else '') for i, column in enumerate(columns)} for row in records[1:]]


def require_columns(file, rows, columns):
    if not rows:
        return
    missing = [c for c in columns if c not in rows[0]]
    if missing:
        raise MissingCsvColumnError(file, missing)


# --- Call model ----------------------------------------------------------------------

FUNCTION_COLUMNS = ['function_id', 'function_signature', 'file', 'invocations', 'avg_duration_ms',
                    'p90_duration_ms', 'error_rate']
CALL_COLUMNS = ['caller_function_id', 'callee_function_id', 'invocations', 'avg_duration_ms', 'error_rate']
CHANGED_FUNCTION_COLUMNS = ['function']

JS_RE = re.ASCII
# `client`: the function is the client call itself (e.g. `RedisClient.get`); it is drawn as the
# dependency, never as a function. `gateway`: the function stays in the flow and also gets a call
# into the dependency.
DEPENDENCY_RULES = [
    ('Redis', 'cache', 'client',
     lambda n: n['signature'].startswith('RedisClient.') or bool(re.match(r'node_?redis', base_name(n['file']), JS_RE | re.I))),
    ('Kafka', 'queue', 'gateway',
     lambda n: bool(re.search(r'KafkaRecordBatcher\.withDeadline\(', n['signature'], JS_RE))
     or n['signature'].startswith('send(pattern')),
    ('Amazon SQS', 'queue', 'gateway', lambda n: bool(re.search(r'Sqs\w*\.send\w*\(', n['signature'], JS_RE | re.I))),
    ('DynamoDB', 'database', 'gateway',
     lambda n: bool(re.match(r'Dynamo\w*\.(get|put|query|scan|update|delete|batch\w*)\(', n['signature'], JS_RE | re.I))),
    ('PostgreSQL', 'database', 'client', lambda n: bool(re.match(r'(Pg|Postgres)\w*(Client|Pool)\.', n['signature'], JS_RE | re.I))),
    ('MongoDB', 'database', 'client', lambda n: bool(re.match(r'Mongo\w*(Client|Collection)\.', n['signature'], JS_RE | re.I))),
    ('Amazon S3', 'storage', 'client', lambda n: bool(re.match(r'S3\w*Client\.', n['signature'], JS_RE | re.I))),
    ('HTTP services', 'service', 'client', lambda n: bool(re.match(r'(axios|fetch|HttpService|got)\b', n['signature'], JS_RE | re.I))),
]

# Shared libraries are compiled into each service under its own path.
LIBRARY_PATH = re.compile(r'(^|/)(libs?|node_modules|packages|vendor)/', JS_RE | re.I)


def base_name(file):
    return file.split('/')[-1]


def build_call_model(function_rows, call_rows):
    """A function shipped from a shared library under two paths becomes one node; everything else
    keeps its own node. Calls between the same two nodes are summed, self-calls and calls to
    unknown functions are dropped."""
    require_columns('functions', function_rows, FUNCTION_COLUMNS)
    require_columns('calls', call_rows, CALL_COLUMNS)

    library_functions = {f"{row['function_signature']}|{base_name(row['file'])}"
                         for row in function_rows if LIBRARY_PATH.search(row['file'])}
    key_of_id = {}
    nodes = {}
    p90_weight = {}
    error_weight = {}
    for row in function_rows:
        fid = js_trim(row['function_id'])
        if not fid:
            continue
        signature = row['function_signature'] or fid
        merge_key = f"{signature}|{base_name(row['file'])}"
        key = f'lib:{merge_key}' if merge_key in library_functions else f'fn:{fid}'
        key_of_id[fid] = key

        invocations = to_number(row['invocations'])
        node = nodes.get(key)
        if node is None:
            node = nodes[key] = {'key': key, 'signature': signature, 'file': row['file'], 'invocations': 0.0,
                                 'totalMs': 0.0, 'avgMs': 0.0, 'p90Ms': 0.0, 'errorRate': 0.0,
                                 'dependency': None, 'out': [], 'in': []}
        node['invocations'] += invocations
        node['totalMs'] += invocations * to_number(row['avg_duration_ms'])
        p90_weight[key] = p90_weight.get(key, 0.0) + invocations * to_number(row['p90_duration_ms'])
        error_weight[key] = error_weight.get(key, 0.0) + invocations * to_number(row['error_rate'])
        if not LIBRARY_PATH.search(row['file']):
            node['file'] = row['file']

    for node in nodes.values():
        runs = or_one(node['invocations'])
        node['avgMs'] = node['totalMs'] / runs
        node['p90Ms'] = p90_weight.get(node['key'], 0.0) / runs
        node['errorRate'] = error_weight.get(node['key'], 0.0) / runs
        rule = next((r for r in DEPENDENCY_RULES if r[3](node)), None)
        node['dependency'] = {'name': rule[0], 'type': rule[1], 'role': rule[2]} if rule else None

    edges = {}
    edge_error_weight = {}
    for row in call_rows:
        src = key_of_id.get(js_trim(row['caller_function_id']))
        dst = key_of_id.get(js_trim(row['callee_function_id']))
        if not src or not dst or src == dst:
            continue
        key = f'{src}>{dst}'
        edge = edges.get(key)
        if edge is None:
            edge = edges[key] = {'key': key, 'from': src, 'to': dst, 'invocations': 0.0, 'totalMs': 0.0, 'errorRate': 0.0}
            nodes[src]['out'].append(edge)
            nodes[dst]['in'].append(edge)
        invocations = to_number(row['invocations'])
        edge['invocations'] += invocations
        edge['totalMs'] += invocations * to_number(row['avg_duration_ms'])
        edge_error_weight[key] = edge_error_weight.get(key, 0.0) + invocations * to_number(row['error_rate'])
    for edge in edges.values():
        edge['errorRate'] = edge_error_weight.get(edge['key'], 0.0) / or_one(edge['invocations'])

    roots = sorted((n for n in nodes.values() if not n['in'] and n['out'] and not n['dependency']),
                   key=lambda n: -n['totalMs'])
    return {'nodes': nodes, 'edges': edges, 'roots': roots}


# --- Changed functions ---------------------------------------------------------------

def change_kind_of(value):
    """`added` / `new` (or text starting with "New") mean added; anything else means modified."""
    return 'added' if re.match(r'(added|new)\b', js_trim(value), JS_RE | re.I) else 'modified'


def read_changed_functions(rows):
    require_columns('modified functions', rows, CHANGED_FUNCTION_COLUMNS)
    return [{'name': js_trim(row['function']), 'file': js_trim(row.get('file', '')),
             'change': change_kind_of(row.get('change', ''))}
            for row in rows if js_trim(row['function']) != '']


def name_of(signature):
    paren = signature.find('(')
    return signature[:paren] if paren > 0 else signature


def match_changed_functions(model, changed_functions):
    matches = {}
    for changed in changed_functions:
        name = name_of(changed['name'])
        file = base_name(changed['file'])
        for node in model['nodes'].values():
            if name_of(node['signature']) != name:
                continue
            if file and base_name(node['file']) != file:
                continue
            matches[node['key']] = changed['change']
    return matches


# --- Graph selection -----------------------------------------------------------------

DEFAULTS = {'cardBudget': 4, 'changedCards': 3, 'minorCallsPerCard': 8}
# A function scoring below this share is never a card on weight alone.
MIN_CARD_SCORE = 0.003
MIN_CHANGED_CARD_SHARE = 0.01
# Rounds of promoting functions that several card paths run through.
BRANCH_POINT_ROUNDS = 6


def is_client_call(node):
    return bool(node['dependency']) and node['dependency']['role'] == 'client'


def analyze_flow(model, root):
    nodes = model['nodes']
    state = {}
    back_edges = set()
    # Depth-first, iteratively: a deep call chain would overflow Python's recursion limit.
    state[root['key']] = 'open'
    stack = [(root['key'], iter(nodes[root['key']]['out']))]
    while stack:
        key, edges = stack[-1]
        for edge in edges:
            if is_client_call(nodes[edge['to']]):
                continue
            seen = state.get(edge['to'])
            if seen == 'open':
                back_edges.add(edge['key'])
            elif not seen:
                state[edge['to']] = 'open'
                stack.append((edge['to'], iter(nodes[edge['to']]['out'])))
                break
        else:
            state[key] = 'done'
            stack.pop()

    members = list(state)
    member_set = set(members)

    def is_forward(edge):
        return edge['from'] in member_set and edge['to'] in member_set and edge['key'] not in back_edges

    endpoint_ms = or_one(root['totalMs'])
    weights = {}
    for key in members:
        fn = nodes[key]
        callees_ms = 0
        heaviest_callee_ms = 0
        for edge in fn['out']:
            callees_ms = callees_ms + edge['totalMs']
            heaviest_callee_ms = js_max(heaviest_callee_ms, edge['totalMs'])
        self_ms = js_max(0, fn['totalMs'] - callees_ms)
        weights[key] = {
            'selfMs': self_ms,
            'score': self_ms / endpoint_ms + (0.08 * fn['totalMs']) / endpoint_ms
            + (0.04 + fn['errorRate'] if fn['errorRate'] > 0 else 0),
            'passThrough': (key != root['key'] and fn['totalMs'] > 0 and self_ms < 0.15 * fn['totalMs']
                            and heaviest_callee_ms > 0.75 * fn['totalMs'] and not fn['dependency']
                            and fn['errorRate'] == 0),
        }

    heaviest_caller = {}
    for key in members:
        if key == root['key']:
            continue
        best = None
        for edge in nodes[key]['in']:
            if not is_forward(edge):
                continue
            if (best is None or edge['totalMs'] > best['totalMs']
                    or (edge['totalMs'] == best['totalMs'] and edge['invocations'] > best['invocations'])):
                best = edge
        if best:
            heaviest_caller[key] = best['from']
    return {'model': model, 'root': root, 'members': members, 'is_forward': is_forward,
            'weights': weights, 'heaviest_caller': heaviest_caller}


def select_cards(flow, changes, card_budget, changed_cards):
    root, weights, heaviest_caller, nodes = flow['root'], flow['weights'], flow['heaviest_caller'], flow['model']['nodes']
    candidates = [k for k in flow['members'] if k != root['key']]

    picked = dict.fromkeys(sorted(
        (k for k in candidates if not weights[k]['passThrough'] and weights[k]['score'] >= MIN_CARD_SCORE),
        key=lambda k: -weights[k]['score'])[:card_budget])
    for key in candidates:
        if nodes[key]['errorRate'] > 0:
            picked[key] = None
    endpoint_ms = or_one(root['totalMs'])
    for key in sorted((k for k in candidates if k in changes and k not in picked
                       and nodes[k]['totalMs'] / endpoint_ms >= MIN_CHANGED_CARD_SHARE),
                      key=lambda k: -nodes[k]['totalMs'])[:changed_cards]:
        picked[key] = None

    cards = dict.fromkeys([root['key'], *picked])

    def up_chain(card):
        key = heaviest_caller.get(card)
        while key and key not in cards:
            yield key
            key = heaviest_caller.get(key)

    # A function that several card paths run through becomes a card too, so branches stay visible.
    for _ in range(BRANCH_POINT_ROUNDS):
        paths_through = {}
        for card in cards:
            for key in up_chain(card):
                paths_through[key] = paths_through.get(key, 0) + 1
        branch_points = {}
        for card in cards:
            for key in up_chain(card):
                if paths_through.get(key, 0) >= 2:
                    branch_points[key] = None
                    break
        if not branch_points:
            break
        cards.update(branch_points)

    parent_card = {}
    wrappers = {}
    wrapper_owner = {}
    for card in cards:
        if card == root['key']:
            continue
        chain = []
        key = heaviest_caller.get(card)
        while key and key not in cards:
            chain.insert(0, key)
            key = heaviest_caller.get(key)
        parent_card[card] = key or root['key']
        wrappers[card] = chain
        for wrapper in chain:
            wrapper_owner[wrapper] = card
    return cards, parent_card, wrappers, wrapper_owner


def to_percent(fraction):
    return js_round(fraction * 10000) / 100


def round_ms(value):
    return js_round(value * 1000) / 1000


def stats_of(node):
    return {'runs': node['invocations'], 'p90Ms': round_ms(node['p90Ms']), 'errorRatePercent': to_percent(node['errorRate'])}


def label_of(signature):
    """"Class.method(args)" -> "method(args)"; callbacks and anonymous functions keep their full signature."""
    paren = signature.find('(')
    head = signature[:paren] if paren > 0 else ''
    if not head or signature.endswith('callback'):
        return signature
    return head[head.rfind('.') + 1:] + signature[paren:]


def dependency_id(name):
    return 'dependency:' + re.sub(r'\W+', '-', name.lower(), flags=JS_RE)


def select_impact_graph(model, changes, service, endpoint_label=None, card_budget=None, changed_cards=None,
                        minor_calls_per_card=None):
    if not model['roots']:
        raise NoEndpointFunctionError()
    root = model['roots'][0]
    card_budget = DEFAULTS['cardBudget'] if card_budget is None else card_budget
    changed_cards = DEFAULTS['changedCards'] if changed_cards is None else changed_cards
    minor_calls_per_card = DEFAULTS['minorCallsPerCard'] if minor_calls_per_card is None else minor_calls_per_card
    flow = analyze_flow(model, root)
    cards, parent_card, wrappers, wrapper_owner = select_cards(flow, changes, card_budget, changed_cards)
    nodes, edges, heaviest_caller = model['nodes'], model['edges'], flow['heaviest_caller']

    def change_of(key):
        return changes.get(key, 'unchanged')

    endpoint_ms = or_one(root['totalMs'])

    functions = [{
        'id': key,
        'label': label_of(nodes[key]['signature']),
        'signature': nodes[key]['signature'],
        'change': change_of(key),
        'ownTimePercent': to_percent(flow['weights'][key]['selfMs'] / endpoint_ms),
        'stats': stats_of(nodes[key]),
    } for key in cards if key != root['key']]

    calls = []
    tree_calls = set()
    for card, chain in wrappers.items():
        src = parent_card[card]
        first = edges.get(f'{src}>{chain[0] if chain else card}')
        last = edges.get(f'{chain[-1] if chain else src}>{card}')
        if not first or not last:
            continue
        tree_calls.add(f'{src}>{card}')
        calls.append({
            'from': src,
            'to': card,
            'calls': last['invocations'],
            'timeThroughPercent': to_percent(last['totalMs'] / endpoint_ms),
            'errorRatePercent': to_percent(js_max(first['errorRate'], last['errorRate'])),
            'wrappers': [{
                'label': label_of(nodes[key]['signature']),
                'signature': nodes[key]['signature'],
                'change': change_of(key),
                'timeThroughPercent': to_percent(nodes[key]['totalMs'] / endpoint_ms),
                'stats': stats_of(nodes[key]),
            } for key in chain],
        })
    for edge in edges.values():
        if not (flow['is_forward'](edge) and edge['from'] in cards and edge['to'] in cards
                and edge['to'] != root['key'] and edge['key'] not in tree_calls):
            continue
        calls.append({
            'from': edge['from'],
            'to': edge['to'],
            'calls': edge['invocations'],
            'timeThroughPercent': to_percent(edge['totalMs'] / endpoint_ms),
            'errorRatePercent': to_percent(edge['errorRate']),
            'wrappers': [],
        })

    # A wrapper sits on the line into its card, so what it calls belongs to the card above it:
    # that card reaches it through the wrapper, while the card below does not call it.
    def card_above_wrapper(wrapper):
        return parent_card[wrapper_owner[wrapper]]

    def owning_card(key):
        caller = heaviest_caller.get(key)
        while caller and caller not in cards and caller not in wrapper_owner:
            caller = heaviest_caller.get(caller)
        if not caller:
            return root['key']
        return card_above_wrapper(caller) if caller in wrapper_owner else caller

    all_dots = [key for key in flow['members'] if key not in cards and key not in wrapper_owner]
    all_dot_set = set(all_dots)
    dots_by_card = {}
    for key in all_dots:
        dots_by_card.setdefault(owning_card(key), []).append(key)
    kept_dots = set()
    for dots in dots_by_card.values():
        kept_dots.update(sorted(dots, key=lambda k: -nodes[k]['totalMs'])[:minor_calls_per_card])
        kept_dots.update(key for key in dots if key in changes)

    def caller_of(key):
        # Tie to the nearest dot or card up the chain. Wrappers are not dots, so the walk steps over them.
        current = heaviest_caller.get(key)
        if not current:
            return owning_card(key)
        seen = {key}
        while current not in cards and current not in all_dot_set:
            if current in seen:
                return owning_card(key)
            seen.add(current)
            current = card_above_wrapper(current) if current in wrapper_owner else heaviest_caller.get(current)
            if not current:
                return owning_card(key)
        return current

    minor_calls = [{
        'id': key,
        'label': label_of(nodes[key]['signature']),
        'signature': nodes[key]['signature'],
        'caller': caller_of(key),
        'timeThroughPercent': to_percent(nodes[key]['totalMs'] / endpoint_ms),
        'change': change_of(key),
        'shownByDefault': key in kept_dots,
        'stats': stats_of(nodes[key]),
    } for key in all_dots]

    primary_caller = {call['id']: call['caller'] for call in minor_calls}
    for fn in functions:
        if fn['id'] in parent_card:
            primary_caller[fn['id']] = parent_card[fn['id']]
    graph_ids = dict.fromkeys([root['key'], *(fn['id'] for fn in functions), *(call['id'] for call in minor_calls)])
    extra_callers = [{'from': edge['from'], 'to': key}
                     for key in graph_ids if key != root['key']
                     for edge in nodes[key]['in']
                     if flow['is_forward'](edge) and edge['from'] != primary_caller.get(key)
                     and edge['from'] in graph_ids and not (edge['from'] in cards and key in cards)]

    def visible_card_of(key):
        if key in cards:
            return key
        return card_above_wrapper(key) if key in wrapper_owner else owning_card(key)

    dependencies, dependency_calls = select_dependencies(flow, visible_card_of)
    return {
        'endpoint': {
            'id': root['key'],
            'service': service,
            'label': root['signature'] if endpoint_label is None else endpoint_label,
            'stats': stats_of(root),
        },
        'functions': functions,
        'dependencies': dependencies,
        'calls': calls,
        'dependencyCalls': dependency_calls,
        'minorCalls': minor_calls,
        'extraCallers': extra_callers,
    }


def select_dependencies(flow, visible_card_of):
    """Dependencies are reached through client calls (the callee is the client, e.g. `RedisClient.get`)
    or gateway functions. A request's time waiting on a dependency is the own time of the functions
    that call it; lines start at the visible card that owns the calling function."""
    model, root, weights = flow['model'], flow['root'], flow['weights']
    nodes = model['nodes']
    totals = {}

    def record(kind, caller_key, invocations, error_rate):
        t = totals.get(kind['name'])
        if t is None:
            t = totals[kind['name']] = {
                'dependency': {'id': dependency_id(kind['name']), 'name': kind['name'], 'type': kind['type'],
                               'calls': 0.0, 'waitMsPerRequest': 0, 'errorRatePercent': 0},
                'errorWeight': 0.0, 'waitMs': 0.0, 'callers': {}, 'waiting': set(),
            }
        card = visible_card_of(caller_key)
        caller = t['callers'].setdefault(card, {'invocations': 0.0, 'errorWeight': 0.0, 'waitMs': 0.0})
        t['dependency']['calls'] += invocations
        t['errorWeight'] += invocations * error_rate
        caller['invocations'] += invocations
        caller['errorWeight'] += invocations * error_rate
        if caller_key not in t['waiting']:
            t['waiting'].add(caller_key)
            self_ms = weights[caller_key]['selfMs']
            t['waitMs'] += self_ms
            caller['waitMs'] += self_ms

    for key in flow['members']:
        fn = nodes[key]
        if fn['dependency'] and fn['dependency']['role'] == 'gateway' and key != root['key']:
            record(fn['dependency'], key, fn['invocations'], fn['errorRate'])
        for edge in fn['out']:
            callee = nodes[edge['to']]
            if callee['dependency'] and callee['dependency']['role'] == 'client':
                record(callee['dependency'], key, edge['invocations'], edge['errorRate'])

    requests = or_one(root['invocations'])
    endpoint_ms = or_one(root['totalMs'])
    dependencies = [{
        **t['dependency'],
        'waitMsPerRequest': round_ms(t['waitMs'] / requests),
        'errorRatePercent': to_percent(t['errorWeight'] / t['dependency']['calls'] if truthy(t['dependency']['calls']) else 0),
    } for t in totals.values()]
    dependency_calls = [{
        'from': card,
        'to': t['dependency']['id'],
        'calls': caller['invocations'],
        'timeThroughPercent': to_percent(caller['waitMs'] / endpoint_ms),
        'errorRatePercent': to_percent(caller['errorWeight'] / caller['invocations'] if truthy(caller['invocations']) else 0),
    } for t in totals.values() for card, caller in t['callers'].items()]
    return dependencies, dependency_calls


# --- Entry points --------------------------------------------------------------------

def build_impact_graph(functions_csv_text, calls_csv_text, changed_csv_text, service, endpoint_label=None,
                       card_budget=None, changed_cards=None, minor_calls_per_card=None):
    """The report's impact graph from the three CSV texts, and the PR card's added/modified counters."""
    model = build_call_model(parse_csv(functions_csv_text), parse_csv(calls_csv_text))
    changed = read_changed_functions(parse_csv(changed_csv_text))
    graph = select_impact_graph(model, match_changed_functions(model, changed), service, endpoint_label,
                                card_budget, changed_cards, minor_calls_per_card)
    added = sum(1 for fn in changed if fn['change'] == 'added')
    return {'graph': graph, 'addedFunctions': added, 'modifiedFunctions': len(changed) - added}


def with_impact_graph(report, graph_input):
    """The report with this graph and the PR card's added/modified counters."""
    report = js_spread(report)
    return {**report, 'prExample': {
        **js_spread(report.get('prExample')),
        'graph': graph_input['graph'],
        'addedFunctions': graph_input['addedFunctions'],
        'modifiedFunctions': graph_input['modifiedFunctions'],
    }}


def read_text(path):
    with open(path, encoding='utf-8', errors='replace', newline='') as fh:
        return fh.read()


def read_count(value, flag):
    if value is None:
        return None
    count = js_number(value)
    if not math.isfinite(count) or count != math.floor(count) or count < 0:
        raise ValueError(f'{flag} must be a whole number, got "{value}"')
    return int(count)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 allow_abbrev=False)
    for flag in ('--functions', '--calls', '--changed', '--service', '--output', '--endpoint', '--into',
                 '--cards', '--changed-cards', '--dots-per-card'):
        ap.add_argument(flag)
    args = ap.parse_args(argv)
    if not (args.functions and args.calls and args.changed and args.service and args.output):
        ap.print_usage(sys.stderr)
        sys.exit('--functions, --calls, --changed, --service and --output are required')
    try:
        graph_input = build_impact_graph(
            read_text(args.functions), read_text(args.calls), read_text(args.changed), args.service,
            endpoint_label=args.endpoint,
            card_budget=read_count(args.cards, '--cards'),
            changed_cards=read_count(args.changed_cards, '--changed-cards'),
            minor_calls_per_card=read_count(args.dots_per_card, '--dots-per-card'),
        )
        result = with_impact_graph(parse_json(read_text(args.into)), graph_input) if args.into else graph_input['graph']
    except (OSError, ValueError, MissingCsvColumnError, NoEndpointFunctionError) as error:
        sys.exit(str(error))
    with open(args.output, 'w', encoding='utf-8', newline='') as fh:
        fh.write(to_json(result, 2) + '\n')
    graph = graph_input['graph']
    print(f"Wrote {os.path.abspath(args.output)}: {len(graph['functions'])} cards, {len(graph['minorCalls'])} dots, "
          f"{len(graph['dependencies'])} dependencies ({graph_input['addedFunctions']} added, "
          f"{graph_input['modifiedFunctions']} modified functions listed)")


if __name__ == '__main__':
    main()
